"""Phase 2 gate: bounded visits, a budget that holds, and a workspace that
carries more than one idea."""

from __future__ import annotations

import time

import numpy as np
import pytest

from you2 import DAY, BrainConfig, SimClock
from you2.activation import (
    Cue, availability, broadcast, build_cues, compete, estimate_tokens,
    half_life_days, on_retrieval, parse_window, recall, retrieval_strength,
    spread,
)
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


def soma_of(store, nid):
    return store.soma[nid]


# ----------------------------------------------------------------- baselevel

def test_retrieval_strength_decays_as_a_power_law(store):
    cfg = store.cfg
    n = store.add(Neuron(kind=Kind.EPISODE, content="a thing happened"))
    base = retrieval_strength(soma_of(store, n.id), store.clock.now(), cfg)

    store.clock.advance(days=30)
    month = retrieval_strength(soma_of(store, n.id), store.clock.now(), cfg)
    store.clock.advance(days=335)
    year = retrieval_strength(soma_of(store, n.id), store.clock.now(), cfg)

    assert base > month > year > cfg.retrieval_floor
    assert year > 0.0, "forgetting is loss of access, not loss of data"


def test_storage_strength_slows_forgetting(store):
    cfg = store.cfg
    weak = store.add(Neuron(kind=Kind.EPISODE, content="weak", storage_strength=1.0))
    strong = store.add(Neuron(kind=Kind.EPISODE, content="strong", storage_strength=6.0))
    store.clock.advance(days=365)
    now = store.clock.now()
    assert (retrieval_strength(soma_of(store, strong.id), now, cfg)
            > retrieval_strength(soma_of(store, weak.id), now, cfg))
    assert half_life_days(6.0, cfg) > half_life_days(1.0, cfg)


def test_desirable_difficulty(store):
    """Recovering a faded memory should make it much more durable than
    re-reading a fresh one."""
    cfg = store.cfg
    fresh = store.add(Neuron(kind=Kind.EPISODE, content="fresh"))
    faded = store.add(Neuron(kind=Kind.EPISODE, content="faded"))
    store.clock.advance(days=400)
    now = store.clock.now()

    fresh_n = store.get(fresh.id)
    fresh_n.last_access = now                       # accessed just now
    store.save(fresh_n)

    gain_fresh = on_retrieval(soma_of(store, fresh.id), now, cfg)[0] - 1.0
    gain_faded = on_retrieval(soma_of(store, faded.id), now, cfg)[0] - 1.0
    assert gain_faded > 4 * gain_fresh


def test_pinned_memories_do_not_fade(store):
    cfg = store.cfg
    pinned = store.add(Neuron(kind=Kind.FACT, content="my name is Soumil",
                              key="user/name", pinned=True))
    store.clock.advance(days=3 * 365)
    assert availability(soma_of(store, pinned.id), store.clock.now(), cfg) >= 0.8


# -------------------------------------------------------------------- spread

def _chain(store, n=12):
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"node {i}")).id
           for i in range(n)]
    for a, b in zip(ids, ids[1:]):
        store.connect(a, b, Edge.ASSOC, 0.9)
    return ids


def test_activation_dies_out_with_distance(store):
    ids = _chain(store)
    cas = spread(store.graph, [Cue(ids[0], 1.0)], store.cfg)
    acts = [cas.activation.get(i, 0.0) for i in ids]
    reached = [a for a in acts if a > 0.0]
    assert len(reached) < len(ids), "the cascade must not reach the whole chain"
    for a, b in zip(reached, reached[1:]):
        assert a > b
    assert acts[6] < 0.01, "activation must not survive six hops"
    assert cas.exhausted, "cascade should die out, not hit the budget"


def test_visit_budget_is_a_hard_cap(store):
    """The guarantee: retrieval cost is set by the cascade, not the corpus."""
    rng = np.random.default_rng(7)
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"n{i}")).id
           for i in range(2000)]
    for _ in range(20_000):
        a, b = rng.integers(0, len(ids), 2)
        if a != b:
            store.connect(ids[a], ids[b], Edge.ASSOC, 0.95)

    cfg = store.cfg.merged(visit_budget=150, fire_threshold=0.0001)
    cas = spread(store.graph, [Cue(ids[0], 1.0)], cfg)
    assert cas.visited <= 150
    assert not cas.exhausted


