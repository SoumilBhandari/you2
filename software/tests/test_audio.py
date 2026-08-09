"""Ears: wake word, endpointing, and barge-in.

Barge-in is the item docs/design.md ranks as most likely to sink the project,
and the one that "cannot be tested any other way" than on hardware. That is
true of the *acoustic* half — whether the AEC holds when a speaker is 60 mm
from a microphone. It is not true of the orchestration half, which is whether
the loop keeps capturing while it talks and cancels when it hears you. That
half is most of the code, all of the logic, and it is tested here.
"""

from __future__ import annotations

import numpy as np
import pytest

from you2 import BrainConfig, SimClock
from you2.audio import (
    BARGE_MS, FRAME, FRAME_MS, PREROLL_MS, EnergyVAD, KeywordWake, Listener,
    ScriptedASR, SilenceTurn, ToneTTS, converse, make, sentences, text_frames,
)
from you2.llm import FakeLLM
from you2.runtime import Brain, Bus, State
from you2.substrate import HashEmbedder, Kind


def listener(**kw):
    asr = kw.pop("asr", None) or ScriptedASR()
    return Listener(wake=KeywordWake("hey friday"), asr=asr, bus=Bus(), **kw), asr


class Ticker:
    """A clock that advances one frame per frame.

    The loop takes `now` precisely so timing can be driven deterministically:
    dwell windows and barge-in thresholds are in milliseconds, and a test that
    depends on how fast the machine happens to run is not a test.
    """

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += FRAME_MS / 1000.0
        return self.t


def run(lines, **kw):
    ear, asr = listener(**kw)
    turns: list = []
    ear.run(text_frames(lines, asr), turns.append, now=Ticker())
    return ear, turns


# ------------------------------------------------------------------ waking

def test_nothing_is_heard_until_the_wake_word():
    """Other people are in the room and have not agreed to anything."""
    _, turns = run(["what time is the meeting", "someone else talking entirely"])
    assert turns == []


def test_the_wake_word_opens_the_session():
    _, turns = run(["hey friday what time is the meeting"])
    assert len(turns) == 1
    assert turns[0].woken


def test_the_wake_phrase_is_stripped_from_the_utterance():
    """"Hey Friday, what's on today" must reach the brain as "what's on
    today", or every episode begins with the device's own name and drags every
    embedding toward it."""
    _, turns = run(["hey friday what is on today"])
    assert turns[0].text == "what is on today"
    assert "friday" not in turns[0].text.lower()


def test_dwell_lets_a_follow_up_through_without_the_wake_word():
    _, turns = run(["hey friday how is the garden wall",
                    "and how much will that cost"])
    assert len(turns) == 2
    assert turns[0].woken and not turns[1].woken


def test_dwell_expires():
    """The gap between the two lines is 30 frames of silence — 0.9 s — so a
    0.3 s dwell must have closed before the second one starts."""
    ear, asr = listener(dwell_s=0.3)
    turns: list = []
    ear.run(text_frames(["hey friday hello there", "this should not be heard"], asr),
            turns.append, now=Ticker())
    assert len(turns) == 1
    assert turns[0].woken


# -------------------------------------------------------------------- VAD

def test_energy_vad_adapts_to_the_noise_floor():
    """A room with a fridge in it must not read as continuous speech."""
    vad = EnergyVAD()
    rng = np.random.default_rng(0)
    for _ in range(200):                       # a noisy but quiet room
        vad.speaking(rng.normal(0, 0.01, FRAME).astype(np.float32))
    assert not vad.speaking(rng.normal(0, 0.01, FRAME).astype(np.float32))
    assert vad.speaking(rng.normal(0, 0.25, FRAME).astype(np.float32))


def test_energy_vad_has_hysteresis():
    """Without it the gate chatters on every inter-word pause and the turn
    detector sees a dozen endpoints per sentence."""
    vad = EnergyVAD()
    rng = np.random.default_rng(1)
    for _ in range(60):
        vad.speaking(rng.normal(0, 0.005, FRAME).astype(np.float32))
    assert vad.speaking(rng.normal(0, 0.3, FRAME).astype(np.float32))
    # A brief dip mid-word must not close the gate.
    assert vad.speaking(rng.normal(0, 0.09, FRAME).astype(np.float32))


# --------------------------------------------------------- turn detection

def test_endpointing_waits_longer_mid_thought():
    """Interrupting someone mid-sentence is the failure people notice and
    resent; a flat timeout does it constantly."""
    turn = SilenceTurn()
    assert not turn.update(speaking=False, silence_ms=500,
                           partial="I was going to say that and").ended
    assert turn.update(speaking=False, silence_ms=500,
                       partial="that is all done.").ended


def test_endpointing_never_fires_while_speaking():
    turn = SilenceTurn()
    assert not turn.update(speaking=True, silence_ms=5000, partial="anything").ended


# ---------------------------------------------------------------- barge-in

