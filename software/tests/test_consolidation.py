"""Phase 6 gate: facts supersede, and downscaling improves precision."""

from __future__ import annotations

import numpy as np
import pytest

from you2 import DAY, WEEK, YEAR, BrainConfig, SimClock
from you2.activation import recall
from you2.consolidation import (
    abstraction, dream, forgetting, homeostasis, night, replay, supersede,
)
from you2.encoding import Segmenter, encode
from you2.llm import FakeLLM
from you2.perception import Attention, evaluate
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store, Tier


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


@pytest.fixture
def llm(store):
    return FakeLLM(now=store.clock.now())


def converse(store, texts, *, gap=600.0, source="speech", force=False):
    """Run utterances through the real write path.

    `force` bypasses the salience gate, for tests about what happens to a
    repeated statement — which by construction is unsurprising and unnovel,
    so the gate correctly drops it.
    """
    a = Attention(store.cfg, store.clock)
    seg = Segmenter(store.embed)
    previous = None
    for text in texts:
        now = store.clock.now()
        if not a.accepts(now):
            a.wake(now)
        turn = a.hear(text, now)
        sal = evaluate(store, text, workspace=recall(store, text, now=now),
                       source=source, now=now)
        if force:
            sal.total = max(sal.total, store.cfg.salience_strong)
            sal.decision = "strong"
        closed = seg.feed(turn, sal)
        if closed is not None:
            out = encode(store, closed, previous=previous, source=source)
            if out.episode:
                previous = out.episode.id
        store.clock.advance(seconds=gap)
    final = seg.flush()
    if final is not None:
        out = encode(store, final, previous=previous, source=source)
    store.flush()


# -------------------------------------------------------------- supersession

def test_a_changed_preference_replaces_rather_than_accumulates(store, llm):
    converse(store, ["I prefer cow milk"])
    night.run(store, llm, now=store.clock.now())
    assert "cow milk" in store.by_key("user/pref_milk").content

    store.clock.advance(days=60)
    llm.now = store.clock.now()
    converse(store, ["actually I prefer oat milk now"])
    night.run(store, llm, now=store.clock.now())

    live = store.by_key("user/pref_milk")
    assert "oat" in live.content
    history = store.history_of("user/pref_milk")
    assert len(history) == 2
    old = [h for h in history if h.id != live.id][0]
    assert old.invalid_at is not None and old.superseded_by == live.id
    assert store.count(Kind.FACT, live_only=True) == 1


def test_superseded_facts_are_starved_but_still_reachable(store, llm):
    converse(store, ["I work at Kestrel Systems"])
    night.run(store, llm, now=store.clock.now())
    store.clock.advance(days=90)
    llm.now = store.clock.now()
    converse(store, ["I work at Lumen now"])
    night.run(store, llm, now=store.clock.now())

    ws = recall(store, "where do I work")
    texts = " ".join(n.text() for n in ws.neurons())
    assert "Lumen" in texts or "lumen" in texts
    assert "Kestrel" not in texts and "kestrel" not in texts
    # Still reachable for "what did I used to think".
    assert len(store.history_of("user/employer")) == 2


def test_restatement_raises_confidence_without_a_new_record(store, llm):
    converse(store, ["I prefer oat milk"])
    night.run(store, llm, now=store.clock.now())
    first = store.by_key("user/pref_milk")

    for _ in range(3):
        store.clock.advance(days=20)
        llm.now = store.clock.now()
        converse(store, ["I prefer oat milk"], force=True)
        night.run(store, llm, now=store.clock.now())

    assert store.count(Kind.FACT) == 1
    assert store.by_key("user/pref_milk").confidence > first.confidence


def test_unresolvable_conflict_becomes_a_question(store):
    llm = FakeLLM(now=store.clock.now())
    llm.scripted = ['{"relation":"conflict","why":"cannot tell",'
                    '"question":"Is the Anderson deadline still firm?"}']
    a = store.add(Neuron(kind=Kind.EPISODE, content="the anderson deadline is firm"))
    supersede.resolve(store, None, "anderson/deadline",
                      "the Anderson deadline is firm", [a.id], 0.8,
                      store.clock.now(), store.cfg)
    store.clock.advance(days=30)
    b = store.add(Neuron(kind=Kind.EPISODE, content="the anderson deadline moved"))
    res = supersede.resolve(store, llm, "anderson/deadline",
                            "the Anderson deadline has moved", [b.id], 0.8,
                            store.clock.now(), store.cfg)

    assert res.relation == "conflict"
    assert res.question
    assert res.fact.is_live and res.replaced.is_live, "both stay valid until asked"
    assert supersede.open_questions(store)

    supersede.settle(store, res.fact.id, res.replaced.id, store.clock.now())
    assert store.get(res.replaced.id).invalid_at is not None
    assert not supersede.open_questions(store)


