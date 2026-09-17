"""The person: who they are, not just what they said.

Six components, all derived from what is already in the store and none of them
a setting anyone picked:

    identity    the durable self
    rhythms     the shape of a week
    stances     how they feel about each thing, and which way it is moving
    style       how they talk, and therefore how to talk back
    concerns    what is live and weighing on them
    arc         what has changed about them lately

Rebuilt nightly. Every claim carries its sample size, and thin evidence
produces an empty section rather than a hedged one — telling someone something
wrong about themselves is far worse than saying nothing, because it is the
moment they stop believing any of it.
"""

from __future__ import annotations

from . import rhythms, stance, style
from .model import Person, build, load_snapshot, save
from .rhythms import Rhythms
from .stance import Stance
from .style import Style

__all__ = ["Person", "Rhythms", "Stance", "Style", "build", "load_snapshot",
           "rhythms", "save", "stance", "style"]
