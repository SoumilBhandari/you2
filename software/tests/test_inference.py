"""Inference: concluding what was never said, and not storing it all."""

from __future__ import annotations

import pytest

from you2 import DAY, BrainConfig, SimClock
from you2.activation import recall
from you2.inference import attach, cheap, compilation, parsimony, tms
from you2.inference.engine import Inference, contrast, inherit, transitive
from you2.substrate import Edge, HashEmbedder, Kind, Neuron, Store


@pytest.fixture
def store(tmp_path):
    cfg = BrainConfig(db_path=str(tmp_path / "brain.db"))
    s = Store(cfg, SimClock(), HashEmbedder(dim=cfg.embed_dim))
    yield s
    s.close()


# ------------------------------------------------------------- graph rules

def test_transitive_chains_only_composable_edges(store):
    a = store.add(Neuron(kind=Kind.ENTITY, content="the login rewrite")).id
    b = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration")).id
    c = store.add(Neuron(kind=Kind.ENTITY, content="Q3 delivery")).id
    store.connect(a, b, Edge.PART_OF, 0.9)
    store.connect(b, c, Edge.PART_OF, 0.9)

    out = transitive(store, [a], store.cfg)
    assert any(c in i.premises and i.kind == "transitive" for i in out)
    assert all(i.confidence < 0.9 for i in out), "chains must lose confidence"


def test_association_is_never_chained_as_inference(store):
    """Chaining plain association is spreading activation with a straight
    face, and it is where every 'you mentioned coffee here and also here'
    feature comes from."""
    a = store.add(Neuron(kind=Kind.ENTITY, content="coffee")).id
    b = store.add(Neuron(kind=Kind.ENTITY, content="mornings")).id
    c = store.add(Neuron(kind=Kind.ENTITY, content="the commute")).id
    store.connect(a, b, Edge.ASSOC, 0.95)
    store.connect(b, c, Edge.ASSOC, 0.95)
    assert transitive(store, [a], store.cfg) == []


def test_inherit_is_defeasible(store):
    parent = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration")).id
    child = store.add(Neuron(kind=Kind.ENTITY, content="the login rewrite")).id
    fact = store.add(Neuron(kind=Kind.FACT, key="kestrel/blocker",
                            content="the Kestrel migration is blocked on the test team",
                            confidence=0.9))
    store.connect(child, parent, Edge.PART_OF, 0.9)
    store.connect(fact.id, parent, Edge.ABOUT, 0.9)

    out = inherit(store, [child], store.cfg)
    assert out
    assert out[0].confidence < 0.9, "inherited properties are weaker than stated ones"
    assert "probably" in out[0].claim


def test_contrast_reads_the_shape_of_a_history(store):
    """Not retrievable by any similarity search: it is a fact about how a key
    has moved, which only exists because supersession keeps the old versions."""
    first = store.add(Neuron(kind=Kind.FACT, key="user/employer",
                             content="the user works at Fenwick"))
    store.clock.advance(days=120)
    second = store.add(Neuron(kind=Kind.FACT, key="user/employer",
                              content="the user works at Lumen"))
    first.superseded_by = second.id
    first.invalid_at = store.clock.now()
    store.save(first)

    out = contrast(store, [second.id], store.cfg, store.clock.now())
    assert out and out[0].kind == "contrast"
    assert "changed 1 time" in out[0].claim
    assert first.id in out[0].premises


def test_confidence_multiplies_down_a_chain(store):
    ids = [store.add(Neuron(kind=Kind.ENTITY, content=f"level {i}")).id
           for i in range(4)]
    for a, b in zip(ids, ids[1:]):
        store.connect(a, b, Edge.PART_OF, 0.9)
    out = transitive(store, [ids[0]], store.cfg, max_depth=3)
    depths = sorted(out, key=lambda i: len(i.premises))
    assert len(depths) >= 2
    assert depths[0].confidence > depths[-1].confidence


# --------------------------------------------------------------- workspace

def test_inferences_are_labelled_as_inferred(store):
    """Without the label the model asserts an inference back as something the
    person said, which is the fastest way to lose their trust."""
    parent = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration"),
                       aliases=["kestrel"]).id
    child = store.add(Neuron(kind=Kind.ENTITY, content="the login rewrite"),
                      aliases=["login rewrite"]).id
    fact = store.add(Neuron(kind=Kind.FACT, key="kestrel/blocker",
                            content="the Kestrel migration is blocked on the test team",
                            confidence=0.9))
    store.connect(child, parent, Edge.PART_OF, 0.9)
    store.connect(fact.id, parent, Edge.ABOUT, 0.9)

    ws = recall(store, "how is the login rewrite going", infer=True)
    if ws.inferences:
        assert "inferred (not stated" in ws.block()
        assert "%" in ws.block()


