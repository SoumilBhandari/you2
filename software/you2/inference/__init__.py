"""Inference: concluding what was never said, without storing it all.

The weights are the cortex; the graph is the person. Inference is running one
over the other. Three files:

    engine.py     the rules, split by cost. Graph rules ride along with every
                  turn; model rules are a night job or an explicit question.
    compile.py    a derivation drawn three times becomes a stored fact. The
                  only thing here that grows with use rather than with time.
    tms.py        when a premise dies, everything concluded from it is marked
                  stale. Without this, compilation is a machine for
                  manufacturing confident obsolete claims.
    parsimony.py  never store what the model already knows.
"""

from __future__ import annotations

from ..activation.workspace import Workspace, estimate_tokens
from ..config import BrainConfig
from ..substrate.store import Store
from . import compile as compilation
from . import parsimony, tms
from .engine import CHEAP, EXPENSIVE, RELIABILITY, Inference, cheap, expensive
from .tms import invalidate

__all__ = [
    "CHEAP", "EXPENSIVE", "Inference", "RELIABILITY", "attach", "cheap",
    "compilation", "expensive", "invalidate", "parsimony", "tms",
]


def attach(store: Store, ws: Workspace, cfg: BrainConfig, now: float) -> Workspace:
    """Run the cheap rules over what just lit up and fit them in the block.

    Derived claims compete for the *same* token budget as things the person
    actually said, from a small sub-budget, and they lose ties. An inference
    is worth less than a statement, and the block should show that.
    """
    if not ws.items or cfg.inference_workspace_tokens <= 0:
        return ws
    drawn = cheap(store, ws, cfg, now)
    if not drawn:
        return ws

    budget = min(cfg.inference_workspace_tokens,
                 max(0, cfg.workspace_tokens - ws.tokens))
    used = 0
    kept: list[Inference] = []
    for inference in drawn:
        cost = estimate_tokens(inference.render()) + 3
        if used + cost > budget:
            continue
        kept.append(inference)
        used += cost
    ws.inferences = kept
    ws.tokens += used
    return ws
