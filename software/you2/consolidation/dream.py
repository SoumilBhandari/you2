"""REM: remote association, structure mapping, counterfactual probing.

Runs after at least one full NREM pass, on the freshly consolidated
structure. Consolidation has to happen before recombination has anything
worth recombining, which is why the night is cycles and not one flat pass.

**The inversion that makes this different from every "here's a connection!"
feature.** Embeddings measure surface similarity. Analogy is structural
correspondence between things whose surfaces *differ*. SME ignores surface
features precisely so it can find mappings between distant domains that share
representational structure -- so cosine distance cannot find analogy in
principle, not merely badly. That is why every bridging feature built on
nearest neighbours surfaces topical noise: it is measuring the one thing that
should be scored *negatively*.

So alignment here rewards matching relational shape and **penalises matching
content**.

Whether this finds anything real at personal scale is genuinely open.
Conversation-derived graphs are sparse and noisy, and it is possible a
personal graph never contains two subgraphs with alignable structure. That is
what the planted analogies in the eval harness are for, and if it does not
clear scoring it should be cut rather than defended.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..clock import DAY
from ..config import BrainConfig
from ..llm import prompts
from ..llm.client import LLMError
from ..substrate.neuron import Kind
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: Nodes considered as one end of a bridge. Weighted toward recent activation,
#: so the device dreams about what is currently on your mind rather than
#: uniformly about everything.
ANCHOR_POOL = 40

#: Neighbourhood radius for the structural comparison.
SHAPE_HOPS = 2


@dataclass(slots=True)
class Bridge:
    a: int
    b: int
    hops: int
    alignment: float
    relation_sim: float
    degree_sim: float
    content_sim: float
    text: str = ""
    useful: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"a": self.a, "b": self.b, "hops": self.hops,
                "alignment": round(self.alignment, 3),
                "relation_sim": round(self.relation_sim, 3),
                "degree_sim": round(self.degree_sim, 3),
                "content_sim": round(self.content_sim, 3),
                "useful": self.useful}

    def __repr__(self) -> str:
        return f"Bridge(#{self.a}~#{self.b}, {self.alignment:.2f}, {self.hops}h)"


def _cosine_counts(a: dict[int, int], b: dict[int, int]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    va = np.array([a.get(k, 0) for k in keys], dtype=np.float64)
    vb = np.array([b.get(k, 0) for k in keys], dtype=np.float64)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb / (na * nb))


def _degree_profile(store: Store, node: int) -> list[int]:
    """Sorted degree sequence of the local neighbourhood. A crude but genuinely
    content-free description of shape."""
    hood = store.graph.neighbourhood(node, hops=SHAPE_HOPS, limit=32)
    return sorted((store.graph.fan(n) for n in hood), reverse=True)[:8]


def _degree_similarity(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    n = max(len(a), len(b))
    pa = a + [0] * (n - len(a))
    pb = b + [0] * (n - len(b))
    num = sum(min(x, y) for x, y in zip(pa, pb))
    den = sum(max(x, y) for x, y in zip(pa, pb))
    return num / den if den else 0.0


def alignment(store: Store, a: int, b: int, hops: int | None = None) -> Bridge | None:
    """Structural alignment between two neighbourhoods.

    Cheap filters first -- relation-type multiset, degree sequence -- so that
    only survivors cost a model call.

    `hops` is passed in by the only caller that has already computed it. It
    is carried for reporting and does not enter the score, so recomputing it
    here was a second full breadth-first search per pair for a number nobody
    reads.
    """
    if hops is None:
        hops = store.graph.hops(a, b, max_hops=6)
    sig_a = store.graph.relation_signature(a)
    sig_b = store.graph.relation_signature(b)
    if not sig_a or not sig_b:
        return None

    relation_sim = _cosine_counts(sig_a, sig_b)
    degree_sim = _degree_similarity(_degree_profile(store, a), _degree_profile(store, b))
    content_sim = store.vectors.similarity(a, b)

    # Same shape, different content. The content term is subtracted, which is
    # the whole difference between this and a nearest-neighbour search.
    score = 0.45 * relation_sim + 0.30 * degree_sim + 0.25 * (1.0 - max(0.0, content_sim))
    return Bridge(a=a, b=b, hops=hops if hops >= 0 else 99, alignment=score,
                  relation_sim=relation_sim, degree_sim=degree_sim,
                  content_sim=content_sim)


def anchors(store: Store, cfg: BrainConfig, now: float,
            rng: np.random.Generator) -> list[int]:
    """One end of each candidate pair: what is currently on the person's mind."""
    pool: list[tuple[float, int]] = []
    for kind in (Kind.FACT, Kind.ENTITY, Kind.LOOP, Kind.SCHEMA):
        for n in store.by_kind(kind, limit=400, order="last_access DESC"):
            recency = 0.5 ** (max(0.0, now - n.last_access) / (30 * DAY))
            pool.append((recency * (1.0 + math.log1p(n.access_count)), n.id))
    if not pool:
        return []
    pool.sort(key=lambda t: -t[0])
    top = pool[: ANCHOR_POOL * 2]
    weights = np.array([w for w, _ in top], dtype=np.float64)
    total = weights.sum()
    if total <= 0:
        return [nid for _w, nid in top[:ANCHOR_POOL]]
    k = min(ANCHOR_POOL, len(top))
    picked = rng.choice(len(top), size=k, replace=False, p=weights / total)
    return [top[i][1] for i in picked]


