"""Spreading activation. The core loop of the entire system.

You do not scan your memory when someone says a sentence. A few things light
up, they light up what is attached to them, and it dies out after two or three
hops. The cost of remembering is proportional to how much lit up -- never to
how much you know.

This is the answer to "it shouldn't just read everything". Not a heuristic
bolted onto a scan: a retrieval mechanism whose cost is structurally
independent of corpus size. Embeddings are used exactly once, to pick a
handful of starting points. Everything after that is graph traversal with a
decay term and a firing threshold, and it terminates because activation runs
out.

Four things bound it:

  gamma        decay per hop. At 0.45, three hops leaves 9% of source
               activation, so nothing survives that far unless several paths
               converge on it -- which is exactly the convergent evidence
               worth having.
  theta        firing threshold. Below it a neuron does not fire and does not
               propagate.
  fan          the ACT-R fan effect. Hubs spread thin, so "work" and "coffee"
               cannot dominate every retrieval.
  budget       a hard cap on neurons visited. The guarantee.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from ..config import BrainConfig
from ..substrate.graph import Graph
from ..substrate.synapse import CODE_EDGES, CONDUCTION, Edge


@dataclass(slots=True)
class Cue:
    neuron_id: int
    weight: float
    source: str = "semantic"

    def __repr__(self) -> str:
        return f"Cue(#{self.neuron_id}, {self.weight:.2f}, {self.source})"


@dataclass(slots=True)
class Cascade:
    """What lit up, how much, and by what route.

    The route matters: `you2 why` prints it, and a memory system whose
    retrievals cannot be explained is one you cannot debug or trust.
    """
    activation: dict[int, float] = field(default_factory=dict)
    hops: dict[int, int] = field(default_factory=dict)
    via: dict[int, tuple[int, Edge]] = field(default_factory=dict)
    fired: list[int] = field(default_factory=list)
    seeds: dict[int, str] = field(default_factory=dict)
    visited: int = 0
    exhausted: bool = True
    """True if the cascade died out on its own rather than hitting the budget.
    Persistently False means the budget is too tight or gamma too generous."""

    def ranked(self, limit: int | None = None) -> list[tuple[int, float]]:
        items = sorted(self.activation.items(), key=lambda kv: -kv[1])
        return items[:limit] if limit else items

    def path_to(self, node: int, max_len: int = 8) -> list[tuple[int, Edge | None]]:
        """Walk back to the seed that lit this neuron."""
        out: list[tuple[int, Edge | None]] = []
        cur: int | None = node
        seen: set[int] = set()
        while cur is not None and cur not in seen and len(out) < max_len:
            seen.add(cur)
            step = self.via.get(cur)
            out.append((cur, step[1] if step else None))
            cur = step[0] if step else None
        return list(reversed(out))

    def __repr__(self) -> str:
        return (f"Cascade(lit={len(self.activation)}, fired={len(self.fired)}, "
                f"visited={self.visited}, exhausted={self.exhausted})")


def spread(graph: Graph, cues: Sequence[Cue], cfg: BrainConfig, *,
           availability: Callable[[int], float] | None = None,
           blocked: Iterable[int] | None = None) -> Cascade:
    """Bounded best-first spreading activation.

    `availability` is the base-level term (recency and frequency); returning
    1.0 for everything disables it, which is what the ablation harness does.
    """
    avail = availability or (lambda _nid: 1.0)
    block = set(blocked or ())
    gamma = cfg.spread_decay
    theta = cfg.fire_threshold
    edge_theta = cfg.edge_threshold
    k_fan = cfg.fan_dampening

    cascade = Cascade()
    heap: list[tuple[float, int]] = []

    for cue in cues:
        if cue.neuron_id in block or cue.weight <= 0.0:
            continue
        prior = cascade.activation.get(cue.neuron_id, 0.0)
        cascade.activation[cue.neuron_id] = prior + cue.weight
        cascade.hops[cue.neuron_id] = 0
        if cue.neuron_id not in cascade.seeds:
            cascade.seeds[cue.neuron_id] = cue.source
    for nid, a in cascade.activation.items():
        heapq.heappush(heap, (-a * avail(nid), nid))

    fired: set[int] = set()
    while heap:
        if cascade.visited >= cfg.visit_budget:
            cascade.exhausted = False
            break

        neg_priority, node = heapq.heappop(heap)
        if node in fired:
            continue
        priority = -neg_priority
        current = cascade.activation.get(node, 0.0) * avail(node)
        # A stale heap entry: this node was pushed again with more activation.
        if priority < current - 1e-9:
            continue
        # Best-first, so once the strongest remaining candidate is sub-threshold
        # everything else is too. Activation arriving after the cascade has
        # passed does not fire, which is also how a real network behaves --
        # temporal coincidence is part of the mechanism, not an approximation
        # we are apologising for.
        if priority < theta:
            break

        fired.add(node)
        cascade.fired.append(node)
        cascade.visited += 1

        hop = cascade.hops.get(node, 0)
        if hop >= cfg.max_hops:
            continue

        fan = graph.fan(node)
        damp = 1.0 / (1.0 + k_fan * math.log1p(fan)) if (k_fan > 0.0 and fan > 1) else 1.0
        # Propagate from the *gated* activation: a faded neuron that manages
        # to fire still fires weakly, and passes on correspondingly less.
        outgoing = current * damp * gamma

        for other, weight, code, backward in graph.neighbours(node):
            if weight <= 0.0 or other in fired or other in block:
                continue
            conduct = CONDUCTION[CODE_EDGES[code]]
            delta = outgoing * weight * (conduct.back if backward else conduct.forward)
            if delta < edge_theta:
                continue
            prior = cascade.activation.get(other, 0.0)
            cascade.activation[other] = prior + delta
            if prior == 0.0 or delta > prior:
                cascade.via[other] = (node, CODE_EDGES[code])
            h = hop + 1
            if h < cascade.hops.get(other, 1 << 30):
                cascade.hops[other] = h
            heapq.heappush(heap, (-(prior + delta) * avail(other), other))

    # The final score is activation gated by availability: a memory can be
    # strongly implied by context and still be too faded to reach.
    for nid in list(cascade.activation):
        cascade.activation[nid] *= avail(nid)
    return cascade
