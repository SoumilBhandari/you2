"""Temporal rhythms: the shape of a week.

A memory of what someone said is not a model of them. This is the first of
three things that turn one into the other, and it is the cheapest: every
episode already carries a timestamp, a valence and an arousal, so the weekly
and daily shape of a person's mood and energy is *already in the store* and
nobody has ever looked.

What it yields is the kind of thing only something that has watched you for
months can say:

    "You're consistently flat on Tuesday evenings."
    "You do your thinking before ten."
    "You haven't had a quiet weekend since March."

**Every claim carries its sample size and its spread**, because the failure
mode here is confidently telling someone a fact about themselves derived from
four data points. A rhythm with n=4 is a coincidence; the code says so and
refuses to surface it.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from ..clock import DAY, part_of_day, weekday
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron, Tier
from ..substrate.store import Store

#: Below this an average is an anecdote. Stated in the code rather than left
#: to judgement, because the temptation to report a striking pattern from five
#: observations is exactly how this feature loses trust.
MIN_SAMPLES = 8

#: How far a bucket must sit from the person's own baseline to be worth
#: mentioning, in standard deviations.
#: How many standard errors a bucket must sit from the person's own baseline
#: before it is worth mentioning. A significance test, not an effect size:
#: the denominator shrinks as evidence accumulates, so this tightens the gate
#: when n is small instead of letting eight observations speak as loudly as
#: eight hundred.
NOTABLE_T = 2.5

#: ...and a floor on the raw difference, so that a hair-thin gap does not
#: become "notable" merely because there are thousands of observations.
NOTABLE_MIN = 0.05


@dataclass(slots=True)
class Bucket:
    label: str
    n: int = 0
    valence: float = 0.0
    arousal: float = 0.0
    volume: float = 0.0
    """Episodes per occurrence of this bucket -- how much they talk then."""

    @property
    def reliable(self) -> bool:
        return self.n >= MIN_SAMPLES

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "n": self.n,
                "valence": round(self.valence, 3),
                "arousal": round(self.arousal, 3),
                "volume": round(self.volume, 2)}


@dataclass
class Rhythms:
    by_weekday: dict[str, Bucket] = field(default_factory=dict)
    by_part: dict[str, Bucket] = field(default_factory=dict)
    baseline_valence: float = 0.0
    spread_valence: float = 0.0
    total: int = 0
    span_days: float = 0.0

    @property
    def reliable(self) -> bool:
        """Enough history for any of this to mean anything.

        Three weeks is the floor: below that, a weekday bucket cannot have
        more than a couple of observations and "you're flat on Tuesdays" is a
        statement about one Tuesday.
        """
        return self.total >= MIN_SAMPLES * 3 and self.span_days >= 21.0

    def notable(self, limit: int = 3) -> list[tuple[str, str, float]]:
        """(bucket, what, t) for buckets that genuinely stand out.

        Gated on how *sure* the difference is, not on how big it is. This
        divided a bucket mean's deviation by `spread_valence` -- the
        per-episode population standard deviation, which does not shrink as
        evidence accumulates -- and compared it to a fixed constant. So the
        test had no term for n beyond MIN_SAMPLES, which made it maximally
        permissive exactly where the evidence is thinnest and silent where it
        is strongest: a planted Tuesday rhythm at 11 standard errors after a
        year scored 0.75 and stayed quiet, while a bucket sitting on the n=8
        floor with three bad days scored 1.33 and fired.

        The standard error of a bucket mean is sd/sqrt(n), so dividing by
        that makes the gate tighten as n falls, which is what MIN_SAMPLES is
        trying and failing to do on its own. The second test keeps a
        hair-thin difference quiet once n is enormous.
        """
        if not self.reliable or self.spread_valence < 1e-6:
            return []
        out: list[tuple[str, str, float]] = []
        for bucket in list(self.by_weekday.values()) + list(self.by_part.values()):
            if not bucket.reliable:
                continue
            deviation = bucket.valence - self.baseline_valence
            stderr = self.spread_valence / math.sqrt(bucket.n)
            t = deviation / stderr if stderr > 0 else 0.0
            if abs(t) >= NOTABLE_T and abs(deviation) >= NOTABLE_MIN:
                out.append((bucket.label, "flat" if t < 0 else "good", t))
        out.sort(key=lambda x: -abs(x[2]))
        return out[:limit]

    def best_hours(self) -> str | None:
        """When they are at their best, or nothing.

        A bare argmax over three buckets always has a winner, and on a real
        year of data afternoon beat morning by 0.0035 valence against a
        standard error of 0.012 on either mean -- 0.29 standard errors, which
        a bootstrap flips about two times in five. Since this is the only
        rhythm line that reaches the prompt, that is the device asserting a
        fact about someone's day, every turn, from noise.

        So the winner has to be separated from the runner-up by more than the
        two means' combined error. Saying nothing is the module's default
        everywhere else and it is the right answer here too.
        """
        parts = [b for b in self.by_part.values() if b.reliable]
        if len(parts) < 2:
            return None
        ranked = sorted(parts, key=lambda b: b.valence - 0.3 * abs(b.arousal - 0.5),
                        reverse=True)
        best, second = ranked[0], ranked[1]
        if best.n < MIN_SAMPLES or self.spread_valence < 1e-6:
            return None
        margin = best.valence - second.valence
        combined = self.spread_valence * math.sqrt(1.0 / best.n + 1.0 / second.n)
        if margin < NOTABLE_T * combined:
            return None
        return best.label

    def brief(self) -> list[str]:
        """Lines for the persona block. Empty when the evidence is thin, which
        is most of the first month and should be."""
        if not self.reliable:
            return []
        out: list[str] = []
        for label, what, z in self.notable():
            out.append(f"usually {what} {label} (n={self.by_weekday.get(label, self.by_part.get(label, Bucket(label))).n})")
        best = self.best_hours()
        if best:
            out.append(f"at their best in the {best}")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total, "span_days": round(self.span_days, 1),
            "reliable": self.reliable,
            "baseline_valence": round(self.baseline_valence, 3),
            "by_weekday": {k: v.as_dict() for k, v in self.by_weekday.items()},
            "by_part": {k: v.as_dict() for k, v in self.by_part.items()},
            "notable": [(l, w, round(z, 2)) for l, w, z in self.notable()],
        }


def _occurrences(ep: Neuron) -> list[float]:
    """Every time this happened, repeats included -- otherwise the recurring
    Tuesday complaint counts once and the rhythm it defines is invisible."""
    times = [ep.created_at]
    for t in (ep.meta.get("repeats") or []):
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    return times


def build(store: Store, cfg: BrainConfig, now: float,
          window_days: float = 365.0) -> Rhythms:
    out = Rhythms()
    events: list[tuple[float, Neuron]] = []
    for ep in store.by_kind(Kind.EPISODE, limit=50000,
                            tiers=(Tier.HOT, Tier.WARM), live_only=False):
        for when in _occurrences(ep):
            if (now - when) / DAY <= window_days:
                events.append((when, ep))
    if not events:
        return out

    out.total = len(events)
    out.span_days = (max(t for t, _ in events) - min(t for t, _ in events)) / DAY

    valences = [ep.valence for _t, ep in events]
    out.baseline_valence = statistics.fmean(valences)
    out.spread_valence = (statistics.pstdev(valences) if len(valences) > 1 else 0.0)

    def accumulate(target: dict[str, Bucket], key: str, ep: Neuron) -> None:
        b = target.setdefault(key, Bucket(key))
        b.n += 1
        # Running mean, so one very bad Tuesday cannot define the weekday.
        b.valence += (ep.valence - b.valence) / b.n
        b.arousal += (ep.arousal - b.arousal) / b.n

    for when, ep in events:
        accumulate(out.by_weekday, weekday(when), ep)
        accumulate(out.by_part, part_of_day(when), ep)

    weeks = max(1.0, out.span_days / 7.0)
    for b in out.by_weekday.values():
        b.volume = b.n / weeks
    for b in out.by_part.values():
        b.volume = b.n / max(1.0, out.span_days)
    return out