def test_cost_is_independent_of_corpus_size(tmp_path):
    """The property the whole design exists to have. A store 10x larger must
    not make one retrieval measurably slower."""
    def build(n_extra):
        cfg = BrainConfig(db_path=str(tmp_path / f"c{n_extra}.db"))
        s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
        core = [s.add(Neuron(kind=Kind.ENTITY, content=f"core {i}")).id
                for i in range(20)]
        for a, b in zip(core, core[1:]):
            s.connect(a, b, Edge.ASSOC, 0.8)
        for i in range(n_extra):                    # unconnected bulk
            s.add(Neuron(kind=Kind.EPISODE, content=f"unrelated chatter {i}"))
        return s, core

    small, core_s = build(200)
    big, core_b = build(6000)

    def timed(s, seed):
        t0 = time.perf_counter()
        for _ in range(50):
            spread(s.graph, [Cue(seed, 1.0)], s.cfg)
        return (time.perf_counter() - t0) / 50

    t_small, t_big = timed(small, core_s[0]), timed(big, core_b[0])
    a = spread(small.graph, [Cue(core_s[0], 1.0)], small.cfg)
    b = spread(big.graph, [Cue(core_b[0], 1.0)], big.cfg)
    assert a.visited == b.visited
    assert t_big < t_small * 3 + 0.002, f"{t_small*1e3:.2f} vs {t_big*1e3:.2f} ms"
    small.close(); big.close()


def test_fan_effect_damps_hubs(store):
    """A cue attached to 200 things should give each of them less than a cue
    attached to two."""
    hub = store.add(Neuron(kind=Kind.ENTITY, content="work")).id
    many = [store.add(Neuron(kind=Kind.EPISODE, content=f"m{i}")).id
            for i in range(200)]
    for m in many:
        store.connect(hub, m, Edge.ASSOC, 0.5)

    rare = store.add(Neuron(kind=Kind.ENTITY, content="kintsugi")).id
    few = [store.add(Neuron(kind=Kind.EPISODE, content=f"f{i}")).id for i in range(2)]
    for f in few:
        store.connect(rare, f, Edge.ASSOC, 0.5)

    hub_cas = spread(store.graph, [Cue(hub, 1.0)], store.cfg)
    rare_cas = spread(store.graph, [Cue(rare, 1.0)], store.cfg)
    assert rare_cas.activation[few[0]] > hub_cas.activation[many[0]] * 3


def test_supersession_starves_the_old_fact(store):
    """The single number that makes supersession worth having for retrieval."""
    old = store.add(Neuron(kind=Kind.FACT, content="drinks cow milk", key="user/milk"))
    new = store.add(Neuron(kind=Kind.FACT, content="drinks oat milk", key="user/milk"))
    store.connect(new.id, old.id, Edge.SUPERSEDES, 1.0)
    sibling = store.add(Neuron(kind=Kind.FACT, content="likes strong coffee",
                               key="user/coffee"))
    store.connect(new.id, sibling.id, Edge.ASSOC, 1.0)

    cas = spread(store.graph, [Cue(new.id, 1.0)], store.cfg)
    assert cas.activation[old.id] < cas.activation[sibling.id] / 3


def test_entity_cue_reaches_its_episodes(store):
    ent = store.add(Neuron(kind=Kind.ENTITY, content="Priya"), aliases=["priya"])
    eps = [store.add(Neuron(kind=Kind.EPISODE, content=f"lunch with priya {i}"))
           for i in range(3)]
    for e in eps:
        store.connect(e.id, ent.id, Edge.ABOUT, 0.9)

    cas = spread(store.graph, [Cue(ent.id, 1.0)], store.cfg)
    for e in eps:
        assert cas.activation.get(e.id, 0.0) > 0.05


def test_path_explanation(store):
    ids = _chain(store, 4)
    cas = spread(store.graph, [Cue(ids[0], 1.0)], store.cfg)
    path = cas.path_to(ids[3])
    assert [p for p, _ in path] == ids[:4]


# ---------------------------------------------------------------------- cues

def test_time_windows_parse():
    now = 1_800_000_000.0
    assert parse_window("what did I say last week", now) is not None
    start, end = parse_window("three weeks ago I mentioned it", now)
    assert start < now - 20 * DAY < end
    assert parse_window("tell me a joke", now) is None