def test_speech_during_playback_cancels_the_reply():
    """The single highest-risk item in the build. Capture never stops."""
    ear, _ = listener()
    ear._speaking_until = 1e9            # pretend playback is in progress
    rng = np.random.default_rng(2)

    frames = int(BARGE_MS / FRAME_MS) + 30
    ear.run((rng.normal(0, 0.3, FRAME).astype(np.float32) for _ in range(frames)),
            lambda t: None, now=Ticker())
    assert ear.interrupted


def test_a_single_cough_does_not_cancel():
    ear, _ = listener()
    ear._speaking_until = 1e9
    rng = np.random.default_rng(3)

    def frames():
        yield rng.normal(0, 0.3, FRAME).astype(np.float32)
        for _ in range(20):
            yield np.zeros(FRAME, dtype=np.float32)

    ear.run(frames(), lambda t: None, now=Ticker())
    assert not ear.interrupted


def test_speaking_is_interruptible_mid_stream():
    ear, _ = listener(tts=ToneTTS())
    spoken: list[int] = []
    ear.playout = lambda chunk: (spoken.append(len(chunk)),
                                 ear.interrupt() if len(spoken) == 3 else None)
    ear.say(iter(["This is a fairly long reply. " * 8]))
    assert len(spoken) == 3, "playback did not stop when interrupted"


def test_interrupting_speech_becomes_the_next_turn():
    """Dropping the speech that interrupted makes the person repeat
    themselves, which is worse than not being interruptible."""
    ear, asr = listener()
    ear._speaking_until = 1e9
    turns: list = []
    ear.run(text_frames(["stop that and tell me about the wall"], asr),
            turns.append, now=Ticker())
    assert ear.interrupted
    assert turns and "wall" in turns[0].text


# ----------------------------------------------------------------- preroll

def test_the_start_of_an_utterance_is_not_lost():
    """People start the sentence before the detector fires; without a pre-roll
    the first word or two of every utterance is simply gone."""
    ear, _ = listener()
    assert ear._preroll_max == int(PREROLL_MS / FRAME_MS)
    assert ear._preroll_max >= 20


# --------------------------------------------------------------- sentences

def test_generation_is_chunked_into_speakable_clauses():
    """The decision that turns 4.5 s of generation into ~600 ms of perceived
    latency."""
    out = list(sentences(iter(["The Anderson ", "contract moved to September. ",
                               "I can chase it Monday. ", "Shall I?"])))
    assert len(out) >= 2
    assert out[0].endswith(".")


# ------------------------------------------------------------- degradation

def test_construction_never_refuses_to_start():
    """A device that will not boot because one model is missing is worse than
    one that boots degraded and says so."""
    cfg = BrainConfig(wake_model="/nope", asr_model="/nope", tts_model="/nope",
                      turn_model="/nope")
    ear = make(cfg, bus=Bus())
    assert ear.wake.phrase == cfg.wake_phrase
    assert all("fallback" in how for how in ear.stages.values())


def test_the_wake_phrase_is_configurable():
    cfg = BrainConfig(wake_phrase="ok computer")
    ear = make(cfg, bus=Bus())
    assert ear.wake.phrase == "ok computer"
    assert ear.wake.strip("ok computer what time is it") == "what time is it"


# ------------------------------------------------------- ears joined to brain

@pytest.fixture
def brain(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "b.db"))
    b = Brain(cfg, clock=SimClock(), llm=FakeLLM(),
              embedder=HashEmbedder(dim=cfg.embed_dim), bus=Bus())
    yield b
    b.close_store()


def test_the_whole_device_end_to_end(brain):
    ear, asr = listener()
    replies: list = []
    converse(brain, ear,
             text_frames(["hey friday I had lunch with David Anderson "
                          "about the Kestrel contract",
                          "and what about the garden wall"], asr),
             on_reply=lambda t, r: replies.append((t, r)))
    brain.close()

    assert len(replies) == 2
    assert replies[0][0].woken and not replies[1][0].woken
    assert brain.store.count(Kind.EPISODE) >= 1
    assert brain.store.find_entity("anderson") is not None


def test_nothing_is_written_before_the_reply(brain):
    """A memory write in the critical path is how a device that is fast on a
    bench becomes slow in a house."""
    order: list[str] = []
    ear, asr = listener()

    original = brain.store.add
    brain.store.add = lambda *a, **k: (order.append("write"), original(*a, **k))[1]
    original_say = ear.say
    ear.say = lambda stream: (order.append("speak"), original_say(stream))[1]

    converse(brain, ear,
             text_frames(["hey friday I really cannot stand cilantro"], asr))
    brain.close()
    assert "speak" in order
    assert order.index("speak") < order.index("write")


def test_prosody_reaches_the_salience_gate(brain):
    ear, asr = listener()
    seen: list = []
    converse(brain, ear,
             text_frames(["hey friday the deadline moved again"], asr),
             on_reply=lambda t, r: seen.append((t, r)))
    turn, reply = seen[0]
    assert turn.prosody is not None
    assert reply.salience is not None
