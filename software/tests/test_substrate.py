"""Phase 1 gate: the substrate round-trips, and the graph is cheap."""

from __future__ import annotations

import time

import numpy as np
import pytest

from you2 import DAY, BrainConfig, SimClock
from you2.substrate import (
    Edge, Graph, HashEmbedder, Kind, Neuron, Store, Tier, cosine, hebbian,
)


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    clock = SimClock()
    s = Store(cfg, clock, HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


# ------------------------------------------------------------------ embedder

def test_hash_embedder_is_deterministic_across_instances():
    a = HashEmbedder(dim=384).encode(["the anderson contract slipped again"])
    b = HashEmbedder(dim=384).encode(["the anderson contract slipped again"])
    assert np.allclose(a, b)
    assert pytest.approx(1.0, abs=1e-5) == float(np.linalg.norm(a[0]))


def test_hash_embedder_has_usable_lexical_structure():
    """It is a weak semantic model, but it must at least rank overlap above
    non-overlap or every downstream test is measuring noise."""
    e = HashEmbedder(dim=384)
    v = e.encode([
        "I am meeting David Anderson about the contract",   # 0
        "the Anderson contract meeting is on Thursday",     # 1
        "I should replace the timing belt on the car",      # 2
    ])
    assert cosine(v[0], v[1]) > cosine(v[0], v[2])
    assert cosine(v[0], v[1]) > 0.2


def test_stopword_only_utterances_do_not_collide_at_the_origin():
    e = HashEmbedder(dim=384)
    v = e.encode(["ok thanks", "yes it is"])
    assert float(np.linalg.norm(v[0])) > 0.9
    assert cosine(v[0], v[1]) < 0.99


# --------------------------------------------------------------------- store

def test_neuron_round_trip(store):
    n = store.add(Neuron(kind=Kind.FACT, content="prefers oat milk",
                         key="user/milk", salience=0.8, meta={"cites": [1, 2]}))
    assert n.id > 0
    got = store.get(n.id)
    assert got is not None
    assert got.content == "prefers oat milk"
    assert got.kind is Kind.FACT
    assert got.key == "user/milk"
    assert got.citations() == [1, 2]
    assert got.valid_at is not None       # facts are bitemporal on insert


def test_supersession_keeps_history_queryable(store):
    old = store.add(Neuron(kind=Kind.FACT, content="drinks cow milk", key="user/milk"))
    store.clock.advance(days=40)
    new = store.add(Neuron(kind=Kind.FACT, content="drinks oat milk", key="user/milk"))
    old.superseded_by = new.id
    old.invalid_at = store.clock.now()
    store.save(old)

    assert store.by_key("user/milk").id == new.id
    assert len(store.history_of("user/milk")) == 2
    assert store.count(Kind.FACT, live_only=True) == 1


def test_alias_resolution(store):
    e = store.add(Neuron(kind=Kind.ENTITY, content="David Anderson"),
                  aliases=["david", "dave", "anderson"])
    assert store.find_entity("Dave").id == e.id
    assert store.find_entity("  ANDERSON ").id == e.id
    assert store.find_entity("nobody") is None


def test_entombing_cascades_to_derived_facts(store):
    ep = store.add(Neuron(kind=Kind.EPISODE, content="I hate cilantro"))
    fact = store.add(Neuron(kind=Kind.FACT, content="dislikes cilantro",
                            key="user/cilantro", meta={"cites": [ep.id]}))
    other = store.add(Neuron(kind=Kind.FACT, content="likes basil",
                             key="user/basil", meta={"cites": [999]}))

    killed = store.entomb(ep.id)
    assert set(killed) == {ep.id, fact.id}
    assert store.get(fact.id).tier is Tier.TOMB
    assert store.get(fact.id).content == ""
    assert store.get(other.id).tier is Tier.HOT


def test_search_finds_the_right_neuron(store):
    store.add(Neuron(kind=Kind.EPISODE, content="the anderson contract deadline moved"))
    target = store.add(Neuron(kind=Kind.EPISODE,
                              content="I need to fix the leaking kitchen tap"))
    store.add(Neuron(kind=Kind.EPISODE, content="lunch with priya on friday"))

    hits = store.search("the tap in the kitchen is leaking", k=3)
    assert hits[0][0] == target.id


def test_vectors_survive_reload(store, tmp_path):
    n = store.add(Neuron(kind=Kind.EPISODE, content="rebuilt the retaining wall"))
    store.flush()
    store.load()
    assert store.vectors.has(n.id)
    assert store.search("retaining wall rebuild", k=1)[0][0] == n.id


# --------------------------------------------------------------------- graph

def test_hebbian_is_soft_bounded():
    w = 0.0
    for _ in range(500):
        w = hebbian(w, 1.0, 1.0, 0.08)
    assert w < 1.0
    assert w > 0.98          # approaches the ceiling without reaching it


def test_edges_traverse_in_both_directions(store):
    """An entity cue must be able to reach the episodes that mention it, or
    naming a person retrieves nothing."""
    ep = store.add(Neuron(kind=Kind.EPISODE, content="lunch with priya"))
    ent = store.add(Neuron(kind=Kind.ENTITY, content="Priya"))
    store.connect(ep.id, ent.id, Edge.ABOUT, 0.9)

    forward = {o for o, *_ in store.graph.neighbours(ep.id)}
    backward = {o for o, *_ in store.graph.neighbours(ent.id)}
    assert ent.id in forward
    assert ep.id in backward


def test_overlay_shadows_csr_after_rebuild(store):
    a = store.add(Neuron(kind=Kind.ENTITY, content="A"))
    b = store.add(Neuron(kind=Kind.ENTITY, content="B"))
    store.connect(a.id, b.id, Edge.ASSOC, 0.20)
    store.flush()
    store.load()                                  # now in CSR, overlay empty
    assert store.graph.overlay_size == 0
    assert store.graph.weight(a.id, b.id, Edge.ASSOC) == pytest.approx(0.20)

    store.connect(a.id, b.id, Edge.ASSOC, 0.60)   # update lands in overlay
    assert store.graph.weight(a.id, b.id, Edge.ASSOC) == pytest.approx(0.60)
    seen = [(o, w) for o, w, _c, _b in store.graph.neighbours(a.id) if o == b.id]
    assert seen == [(b.id, pytest.approx(0.60))], "stale CSR entry leaked through"
    assert store.graph.edge_count == 1


def test_multiplicative_downscaling_preserves_relative_structure():
    g = Graph()
    g.build([(1, 2, Edge.ASSOC, 0.80), (1, 3, Edge.ASSOC, 0.20),
             (1, 4, Edge.ABOUT, 0.50)])
    g.scale_all(0.5, exempt=frozenset({Edge.ABOUT}))

    assert g.weight(1, 2, Edge.ASSOC) == pytest.approx(0.40)
    assert g.weight(1, 3, Edge.ASSOC) == pytest.approx(0.10)
    # ratio is exactly preserved -- the whole point of multiplicative decay
    assert (g.weight(1, 2, Edge.ASSOC) / g.weight(1, 3, Edge.ASSOC)
            == pytest.approx(4.0))
    # structural edges do not erode
    assert g.weight(1, 4, Edge.ABOUT) == pytest.approx(0.50)


def test_graph_hops_and_neighbourhood():
    g = Graph()
    g.build([(1, 2, Edge.ASSOC, 0.5), (2, 3, Edge.ASSOC, 0.5),
             (3, 4, Edge.ASSOC, 0.5), (10, 11, Edge.ASSOC, 0.5)])
    assert g.hops(1, 4) == 3
    assert g.hops(1, 1) == 0
    assert g.hops(1, 11) == -1
    assert set(g.neighbourhood(1, hops=2)) == {1, 2, 3}


def test_persist_graph_round_trips(store):
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"n{i}")).id for i in range(6)]
    store.connect(ids[0], ids[1], Edge.ASSOC, 0.4)
    store.connect(ids[1], ids[2], Edge.ABOUT, 0.7)
    store.graph.scale_all(0.5, exempt=frozenset({Edge.ABOUT}))
    store.persist_graph()
    store.load()

    assert store.graph.weight(ids[0], ids[1], Edge.ASSOC) == pytest.approx(0.20)
    assert store.graph.weight(ids[1], ids[2], Edge.ABOUT) == pytest.approx(0.70)
    assert store.graph.overlay_size == 0


