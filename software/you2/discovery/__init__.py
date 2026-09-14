"""Discovery: contradiction, recurrence, open loops, bridging, and the gate
that decides whether any of them are worth saying out loud."""

from __future__ import annotations

from .contradiction import Conflict, pending as pending_conflicts
from .openloops import Stale, nudged, stale
from .recurrence import Pattern, detect as detect_recurrence
from .scoring import (
    PULL_ONLY, PUSHABLE, Score, deliver, may_push, pending, score, surface, verdict,
)

__all__ = [
    "Conflict", "PULL_ONLY", "PUSHABLE", "Pattern", "Score", "Stale",
    "deliver", "detect_recurrence", "may_push", "nudged",
    "pending", "pending_conflicts", "score", "stale",
    "surface", "verdict",
]
