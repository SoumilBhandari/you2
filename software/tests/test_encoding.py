"""Phase 4/5 gate: episodes, entities, loops, Hebbian binding — and a write
path that makes no model call."""

from __future__ import annotations

import pytest

from you2 import DAY, BrainConfig, SimClock
from you2.activation import recall
from you2.encoding import Segmenter, encode
from you2.llm import FakeLLM, rules
from you2.perception import Attention, evaluate
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


def say(store, attention, seg, text, at=None, source="speech"):
    """Wake -> hear -> score -> segment. Returns any closed episode."""
    at = at if at is not None else store.clock.now()
    if not attention.accepts(at):
        attention.wake(at)
    turn = attention.hear(text, at)
    ws = recall(store, text, now=at)
    sal = evaluate(store, text, workspace=ws, source=source, now=at)
    return seg.feed(turn, sal)


# ------------------------------------------------------------------- rules

def test_key_derivation_survives_a_change_of_value():
    """The single thing that makes supersession possible. 'cow milk' and 'oat
    milk' must produce the same key or the store keeps both forever."""
    a = rules.find_facts("I prefer cow milk in my coffee")
    b = rules.find_facts("I prefer oat milk in my coffee")
    assert a and b
    assert a[0]["key"] == b[0]["key"]

    c = rules.find_facts("I work at Kestrel")
    d = rules.find_facts("I work at Lumen Systems")
    assert c[0]["key"] == d[0]["key"] == "user/employer"


def test_entities_ignore_sentence_initial_capitals():
    found = {e["name"] for e in rules.find_entities(
        "The meeting with David Anderson went well. It was long.")}
    assert "David Anderson" in found
    assert not any(n in found for n in ("The", "It", "The meeting"))


def test_relation_judgement_is_conservative():
    assert rules.relation("the user prefers cow milk",
                          "the user prefers cow milk") == "restatement"
    assert rules.relation("the user prefers cow milk",
                          "the user prefers oat milk") == "reversal"
    assert rules.relation("the user prefers oat milk",
                          "the user's sister lives in Lisbon") == "distinct"


def test_fake_llm_is_deterministic():
    from you2.llm import prompts
    eps = [Neuron(id=1, kind=Kind.EPISODE, content="I prefer oat milk", created_at=0)]
    msgs = prompts.extract_batch(eps, 0.0)
    a = FakeLLM().structured(msgs)
    b = FakeLLM().structured(msgs)
    assert a == b
    assert a["facts"][0]["key"] == "user/pref_milk"
    assert a["facts"][0]["cites"] == [1]


# -------------------------------------------------------------- segmentation

def test_a_long_gap_ends_the_episode(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    assert say(store, a, seg, "the retaining wall needs rebuilding", at=100.0) is None
    assert say(store, a, seg, "and the fence posts are rotten", at=110.0) is None
    closed = say(store, a, seg, "anyway, lunch with Priya on Friday", at=400.0)
    assert closed is not None and closed.boundary_reason == "gap"
    assert len(closed) == 2


def test_surprise_acts_as_an_event_boundary(store):
    """High prediction error ends the previous episode, so a surprising remark
    is remembered as its own moment rather than blurred into what preceded it."""
    store.add(Neuron(kind=Kind.EPISODE, content="the retaining wall needs rebuilding"))
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "the retaining wall needs rebuilding", at=100.0)
    say(store, a, seg, "the retaining wall is really in bad shape", at=110.0)
    closed = say(store, a, seg, "I have decided to learn to sail a dinghy", at=120.0)
    assert closed is not None
    assert closed.boundary_reason in ("surprise", "topic")


def test_episode_salience_is_the_max_not_the_mean(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "yeah", at=100.0)
    say(store, a, seg, "remember that my passport expires in March", at=105.0)
    ep = seg.flush()
    assert ep.salience > store.cfg.salience_strong


# ------------------------------------------------------------------ encoding

def test_encoding_creates_the_episode_and_binds_entities(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "I had lunch with David Anderson about the Kestrel contract")
    ep = seg.flush()
    out = encode(store, ep)

    assert out.episode is not None
    assert {e.content for e in out.entities} >= {"David Anderson"}
    for e in out.entities:
        assert store.graph.weight(out.episode.id, e.id, Edge.ABOUT) > 0.0
    assert store.find_entity("anderson") is not None


def test_co_mentioned_entities_are_wired_together(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "David Anderson introduced me to Priya Raman last week")
    out = encode(store, seg.flush())
    ids = sorted(e.id for e in out.entities)
    assert len(ids) >= 2
    assert store.graph.weight(ids[0], ids[1], Edge.ASSOC) > 0.0


def test_entities_are_resolved_by_alias_not_recreated(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "spoke to David Anderson about the roof")
    encode(store, seg.flush())
    store.clock.advance(days=3)
    say(store, a, seg, "Anderson called again about the roof", at=store.clock.now())
    encode(store, seg.flush())

    entities = [n for n in store.by_kind(Kind.ENTITY, limit=100)
                if "anderson" in n.content.lower()]
    assert len(entities) == 1, [e.content for e in entities]