def test_citations_outside_the_batch_are_dropped(store):
    """A fact whose provenance is invented is unfalsifiable and 'forget that'
    can never reach it."""
    real = store.add(Neuron(kind=Kind.EPISODE, content="I prefer oat milk"))
    llm = FakeLLM()
    llm.scripted = []
    payload = {"gist": "", "facts": [{"key": "user/pref_milk",
                                      "text": "the user prefers oat milk",
                                      "cites": [real.id, 9999], "confidence": 0.8}],
               "entities": [], "loops": [], "closed": []}

    class Canned:
        def structured(self, *a, **k):
            return payload

    batch = replay.Batch([real], set())
    abstraction.consolidate(store, Canned(), batch, store.cfg, store.clock.now())
    fact = store.by_key("user/pref_milk")
    assert fact.citations() == [real.id]


# -------------------------------------------------------------- homeostasis

def test_downscaling_preserves_ratios_and_prunes_noise(store):
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"n{i}")).id for i in range(4)]
    store.connect(ids[0], ids[1], Edge.ASSOC, 0.90)      # signal
    store.connect(ids[0], ids[2], Edge.ASSOC, 0.30)      # weaker signal
    store.connect(ids[0], ids[3], Edge.ASSOC, 0.025)     # noise
    store.connect(ids[1], ids[2], Edge.ABOUT, 0.50)      # structural

    before = store.graph.edge_count
    out = homeostasis.downscale(store, store.cfg.merged(downscale_rho=0.5,
                                                        prune_weight_min=0.05))
    assert out.pruned == 1
    assert store.graph.edge_count == before - 1
    assert store.graph.weight(ids[0], ids[1], Edge.ASSOC) == pytest.approx(0.45)
    assert (store.graph.weight(ids[0], ids[1], Edge.ASSOC)
            / store.graph.weight(ids[0], ids[2], Edge.ASSOC)) == pytest.approx(3.0)
    assert store.graph.weight(ids[1], ids[2], Edge.ABOUT) == pytest.approx(0.50)


def test_downscaling_improves_retrieval_precision_over_time(store):
    """The claim of the synaptic homeostasis hypothesis, measured where it is
    actually made: retrieval.

    A single night barely moves anything, and an earlier version of this test
    asserted a per-night contrast gain that simply is not true. The mechanism
    is *compounding against reinforcement*: an association that keeps being
    used gets pushed back up by Hebbian learning each time it wins the
    workspace, while one that does not decays multiplicatively until it falls
    through the floor. The two diverge over weeks.

    So: build a real cluster plus accumulated noise, run 90 nights with the
    cluster being used and the noise not, and measure what fraction of a
    cascade's activation lands on the cluster.
    """
    from you2.activation import Cue, spread

    def build(with_downscaling):
        cfg = BrainConfig(db_path=":memory:")
        s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
        rng = np.random.default_rng(3)
        ids = [s.add(Neuron(kind=Kind.ENTITY, content=f"n{i}")).id for i in range(60)]
        cluster = set(ids[:10])
        for i in range(10):
            for j in range(i + 1, 10):
                s.connect(ids[i], ids[j], Edge.ASSOC, 0.35)
        for _ in range(900):
            a, b = rng.integers(0, 60, 2)
            if a != b:
                s.connect(ids[a], ids[b], Edge.ASSOC, 0.30)

        for _ in range(90):
            # Daytime: the cluster is used, so its edges are reinforced.
            for i in range(10):
                for j in range(i + 1, 10):
                    s.strengthen(ids[i], ids[j], 0.9, 0.9)
            if with_downscaling:
                homeostasis.downscale(s, s.cfg, persist=False)
        return s, ids, cluster

    def concentration(s, ids, cluster):
        cas = spread(s.graph, [Cue(ids[0], 1.0)], s.cfg)
        total = sum(cas.activation.values())
        inside = sum(a for n, a in cas.activation.items() if n in cluster)
        return inside / total if total else 0.0

    on = concentration(*build(True))
    off = concentration(*build(False))
    assert on > off, f"downscaling {on:.3f} vs none {off:.3f}"
    assert on > 0.6