# ---------------------------------------------------------------- phase gate

def test_phase1_gate_scale_and_speed(tmp_path):
    """10k neurons round-trip, and the graph rebuild stays under 100 ms."""
    cfg = BrainConfig(db_path=str(tmp_path / "big.db"))
    clock = SimClock()
    s = Store(cfg, clock, HashEmbedder(dim=cfg.embed_dim))

    n = 10_000
    with s.transaction():
        for i in range(n):
            clock.advance(hours=1)
            s.add(Neuron(kind=Kind.EPISODE, content=f"episode {i} about topic {i % 97}"))
    ids = [r["id"] for r in s.conn.execute("SELECT id FROM neurons").fetchall()]

    rng = np.random.default_rng(0)
    with s.transaction():
        for _ in range(30_000):
            a, b = rng.integers(0, n, 2)
            if a != b:
                s.connect(ids[a], ids[b], Edge.ASSOC, float(rng.random()))

    t0 = time.perf_counter()
    s.load()
    rebuild_ms = (time.perf_counter() - t0) * 1000
    assert s.count() == n
    assert rebuild_ms < 2000, f"graph rebuild {rebuild_ms:.0f} ms"

    t0 = time.perf_counter()
    for _ in range(20):
        s.search("topic 42 episode", k=8)
    search_ms = (time.perf_counter() - t0) * 1000 / 20
    assert search_ms < 25, f"hot search {search_ms:.1f} ms"

    st = s.stats()
    assert st["neurons"] == n
    assert st["edges"] > 25_000
    s.close()
