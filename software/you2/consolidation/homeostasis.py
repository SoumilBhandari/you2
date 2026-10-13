"""Synaptic downscaling. Forty lines, and the most valuable thing here.

Waking experience is net synaptic potentiation: everything attended to
strengthens something. Left alone that saturates -- runaway potentiation,
falling signal-to-noise, and learning stops working. The synaptic homeostasis
hypothesis says slow-wave sleep renormalises: synapses are downscaled
globally, weak ones fall below threshold and are lost, strong ones survive
proportionally. Signal-to-noise improves overnight.

That is precisely the failure docs/design.md identifies -- a memory system
that only appends gets worse with use -- and precisely the fix.

**Multiplicative is the essential detail.** Subtractive decay destroys
weak-but-real structure and leaves strong-but-noisy structure intact.
Multiplicative preserves relative structure *exactly* while pushing the
accumulated noise floor below threshold.

**Structural edges are exempt.** An `about` edge between an episode and the
person it is about is not a statistical association that should erode; it is
a fact about the record. Only learned associations decay.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import BrainConfig
from ..substrate.store import Store
from ..substrate.synapse import STRUCTURAL


@dataclass(slots=True)
class Homeostasis:
    edges_before: int = 0
    edges_after: int = 0
    pruned: int = 0
    mean_before: float = 0.0
    mean_after: float = 0.0
    saturated_before: int = 0
    saturated_after: int = 0
    mass_before: float = 0.0
    mass_after: float = 0.0

    @property
    def pruned_share(self) -> float:
        return self.pruned / self.edges_before if self.edges_before else 0.0

    @property
    def mass_retained(self) -> float:
        """Fraction of total learned synaptic weight that survived the night.

        NOTE: a single night's numbers say very little. Downscaling works by
        *compounding* against reinforcement -- an association that keeps being
        used gets pushed back up by Hebbian learning every time it wins the
        workspace, while one that does not decays multiplicatively until it
        falls through the floor. The effect is a divergence over weeks, not a
        contrast improvement in one pass.

        An earlier version of this class reported a "contrast gain" from the
        coefficient of variation and claimed it should exceed 1.0 per night.
        It does not, and it was measuring the wrong thing: pruning a mass of
        near-zero noise *reduces* variance relative to the mean. The real
        claim is about retrieval precision over time, and the eval harness
        measures it there.
        """
        return self.mass_after / self.mass_before if self.mass_before else 1.0

    def as_dict(self) -> dict[str, float | int]:
        return {"edges_before": self.edges_before, "edges_after": self.edges_after,
                "pruned": self.pruned, "mean_before": round(self.mean_before, 4),
                "mean_after": round(self.mean_after, 4),
                "saturated_before": self.saturated_before,
                "saturated_after": self.saturated_after,
                "pruned_share": round(self.pruned_share, 3),
                "mass_retained": round(self.mass_retained, 3)}


def _stats(store: Store) -> tuple[int, float, float, int, float]:
    weights = np.fromiter(
        (w for _s, _d, t, w in store.graph.all_edges()
         if w > 0.0 and t not in STRUCTURAL), dtype=np.float32)
    if weights.size == 0:
        return 0, 0.0, 0.0, 0, 0.0
    return (int(weights.size), float(weights.mean()), float(weights.max()),
            int((weights > 0.9).sum()), float(weights.sum()))


def downscale(store: Store, cfg: BrainConfig, *, persist: bool = True) -> Homeostasis:
    """One night of renormalisation."""
    n_before, mean_before, _max_before, sat_before, mass_before = _stats(store)

    store.graph.scale_all(cfg.downscale_rho, exempt=STRUCTURAL)
    pruned = store.graph.prune(cfg.prune_weight_min, exempt=STRUCTURAL)

    n_after, mean_after, _max_after, sat_after, mass_after = _stats(store)

    out = Homeostasis(
        edges_before=n_before, edges_after=n_after, pruned=pruned,
        mean_before=mean_before, mean_after=mean_after,
        saturated_before=sat_before, saturated_after=sat_after,
        mass_before=mass_before, mass_after=mass_after,
    )

    if persist:
        # The overlay folds into the CSR here, which is why it is empty every
        # morning: the pass that touches every edge is also the compaction.
        store.persist_graph()
        store.load()

    store.log("homeostasis", out.as_dict())
    return out