def test_one_night_prunes_noise_and_preserves_ratios(store):
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"n{i}")).id for i in range(40)]
    for i in range(6):
        store.connect(ids[i], ids[i + 1], Edge.ASSOC, 0.8)
    rng = np.random.default_rng(3)
    for _ in range(300):
        a, b = rng.integers(0, 40, 2)
        if a != b:
            store.connect(ids[a], ids[b], Edge.ASSOC, float(rng.random()) * 0.05)

    out = homeostasis.downscale(store, store.cfg.merged(downscale_rho=0.7))
    assert out.pruned > 50
    assert 0.0 < out.mass_retained < 1.0
    assert store.graph.weight(ids[0], ids[1], Edge.ASSOC) == pytest.approx(0.56)


# --------------------------------------------------------------- forgetting

def test_nothing_unabsorbed_is_ever_compacted(store, llm):
    """The invariant that makes aggressive compaction safe."""
    converse(store, ["the retaining wall needs rebuilding before winter",
                     "I prefer oat milk in my coffee"])
    night.run(store, llm, now=store.clock.now())

    for _ in range(6):
        store.clock.advance(days=120)
        llm.now = store.clock.now()
        night.run(store, llm, now=store.clock.now())

    assert forgetting.audit(store) == []
    for n in store.by_kind(Kind.EPISODE, limit=1000, live_only=False,
                           tiers=(Tier.HOT, Tier.WARM, Tier.COLD)):
        if n.meta.get("compacted_at"):
            assert n.absorbed and n.gist


def test_tiers_demote_but_pinned_and_loops_never_do(store, llm):
    converse(store, ["remember that my passport expires in March",
                     "I'll book the boiler service by Friday",
                     "the train was busy this morning again"])
    for _ in range(3):
        store.clock.advance(days=150)
        llm.now = store.clock.now()
        night.run(store, llm, now=store.clock.now())

    pinned = [n for n in store.by_kind(Kind.EPISODE, limit=100) if n.pinned]
    assert pinned and all(n.tier is Tier.HOT for n in pinned)
    loops = [n for n in store.by_kind(Kind.LOOP, limit=10) if n.invalid_at is None]
    assert all(n.tier is Tier.HOT for n in loops)
    assert any(n.tier is not Tier.HOT
               for n in store.by_kind(Kind.EPISODE, limit=100, live_only=False))


def test_facts_stay_hot_however_old(store, llm):
    converse(store, ["I am allergic to penicillin"])
    night.run(store, llm, now=store.clock.now())
    for _ in range(8):
        store.clock.advance(days=200)
        llm.now = store.clock.now()
        night.run(store, llm, now=store.clock.now())
    facts = store.by_kind(Kind.FACT, limit=50)
    assert facts and all(f.tier is Tier.HOT for f in facts)


# ------------------------------------------------------------------- replay

def test_replay_priority_falls_with_repetition(store):
    n = Neuron(kind=Kind.EPISODE, content="x", created_at=0.0, salience=0.8,
               surprise=0.8)
    fresh = replay.priority(n, 0.0, store.cfg)
    n.times_replayed = 4
    worn = replay.priority(n, 0.0, store.cfg)
    assert worn < fresh / 2


def test_ripple_batches_group_by_shared_entity(store):
    ent = store.add(Neuron(kind=Kind.ENTITY, content="Anderson"))
    shared = [store.add(Neuron(kind=Kind.EPISODE, content=f"anderson thing {i}",
                               created_at=i * DAY)) for i in range(4)]
    for e in shared:
        store.connect(e.id, ent.id, Edge.ABOUT, 0.9)
    lone = store.add(Neuron(kind=Kind.EPISODE, content="unrelated",
                            created_at=90 * DAY))

    batches = replay.ripple_batches(store, shared + [lone], store.cfg)
    first = {n.id for n in batches[0].episodes}
    assert {e.id for e in shared} <= first
    assert lone.id not in first or len(batches) == 1


def test_replay_truncation_is_logged_not_silent(store):
    eps = []
    for i in range(200):
        eps.append(store.add(Neuron(kind=Kind.EPISODE, content=f"e{i}",
                                    created_at=i * 3600.0)))
    cfg = store.cfg.merged(replay_max_batches=2, replay_batch_max=3)
    replay.ripple_batches(store, eps, cfg)
    assert store.journal("replay_truncated"), "silent truncation reads as 'covered everything'"