def test_inference_never_breaks_the_token_budget(store):
    ids = []
    for i in range(30):
        ids.append(store.add(Neuron(kind=Kind.ENTITY, content=f"thing {i}")).id)
    for a, b in zip(ids, ids[1:]):
        store.connect(a, b, Edge.PART_OF, 0.95)

    cfg = store.cfg.merged(workspace_tokens=140, inference_workspace_tokens=60)
    ws = recall(store, "thing 0", cfg=cfg)
    attach(store, ws, cfg, store.clock.now())
    assert ws.tokens <= cfg.workspace_tokens


# ------------------------------------------------------------- compilation

def test_a_derivation_compiles_only_after_repetition(store):
    """The storage answer: most derivations are drawn once and never cost a
    byte. Only the ones that keep coming back get materialised."""
    a = store.add(Neuron(kind=Kind.ENTITY, content="alpha")).id
    b = store.add(Neuron(kind=Kind.ENTITY, content="beta")).id
    inf = Inference(claim="alpha is part of beta", kind="transitive",
                    premises=[a, b], confidence=0.8)
    now = store.clock.now()

    for i in range(store.cfg.inference_compile_threshold - 1):
        out = compilation.sweep(store, [inf], store.cfg, now)
        assert out.compiled == []
    out = compilation.sweep(store, [inf], store.cfg, now)
    assert len(out.compiled) == 1

    fact = store.get(out.compiled[0])
    assert fact.meta["derived"] is True
    assert fact.meta["derived_from"] == [a, b]
    assert fact.confidence == pytest.approx(0.8)

    # And never twice.
    assert compilation.sweep(store, [inf], store.cfg, now).compiled == []


def test_low_confidence_derivations_are_never_compiled(store):
    a = store.add(Neuron(kind=Kind.ENTITY, content="alpha")).id
    b = store.add(Neuron(kind=Kind.ENTITY, content="beta")).id
    weak = Inference(claim="a guess", kind="project", premises=[a, b],
                     confidence=0.40)
    for _ in range(8):
        compilation.sweep(store, [weak], store.cfg, store.clock.now())
    assert compilation.stats(store)["compiled"] == 0


# --------------------------------------------------- truth maintenance

def test_superseding_a_premise_invalidates_what_was_derived_from_it(store):
    """The failure this was built for: on 180 days of synthetic history,
    retrieval precision was a clean 1.00 while half the probes still leaked a
    superseded value — and every leak was a stored insight quoting a fact
    replaced months earlier."""
    from you2.consolidation.supersede import resolve

    episode = store.add(Neuron(kind=Kind.EPISODE, content="I work at Fenwick"))
    old = resolve(store, None, "user/employer", "the user works at Fenwick",
                  [episode.id], 0.9, store.clock.now(), store.cfg).fact
    insight = store.add(Neuron(
        kind=Kind.INSIGHT, content="you have been at Fenwick a long time",
        meta={"cites": [old.id], "type": "bridge"}))
    store.connect(insight.id, old.id, Edge.SUPPORTS, 0.8)

    store.clock.advance(days=90)
    resolve(store, None, "user/employer", "the user works at Lumen",
            [episode.id], 0.9, store.clock.now(), store.cfg)

    stale = store.get(insight.id)
    assert stale.invalid_at is not None, "the derived claim is still asserted"
    assert stale.meta["stale_reason"] == "premise_superseded"
    assert tms.audit(store) == []


def test_a_conclusion_with_several_premises_survives_losing_one(store):
    from you2.consolidation.supersede import resolve

    ep = store.add(Neuron(kind=Kind.EPISODE, content="context"))
    facts = [resolve(store, None, f"user/{k}", f"claim {k}", [ep.id], 0.9,
                     store.clock.now(), store.cfg).fact
             for k in "abcd"]
    insight = store.add(Neuron(kind=Kind.INSIGHT, content="a pattern across four",
                               confidence=0.8,
                               meta={"cites": [f.id for f in facts],
                                     "type": "recurrence"}))
    for f in facts:
        store.connect(insight.id, f.id, Edge.SUPPORTS, 0.8)

    store.clock.advance(days=60)
    resolve(store, None, "user/a", "claim a, revised", [ep.id], 0.9,
            store.clock.now(), store.cfg)

    survivor = store.get(insight.id)
    assert survivor.is_live, "losing one of four premises should weaken, not kill"
    assert survivor.confidence < 0.8