def candidates(store: Store, cfg: BrainConfig, now: float,
               rng: np.random.Generator | None = None) -> list[Bridge]:
    """Sample far-apart pairs and score their structural alignment."""
    rng = rng or np.random.default_rng()
    ends = anchors(store, cfg, now, rng)
    if len(ends) < 2:
        return []

    universe = [n.id for kind in (Kind.FACT, Kind.ENTITY, Kind.SCHEMA)
                for n in store.by_kind(kind, limit=800)]
    if len(universe) < 4:
        return []

    out: list[Bridge] = []
    seen: set[tuple[int, int]] = set()
    budget = cfg.bridge_candidates_per_night
    attempts = 0
    while len(out) < budget and attempts < budget * 12:
        attempts += 1
        a = int(ends[rng.integers(0, len(ends))])
        b = int(universe[rng.integers(0, len(universe))])
        if a == b:
            continue
        pair = (min(a, b), max(a, b))
        if pair in seen:
            continue
        seen.add(pair)
        # Far apart is the point. A pair three hops or fewer apart is already
        # connected, and saying so is not an insight.
        hops = store.graph.hops(a, b, max_hops=cfg.bridge_min_hops)
        if 0 <= hops < cfg.bridge_min_hops:
            continue
        bridge = alignment(store, a, b, hops=hops)
        if bridge is not None and bridge.alignment >= cfg.bridge_min_alignment:
            out.append(bridge)
    out.sort(key=lambda br: -br.alignment)
    return out


def propose(store: Store, llm: Any | None, cfg: BrainConfig, now: float, *,
            limit: int = 3, rng: np.random.Generator | None = None) -> list[Bridge]:
    """Score candidates, then ask the model to state the surviving ones."""
    found = candidates(store, cfg, now, rng)
    stated: list[Bridge] = []
    for bridge in found[: limit * 3]:
        a, b = store.get(bridge.a), store.get(bridge.b)
        if a is None or b is None:
            continue
        if llm is None:
            bridge.text = f"{a.text()} — structurally like — {b.text()}"
            bridge.useful = False       # never assert an analogy without a model
        else:
            try:
                out = llm.structured(prompts.state_insight(
                    "bridge", [a, b], now,
                    hint=(f"These are {bridge.hops} hops apart in the memory "
                          f"graph and share relational structure "
                          f"(alignment {bridge.alignment:.2f}) but not content.")))
                bridge.useful = bool(out.get("useful"))
                bridge.text = str(out.get("text") or "")
            except (LLMError, TypeError, ValueError, KeyError):
                continue
        stated.append(bridge)
        if sum(1 for s in stated if s.useful) >= limit:
            break
    store.log("dream_bridges", {"candidates": len(found),
                                "stated": len(stated),
                                "useful": sum(1 for s in stated if s.useful)})
    return stated


def counterfactual(store: Store, llm: Any | None, cfg: BrainConfig, now: float,
                   *, n: int = 3,
                   rng: np.random.Generator | None = None) -> list[dict[str, Any]]:
    """Generate a plausible-but-false variant of a well-supported fact and
    check that retrieval rejects it.

    A schema-robustness test. Where retrieval *accepts* the false variant, the
    region is weakly grounded, and its replay priority goes up -- which is the
    closest thing here to actually dreaming, and it costs almost nothing.
    """
    if llm is None:
        return []
    rng = rng or np.random.default_rng()
    facts = [f for f in store.by_kind(Kind.FACT, limit=400, live_only=True)
             if f.confidence > 0.6]
    if not facts:
        return []
    picked = rng.choice(len(facts), size=min(n, len(facts)), replace=False)

    out: list[dict[str, Any]] = []
    for i in picked:
        fact = facts[int(i)]
        try:
            variant = str(llm.structured(prompts.dream_probe(fact))
                          .get("false_variant") or "")
        except (LLMError, TypeError, ValueError, KeyError):
            continue
        if not variant:
            continue
        hits = store.search(variant, k=3, scope="hot")
        top = hits[0] if hits else (0, 0.0)
        # The true fact should out-rank the fabrication. If it does not, the
        # region is not well grounded.
        robust = bool(hits) and top[0] == fact.id
        if not robust:
            fact.times_replayed = max(0, fact.times_replayed - 1)
            fact.meta["weak_grounding"] = now
            store.save(fact)
        out.append({"fact": fact.id, "variant": variant[:120], "robust": robust})
    store.log("dream_probe", {"probes": len(out),
                              "weak": sum(1 for o in out if not o["robust"])})
    return out


def link(store: Store, bridge: Bridge, insight_id: int, now: float) -> None:
    store.connect(bridge.a, bridge.b, Edge.ANALOGOUS,
                  min(0.9, bridge.alignment), at=now)
    store.connect(insight_id, bridge.a, Edge.SUPPORTS, 0.8, at=now)
    store.connect(insight_id, bridge.b, Edge.SUPPORTS, 0.8, at=now)