def test_chatter_is_never_written(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "thanks")
    out = encode(store, seg.flush())
    assert out.skipped
    assert store.count(Kind.EPISODE) == 0


def test_commitments_open_a_loop_and_closure_shuts_it(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "I'll look into the Anderson contract by Friday")
    out = encode(store, seg.flush())
    assert len(out.loops) == 1
    loop = out.loops[0]
    assert loop.meta["expected_by"] > store.clock.now()

    store.clock.advance(days=3)
    now = store.clock.now()
    say(store, a, seg, "the Anderson contract is sorted, I sent it over", at=now)
    closed = encode(store, seg.flush())
    assert loop.id in closed.closed
    assert store.get(loop.id).invalid_at is not None


def test_the_same_commitment_does_not_reopen(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    for i in range(3):
        say(store, a, seg, "I need to book the boiler service", at=100.0 + i * 500)
        encode(store, seg.flush())
    assert store.count(Kind.LOOP) == 1


def test_arousal_raises_initial_storage_strength(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "I am absolutely furious that the deadline moved again!")
    hot = encode(store, seg.flush()).episode

    say(store, a, seg, "the deadline moved to a date in September", at=store.clock.now() + 1000)
    calm = encode(store, seg.flush()).episode
    assert hot.storage_strength > calm.storage_strength


def test_explicit_memories_are_pinned(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "remember that the boiler service is due in October")
    ep = encode(store, seg.flush()).episode
    assert ep.pinned


def test_write_path_makes_no_model_call(store):
    """The hot path acquires no new blocking calls, ever — and consolidation
    must keep working with no network."""
    llm = FakeLLM()
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "I had lunch with David Anderson about the Kestrel contract")
    say(store, a, seg, "I'll send him the revised numbers by Friday",
        at=store.clock.now() + 10)
    encode(store, seg.flush())
    assert llm.seen == [], "the write path called the model"


def test_next_edges_chain_episodes(store):
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "the retaining wall needs rebuilding before winter", at=100)
    first = encode(store, seg.flush()).episode
    say(store, a, seg, "I also need to sort out the garage door", at=400)
    second = encode(store, seg.flush(), previous=first.id).episode
    assert store.graph.weight(first.id, second.id, Edge.NEXT) > 0.0


# ------------------------------------- bugs the demo month found, kept fixed

def test_a_prepositional_phrase_does_not_steal_the_topic():
    """'meetings in the morning' and 'meetings in the afternoon these days'
    have to key the same, or the profile ends up asserting both."""
    a = rules.find_facts("I prefer meetings in the morning, I am useless after three")
    b = rules.find_facts("actually I prefer meetings in the afternoon these days")
    assert a[0]["key"] == b[0]["key"]
    assert rules.topic("oat milk in my coffee") == rules.topic("cow milk")


def test_third_person_reads_aloud_correctly():
    """These get spoken. 'the user don't like meetings' makes the device sound
    broken for a reason unrelated to what it knows."""
    say = lambda t: rules.find_facts(t)[0]["text"]
    assert say("I really cannot stand cilantro") == "the user cannot stand cilantro"
    assert say("I don't like meetings") == "the user does not like meetings"
    assert say("I prefer oat milk") == "the user prefers oat milk"


def test_a_closure_is_kept_even_though_it_is_unsurprising(store):
    """A closure is unsurprising by construction — it is about a commitment
    we already recorded. Left to the weighted score it falls below the floor,
    gets discarded, and the loop it resolves stays open forever."""
    s = evaluate(store, "sent David Anderson the revised numbers, that is done")
    assert s.keep
    assert evaluate(store, "I'll book the boiler service on Friday").keep


def test_replay_does_not_reopen_a_resolved_commitment(store):
    """An episode is replayed many times over its life, and each replay
    re-extracts the commitment in it. Deduping only against open loops means
    the device starts chasing you about something you finished a month ago."""
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    say(store, a, seg, "I'll send David Anderson the revised numbers by Friday")
    out = encode(store, seg.flush())
    loop = out.loops[0]

    store.clock.advance(days=2)
    say(store, a, seg, "sent David Anderson the revised numbers, that is done",
        at=store.clock.now())
    encode(store, seg.flush())
    assert store.get(loop.id).invalid_at is not None

    # Now replay the original episode, as every night does.
    from you2.consolidation import abstraction, replay
    from you2.llm import FakeLLM
    episode = store.get(out.episode.id)
    store.clock.advance(days=1)
    abstraction.consolidate(store, FakeLLM(now=store.clock.now()),
                            replay.Batch([episode], set()), store.cfg,
                            store.clock.now())
    assert store.count(Kind.LOOP) == 1, "the resolved commitment was re-opened"
