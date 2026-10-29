"""Real microphones and speakers, and the wiring that joins the loop to a brain.

Two things live here: a capture/playout backend that talks to the sound card,
and `converse()`, which is the only place the audio loop and the memory system
meet.

The join is deliberately thin. `Listener` knows nothing about memory and
`Brain` knows nothing about audio, so either can be tested, replaced or run
alone — and the sequencing that actually matters (reply first, remember after)
is visible in twenty lines rather than spread through both.
"""

from __future__ import annotations

import queue
import sys
from typing import Any, Callable, Iterator

import numpy as np

from ..runtime.events import State
from .loop import Listener, Turn
from .stages import FRAME, SAMPLE_RATE, ScriptedASR


class SoundDevice:
    """Capture and playout through `sounddevice` (PortAudio).

    Both directions go through the *same* device on purpose. The reSpeaker's
    hardware AEC needs playback and capture in one clock domain with an exact
    echo reference; split them across two USB devices and they run on
    independent clocks, drift, and never hold lock — which is where software
    AEC on a Pi dies.
    """

    def __init__(self, device: str | int | None = None,
                 sample_rate: int = SAMPLE_RATE, block: int = FRAME) -> None:
        import sounddevice as sd  # noqa: PLC0415

        self._sd = sd
        self.device = device
        self.sample_rate = sample_rate
        self.block = block
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self._stream: Any = None
        self._out: Any = None

    def start(self) -> None:
        def callback(indata, _frames, _time, status):
            if status:
                pass          # overruns happen; dropping a frame beats blocking
            try:
                self._frames.put_nowait(indata[:, 0].copy())
            except queue.Full:
                # Drop the oldest. A stale frame is worthless and the loop
                # falling behind the microphone is unrecoverable.
                try:
                    self._frames.get_nowait()
                    self._frames.put_nowait(indata[:, 0].copy())
                except queue.Empty:
                    pass

        self._stream = self._sd.InputStream(
            device=self.device, channels=1, samplerate=self.sample_rate,
            blocksize=self.block, dtype="float32", callback=callback)
        self._stream.start()
        self._out = self._sd.OutputStream(
            device=self.device, channels=1, samplerate=self.sample_rate,
            blocksize=self.block, dtype="float32")
        self._out.start()

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            yield self._frames.get()

    def play(self, chunk: np.ndarray) -> None:
        if self._out is not None:
            self._out.write(chunk.reshape(-1, 1))

    def stop(self) -> None:
        for stream in (self._stream, self._out):
            if stream is not None:
                stream.stop()
                stream.close()
        self._stream = self._out = None


def typed_frames(asr: ScriptedASR, *, prompt: str = "> ",
                 stream: Any = None) -> Iterator[np.ndarray]:
    """Frames from typed lines, for developing without a microphone.

    Produces audio with the right *shape* — energy while a word is being said,
    silence between utterances — so the VAD, the endpointer and the dwell
    window behave exactly as they will on a real microphone.
    """
    stream = stream or sys.stdin
    rng = np.random.default_rng(0)
    while True:
        if prompt:
            print(prompt, end="", flush=True)
        line = stream.readline()
        if not line:
            return
        line = line.strip()
        if line in ("/quit", "/exit"):
            return
        if not line:
            continue
        asr.say(line)
        for _ in range(max(2, len(line.split()) * 3)):
            yield rng.normal(0, 0.2, FRAME).astype(np.float32)
        for _ in range(30):
            yield np.zeros(FRAME, dtype=np.float32)


def converse(brain: Any, listener: Listener, frames: Iterator[np.ndarray], *,
             on_reply: Callable[[Turn, Any], None] | None = None) -> None:
    """Join the ears to the memory. The whole device, in one function.

    Order is the entire content of this function, and it is the order the
    design doc argues for:

      1. the turn arrives, already segmented and transcribed
      2. the brain retrieves and answers -- this is the sub-second budget
      3. the answer is spoken, sentence by sentence, interruptibly
      4. only then does anything get written

    Step 4 after step 3 is not an optimisation. A memory write in the critical
    path is how a device that is fast on a bench becomes slow in a house.
    """
    def handle(turn: Turn) -> None:
        brain.bus.state_changed(State.THINKING)
        reply = brain.say(turn.text, prosody=turn.prosody)
        if on_reply is not None:
            on_reply(turn, reply)
        if reply.text:
            listener.say(iter([reply.text]))

    listener.run(frames, handle)


def make(cfg: Any, *, bus: Any = None) -> Listener:
    """Build the best listener the machine can actually support.

    Every stage degrades independently: no wake-word model still gives you the
    transcript-driven fallback, no Silero still gives you the energy gate. A
    device that refuses to start because one model is missing is worse than one
    that starts degraded and says so.
    """
    from ..runtime.events import BUS
    from .stages import (EnergyVAD, KeywordWake, MoonshineASR, OpenWakeWord,
                         PiperTTS, ScriptedASR, SileroVAD, SilenceTurn,
                         SmartTurn, ToneTTS)

    def attempt(build, fallback, label):
        try:
            return build(), label
        except Exception:            # noqa: BLE001 -- degrade, never refuse
            return fallback(), f"{label} (fallback)"

    wake, wake_how = attempt(
        lambda: OpenWakeWord(cfg.wake_model, cfg.wake_phrase) if cfg.wake_model
        else (_ for _ in ()).throw(FileNotFoundError("no wake model")),
        lambda: KeywordWake(cfg.wake_phrase), "wake")
    vad, vad_how = attempt(SileroVAD, EnergyVAD, "vad")
    turn, turn_how = attempt(
        lambda: SmartTurn(cfg.turn_model) if cfg.turn_model
        else (_ for _ in ()).throw(FileNotFoundError("no turn model")),
        SilenceTurn, "turn")
    asr, asr_how = attempt(lambda: MoonshineASR(cfg.asr_model), ScriptedASR, "asr")
    tts, tts_how = attempt(
        lambda: PiperTTS(cfg.tts_model) if cfg.tts_model
        else (_ for _ in ()).throw(FileNotFoundError("no tts model")),
        ToneTTS, "tts")

    listener = Listener(wake=wake, vad=vad, turn=turn, asr=asr, tts=tts,
                        bus=bus or BUS, dwell_s=cfg.dwell_seconds,
                        barge_in=cfg.barge_in)
    listener.stages = {"wake": wake_how, "vad": vad_how, "turn": turn_how,
                       "asr": asr_how, "tts": tts_how}    # type: ignore[attr-defined]
    return listener
