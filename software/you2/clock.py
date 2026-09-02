"""Injectable time.

This is architecture, not a testing convenience. Every core mechanism in this
system -- power-law decay, nightly consolidation, tier demotion, multi-year
drift -- is a function of elapsed time. A system like that cannot be tested in
real time, and one that cannot be tested will not be correct.

So nothing calls time.time() directly. Everything takes a Clock. The eval
harness then runs two years of use in ninety seconds, which is the only way
the question that defines the product -- does retrieval quality hold at month
24? -- is ever answerable before month 24.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

DAY = 86400.0
HOUR = 3600.0
MINUTE = 60.0
WEEK = 7 * DAY
YEAR = 365.0 * DAY


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Unix seconds, as a float."""
        ...


class RealClock:
    """Wall clock. Used in production, never in tests."""

    __slots__ = ()

    def now(self) -> float:
        return time.time()

    def __repr__(self) -> str:
        return "RealClock()"


class SimClock:
    """A clock you drive by hand.

    Starts at a fixed, arbitrary-but-real instant so that timestamps in test
    output look like dates rather than 1970.
    """

    __slots__ = ("_t",)

    #  2026-01-01T00:00:00Z
    EPOCH = 1767225600.0

    def __init__(self, start: float | None = None) -> None:
        self._t = float(start) if start is not None else self.EPOCH

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float = 0.0, *, minutes: float = 0.0,
                hours: float = 0.0, days: float = 0.0) -> float:
        self._t += seconds + minutes * MINUTE + hours * HOUR + days * DAY
        return self._t

    def set(self, t: float) -> float:
        self._t = float(t)
        return self._t

    def set_utc(self, *args: int) -> float:
        """set_utc(2026, 3, 14, 9, 30) -- readable test setup."""
        self._t = datetime(*args, tzinfo=timezone.utc).timestamp()  # type: ignore[arg-type]
        return self._t

    def advance_to_hour(self, hour: int) -> float:
        """Jump forward to the next occurrence of a given UTC hour.

        The night scheduler is defined in local hours, so tests need to land
        on 03:00 without arithmetic.
        """
        dt = datetime.fromtimestamp(self._t, tz=timezone.utc)
        target = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= dt:
            target += timedelta(days=1)
        self._t = target.timestamp()
        return self._t

    def __repr__(self) -> str:
        return f"SimClock({iso(self._t)})"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%a %d %b %Y %H:%M")


def days_between(a: float, b: float) -> float:
    return (b - a) / DAY


def hour_of_day(t: float) -> int:
    return datetime.fromtimestamp(t, tz=timezone.utc).hour


def day_key(t: float) -> str:
    """Stable per-day bucket key, for recurrence detection."""
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def weekday(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%A").lower()


def part_of_day(t: float) -> str:
    h = hour_of_day(t)
    if h < 5:
        return "night"
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    if h < 22:
        return "evening"
    return "night"
