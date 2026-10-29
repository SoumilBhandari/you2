"""Stance: how they feel about each thing, and which way it is moving.

A fact store knows *that* someone is working on the Kestrel migration. A model
of a person knows they have been getting steadily more fed up with it since
March — and that is the difference between an assistant that recalls and one
that understands.

Every episode already carries a valence and a timestamp, and every episode is
already bound to the entities it mentions. So stance is a group-by that nobody
ran, and the trend is a regression over two numbers.

**The trend matters more than the level.** Most people are mildly negative
about most work; that carries no information. "More negative than they were
two months ago" carries a great deal, and it is the thing they will not have
noticed themselves.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge

MIN_MENTIONS = 5

#: Valence change per month that counts as a real drift rather than noise.
DRIFT = 0.18

#: How recently something must have come up to be "live".
LIVE_DAYS = 30.0


@dataclass(slots=True)
class Stance:
    entity_id: int
    name: str
    mentions: int = 0
    valence: float = 0.0
    arousal: float = 0.0
    trend: float = 0.0
    """Valence change per month. Negative means souring."""
    first_at: float = 0.0
    last_at: float = 0.0

    @property
    def reliable(self) -> bool:
        return self.mentions >= MIN_MENTIONS

    def live(self, now: float) -> bool:
        return (now - self.last_at) / DAY <= LIVE_DAYS

    @property
    def drifting(self) -> str | None:
        if not self.reliable or abs(self.trend) < DRIFT:
            return None
        return "souring" if self.trend < 0 else "warming"

    def describe(self, now: float) -> str:
        weeks = max(1, int((now - self.first_at) / (7 * DAY)))
        mood = ("negative" if self.valence < -0.15 else
                "positive" if self.valence > 0.15 else "neutral")
        drift = self.drifting
        tail = f", and {drift}" if drift else ""
        return (f"{self.name}: {mood} over {self.mentions} mentions "
                f"across {weeks} weeks{tail}")

    def as_dict(self) -> dict[str, Any]:
        return {"entity": self.entity_id, "name": self.name,
                "mentions": self.mentions, "valence": round(self.valence, 3),
                "arousal": round(self.arousal, 3), "trend": round(self.trend, 3),
                "drifting": self.drifting}


def _episodes_about(store: Store, entity_id: int) -> list[Neuron]:
    about = list(Edge).index(Edge.ABOUT)
    out: list[Neuron] = []
    for other, weight, code, backward in store.graph.neighbours(entity_id):
        if weight <= 0.0 or code != about or not backward:
            continue
        soma = store.soma.get(other)
        if soma is None or soma.kind is not Kind.EPISODE:
            continue
        n = store.get(other)
        if n is not None:
            out.append(n)
    return out


def _occurrences(ep: Neuron) -> list[tuple[float, float, float]]:
    """(when, valence, arousal), repeats expanded."""
    out = [(ep.created_at, ep.valence, ep.arousal)]
    for t in (ep.meta.get("repeats") or []):
        try:
            out.append((float(t), ep.valence, ep.arousal))
        except (TypeError, ValueError):
            continue
    return out


#: The trend compares this many days against the same span before it.
#: Bounded on purpose -- see `_slope`.
TREND_WINDOW = 60.0

#: Each half of that comparison needs at least this many mentions, or two
#: offhand remarks in a quiet fortnight read as a change of heart.
TREND_MIN = 3


def _slope(points: list[tuple[float, float]], now: float) -> float:
    """Change in valence per month, over a bounded recent window.

    Fitting one line over the *entire* history, which this did, makes the
    answer depend on how long the entity has been known rather than on what
    recently happened. Valence is bounded in [-1, 1], so a least-squares slope
    over a span of T days cannot exceed 60/T per month -- and the DRIFT gate
    is a fixed 0.18. Past ~333 days no history whatsoever can clear it, not
    even a perfect +1 to -1 ramp:

        span  90d   max |slope| 0.667   reachable
        span 333d   max |slope| 0.180   reachable, barely
        span 365d   max |slope| 0.164   UNREACHABLE
        span 730d   max |slope| 0.082   UNREACHABLE

    So the device's ability to notice you souring on something decayed as 1/T
    in how long it had known you, which is the exact inverse of the premise.

    Comparing two adjacent windows is what the module docstring already
    promises -- "more negative than they were two months ago" -- and it is
    scale-free in span, so DRIFT keeps one meaning at month two and at year
    three.
    """
    recent = [v for t, v in points if now - t <= TREND_WINDOW * DAY]
    before = [v for t, v in points
              if TREND_WINDOW * DAY < now - t <= 2 * TREND_WINDOW * DAY]
    if len(recent) < TREND_MIN or len(before) < TREND_MIN:
        return 0.0
    # Per month, so the constant keeps the units it had.
    return ((statistics.fmean(recent) - statistics.fmean(before))
            * 30.0 / TREND_WINDOW)


def build(store: Store, cfg: BrainConfig, now: float,
          limit: int = 60) -> dict[int, Stance]:
    out: dict[int, Stance] = {}
    for entity in store.by_kind(Kind.ENTITY, limit=limit,
                                order="access_count DESC"):
        events: list[tuple[float, float, float]] = []
        for ep in _episodes_about(store, entity.id):
            events.extend(_occurrences(ep))
        if len(events) < 2:
            continue
        events.sort()
        stance = Stance(entity_id=entity.id, name=entity.content,
                        mentions=len(events),
                        first_at=events[0][0], last_at=events[-1][0])
        valences = [v for _t, v, _a in events]
        stance.valence = statistics.fmean(valences)
        stance.arousal = statistics.fmean(a for _t, _v, a in events)
        stance.trend = _slope([(t, v) for t, v, _a in events], now)
        out[entity.id] = stance
    return out


def notable(stances: dict[int, Stance], now: float,
            limit: int = 4) -> list[Stance]:
    """The ones worth putting in front of a model.

    Live, well-evidenced, and either strongly felt or moving. A neutral,
    stable stance about something they mention constantly is the *least*
    informative thing in the set, and it would otherwise dominate by volume.
    """
    live = [s for s in stances.values() if s.reliable and s.live(now)]
    live.sort(key=lambda s: -(abs(s.valence) + 2.0 * abs(s.trend)))
    return live[:limit]


def brief(stances: dict[int, Stance], now: float) -> list[str]:
    out: list[str] = []
    for s in notable(stances, now):
        drift = s.drifting
        if drift:
            out.append(f"{drift} on {s.name}")
        elif abs(s.valence) > 0.2:
            out.append(f"{'positive' if s.valence > 0 else 'negative'} about {s.name}")
    return out