def test_entity_mentions_beat_topical_neighbours(store):
    store.add(Neuron(kind=Kind.ENTITY, content="David Anderson"),
              aliases=["anderson", "david anderson"])
    for i in range(5):
        store.add(Neuron(kind=Kind.EPISODE, content=f"a contract negotiation {i}"))

    cues = build_cues(store, "how did it go with Anderson", store.cfg)
    assert cues.mentions
    top = max(cues.cues, key=lambda c: c.weight)
    assert top.source == "entity"


# ----------------------------------------------------------------- workspace

def test_block_never_exceeds_its_budget(store):
    ids = []
    for i in range(60):
        n = store.add(Neuron(kind=Kind.FACT, key=f"k{i}",
                             content=f"fact number {i} " + "padding words " * 12))
        ids.append(n.id)
    for a, b in zip(ids, ids[1:]):
        store.connect(a, b, Edge.ASSOC, 0.99)

    cfg = store.cfg.merged(workspace_tokens=120, workspace_max_items=40)
    cas = spread(store.graph, [Cue(ids[0], 1.0)], cfg)
    ws = compete(store, cas, cfg)
    assert ws.tokens <= 120
    assert estimate_tokens(ws.block()) <= 160     # headers add a little


def test_lateral_inhibition_produces_a_diverse_block(store):
    """Eight paraphrases of one memory is the failure this prevents."""
    dupes = [store.add(Neuron(kind=Kind.FACT, key=f"d{i}",
                              content="prefers oat milk in coffee"))
             for i in range(6)]
    others = [
        store.add(Neuron(kind=Kind.FACT, key="a", content="cycles to work most days")),
        store.add(Neuron(kind=Kind.FACT, key="b", content="allergic to penicillin")),
        store.add(Neuron(kind=Kind.FACT, key="c", content="sister lives in Lisbon")),
    ]
    everyone = [n.id for n in dupes + others]
    hub = store.add(Neuron(kind=Kind.ENTITY, content="user")).id
    for nid in everyone:
        store.connect(hub, nid, Edge.ASSOC, 0.9)

    cas = spread(store.graph, [Cue(hub, 1.0)], store.cfg)
    on = compete(store, cas, store.cfg)
    off = compete(store, cas, store.cfg.merged(inhibition=0.0))

    n_dupes_on = sum(1 for n in on.neurons() if n.id in {d.id for d in dupes})
    n_dupes_off = sum(1 for n in off.neurons() if n.id in {d.id for d in dupes})
    assert n_dupes_on < n_dupes_off
    assert n_dupes_on <= 2
    assert on.suppressed


def test_facts_outrank_the_episodes_they_came_from(store):
    """Equally activated, the distilled fact should beat the raw episode: it
    is denser signal per token and current by construction."""
    ep = store.add(Neuron(kind=Kind.EPISODE, content="I really cannot stand cilantro"))
    fact = store.add(Neuron(kind=Kind.FACT, key="user/cilantro",
                            content="I really cannot stand cilantro"))

    cfg = store.cfg.merged(inhibition=0.0)
    cas = spread(store.graph, [Cue(ep.id, 1.0), Cue(fact.id, 1.0)], cfg)
    ws = compete(store, cas, cfg)
    assert [i.neuron.kind for i in ws.items][0] is Kind.FACT
    by_id = {i.neuron.id: i.score for i in ws.items}
    assert by_id[fact.id] > by_id[ep.id]


def test_superseded_facts_never_enter_the_block(store):
    old = store.add(Neuron(kind=Kind.FACT, content="works at Kestrel", key="user/employer"))
    new = store.add(Neuron(kind=Kind.FACT, content="works at Lumen", key="user/employer"))
    old.superseded_by = new.id
    old.invalid_at = store.clock.now()
    store.save(old)

    cas = spread(store.graph, [Cue(old.id, 1.0), Cue(new.id, 1.0)], store.cfg)
    ws = compete(store, cas, store.cfg)
    assert old.id not in ws.ids()
    assert new.id in ws.ids()