# --------------------------------------------------------------------- night

def test_a_full_night_runs_end_to_end(store, llm):
    converse(store, [
        "I had lunch with David Anderson about the Kestrel contract",
        "I'll send him the revised numbers by Friday",
        "I really cannot stand cilantro, it ruins everything",
        "my sister is moving to Lisbon in the spring",
        "the retaining wall needs rebuilding before winter",
    ])
    report = night.run(store, llm, now=store.clock.now(), seed=1)

    assert report.batches >= 1
    assert report.episodes_replayed >= 3
    assert report.facts.new >= 2
    assert report.homeostasis["edges_before"] > 0
    assert report.profile
    assert report.audit_failures == []
    assert "night of" in report.journal()
    assert store.journal("night")


def test_the_night_yields_to_a_wake_word(store, llm):
    topics = ["the garden wall", "the Kestrel migration", "my sister in Lisbon",
              "the boiler service", "cycling to work", "the car's timing belt",
              "David Anderson's contract", "the holiday in Crete",
              "the leaking kitchen tap", "Priya's new job"]
    converse(store, [f"I keep thinking about {t}, it needs sorting out"
                     for t in topics] * 2, gap=4000.0)
    calls = {"n": 0}

    def interrupt():
        calls["n"] += 1
        return calls["n"] > 2

    report = night.run(store, llm, now=store.clock.now(), interrupt=interrupt)
    assert report.interrupted
    # Downscaling still runs: skipping it because someone woke the device is
    # how a store quietly saturates.
    assert report.homeostasis["edges_before"] >= 0
    assert "interrupted" in report.journal()


def test_degraded_mode_keeps_consolidating_without_a_model(store):
    converse(store, ["I prefer oat milk in my coffee",
                     "I work at Kestrel Systems"])

    class Broken:
        def structured(self, *a, **k):
            from you2.llm import LLMUnavailable
            raise LLMUnavailable("no network")

    report = night.run(store, Broken(), now=store.clock.now())
    assert report.degraded_batches >= 1
    assert report.facts.new >= 1, "consolidation must not stop when the Wi-Fi drops"
    assert store.journal("consolidate_degraded")


def test_profile_respects_its_budget(store, llm):
    for i in range(40):
        store.add(Neuron(kind=Kind.FACT, key=f"user/k{i}",
                         content=f"the user has preference number {i} " + "x " * 20))
    profile = night.build_profile(store, store.cfg, store.clock.now())
    from you2.activation import estimate_tokens
    assert estimate_tokens(profile) <= store.cfg.workspace_tokens * 1.3


def test_schema_induction_promotes_repeated_patterns(store):
    for i, thing in enumerate(["milk", "coffee", "bread", "cheese", "wine"]):
        store.add(Neuron(kind=Kind.FACT, key=f"user/pref_{thing}",
                         content=f"the user prefers good {thing}"))
    created = abstraction.induce_schema(store, store.cfg, store.clock.now())
    assert created
    schema = store.get(created[0])
    assert schema.kind is Kind.SCHEMA
    assert len(schema.meta["members"]) >= 4


# ----------------------------------------------------------------------- REM

def test_structure_mapping_rewards_shape_and_penalises_content(store):
    """The inversion. Cosine distance cannot find analogy in principle; this
    scores matching relational shape and subtracts matching content."""
    def star(label, n):
        hub = store.add(Neuron(kind=Kind.ENTITY, content=f"{label} hub")).id
        for i in range(n):
            leaf = store.add(Neuron(kind=Kind.EPISODE,
                                    content=f"{label} leaf {i}")).id
            store.connect(leaf, hub, Edge.ABOUT, 0.8)
        return hub

    a = star("alpha kestrel migration", 5)
    b = star("bravo garden retaining", 5)      # same shape, unrelated content
    c = star("alpha kestrel migration copy", 1)  # similar content, different shape

    same_shape = dream.alignment(store, a, b)
    diff_shape = dream.alignment(store, a, c)
    assert same_shape.alignment > diff_shape.alignment
    assert same_shape.relation_sim >= diff_shape.relation_sim - 1e-6
    assert same_shape.content_sim < 0.9