def test_deleting_a_memory_reaches_its_conclusions(store):
    ep = store.add(Neuron(kind=Kind.EPISODE, content="I hate cilantro"))
    fact = store.add(Neuron(kind=Kind.FACT, key="user/cilantro",
                            content="dislikes cilantro", meta={"cites": [ep.id]}))
    store.connect(fact.id, ep.id, Edge.SUPPORTS, 0.9)
    insight = store.add(Neuron(kind=Kind.INSIGHT, content="avoid coriander dishes",
                               meta={"cites": [fact.id], "type": "bridge"}))
    store.connect(insight.id, fact.id, Edge.SUPPORTS, 0.9)

    store.entomb(ep.id)
    assert store.get(fact.id).tier.value == "tomb"
    assert store.get(insight.id).invalid_at is not None
    assert tms.audit(store) == []


# ----------------------------------------------------------- parsimony

def test_world_knowledge_is_refused(store):
    cfg = store.cfg
    assert not parsimony.structural(store, "penicillin/class",
                                    "penicillin is an antibiotic", cfg).keep
    assert not parsimony.structural(store, "people/sleep",
                                    "all people need sleep", cfg).keep


def test_personal_claims_are_kept(store):
    cfg = store.cfg
    assert parsimony.structural(store, "user/allergy",
                                "the user is allergic to penicillin", cfg).keep
    store.add(Neuron(kind=Kind.ENTITY, content="Kestrel"), aliases=["kestrel"])
    assert parsimony.structural(store, "kestrel/state",
                                "Kestrel is running late", cfg).keep


def test_ambiguous_claims_are_kept_not_dropped(store):
    """Dropping a real personal fact is a much worse error than storing a
    generic one, and the asymmetry should be visible."""
    verdict = parsimony.worth_storing(store, None, "boiler/state",
                                      "the boiler is due a service", store.cfg)
    assert verdict.keep


def test_parsimony_audit_is_non_destructive(store):
    store.add(Neuron(kind=Kind.FACT, key="penicillin/class",
                     content="penicillin is an antibiotic"))
    flagged = parsimony.audit(store, store.cfg)
    assert flagged
    assert store.count(Kind.FACT) == 1, "audit must not delete anything"


def test_a_broken_chain_retires_even_though_most_premises_survive(store):
    """A rule's premises are conjunctive; evidence is redundant. The
    survival heuristic did not know the difference.

    "The test suite is part of the Kestrel contract" is derived by chaining
    through the Kestrel migration. Kill the middle link and the conclusion
    rests on nothing — but two of its three premises are still perfectly
    alive, so the heuristic kept it, halved its confidence, and left a
    confident well-cited claim supported by a chain with a hole in it.
    `audit()` reported the store clean, because it only asked whether *all*
    premises were dead.

    That is the exact failure this module exists to prevent, produced by the
    module itself.
    """
    from you2.activation import recall
    from you2.inference import compile as compilation
    from you2.inference import engine, tms

    now = store.clock.now()
    a = store.add(Neuron(kind=Kind.ENTITY, content="the test suite"))
    b = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel migration"))
    c = store.add(Neuron(kind=Kind.ENTITY, content="the Kestrel contract"))
    store.connect(a.id, b.id, Edge.PART_OF, 0.9, at=now)
    store.connect(b.id, c.id, Edge.PART_OF, 0.9, at=now)

    at = now
    for i in range(1, store.cfg.inference_compile_threshold + 1):
        at = now + i * DAY
        compilation.sweep(store, engine.cheap(
            store, recall(store, "the test suite", now=at), store.cfg, at),
            store.cfg, at)

    derived = [n for n in store.by_kind(Kind.FACT, limit=9, live_only=False)
               if n.meta.get("derived")]
    assert len(derived) == 1, "the chain should compile exactly once"

    b.invalid_at = at
    b.superseded_by = a.id
    store.save(b)
    tms.invalidate(store, b.id, at)

    assert not store.get(derived[0].id).is_live, "the chain still has a hole"
    assert tms.audit(store) == []
    assert derived[0].id in [n.id for n in tms.stale(store)]


def test_evidence_still_survives_losing_one_of_several(store):
    """The other half: an extracted fact cites episodes, and episodes are
    evidence. Losing one of four is not grounds for retiring the claim."""
    from you2.inference import tms

    now = store.clock.now()
    eps = [store.add(Neuron(kind=Kind.EPISODE, content=f"said it again {i}",
                            created_at=now)) for i in range(4)]
    fact = store.add(Neuron(kind=Kind.FACT, key="user/pref_milk",
                            content="the user prefers oat milk", confidence=0.9,
                            meta={"cites": [e.id for e in eps]}))
    for e in eps:
        store.connect(fact.id, e.id, Edge.SUPPORTS, 0.85, at=now)

    eps[0].invalid_at = now
    store.save(eps[0])
    tms.invalidate(store, eps[0].id, now)

    survivor = store.get(fact.id)
    assert survivor.is_live
    assert survivor.confidence < 0.9, "weakened rather than retired"
