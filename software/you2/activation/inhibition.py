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
    """Damp every candidate close to `winner`, in proportion to how much of
    its content the winner has already said. Mutates `scores`.

    The proportionality is the point, and it was missing. A flat `strength`
    treats a 0.80 neighbour and an exact restatement identically, so a
    duplicate kept 30% of its score, cleared the firing threshold, and took a
    second slot: at day 365 of the year-long eval the block contained the
    line "spoke to Tom Beckwith about the garden wall for a while" **twice**,
    which is the precise failure this module exists to prevent, printed
    verbatim.

    So the damping ramps from `strength` at the radius to *total* at
    similarity 1.0. Behaviour at the boundary is unchanged; what changes is
    that a memory carrying no new information can no longer buy a slot.

    Returns the (neuron_id, similarity) pairs that were suppressed, so the
    explanation trace can show what lost and to what.
    """
    others = [nid for nid in scores if nid != winner]
    # `strength = 0` means the mechanism is switched off, and it has to be
    # switched off completely: `eval --ablate` sets exactly this to ask what
    # inhibition is worth, and a ramp that still eliminates exact duplicates
    # at zero strength quietly answers a different question.
    if not others or strength <= 0.0:
        return []
    sims = similarity(winner, others)
    span = max(1e-6, 1.0 - radius)
    hit: list[tuple[int, float]] = []
    for nid, sim in zip(others, sims):
        s = float(sim)
        if s >= radius:
            ramp = min(1.0, (s - radius) / span)
            scores[nid] *= max(0.0, 1.0 - (strength + (1.0 - strength) * ramp))
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
    if not others or strength <= 0.0:
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
