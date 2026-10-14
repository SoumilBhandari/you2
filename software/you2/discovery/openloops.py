"""Open loops: the most underrated thing in the connections layer.

"Three weeks ago you said you'd look into the Anderson contract; it hasn't
come up since." No phone does that, it is trivially buildable, and it is the
one category where a false positive costs almost nothing -- the worst case is
being reminded of something you already did.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store

#: Grace period past the expected-by date before it counts as stale. Nobody
#: wants to be chased at 09:00 on the morning something was due.
GRACE_DAYS = 2.0

#: A loop mentioned again recently is not forgotten, it is in progress.
QUIET_DAYS = 5.0


@dataclass(slots=True)
class Stale:
    loop: Neuron
    overdue_days: float
    quiet_days: float

    @property
    def text(self) -> str:
        weeks = self.quiet_days / 7.0
        when = (f"{int(self.quiet_days)} days ago" if weeks < 2
                else f"{int(weeks)} weeks ago")
        return (f"{when} you said \"{self.loop.content.strip()}\" — "
                f"it hasn't come up since.")

    def __repr__(self) -> str:
        return f"Stale(#{self.loop.id}, {self.overdue_days:.0f}d overdue)"


def stale(store: Store, cfg: BrainConfig, now: float,
          limit: int = 10) -> list[Stale]:
    out: list[Stale] = []
    for loop in store.by_kind(Kind.LOOP, limit=500, order="created_at ASC"):
        if loop.invalid_at is not None:
            continue
        expected = loop.meta.get("expected_by")
        if not isinstance(expected, (int, float)):
            expected = loop.created_at + cfg.openloop_stale_days * DAY
        overdue = (now - expected) / DAY
        if overdue < GRACE_DAYS:
            continue
        quiet = (now - loop.last_access) / DAY
        if quiet < QUIET_DAYS:
            continue
        if loop.meta.get("nudged_at") and \
                (now - float(loop.meta["nudged_at"])) / DAY < 14.0:
            # Nudged recently. Asking again in three days is nagging, and
            # nagging is how the whole feature gets muted.
            continue
        out.append(Stale(loop, overdue, quiet))
    out.sort(key=lambda s: -s.overdue_days)
    return out[:limit]


def nudged(store: Store, loop: Neuron, now: float) -> None:
    loop.meta["nudged_at"] = now
    loop.meta["nudges"] = int(loop.meta.get("nudges", 0)) + 1
    # Three nudges and no closure means it is not going to happen. Let it
    # expire rather than nagging forever.
    if loop.meta["nudges"] >= 3:
        loop.invalid_at = now
        loop.meta["closed_reason"] = "abandoned"
    store.save(loop)
