"""Audio: microphone to brain to speaker.

Five stages, each a Protocol with a real adapter and a working fallback, so
the whole loop — including barge-in, which is the thing that sinks these
builds — runs and is tested on a laptop with no models and no hardware.

The wake word is **"Hey Friday"**, and nothing downstream knows its name.
"""

from __future__ import annotations

from .device import SoundDevice, converse, make, typed_frames
from .loop import BARGE_MS, PREROLL_MS, Listener, Turn, text_frames
from .stages import (
    FRAME, FRAME_MS, SAMPLE_RATE, WAKE_PHRASE, ASR, TTS, VAD, EnergyVAD,
    KeywordWake, MoonshineASR, OpenWakeWord, PiperTTS, ScriptedASR, SileroVAD,
    SilenceTurn, SmartTurn, ToneTTS, TurnDecision, TurnDetector, WakeWord,
    sentences,
)

__all__ = [
    "ASR", "BARGE_MS", "EnergyVAD", "FRAME", "FRAME_MS", "KeywordWake",
    "Listener", "MoonshineASR", "OpenWakeWord", "PREROLL_MS", "PiperTTS",
    "SAMPLE_RATE", "SileroVAD", "SilenceTurn", "SmartTurn", "ScriptedASR",
    "TTS", "ToneTTS", "Turn", "TurnDecision", "TurnDetector", "VAD",
    "SoundDevice", "WAKE_PHRASE", "WakeWord", "converse", "make",
    "sentences", "text_frames", "typed_frames",
]