def test_bridges_are_never_pushed(store, llm):
    converse(store, ["the Kestrel migration keeps slipping because of testing",
                     "the garden wall project keeps slipping because of the weather",
                     "I'll chase the Kestrel testing team on Monday"])
    report = night.run(store, llm, now=store.clock.now(), seed=5)
    for nid in report.insights:
        n = store.get(nid)
        if n.meta.get("type") == "bridge":
            assert not n.meta.get("pushed"), "a wrong analogy makes the whole system feel stupid"


def test_a_repeated_claim_cannot_fork_a_key_forever(store):
    """`resolve` only consults the exact holder of a key, so a claim already
    stored under `key_2` is invisible to it and forks `key_3`. Say the same
    sentence on forty nights against a key whose holder is unrelated and the
    store ends up with forty identical facts under forty keys.

    Not hypothetical: a correction that hijacked `coffee_machine/state` did
    exactly this, and live facts went from 14 to 90 over a simulated year
    with no test failing. The hijack is fixed in recall/reconsolidate.py;
    this makes the shape of the failure impossible rather than unlikely.
    """
    store.add(Neuron(kind=Kind.FACT, key="coffee_machine/state",
                     content="the user prefers oat milk now"))
    said = "the coffee machine is making that noise again"
    for _ in range(12):
        supersede.resolve(store, None, "coffee_machine/state", said, [], 0.7,
                          store.clock.now(), store.cfg)

    live = [n for n in store.by_kind(Kind.FACT, limit=99, live_only=True)]
    assert len(live) == 2, [(n.key, n.text()) for n in live]
    assert sum(1 for n in live if said in n.text()) == 1
    # And the one fork that was legitimate is journalled rather than silent.
    assert store.journal("key_forked", limit=10)


def test_a_genuinely_different_claim_still_gets_its_own_key(store):
    """The sibling check must not collapse two real dimensions into one.

    These three share no vocabulary, so the judge calls every pairing
    `distinct` and each is entitled to its own key. Claims with any overlap
    at all go through the refinement branch instead and never reach the fork
    path — which is why they would not test it.
    """
    store.add(Neuron(kind=Kind.FACT, key="thing/state",
                     content="the boiler needs a service"))
    for said in ("the garden wall is finished",
                 "the car insurance renews in March"):
        supersede.resolve(store, None, "thing/state", said, [], 0.7,
                          store.clock.now(), store.cfg)
    keys = {n.key for n in store.by_kind(Kind.FACT, limit=99, live_only=True)}
    assert len(keys) == 3, keys


def test_every_batch_drawn_is_actually_consolidated(store, llm):
    """`len(batches) // cycles` truncates. Five batches over four cycles gave
    one per cycle, and the loop stopped at `cycle >= cycles` with the fifth
    never consolidated — silently, while `report.batches` said five.

    Batches are ordered chronologically, so the abandoned one was always the
    most recent: the material most likely to contain a correction. On the
    demo month "actually I prefer meetings in the afternoon these days" was
    drawn every night, dropped every night, and never reached
    `times_replayed = 1`. The device held the superseded preference forever.
    """
    now = store.clock.now()
    for i in range(30):
        store.add(Neuron(kind=Kind.EPISODE, content=f"a distinct thing about topic {i}",
                         created_at=now - (30 - i) * DAY, last_access=now,
                         salience=0.6, surprise=0.5))
    report = night.run(store, llm, store.cfg, now=now + DAY, seed=3)

    left = [n for n in store.by_kind(Kind.EPISODE, limit=99)
            if n.times_replayed == 0]
    assert left == [], f"{len(left)} episodes drawn but never consolidated"
    assert report.episodes_replayed == 30


def test_a_change_of_mind_reaches_the_store_the_same_night(store, llm):
    """The end-to-end version, and the one that matters: the second statement
    has to supersede the first rather than being dropped with the tail."""
    now = store.clock.now()
    for i, said in enumerate([
            "I prefer meetings in the morning, I am useless after three",
            "the garden wall needs rebuilding before winter",
            "the boiler service is due in October",
            "my sister Priya is moving to Lisbon in the spring",
            "actually I prefer meetings in the afternoon these days"]):
        store.add(Neuron(kind=Kind.EPISODE, content=said,
                         created_at=now + i * DAY, last_access=now,
                         salience=0.7, surprise=0.5))
    night.run(store, llm, store.cfg, now=now + 10 * DAY, seed=1)

    held = store.by_key("user/pref_meet")
    assert held is not None
    assert "afternoon" in held.text(), held.text()


