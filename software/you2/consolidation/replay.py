"""Prioritised replay, in ripple batches.

Hippocampal replay is prioritised rather than uniform, and it is compressed
and clustered rather than sequential. Both facts turn out to matter
operationally.

**Prioritised** -- sample by salience, surprise and recency, divided by how
often this episode has already been replayed. The denominator is what stops
the same dramatic Tuesday being re-consolidated every night for a year.

**Clustered** -- group episodes that share entities and process each group as
one model call. Roughly 10x cheaper than per-episode calls, *and* the
extraction is better: a fact that spans several conversations is only visible
when they are read together. Cheaper and better is rare enough to be worth
noticing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron, Tier
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: Half-life of the recency term.
RECENCY_HALFLIFE = 14.0

#: Replays before an episode leaves the ordinary pool.
#:
#: Measured over a simulated year: the eligible pool is a median of **17**
#: episodes against a draw limit of 600, and the oldest episode still in it
#: is a median of **6.0 days** old. Two consequences the comments here used
#: to deny.
#:
#: The priority function is **inert**. It samples by salience, surprise and
#: recency over a pool that is always smaller than the limit, so every
#: eligible episode is drawn every night regardless of its weight. Priority
#: could bind on 0 of 350 nights.
#:
#: And nothing old is ever revisited. With nightly replay an episode exhausts
#: its budget in six nights and no path decrements it — `dream.py` decrements
#: only for *facts* — so "unless something reactivates it" was never true.
#: §8.1 cites Complementary Learning Systems, whose central mechanism is
#: *interleaving* old material with new, so the obvious reading is that the
#: six-night window is a gap. **It is not**, and the measurement is worth
#: keeping: giving 20% of each night to episodes past their budget took probe
#: precision 1.00 -> 0.75 and stale leakage 0.00 -> **1.00**.
#:
#: The reason is specific to a store that supersedes. Re-consolidating an old
#: episode re-extracts the claim it contained, and that claim has often
#: already been replaced — so interleaving does not reinforce old knowledge,
#: it *resurrects retired facts* and re-litigates settled ones. The hard
#: window is what stops the store arguing with itself about last March.
MAX_REPLAYS = 6


@dataclass(slots=True)
class Batch:
    episodes: list[Neuron]
    shared: set[int]
    """Entity ids the batch has in common. Empty for a time-only batch."""

    def __len__(self) -> int:
        return len(self.episodes)

    def __repr__(self) -> str:
        return f"Batch({len(self.episodes)} eps, {len(self.shared)} shared)"


def priority(n: Neuron, now: float, cfg: BrainConfig) -> float:
    age_days = max(0.0, (now - n.created_at) / DAY)
    recency = 0.5 ** (age_days / RECENCY_HALFLIFE)
    # Surprise floors at 0.2 rather than 0: an unsurprising episode can still
    # carry a durable fact, and multiplying by zero would hide it forever.
    surprise = 0.2 + 0.8 * n.surprise
    salience = max(0.05, n.salience)
    return (salience * surprise * (0.15 + 0.85 * recency)
            / (1.0 + n.times_replayed) ** cfg.replay_beta)



def _draw(pool: list[Neuron], k: int, now: float, cfg: BrainConfig,
          rng: np.random.Generator) -> list[Neuron]:
    """Weighted sample without replacement, or everything if it fits."""
    if not pool:
        return []
    k = min(k, len(pool))
    weights = np.array([priority(n, now, cfg) for n in pool], dtype=np.float64)
    total = weights.sum()
    if total <= 0.0:
        return pool[:k]
    picked = rng.choice(len(pool), size=k, replace=False, p=weights / total)
    return [pool[i] for i in picked]


def sample(store: Store, cfg: BrainConfig, now: float, *,
           limit: int | None = None, rng: np.random.Generator | None = None,
           since: float | None = None) -> list[Neuron]:
    """Draw episodes for tonight, weighted by priority.

    Sampling rather than taking the top-N: taking the top always replays the
    same shape of material, and the tail is where the unexpected consolidation
    comes from.
    """
    rng = rng or np.random.default_rng()
    limit = limit or cfg.replay_max_batches * cfg.replay_batch_max

    everything = [n for n in store.by_kind(Kind.EPISODE, limit=6000,
                                           tiers=(Tier.HOT, Tier.WARM))
                  if since is None or n.created_at >= since]
    pool = [n for n in everything if n.times_replayed < MAX_REPLAYS]
    if not pool:
        return []

    chosen = _draw(pool, limit, now, cfg, rng)
    # Chronological within the night: consolidating a Tuesday before the
    # Monday that set it up produces worse supersession decisions.
    chosen.sort(key=lambda n: n.created_at)
    return chosen


def entities_of(store: Store, episode_id: int) -> set[int]:
    out: set[int] = set()
    for other, weight, code, _back in store.graph.neighbours(episode_id):
        if weight > 0.0 and code == list(Edge).index(Edge.ABOUT):
            soma = store.soma.get(other)
            if soma is not None and soma.kind is Kind.ENTITY:
                out.add(other)
    return out


def ripple_batches(store: Store, episodes: list[Neuron],
                   cfg: BrainConfig) -> list[Batch]:
    """Group by shared entities, falling back to temporal adjacency.

    Greedy rather than a proper clustering: the batches only need to be
    coherent enough that reading them together helps, and an O(n^2) clustering
    over a night's episodes would cost more than the model calls it saves.
    """
    if not episodes:
        return []
    ents = {n.id: entities_of(store, n.id) for n in episodes}
    remaining = list(episodes)
    batches: list[Batch] = []

    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        shared = set(ents[seed.id])
        i = 0
        while i < len(remaining) and len(group) < cfg.replay_batch_max:
            candidate = remaining[i]
            overlap = ents[candidate.id] & shared
            adjacent = abs(candidate.created_at - group[-1].created_at) < 6 * 3600
            if overlap or (adjacent and len(group) < cfg.replay_batch_min):
                group.append(remaining.pop(i))
                shared |= ents[candidate.id]
            else:
                i += 1
        batches.append(Batch(group, shared))
        if len(batches) >= cfg.replay_max_batches:
            # Silent truncation would read as "we consolidated everything".
            dropped = len(remaining)
            if dropped:
                store.log("replay_truncated", {"dropped": dropped,
                                               "cap": cfg.replay_max_batches})
            break
    return batches


def mark_replayed(store: Store, episodes: list[Neuron], now: float) -> None:
    for n in episodes:
        n.times_replayed += 1
        n.meta["last_replay"] = now
        store.save(n)


def coverage(store: Store, cfg: BrainConfig, now: float) -> dict[str, float]:
    """How much of the store has been consolidated at all.

    A large unreplayed backlog means the nightly budget is too small for the
    volume of conversation, and the symptom of that -- facts arriving weeks
    late -- is very hard to diagnose from the outside.
    """
    eps = store.by_kind(Kind.EPISODE, limit=100000, tiers=(Tier.HOT, Tier.WARM))
    if not eps:
        return {"episodes": 0, "replayed": 0.0, "absorbed": 0.0, "backlog": 0}
    replayed = sum(1 for n in eps if n.times_replayed > 0)
    absorbed = sum(1 for n in eps if n.absorbed)
    return {
        "episodes": len(eps),
        "replayed": round(replayed / len(eps), 3),
        "absorbed": round(absorbed / len(eps), 3),
        "backlog": len(eps) - replayed,
    }
