"""Phase 3 gate: the gate separates signal from "thanks"."""

from __future__ import annotations

import numpy as np
import pytest

from you2 import BrainConfig, SimClock
from you2.activation import recall
from you2.perception import (
    Attention, Baseline, Prosody, State, analyse, commitments, evaluate,
    extract, text_arousal, valence,
)
from you2.perception.discourse import self_reference
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


# ----------------------------------------------------------------- discourse

def test_self_reference_ranks_declarations_above_anecdotes():
    strong = self_reference("I really prefer working in the morning")
    identity = self_reference("my sister lives in Lisbon")
    weak = self_reference("the train was late again this morning")
    assert strong > weak
    assert identity > weak


def test_valence_handles_negation():
    assert valence("that was great, really pleased") > 0.3
    assert valence("it was awful and I'm exhausted") < -0.3
    assert valence("not great, honestly") < 0.0
    assert valence("never a problem") > 0.0
    assert valence("the meeting is at four") == 0.0


def test_commitments_are_detected_with_deadlines():
    now = 1_800_000_000.0
    got = commitments("I'll look into the Anderson contract by Friday", now)
    assert len(got) == 1
    assert got[0].expected_by is not None and got[0].expected_by > now
    assert got[0].confidence > 0.7

    assert commitments("the weather is nice today", now) == []
    vague = commitments("I should probably sort out the garage", now)
    assert len(vague) == 1 and vague[0].expected_by is None


def test_explicit_markers_and_questions():
    d = analyse("remember that my passport expires in March", 0.0)
    assert d.explicit == 1.0
    assert analyse("what did I say about the wall?", 0.0).is_question
    assert analyse("that's sorted now", 0.0).is_closure


# ------------------------------------------------------------------- prosody

def _tone(freq, seconds, sr=16000, amp=0.4, noise=0.0):
    t = np.arange(int(sr * seconds)) / sr
    x = amp * np.sin(2 * np.pi * freq * t)
    if noise:
        x = x + np.random.default_rng(0).normal(0, noise, x.shape)
    return x.astype(np.float32)


def test_prosody_extracts_pitch_and_energy():
    p = extract(_tone(150.0, 1.0), 16000)
    assert 130 < p.f0_mean < 175, p.f0_mean
    assert p.rms_mean > 0.1
    assert p.voiced_fraction > 0.8


def test_prosody_handles_silence_and_empty_input():
    assert extract(np.zeros(0, dtype=np.float32)).f0_mean == 0.0
    quiet = extract(np.zeros(16000, dtype=np.float32))
    assert quiet.f0_mean == 0.0 and quiet.rms_mean < 1e-3


def test_arousal_is_speaker_relative(store):
    """A loud turn is only loud relative to how this person usually speaks."""
    base = Baseline(store, "user")
    now = store.clock.now()
    for _ in range(40):                      # a calm baseline
        base.update(Prosody(rms_mean=0.10, f0_mean=120.0, f0_var=40.0,
                            speech_rate=3.0, pause_ratio=0.4), now)
        now += 60.0
    assert base.confidence == 1.0

    calm = base.arousal(Prosody(rms_mean=0.10, f0_mean=120.0, f0_var=40.0,
                                speech_rate=3.0, pause_ratio=0.4))
    hot = base.arousal(Prosody(rms_mean=0.30, f0_mean=190.0, f0_var=400.0,
                               speech_rate=5.5, pause_ratio=0.15))
    assert hot > calm
    assert 0.4 < calm < 0.6


def test_cold_baseline_cannot_produce_a_strong_signal(store):
    """Three samples must not yield a confident arousal estimate."""
    base = Baseline(store, "user")
    base.update(Prosody(rms_mean=0.1), store.clock.now())
    extreme = base.arousal(Prosody(rms_mean=5.0, f0_mean=300.0, f0_var=900.0))
    assert abs(extreme - 0.5) < 0.1


def test_text_arousal_fallback():
    assert text_arousal("I am absolutely furious about this!") > 0.6
    assert text_arousal("the meeting is at four") == pytest.approx(0.5, abs=0.02)


# ----------------------------------------------------------------- attention

def test_dwell_lets_a_follow_up_through_without_a_wake_word(store):
    a = Attention(store.cfg, store.clock)
    assert a.hear("what about the wall", 0.0) is None, "dormant must not listen"

    a.wake(100.0)
    session = a.session_id
    first = a.hear("what about the retaining wall", 100.5)
    assert first is not None and first.woken
    a.reply_start(101.0)
    a.reply_end(103.0)

    follow = a.hear("how much would that cost", 105.0)
    assert follow is not None and not follow.woken
    assert follow.onset_latency == pytest.approx(2.0)
    assert a.session_id == session, "a follow-up stays in the same session"
    assert len(a.turns) == 2


def test_dwell_expires(store):
    a = Attention(store.cfg, store.clock)
    a.wake(100.0)
    a.hear("hello", 100.5)
    a.reply_end(101.0)
    assert a.accepts(105.0)
    assert not a.accepts(101.0 + store.cfg.dwell_seconds + 1.0)
    assert a.state is State.DORMANT