def test_a_confounded_pattern_is_blocked_by_the_one_that_explains_it(store):
    """Lift normalises for the base rate, which stops "you mention work a
    lot" being a discovery. It does nothing about confounding.

    Someone who talks to the device after work generates `in_the_evening`
    alongside `it_is_tuesday` on the very same episodes. Both clear lift 2.0
    honestly, and the device reports two patterns where there is one — the
    eval printed that false positive for the life of the project.

    Kamin blocking: a cue that adds no predictive value over an established
    one is not learned. Redundant, not wrong — and telling someone about it
    costs the credibility of the pattern that was real.
    """
    from you2.discovery.recurrence import Pattern, block

    strong = Pattern(context="it_is_tuesday", outcome="drained", lift=3.1,
                     n=8, span_days=60.0, base_rate=0.2, conditional=0.62,
                     cites=[1, 2, 3, 4, 5])
    confounded = Pattern(context="in_the_evening", outcome="drained", lift=2.2,
                         n=8, span_days=60.0, base_rate=0.2, conditional=0.44,
                         cites=[1, 2, 3, 4, 9])
    genuine = Pattern(context="after_a_deploy", outcome="drained", lift=2.6,
                      n=5, span_days=40.0, base_rate=0.2, conditional=0.52,
                      cites=[20, 21, 22, 23, 24])

    kept = block([strong, genuine, confounded])
    contexts = [p.context for p in kept]
    assert "it_is_tuesday" in contexts
    assert "after_a_deploy" in contexts, "an independent pattern must survive"
    assert "in_the_evening" not in contexts


def test_blocking_does_not_reach_across_different_outcomes(store):
    """Two patterns over the same episodes but different outcomes are two
    findings, not one."""
    from you2.discovery.recurrence import Pattern, block

    a = Pattern(context="it_is_tuesday", outcome="drained", lift=3.0, n=6,
                span_days=50.0, base_rate=0.2, conditional=0.6, cites=[1, 2, 3])
    b = Pattern(context="it_is_tuesday", outcome="ran_late", lift=2.5, n=6,
                span_days=50.0, base_rate=0.2, conditional=0.5, cites=[1, 2, 3])
    assert len(block([a, b])) == 2


def test_compaction_re_embeds_or_the_block_fills_with_one_sentence(store):
    """Compaction blanks `content` and lets the episode render as its batch
    `gist` — and every episode of a batch shares one gist. Left un-re-embedded
    the vectors still describe the *deleted* text, which breaks two things at
    once.

    N compacted episodes render as N identical lines while their stale
    vectors sit 0.41–0.47 apart, so lateral inhibition is structurally unable
    to see the duplicates: the block gets the same sentence four times. And
    the episode stays retrievable by the exact words that were just deleted —
    it answers to "Marta draft" and then renders as a generic gist, which is
    worse than not answering at all.

    Found by the multi-agent audit, and it is a door the earlier
    inhibition fix cannot close: that one relies on similarity reaching 1.0.
    """
    now = store.clock.now()
    hub = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration")).id
    ids = []
    for said in ("spoke to Marta about the Kestrel migration for a while",
                 "Priya sent over the latest Kestrel migration draft",
                 "had a look at the Kestrel migration this evening"):
        n = store.add(Neuron(kind=Kind.EPISODE, content=said, tier=Tier.COLD,
                             created_at=now - 500 * DAY,
                             last_access=now - 500 * DAY))
        n.gist = "kestrel migration, test team, drafts"
        n.absorbed = True
        store.save(n)
        store.connect(hub, n.id, Edge.ASSOC, 0.95, at=now)
        ids.append(n.id)
    store.flush()
    assert forgetting.compact(store, store.cfg, now).compacted == len(ids)
    store.flush()

    # Reactivated the way any later mention would reactivate them.
    for nid in ids:
        n = store.get(nid)
        n.last_access = now - DAY
        n.storage_strength = 2.0
        store.save(n)
    store.flush()

    ws = recall(store, "kestrel migration test team drafts", now=now)
    lines = [i.line for i in ws.items]
    assert len(lines) == len(set(lines)), lines

    # And it must no longer answer to the words it no longer holds.
    stale = recall(store, "Marta draft", now=now)
    assert not [i for i in stale.items
                if "kestrel migration, test team" in i.line]
