"""Recurrence: patterns worth telling someone about.

Not "you mentioned X a lot" -- that is a word count, and it is the shape of
every worthless "insight" feature ever shipped. The useful form is
**conditional**: an outcome that co-occurs with a context far more than its
base rate.

    lift = P(drained | tuesday_meeting) / P(drained)

Three gates, and the third is the one that matters:

    lift >= 2.0     the association is real, not just frequent
    n >= 4          it has happened enough times to be a pattern
    span >= 21d     it is not one bad week

Without the span requirement you notice a pattern on Wednesday, tell the
person about it, and it turns out they had flu. One of those and they stop
reading these permanently -- an unrecoverable failure, structurally identical
to a false-positive health alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import DAY, part_of_day, weekday
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron, Tier
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: How negative an episode's valence must be to count as a bad outcome.
NEGATIVE = -0.25
POSITIVE = 0.30
HIGH_AROUSAL = 0.68


@dataclass(slots=True)
class Pattern:
    context: str
    outcome: str
    lift: float
    n: int
    span_days: float
    base_rate: float
    conditional: float
    cites: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return (f"{self.outcome.replace('_', ' ')} "
                f"{self.n} times out of {self.n if self.base_rate == 0 else ''}"
                f" when {self.context.replace('_', ' ')}").replace("  ", " ")

    def as_dict(self) -> dict[str, float | int | str]:
        return {"context": self.context, "outcome": self.outcome,
                "lift": round(self.lift, 2), "n": self.n,
                "span_days": round(self.span_days, 1),
                "base_rate": round(self.base_rate, 3),
                "conditional": round(self.conditional, 3)}

    def __repr__(self) -> str:
        return (f"Pattern({self.outcome} | {self.context}, "
                f"lift {self.lift:.1f}, n={self.n})")


def _contexts(store: Store, ep: Neuron) -> list[str]:
    out = [f"it_is_{weekday(ep.created_at)}", f"in_the_{part_of_day(ep.created_at)}"]
    for other, weight, code, _back in store.graph.neighbours(ep.id):
        if weight <= 0.0 or code != list(Edge).index(Edge.ABOUT):
            continue
        soma = store.soma.get(other)
        if soma is not None and soma.kind is Kind.ENTITY:
            entity = store.get(other)
            if entity is not None:
                out.append(f"talking_about_{entity.content}")
    return out


def _outcomes(ep: Neuron) -> list[str]:
    out: list[str] = []
    if ep.valence <= NEGATIVE:
        out.append("you_sound_flat")
    if ep.valence >= POSITIVE:
        out.append("you_sound_good")
    if ep.arousal >= HIGH_AROUSAL:
        out.append("you_sound_wound_up")
    if ep.surprise >= 0.75:
        out.append("something_unexpected_comes_up")
    return out


def detect(store: Store, cfg: BrainConfig, now: float, *,
           window_days: float = 180.0) -> list[Pattern]:
    episodes = [n for n in store.by_kind(
        Kind.EPISODE, limit=20000, tiers=(Tier.HOT, Tier.WARM))
        if (now - n.created_at) / DAY <= window_days]
    if len(episodes) < cfg.recurrence_min_count * 3:
        return []

    total = len(episodes)
    ctx_count: dict[str, int] = {}
    out_count: dict[str, int] = {}
    joint: dict[tuple[str, str], list[Neuron]] = {}

    for ep in episodes:
        contexts = _contexts(store, ep)
        outcomes = _outcomes(ep)
        for c in set(contexts):
            ctx_count[c] = ctx_count.get(c, 0) + 1
        for o in set(outcomes):
            out_count[o] = out_count.get(o, 0) + 1
        for c in set(contexts):
            for o in set(outcomes):
                joint.setdefault((c, o), []).append(ep)

    found: list[Pattern] = []
    for (context, outcome), members in joint.items():
        n = len(members)
        if n < cfg.recurrence_min_count:
            continue
        span = (max(m.created_at for m in members)
                - min(m.created_at for m in members)) / DAY
        if span < cfg.recurrence_min_span_days:
            continue
        base = out_count[outcome] / total
        conditional = n / ctx_count[context]
        if base <= 0.0:
            continue
        lift = conditional / base
        if lift < cfg.recurrence_min_lift:
            continue
        found.append(Pattern(
            context=context, outcome=outcome, lift=lift, n=n, span_days=span,
            base_rate=base, conditional=conditional,
            cites=[m.id for m in sorted(members, key=lambda m: -m.salience)[:6]],
        ))

    found.sort(key=lambda p: (-p.lift, -p.n))
    return found


def phrase(p: Pattern) -> str:
    """One sentence, spoken. Concrete about the count and the span, because
    "I noticed a pattern" with no numbers is exactly the kind of vague claim
    that makes a person stop trusting the feature."""
    subject = p.context.replace("_", " ")
    outcome = p.outcome.replace("_", " ")
    weeks = max(1, int(p.span_days / 7))
    return (f"That's {p.n} times in {weeks} weeks that {outcome} "
            f"when {subject}.")
