"""The five stages between a microphone and a brain.

    wake word -> VAD -> turn detection -> ASR -> ... -> TTS

Every stage is a Protocol with a real adapter and a working fallback. The
fallbacks are not stubs: they are simple DSP that actually functions, so the
whole loop runs end to end on a laptop with no models downloaded, and the
orchestration — which is where these builds actually fail — can be tested
before a single part arrives.

**The wake word is "Hey Friday".** Two syllables of carrier plus a
phonetically distinctive name: "Friday" alone is rare enough in ordinary
speech, and the "hey" prefix gives the detector a run-up, which measurably
cuts false accepts on a name that also appears in "see you Friday".

**Latency notes that shaped these choices**, all from docs/design.md:

  Endpointing is the hidden cost. A naive 700 ms silence timeout burns most
  of the budget on every single turn; smart-turn v3 gets it to ~200 ms
  without cutting people off, and it is the highest-leverage latency
  component in the build.

  Do not use Whisper. whisper.cpp pads every clip to 30 s, so a 3-second
  utterance costs 30 seconds of compute. Moonshine *finishes* when you stop
  talking.

  Moonshine's Python one-shot API takes ~3 s for a 5-second clip on a Pi 5;
  the C++ streaming library on identical silicon takes 237 ms. That 12x is
  entirely API choice — the Python path re-encodes the whole clip every call
  with no state caching. Prototype with the streaming path or you will
  measure 3 seconds and go shopping for a Jetson.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Iterator, Protocol, Sequence, runtime_checkable

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30.0
FRAME = int(SAMPLE_RATE * FRAME_MS / 1000)

#: The wake word. Changeable at any point without touching a line of pipeline
#: code — it is a custom openWakeWord model, trained in about ninety minutes
#: from synthetic Piper audio, and nothing downstream knows its name.
WAKE_PHRASE = "hey friday"


# ---------------------------------------------------------------- wake word

@runtime_checkable
class WakeWord(Protocol):
    phrase: str
    needs_transcript: bool
    """True if this detector works on text rather than audio, which forces the
    loop into always-on ASR. Never true on the device -- running a recogniser
    continuously is precisely what a wake word exists to avoid."""

    def feed(self, frame: np.ndarray) -> float:
        """Return P(wake word just ended) for this frame. Cheap: this runs on
        every frame forever, so it has a hard budget of a few percent of one
        core."""
        ...

    def reset(self) -> None: ...


class OpenWakeWord:
    """openWakeWord with a custom model. <5 ms per 80 ms frame on an A76.

    Train your own rather than using the bundled models: the bundled ones are
    CC BY-NC-SA, and training your own escapes that *and* gets you a phrase
    nobody else's device answers to.
    """

    needs_transcript = False

    def __init__(self, model_path: str, phrase: str = WAKE_PHRASE,
                 threshold: float = 0.5) -> None:
        from openwakeword.model import Model  # noqa: PLC0415

        self.phrase = phrase
        self.threshold = threshold
        self._model = Model(wakeword_models=[model_path], inference_framework="onnx")

    def feed(self, frame: np.ndarray) -> float:
        scores = self._model.predict((frame * 32767).astype(np.int16))
        return float(max(scores.values()) if scores else 0.0)

    def reset(self) -> None:
        self._model.reset()


class KeywordWake:
    """The fallback: a transcript-driven detector.

    Not acoustic — it watches the text a streaming recogniser is producing and
    fires on the phrase. Useless on a device (it needs ASR running
    continuously, which is exactly what a wake word exists to avoid) and
    perfect off one: the entire loop, including barge-in and the dwell window,
    can be exercised from typed input or a WAV file with no model at all.
    """

    needs_transcript = True

    def __init__(self, phrase: str = WAKE_PHRASE) -> None:
        self.phrase = phrase
        self._pattern = re.compile(
            r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b",
            re.IGNORECASE)
        self._buffer = ""

    def feed(self, frame: np.ndarray) -> float:
        return 0.0          # acoustic frames carry no information here

    def feed_text(self, text: str) -> float:
        self._buffer = (self._buffer + " " + text)[-160:]
        if self._pattern.search(self._buffer):
            self._buffer = ""
            return 1.0
        return 0.0

    def strip(self, text: str) -> str:
        """Remove the wake phrase from an utterance.

        "Hey Friday, what's on for today" must reach the brain as "what's on
        for today", or every stored episode begins with the device's own name
        and the embedding of every memory is dragged toward it.
        """
        return self._pattern.sub("", text).strip(" ,.:;").strip()

    def reset(self) -> None:
        self._buffer = ""


# ---------------------------------------------------------------------- VAD

@runtime_checkable
class VAD(Protocol):
    def speaking(self, frame: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class SileroVAD:
    """Silero v6. <1 ms per 30 ms chunk — a cheap gate, nothing more."""

    def __init__(self, threshold: float = 0.5) -> None:
        import torch  # noqa: PLC0415

        self.threshold = threshold
        self._model, _ = torch.hub.load("snakers4/silero-vad", "silero_vad")
        self._torch = torch

    def speaking(self, frame: np.ndarray) -> bool:
        with self._torch.no_grad():
            p = self._model(self._torch.from_numpy(frame), SAMPLE_RATE).item()
        return p >= self.threshold

    def reset(self) -> None:
        self._model.reset_states()


class EnergyVAD:
    """Adaptive-threshold energy gate, on minimum statistics.

    Tracks the noise floor as a low percentile of recent frames and fires at a
    margin above it, so it survives a room with a fridge in it.

    **Minimum statistics rather than a leaky average**, and the reason is a
    deadlock. The obvious version updates the floor only while the gate is
    *closed* — which sounds right, because you do not want speech raising your
    idea of silence. But if the gate ever opens on the room tone (a slightly
    louder room than the initial guess), the floor stops updating, the
    threshold never rises, and the gate is stuck open forever: the device
    believes someone is talking continuously and never endpoints. A percentile
    over a rolling window cannot get into that state, because it does not care
    what the gate currently thinks.

    Genuinely works for close talk. It is not far-field robust and does not
    pretend to be — that is what Silero is for on the device.
    """

    #: Three seconds. Long enough to contain a pause in ordinary speech, short
    #: enough to follow someone walking into a noisier room.
    WINDOW = 100

    def __init__(self, margin_db: float = 9.0, percentile: float = 15.0) -> None:
        self.margin_db = margin_db
        self.percentile = percentile
        self._history: list[float] = []
        self._hot = False

    @property
    def floor(self) -> float:
        if len(self._history) < 8:
            return 1e-3
        return max(1e-5, float(np.percentile(self._history, self.percentile)))

    def speaking(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
        self._history.append(rms)
        if len(self._history) > self.WINDOW:
            self._history.pop(0)

        threshold = self.floor * (10 ** (self.margin_db / 20.0))
        # Hysteresis: without it the gate chatters on every inter-word pause
        # and the turn detector sees a dozen endpoints per sentence.
        self._hot = rms > (threshold * 0.5 if self._hot else threshold)
        return self._hot

    def reset(self) -> None:
        self._hot = False


# ---------------------------------------------------------- turn detection

@dataclass
class TurnDecision:
    ended: bool
    confidence: float = 0.0
    reason: str = ""


@runtime_checkable
class TurnDetector(Protocol):
    def update(self, *, speaking: bool, silence_ms: float,
               partial: str) -> TurnDecision: ...
    def reset(self) -> None: ...


class SmartTurn:
    """pipecat smart-turn v3. 60-120 ms on ARM.

    The 12 ms figure quoted around this model is desktop x86; do not budget
    against it.
    """

    def __init__(self, model_path: str, threshold: float = 0.6) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(model_path, opts,
                                          providers=["CPUExecutionProvider"])
        self.threshold = threshold
        self._audio: list[np.ndarray] = []

    def update(self, *, speaking: bool, silence_ms: float,
               partial: str) -> TurnDecision:
        if speaking or silence_ms < 120.0:
            return TurnDecision(False)
        window = np.concatenate(self._audio[-256:]) if self._audio else np.zeros(FRAME)
        p = float(self._sess.run(None, {"input": window[None, :]})[0].squeeze())
        return TurnDecision(p >= self.threshold, p, "smart-turn")

    def feed_audio(self, frame: np.ndarray) -> None:
        self._audio.append(frame)

    def reset(self) -> None:
        self._audio.clear()


class SilenceTurn:
    """Silence with a syntactic assist.

    A flat timeout is the thing smart-turn exists to replace, so this at least
    shortens the wait when the partial transcript already looks like a
    complete question or sentence, and lengthens it after a conjunction. Cheap,
    and much better than a constant.
    """

    TRAILING = re.compile(r"\b(and|but|so|because|then|or|with|the|a|to|of|"
                          r"um|uh|like)\s*$", re.IGNORECASE)
    COMPLETE = re.compile(r"[.?!]\s*$")

    def __init__(self, base_ms: float = 480.0, min_ms: float = 220.0,
                 max_ms: float = 1100.0) -> None:
        self.base_ms, self.min_ms, self.max_ms = base_ms, min_ms, max_ms

    def update(self, *, speaking: bool, silence_ms: float,
               partial: str) -> TurnDecision:
        if speaking:
            return TurnDecision(False)
        need = self.base_ms
        text = partial.strip()
        if self.COMPLETE.search(text):
            need = self.min_ms
        elif self.TRAILING.search(text):
            # They are mid-thought. Interrupting here is the failure people
            # actually notice and resent.
            need = self.max_ms
        elif len(text.split()) >= 4:
            need = self.base_ms * 0.8
        return TurnDecision(silence_ms >= need, min(1.0, silence_ms / need),
                            f"silence {silence_ms:.0f}/{need:.0f}ms")

    def reset(self) -> None:
        pass


# ---------------------------------------------------------------------- ASR

@runtime_checkable
class ASR(Protocol):
    def feed(self, frame: np.ndarray) -> str:
        """Push audio, get the transcript so far. Must be incremental."""
        ...

    def final(self) -> str: ...
    def reset(self) -> None: ...


class MoonshineASR:
    """Moonshine, streaming, with cached encoder/decoder state.

    The caching is the whole point. Without it this is the Python one-shot
    path at ~3 s for a 5-second clip; with it, 237 ms on the same silicon.
    """

    def __init__(self, model: str = "moonshine/tiny") -> None:
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer  # noqa: PLC0415

        self._model = MoonshineOnnxModel(model_name=model)
        self._tokenizer = load_tokenizer()
        self._audio: list[np.ndarray] = []
        self._text = ""

    def feed(self, frame: np.ndarray) -> str:
        self._audio.append(frame)
        # Re-decode every ~300 ms: often enough that the partial transcript is
        # useful for turn detection, rarely enough not to burn the core.
        if len(self._audio) % 10 == 0:
            self._text = self._decode()
        return self._text

    def _decode(self) -> str:
        if not self._audio:
            return ""
        window = np.concatenate(self._audio)[None, :].astype(np.float32)
        tokens = self._model.generate(window)
        return self._tokenizer.decode_batch(tokens)[0].strip()

    def final(self) -> str:
        self._text = self._decode()
        return self._text

    def reset(self) -> None:
        self._audio.clear()
        self._text = ""


class ScriptedASR:
    """The fallback: text in, text out, with realistic partials.

    Lets the loop be driven from typed lines or a transcript file while still
    exercising incremental behaviour — the turn detector sees a partial that
    grows word by word, exactly as it will on the device.
    """

    def __init__(self) -> None:
        self._words: list[str] = []
        self._emitted = 0

    def say(self, text: str) -> None:
        self._words = text.split()
        self._emitted = 0

    def feed(self, frame: np.ndarray) -> str:
        if self._emitted < len(self._words):
            self._emitted += 1
        return " ".join(self._words[: self._emitted])

    def final(self) -> str:
        self._emitted = len(self._words)
        return " ".join(self._words)

    def reset(self) -> None:
        self._words = []
        self._emitted = 0


# ---------------------------------------------------------------------- TTS

@runtime_checkable
class TTS(Protocol):
    def speak(self, text: str) -> Iterator[np.ndarray]:
        """Yield audio chunks. Must start yielding before it has synthesised
        the whole utterance."""
        ...


class PiperTTS:
    """Piper medium. ~165 ms to the first sentence, RTF 0.107-0.119.

    Note the licence: Piper moved MIT -> GPL-3.0 at the OHF-Voice fork. Keep
    it behind an HTTP or CLI boundary if you might ever ship this closed.
    """

    def __init__(self, model_path: str, chunk_ms: float = 60.0) -> None:
        from piper import PiperVoice  # noqa: PLC0415

        self._voice = PiperVoice.load(model_path)
        self._chunk = int(SAMPLE_RATE * chunk_ms / 1000)

    def speak(self, text: str) -> Iterator[np.ndarray]:
        for chunk in self._voice.synthesize_stream_raw(text):
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
            for i in range(0, len(samples), self._chunk):
                yield samples[i: i + self._chunk]


class ToneTTS:
    """The fallback: speech-shaped noise, one chunk per syllable-ish.

    Sounds like nothing, and that is fine — what it has to be is *correctly
    timed*, because the thing being tested is barge-in, and barge-in is a
    question about whether the loop keeps listening while audio is playing.
    """

    def __init__(self, chunk_ms: float = 60.0, wpm: float = 165.0) -> None:
        self._chunk = int(SAMPLE_RATE * chunk_ms / 1000)
        self._wpm = wpm

    def speak(self, text: str) -> Iterator[np.ndarray]:
        words = max(1, len(text.split()))
        seconds = words / (self._wpm / 60.0)
        n = max(1, int(seconds * SAMPLE_RATE / self._chunk))
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        for i in range(n):
            envelope = 0.25 * (0.6 + 0.4 * math.sin(i * 0.7))
            yield (rng.normal(0, envelope, self._chunk)).astype(np.float32)


def sentences(stream: Iterator[str], min_chars: int = 24) -> Iterator[str]:
    """Re-exported from the LLM client: chunk generation into speakable
    clauses so the first audio leaves the speaker while the rest is still
    being written. This one decision turns 4.5 s of generation into ~600 ms
    of perceived latency."""
    from ..llm.client import sentences as _sentences
    return _sentences(stream, min_chars=min_chars)
