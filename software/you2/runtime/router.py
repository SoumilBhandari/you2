"""The intent router.

A small local classifier that answers timers, time, and memory lookups in
~200 ms and never touches the model at all. Two payoffs:

  * It covers a large slice of daily utterances at a fraction of the latency.
  * It keeps working when the network is down, so the offline story is
    "slightly less capable" rather than "wakes up, transcribes, and has
    nothing to say" — which on the API build is otherwise a brick.

Regexes, not a model. A learned classifier here would be a second thing to
train, a second thing to get wrong, and slower.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..activation.workspace import Workspace
from ..clock import HOUR, MINUTE, Clock, human
from ..config import BrainConfig
from typing import Sequence

from ..recall.answer import answer as answer_from_memory, answerable
from ..substrate.store import Store

_TIME = re.compile(r"\b(what(?:'s| is) the time|what time is it|"
                   r"what(?:'s| is) (today'?s )?(the )?date|what day is it)\b", re.I)

_SET_TIMER = re.compile(
    r"\b(?:set (?:a )?timer|remind me|timer)\s+(?:for\s+)?"
    r"(\d+)\s*(second|sec|minute|min|hour|hr)s?\b", re.I)

_LIST_TIMERS = re.compile(r"\b(what timers|any timers|timers)\b", re.I)
_CANCEL_TIMER = re.compile(r"\b(cancel|stop|clear)\s+(the\s+)?timers?\b", re.I)

_UNITS = {"second": 1.0, "sec": 1.0, "minute": MINUTE, "min": MINUTE,
          "hour": HOUR, "hr": HOUR}


@dataclass(slots=True)
class Timer:
    label: str
    fires_at: float


@dataclass(slots=True)
class Routed:
    handled: bool = False
    text: str = ""
    intent: str = ""
    cites: list[int] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Routed({self.intent!r}, handled={self.handled})"


class Router:
    """Stateful only in that it holds timers, which are not memories."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.timers: list[Timer] = []

    def route(self, store: Store, text: str, ws: Workspace | None,
              cfg: BrainConfig, now: float | None = None,
              recent: "Sequence[str]" = ()) -> Routed:
        now = now if now is not None else self.clock.now()

        if _TIME.search(text):
            return Routed(True, human(now) + ".", "time")

        m = _SET_TIMER.search(text)
        if m:
            seconds = int(m.group(1)) * _UNITS[m.group(2).lower()]
            label = f"{m.group(1)} {m.group(2)}{'s' if int(m.group(1)) != 1 else ''}"
            self.timers.append(Timer(label, now + seconds))
            return Routed(True, f"Timer set for {label}.", "timer_set")

        if _CANCEL_TIMER.search(text):
            n = len(self.timers)
            self.timers.clear()
            return Routed(True, "Cancelled." if n else "No timers running.",
                          "timer_cancel")

        if _LIST_TIMERS.search(text):
            if not self.timers:
                return Routed(True, "No timers running.", "timer_list")
            parts = [f"{t.label} in {int((t.fires_at - now) / 60)} minutes"
                     for t in self.timers]
            return Routed(True, "; ".join(parts) + ".", "timer_list")

        # Memory-answerable. The fastest path in the system, and the one that
        # keeps working with no network.
        if ws is not None and answerable(text):
            found = answer_from_memory(store, ws, text, cfg, now, recent)
            if found is not None:
                return Routed(True, found.text, f"memory:{found.kind}", found.cites)

        return Routed(False, "", "")


OFFLINE_FALLBACK = (
    "I can't reach the model right now, so I'm only good for what I "
    "already know about you.")