def test_broadcast_wires_the_winners_together(store):
    """The association graph is a by-product of use. Nothing else writes
    these edges."""
    a = store.add(Neuron(kind=Kind.FACT, key="a", content="rides a bicycle to work"))
    b = store.add(Neuron(kind=Kind.FACT, key="b", content="the office is in Shoreditch"))
    hub = store.add(Neuron(kind=Kind.ENTITY, content="commute")).id
    store.connect(hub, a.id, Edge.ASSOC, 0.9)
    store.connect(hub, b.id, Edge.ASSOC, 0.9)

    assert store.graph.weight(min(a.id, b.id), max(a.id, b.id), Edge.ASSOC) == 0.0
    cas = spread(store.graph, [Cue(hub, 1.0)], store.cfg)
    ws = compete(store, cas, store.cfg)
    broadcast(store, ws, store.cfg)
    assert store.graph.weight(min(a.id, b.id), max(a.id, b.id), Edge.ASSOC) > 0.0


def test_broadcast_records_access_and_strengthens(store):
    n = store.add(Neuron(kind=Kind.FACT, key="x", content="prefers trains to planes"))
    before = store.get(n.id)
    store.clock.advance(days=200)
    cas = spread(store.graph, [Cue(n.id, 1.0)], store.cfg)
    ws = compete(store, cas, store.cfg)
    broadcast(store, ws, store.cfg)

    after = store.get(n.id)
    assert after.access_count == before.access_count + 1
    assert after.storage_strength > before.storage_strength
    assert after.last_access == store.clock.now()


def test_recall_end_to_end(store):
    ent = store.add(Neuron(kind=Kind.ENTITY, content="David Anderson"),
                    aliases=["anderson"])
    fact = store.add(Neuron(kind=Kind.FACT, key="anderson/role",
                            content="David Anderson is the client at Kestrel"))
    ep = store.add(Neuron(kind=Kind.EPISODE,
                          content="the anderson deadline moved to September"))
    store.connect(ep.id, ent.id, Edge.ABOUT, 0.9)
    store.connect(fact.id, ent.id, Edge.ABOUT, 0.9)

    ws = recall(store, "any news on Anderson?", learn=True)
    ids = set(ws.ids())
    assert ent.id in ids and fact.id in ids
    assert ws.tokens <= store.cfg.workspace_tokens
    assert "Anderson" in ws.block()
    assert "via" in ws.explain() or "seed" in ws.explain()


def test_workspace_context_carries_to_the_next_turn(store):
    ent = store.add(Neuron(kind=Kind.ENTITY, content="the retaining wall"),
                    aliases=["retaining wall"])
    ep = store.add(Neuron(kind=Kind.EPISODE,
                          content="the retaining wall needs rebuilding before winter"))
    store.connect(ep.id, ent.id, Edge.ABOUT, 0.9)

    first = recall(store, "what about the retaining wall")
    assert ent.id in first.ids()

    # A bare pronoun shares no lexical content with the referent, so the only
    # thing that can bind it is the previous workspace.
    with_ctx = recall(store, "how much will that cost", context=first.context())
    without = recall(store, "how much will that cost")
    a_with = dict(with_ctx.context())[ent.id]
    a_without = dict(without.context()).get(ent.id, 0.0)
    assert a_with > 3 * a_without


def test_an_exact_duplicate_cannot_buy_a_second_slot(store):
    """Found in the year-long eval, printed verbatim: the day-365 block
    contained "spoke to Tom Beckwith about the garden wall for a while"
    *twice*. A flat damping treated a 0.80 neighbour and an identical
    restatement the same, so the copy kept 30% of its score, cleared the
    firing threshold and took a slot. Suppression now scales with similarity
    and is total at 1.0, because a memory the winner has already said in full
    carries no information at any price."""
    said = "spoke to Tom Beckwith about the garden wall for a while"
    twins = [store.add(Neuron(kind=Kind.EPISODE, content=said)) for _ in range(2)]
    other = store.add(Neuron(kind=Kind.FACT, key="u/a",
                             content="allergic to penicillin"))
    hub = store.add(Neuron(kind=Kind.ENTITY, content="the garden wall")).id
    for n in twins + [other]:
        store.connect(hub, n.id, Edge.ASSOC, 0.9)

    ws = compete(store, spread(store.graph, [Cue(hub, 1.0)], store.cfg), store.cfg)
    lines = [i.line for i in ws.items]
    assert len(lines) == len(set(lines)), lines
    assert sum(1 for n in ws.neurons() if n.content == said) == 1
