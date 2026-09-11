"""Activation: base level, spreading, inhibition, workspace."""

from __future__ import annotations

from ..config import BrainConfig
from ..substrate.store import Store
from .baselevel import (
    availability, decay_rate, half_life_days, on_retrieval, retrieval_strength,
)
from .cues import CueSet, build as build_cues, find_mentions, parse_window
from .inhibition import suppress
from .spread import Cascade, Cue, spread
from .workspace import (
    Item, Workspace, availability_fn, broadcast, compete, estimate_tokens, render,
)

__all__ = [
    "Cascade", "Cue", "CueSet", "Item", "Workspace",
    "availability", "availability_fn", "broadcast", "build_cues", "compete",
    "decay_rate", "estimate_tokens", "find_mentions",
    "half_life_days", "on_retrieval", "parse_window", "recall", "render",
    "retrieval_strength", "spread", "suppress",
]


def recall(store: Store, text: str, cfg: BrainConfig | None = None, *,
           context=(), now: float | None = None, budget: int | None = None,
           learn: bool = False, infer: bool = False) -> Workspace:
    """Cue -> spread -> compete, in one call.

    The whole hot-path retrieval. `learn=False` by default because retrieval
    is also used for salience scoring and for the eval harness, and neither
    should leave Hebbian fingerprints on the graph.
    """
    cfg = cfg or store.cfg
    now = now if now is not None else store.clock.now()
    cues = build_cues(store, text, cfg, context=context, now=now)
    cascade = spread(store.graph, cues.cues, cfg,
                     availability=availability_fn(store, cfg, now))
    ws = compete(store, cascade, cfg, cues=cues, now=now, budget=budget)
    if infer:
        from ..inference import attach
        attach(store, ws, cfg, now)
    if learn:
        broadcast(store, ws, cfg, now=now)
    return ws
