"""The hot loop: microphone to speaker, with barge-in.

Run sequentially the stages sum to roughly 1.9 s and the product fails. The
fix is not faster components, it is overlap:

    ASR      starts on the first audio frame, not when you stop talking
    LLM      starts on a probably-complete transcript, not a final one
    TTS      starts on the first complete sentence, not on completion
    playout  starts on the first audio chunk, not on completed synthesis

**Barge-in is the thing to get right.** The microphones and the speaker share
a small plastic box. Without cancellation the device hears itself, transcribes
its own speech, and answers it — you cannot interrupt it, and it goes deaf
whenever it talks. The hardware half is buying AEC where playback and capture
are the same UAC2 device, which gives one clock domain and an exact echo
reference for free. The software half is this loop: **capture never stops**,
not while thinking and not while speaking, and any speech detected during
playback cancels playback immediately.

Everything memory-related happens after the reply. The hot path acquires no
new blocking calls, ever.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import numpy as np

from ..perception.prosody import Prosody, extract
from ..runtime.events import BUS, Bus, State
from .stages import (
    FRAME, FRAME_MS, SAMPLE_RATE, WAKE_PHRASE, ASR, TTS, EnergyVAD, KeywordWake,
    ScriptedASR, SilenceTurn, ToneTTS, TurnDetector, VAD, WakeWord,
)

#: How much audio before the wake word to keep. People start the sentence
#: before the detector fires, and without a pre-roll the first word or two of
#: every utterance is simply gone.
PREROLL_MS = 700.0

#: Speech during playback for this long cancels the reply. Short enough to
#: feel instant, long enough that one cough does not stop it mid-sentence.
BARGE_MS = 180.0

#: Hard ceiling on a single utterance. Something has gone wrong past this --
#: usually the device transcribing a radio.
MAX_UTTERANCE_S = 30.0


@dataclass
class Turn:
    text: str
    started_at: float
    ended_at: float
    prosody: Prosody | None = None
    woken: bool = False
    barged: bool = False
    stages: dict[str, float] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return self.ended_at - self.started_at


@dataclass
class Listener:
    """The loop. Owns no memory and no policy — it turns audio into turns and
    text into audio, and hands both to whoever is listening."""

    wake: WakeWord = field(default_factory=KeywordWake)
    vad: VAD = field(default_factory=EnergyVAD)
    turn: TurnDetector = field(default_factory=SilenceTurn)
    asr: ASR = field(default_factory=ScriptedASR)
    tts: TTS = field(default_factory=ToneTTS)
    bus: Bus = BUS
    dwell_s: float = 8.0

    def __post_init__(self) -> None:
        self._preroll: list[np.ndarray] = []
        self._preroll_max = int(PREROLL_MS / FRAME_MS)
        self._speaking_until = 0.0
        self._cancel = threading.Event()
        self._awake_until = 0.0
        self._level = 0.0

    # ------------------------------------------------------------- listening

    def run(self, frames: Iterator[np.ndarray], on_turn: Callable[[Turn], None],
            *, now: Callable[[], float] = time.monotonic) -> None:
        """Consume frames forever. Calls `on_turn` for each completed turn.

        Deliberately synchronous and single-threaded: an async version of this
        was the first thing tried and the ordering bugs it produced were far
        more expensive than the concurrency it bought. The stages are all
        sub-millisecond except ASR, and ASR is incremental.
        """
        collecting = False
        started = 0.0
        silence_ms = 0.0
        collected: list[np.ndarray] = []
        speech_during_playback = 0.0
        idle_silence_ms = 0.0

        # A transcript-driven wake detector cannot work without a recogniser
        # already running, which is exactly what a wake word exists to avoid.
        # So it forces always-on ASR, and that mode is development only: on
        # the device the detector is acoustic and this is False.
        always_on = getattr(self.wake, "needs_transcript", False)

        for frame in frames:
            t = now()
            self._level = float(np.sqrt(np.mean(np.square(frame)) + 1e-12))
            speaking = self.vad.speaking(frame)

            # Capture never stops. Not while thinking, not while speaking --
            # that is the whole of barge-in on this side.
            if t < self._speaking_until:
                speech_during_playback = (speech_during_playback + FRAME_MS
                                          if speaking else 0.0)
                if speech_during_playback >= BARGE_MS:
                    self.interrupt()
                    speech_during_playback = 0.0
                    # Fall through: the speech that interrupted is the start of
                    # the next turn, and dropping it makes the person repeat
                    # themselves, which is worse than not being interruptible.
                    self._awake_until = t + self.dwell_s

            if not collecting:
                self._preroll.append(frame)
                if len(self._preroll) > self._preroll_max:
                    self._preroll.pop(0)

            woken = False
            score = self.wake.feed(frame)
            if always_on and not collecting:
                partial = self.asr.feed(frame)
                score = max(score, self.wake.feed_text(partial))
                idle_silence_ms = 0.0 if speaking else idle_silence_ms + FRAME_MS
                if idle_silence_ms > 600.0:
                    # Otherwise the always-on transcript grows without bound
                    # and the wake phrase matches something said an hour ago.
                    self.asr.reset()
                    idle_silence_ms = 0.0
            if score >= 0.5:
                woken = True
                self._awake_until = t + self.dwell_s
                self.bus.state_changed(State.LISTENING)

            # While collecting, the device is attending by definition.
            # Checking the dwell window here unconditionally means a long
            # sentence that outlasts the window is abandoned mid-utterance and
            # silently dropped -- the person finishes speaking and nothing
            # happens at all, which is the worst failure this loop has.
            attending = woken or t < self._awake_until or collecting
            if not attending:
                continue

            if speaking and not collecting:
                collecting = True
                started = t
                collected = list(self._preroll)      # keep the run-up
                if not always_on:
                    # In always-on mode the recogniser already holds this
                    # utterance, including the wake phrase; resetting here
                    # would throw away everything said before the trigger.
                    self.asr.reset()
                self.turn.reset()
                silence_ms = 0.0
                self.bus.state_changed(State.LISTENING)

            if not collecting:
                continue

            collected.append(frame)
            partial = self.asr.feed(frame)
            silence_ms = 0.0 if speaking else silence_ms + FRAME_MS
            self.bus.publish("level", level=round(min(1.0, self._level * 8), 3))

            decision = self.turn.update(speaking=speaking, silence_ms=silence_ms,
                                        partial=partial)
            overlong = (t - started) > MAX_UTTERANCE_S
            if not (decision.ended or overlong):
                continue

            collecting = False
            text = self.asr.final().strip()
            if isinstance(self.wake, KeywordWake):
                # "Hey Friday, what's on today" must reach the brain as
                # "what's on today", or every episode starts with the device's
                # own name and drags every embedding toward it.
                stripped = self.wake.strip(text)
                woken = woken or stripped != text
                text = stripped
            if not text:
                continue

            audio = np.concatenate(collected) if collected else np.zeros(FRAME)
            out = Turn(text=text, started_at=started, ended_at=t, woken=woken,
                       prosody=extract(audio, SAMPLE_RATE,
                                       onset_latency=max(0.0, started - self._speaking_until)))
            self._awake_until = t + self.dwell_s
            # A completed turn must leave no residue. Without this the
            # always-on transcript still holds the utterance that just ended,
            # keeps being re-fed, and re-triggers the wake word on words the
            # device has already answered.
            self.asr.reset()
            self.wake.reset()
            idle_silence_ms = 0.0
            on_turn(out)

    # -------------------------------------------------------------- speaking

    def say(self, stream: Iterator[str], *,
            now: Callable[[], float] = time.monotonic) -> float:
        """Speak a token stream, sentence by sentence, interruptibly.

        Returns seconds to first audio -- the number that actually matters,
        and the one people mean when they say a device feels fast.
        """
        from .stages import sentences

        self._cancel.clear()
        self.bus.state_changed(State.SPEAKING)
        t0 = now()
        first_audio = 0.0

        for clause in sentences(stream):
            if self._cancel.is_set():
                break
            self.bus.publish("transcript", text=clause)
            for chunk in self.tts.speak(clause):
                if self._cancel.is_set():
                    break
                if first_audio == 0.0:
                    first_audio = now() - t0
                self._speaking_until = now() + len(chunk) / SAMPLE_RATE
                self.playout(chunk)

        self.bus.state_changed(State.IDLE)
        return first_audio

    def playout(self, chunk: np.ndarray) -> None:
        """Overridden by the device backend. Default is a no-op sink, which is
        what the tests want and what a headless box does."""

    def interrupt(self) -> None:
        self._cancel.set()
        self._speaking_until = 0.0
        self.bus.publish("interrupted", at=time.time())
        self.bus.state_changed(State.LISTENING)

    @property
    def interrupted(self) -> bool:
        return self._cancel.is_set()


def text_frames(lines: list[str], asr: ScriptedASR, *,
                frames_per_word: int = 3, gap_frames: int = 24,
                loud: float = 0.2) -> Iterator[np.ndarray]:
    """Drive the loop from typed lines.

    Synthesises audio with the right *shape* — energy while a word is being
    said, silence between utterances — so the VAD, the turn detector and the
    dwell window all behave exactly as they will on a microphone, without one
    being present.
    """
    rng = np.random.default_rng(0)
    for line in lines:
        asr.say(line)
        for _ in range(max(1, len(line.split()) * frames_per_word)):
            yield rng.normal(0, loud, FRAME).astype(np.float32)
        for _ in range(gap_frames):
            yield np.zeros(FRAME, dtype=np.float32)
