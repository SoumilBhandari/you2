"""Base-level activation: recency, frequency, and the two-component model.

ACT-R gives base-level activation as B = ln(sum over past accesses of
t_k^-d), and the standard optimisation approximates that with just an access
count and an age. On top of that sits Bjork's New Theory of Disuse, which
splits memory strength in two:

  retrieval strength  -- how accessible this is *now*. Decays with time.
  storage strength    -- how deeply learned. Never decreases, and governs how
                         slowly retrieval strength decays.

The consequence that makes it worth having: storage strength grows most when
retrieval was *hard*. Recovering something you have not touched in eight
months makes it far more durable than re-reading something you saw yesterday.
That is a real effect, and it gives forgetting a principled shape -- the thing
docs/design.md says no framework has.

**A deliberate departure from ACT-R.** ACT-R composes base level and spreading
in the log domain. Log-domain quantities do not compose with a graph
traversal that accumulates contributions additively along paths, so here base
level is a multiplicative *availability* in (0, 1] applied to a linear
activation. It is monotone in the same things ACT-R's is monotone in, it is
bounded, and it is fast. It is not the same equation, and pretending otherwise
would be the kind of unchecked load-bearing claim the corrections log exists
to catch.
"""

from __future__ import annotations

import math

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.store import Soma

#: How much repeated access offsets decay. Sublinear -- the tenth retrieval is
#: worth much less than the second.
FREQUENCY_GAIN = 0.15

#: Availability floor for pinned memories. When someone says "remember this",
#: it is remembered, and no amount of elapsed time changes that.
PINNED_FLOOR = 0.80


def decay_rate(storage_strength: float, cfg: BrainConfig) -> float:
    """Effective decay exponent. Well-learned memories fade more slowly."""
    return cfg.base_decay / (1.0 + max(0.0, storage_strength))


def retrieval_strength(soma: Soma, now: float, cfg: BrainConfig) -> float:
    """Power-law forgetting: (1 + days since access) ** -d.

    Floored rather than allowed to reach zero, because forgetting here is
    loss of access, not loss of data -- an explicit deep-recall query can
    still reach a memory whose retrieval strength has bottomed out.
    """
    age_days = max(now - soma.last_access, 60.0) / DAY
    rs = (1.0 + age_days) ** -decay_rate(soma.storage_strength, cfg)
    return max(cfg.retrieval_floor, min(1.0, rs))


def availability(soma: Soma, now: float, cfg: BrainConfig) -> float:
    """How readily this neuron participates in the current cascade, in (0, 1].

    This is the base-level term. It gates both how far activation spreads
    through a neuron and whether it can win a place in the workspace.
    """
    rs = retrieval_strength(soma, now, cfg)
    boost = 1.0 + FREQUENCY_GAIN * math.log1p(max(0, soma.access_count))
    avail = min(1.0, rs * boost)
    if soma.pinned:
        avail = max(avail, PINNED_FLOOR)
    return avail


def on_retrieval(soma: Soma, now: float, cfg: BrainConfig) -> tuple[float, int, float]:
    """Apply a retrieval. Returns (storage_strength, access_count, last_access).

    Desirable difficulty: the gain is scaled by how inaccessible the memory
    was *before* this retrieval, so recovering a faded memory is worth much
    more than re-reading a fresh one.
    """
    rs_before = retrieval_strength(soma, now, cfg)
    difficulty = 1.0 - rs_before
    storage = soma.storage_strength + cfg.storage_gain * difficulty
    return storage, soma.access_count + 1, now


def half_life_days(storage_strength: float, cfg: BrainConfig) -> float:
    """When does retrieval strength reach 0.5? Diagnostic, for the CLI."""
    d = decay_rate(storage_strength, cfg)
    if d <= 0.0:
        return float("inf")
    return 2.0 ** (1.0 / d) - 1.0
