"""Lateral inhibition: winners suppress their neighbours.

Without it the workspace fills with eight restatements of one memory, which
reads to the user as the device having exactly one thing to say about them.

Mechanically this is maximal marginal relevance wearing a lab coat, and the
framing earns its keep because it says *why* the suppression exists and where
the radius comes from: a winner has already broadcast its content, so a near
duplicate adds tokens and no information.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np


def suppress(scores: dict[int, float], winner: int,
             similarity: Callable[[int, Sequence[int]], np.ndarray],
             *, radius: float = 0.80, strength: float = 0.70,
             ) -> list[tuple[int, float]]:
    """Damp every candidate close to `winner`. Mutates `scores`.

    Returns the (neuron_id, similarity) pairs that were suppressed, so the
    explanation trace can show what lost and to what.
    """
    others = [nid for nid in scores if nid != winner]
    if not others:
        return []
    sims = similarity(winner, others)
    hit: list[tuple[int, float]] = []
    for nid, sim in zip(others, sims):
        s = float(sim)
        if s >= radius:
            scores[nid] *= (1.0 - strength)
            hit.append((nid, s))
    return hit


def graded_suppress(scores: dict[int, float], winner: int,
                    similarity: Callable[[int, Sequence[int]], np.ndarray],
                    *, radius: float = 0.80, strength: float = 0.70,
                    ) -> list[tuple[int, float]]:
    """As `suppress`, but the damping ramps in over the radius instead of
    switching at it.

    A hard threshold makes selection unstable near the boundary: a similarity
    of 0.799 survives untouched while 0.801 loses 70%, so a trivial rewording
    of the same memory flips which one appears. Available as an alternative
    policy; the default stays hard because it is easier to reason about when
    a block looks wrong.
    """
    others = [nid for nid in scores if nid != winner]
    if not others:
        return []
    sims = similarity(winner, others)
    hit: list[tuple[int, float]] = []
    span = max(1e-6, 1.0 - radius)
    for nid, sim in zip(others, sims):
        s = float(sim)
        if s >= radius:
            ramp = min(1.0, (s - radius) / span)
            scores[nid] *= (1.0 - strength * ramp)
            hit.append((nid, s))
    return hit