def test_orienting_captures_the_gap(store):
    a = Attention(store.cfg, store.clock)
    a.wake(1_800_000_000.0)
    a.hear("morning", 1_800_000_001.0)
    a.close(1_800_000_010.0)
    o = a.wake(1_800_000_010.0 + 40_000.0)
    assert o.gap_seconds == pytest.approx(40_000.0)
    assert o.weekday and o.part_of_day


def test_engagement_rises_with_fast_follow_ups(store):
    quick = Attention(store.cfg, store.clock)
    quick.wake(0.0)
    for i in range(5):
        quick.hear(f"turn {i}", i * 1.5)

    slow = Attention(store.cfg, store.clock)
    slow.wake(0.0)
    for i in range(5):
        slow.hear(f"turn {i}", i * 11.0)

    assert quick.engagement() > slow.engagement()


# ------------------------------------------------------------------ salience

def test_acknowledgements_are_discarded(store):
    for junk in ("thanks", "ok", "yeah", "sure thanks"):
        s = evaluate(store, junk, workspace=recall(store, junk))
        assert s.decision == "discard", f"{junk!r} scored {s.total}"


def test_a_preference_statement_is_kept(store):
    s = evaluate(store, "I really prefer working in the morning, I'm useless after 3pm")
    assert s.keep
    assert s.self_reference > 0.3


def test_explicit_instruction_always_encodes_strongly(store):
    s = evaluate(store, "remember that my passport expires in March")
    assert s.decision == "strong"
    assert s.explicit == 1.0


def test_surprise_falls_when_memory_already_predicts_the_utterance(store):
    """The core claim of the gate. The retrieval that already ran is the
    predictor, so this costs one dot product."""
    text = "the retaining wall at the back needs rebuilding before winter"
    cold = evaluate(store, text, workspace=recall(store, text))

    store.add(Neuron(kind=Kind.EPISODE, content=text))
    store.add(Neuron(kind=Kind.FACT, key="wall/state",
                     content="the retaining wall needs rebuilding before winter"))
    warm = evaluate(store, text, workspace=recall(store, text))

    assert warm.surprise < cold.surprise - 0.2
    assert warm.predicted_by is not None
    assert warm.total < cold.total


def test_novelty_is_distinct_from_surprise(store):
    """A recurring complaint is unsurprising and unnovel; a topic switch is
    surprising without being novel."""
    store.add(Neuron(kind=Kind.EPISODE, content="the kitchen tap is dripping again"))
    text = "the kitchen tap is dripping again"
    s = evaluate(store, text, workspace=recall(store, text))
    assert s.novelty < 0.5

    fresh = evaluate(store, "I have decided to learn to sail a dinghy")
    assert fresh.novelty > 0.6


def test_open_loops_raise_goal_relevance(store):
    loop = store.add(Neuron(kind=Kind.LOOP,
                            content="said you'd look into the Anderson contract"))
    ent = store.add(Neuron(kind=Kind.ENTITY, content="Anderson contract"),
                    aliases=["anderson contract", "anderson"])
    store.connect(loop.id, ent.id, Edge.ABOUT, 0.95)

    ws = recall(store, "I finally called Anderson about the contract")
    s = evaluate(store, "I finally called Anderson about the contract", workspace=ws)
    assert s.goal_relevance > 0.0
    assert s.keep


def test_documents_are_discounted(store):
    text = "I prefer to write specifications before writing any code"
    spoken = evaluate(store, text, source="speech")
    written = evaluate(store, text, source="document")
    assert written.total < spoken.total
    assert written.total == pytest.approx(
        spoken.total * store.cfg.document_salience_discount, abs=1e-3)


def test_arousal_from_prosody_raises_salience(store):
    text = "the deadline moved again and nobody told me"
    flat = evaluate(store, text, arousal=0.2)
    agitated = evaluate(store, text, arousal=0.95)
    assert agitated.total > flat.total
    assert agitated.total - flat.total == pytest.approx(
        store.cfg.w_arousal * 0.75, abs=1e-6)


def test_gate_separates_signal_from_chatter(store):
    """The phase gate: run a realistic mixed transcript through and check the
    partition is sane."""
    keep = [
        "I really cannot stand cilantro, it ruins everything",
        "remember that the boiler service is due in October",
        "I'll email David about the Anderson contract tomorrow",
        "my sister is moving to Lisbon in the spring",
        "I've decided to stop taking meetings before ten",
    ]
    drop = ["thanks", "ok", "yeah sure", "morning", "cool"]

    for text in keep:
        s = evaluate(store, text, workspace=recall(store, text))
        assert s.keep, f"dropped signal: {text!r} -> {s}"
    for text in drop:
        s = evaluate(store, text, workspace=recall(store, text))
        assert not s.keep, f"kept chatter: {text!r} -> {s}"
