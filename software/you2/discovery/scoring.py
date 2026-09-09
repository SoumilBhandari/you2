"""Insight scoring, and the push/pull rule.

Four axes, all required:

    non-obvious   graph distance >= 3 and low prior co-activation
    grounded      cites >= 2 real neurons above a confidence floor -- BINARY
    actionable    touches an open loop, an active project, or a decision
    novel         not a restatement of something already surfaced

**Grounding is binary and non-negotiable.** An insight that cannot cite two
real memories is a hallucination with a citation field, and shipping one of
those costs more trust than ten good insights buy.

**Push rarely, pull generously.** A system that volunteers insights becomes
noise fast: one irrelevant "I noticed something!" a day and the person stops
reading them permanently. Contradictions, recurrences and open loops may
push, because a false positive is cheap and a true positive is valuable.
Bridges may only answer a question, because a wrong analogy is embarrassing
in a way a wrong contradiction is not -- it makes the whole system feel
stupid, and that impression does not wash out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..clock import DAY, day_key
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: Which connection types are allowed to interrupt someone.
PUSHABLE = frozenset({"contradiction", "recurrence", "openloop"})
PULL_ONLY = frozenset({"bridge"})

CONFIDENCE_FLOOR = 0.35


@dataclass(slots=True)
class Score:
    nonobvious: float = 0.0
    grounded: float = 0.0
    actionable: float = 0.0
    novel: float = 0.0
    reason: str = ""

    def passes(self, cfg: BrainConfig) -> bool:
        if self.grounded < 1.0:
            return False
        return (self.nonobvious >= cfg.insight_min_nonobvious
                and self.actionable >= cfg.insight_min_actionable
                and self.novel >= cfg.insight_min_novel)

    def as_dict(self) -> dict[str, float]:
        return {"nonobvious": round(self.nonobvious, 3),
                "grounded": round(self.grounded, 3),
                "actionable": round(self.actionable, 3),
                "novel": round(self.novel, 3)}

    def __repr__(self) -> str:
        return (f"Score(no={self.nonobvious:.2f} gr={self.grounded:.0f} "
                f"ac={self.actionable:.2f} nv={self.novel:.2f})")


def score(store: Store, cfg: BrainConfig, now: float, *, text: str,
          cites: Sequence[int], kind: str,
          nonobvious: float | None = None) -> Score:
    """Score a candidate discovery.

    `nonobvious` may be supplied by the caller, and usually should be. The
    axis means "would they have worked this out themselves", and *how you
    measure that depends on the connection type*. Graph distance is the right
    measure for a bridge and the wrong one for everything else: a recurrence
    pattern cites episodes that are closely connected **by construction** --
    that is what makes it a pattern -- so scoring it on distance rejects every
    true positive. That bug rejected 100% of contradictions, recurrences and
    open loops on synthetic history while happily passing bridges.
    """
    out = Score()

    # ---- grounded (binary) -------------------------------------------------
    live = [store.get(c) for c in dict.fromkeys(cites)]
    live = [n for n in live if n is not None and n.confidence >= CONFIDENCE_FLOOR]
    if len(live) < cfg.insight_min_citations:
        out.reason = f"only {len(live)} usable citations"
        return out
    out.grounded = 1.0

    # ---- non-obvious -------------------------------------------------------
    if nonobvious is not None:
        out.nonobvious = max(0.0, min(1.0, nonobvious))
    else:
        # Default, and the right measure for a bridge: two memories already
        # three hops apart were not going to be recalled together, so saying
        # so is the insight. Two that sit next to each other were obvious.
        distances: list[int] = []
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                d = store.graph.hops(live[i].id, live[j].id, max_hops=6)
                distances.append(d if d >= 0 else 6)
        span = min(distances) if distances else 0
        out.nonobvious = min(1.0, span / 4.0)

        # A pair that has repeatedly co-activated is not a discovery.
        coactivation = max(
            (store.graph.weight(min(a.id, b.id), max(a.id, b.id), Edge.ASSOC)
             for a in live for b in live if a.id != b.id), default=0.0)
        out.nonobvious *= (1.0 - min(0.9, coactivation))

    # ---- actionable --------------------------------------------------------
    hits = 0.0
    for n in live:
        if n.kind is Kind.LOOP and n.invalid_at is None:
            hits += 1.0
        elif n.kind is Kind.ENTITY and n.meta.get("status") == "active":
            hits += 0.5
        elif n.kind is Kind.FACT and (now - n.last_access) < 30 * DAY:
            hits += 0.4
    # Contradictions are actionable by construction: there is a specific
    # question to ask and answering it repairs the store.
    if kind == "contradiction":
        hits = max(hits, 1.0)
    out.actionable = min(1.0, hits / 1.5)

    # ---- novel -------------------------------------------------------------
    prior = store.search(text, k=3, scope="hot", min_score=0.0)
    worst = 0.0
    for nid, sim in prior:
        soma = store.soma.get(nid)
        if soma is not None and soma.kind is Kind.INSIGHT:
            worst = max(worst, sim)
    out.novel = 1.0 if worst < 0.85 else max(0.0, 1.0 - worst)

    return out


def pushes_today(store: Store, now: float) -> int:
    today = day_key(now)
    return sum(1 for row in store.journal("surfaced", limit=60)
               if row.get("pushed") and day_key(row["ts"]) == today)


def may_push(store: Store, cfg: BrainConfig, now: float, kind: str) -> bool:
    if kind not in PUSHABLE:
        return False
    return pushes_today(store, now) < cfg.max_pushes_per_day


def surface(store: Store, cfg: BrainConfig, now: float, *, kind: str, text: str,
            cites: Sequence[int], scored: Score | None = None,
            push: bool | None = None,
            nonobvious: float | None = None) -> Neuron | None:
    """Record a discovery as an insight neuron, and log it for rating.

    Everything surfaced is logged with its four axis scores and a slot for the
    user's one-keypress verdict. After three months that is a precision figure
    *per connection type*, which tells you exactly which categories to cut.
    There is no benchmark for "does this feel like it knows me after two
    years", so build the instrument that produces one on the first day.
    """
    scored = scored or score(store, cfg, now, text=text, cites=cites, kind=kind,
                             nonobvious=nonobvious)
    if not scored.passes(cfg):
        store.log("insight_rejected", {"kind": kind, "text": text[:120],
                                       **scored.as_dict(),
                                       "reason": scored.reason})
        return None

    if push is None:
        push = may_push(store, cfg, now, kind)

    insight = store.add(Neuron(
        kind=Kind.INSIGHT, content=text, created_at=now, last_access=now,
        salience=0.5 + 0.3 * scored.actionable,
        confidence=min(1.0, 0.4 + 0.3 * scored.nonobvious + 0.3 * scored.actionable),
        storage_strength=1.2,
        meta={"cites": list(dict.fromkeys(cites)), "type": kind,
              "axes": scored.as_dict(), "pushed": bool(push)},
    ))
    for c in dict.fromkeys(cites):
        store.connect(insight.id, c, Edge.SUPPORTS, 0.8, at=now)

    rating_id = store.rate(insight.id, kind, scored.as_dict())
    insight.meta["rating_id"] = rating_id
    store.save(insight)
    store.log("surfaced", {"insight": insight.id, "kind": kind, "pushed": bool(push),
                           "text": text[:160], **scored.as_dict()})
    return insight


def pending(store: Store, limit: int = 10, *, pushed_only: bool = False) -> list[Neuron]:
    """Insights waiting to be told or asked about."""
    out: list[Neuron] = []
    for n in store.by_kind(Kind.INSIGHT, limit=200, order="created_at DESC"):
        if n.meta.get("delivered"):
            continue
        if pushed_only and not n.meta.get("pushed"):
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def deliver(store: Store, insight: Neuron, now: float) -> None:
    insight.meta["delivered"] = now
    store.save(insight)


def verdict(store: Store, insight_id: int, useful: bool) -> None:
    """The one keypress. This is the whole evaluation loop for the connections
    layer, and it only works if it is there from the first day."""
    n = store.get(insight_id)
    if n is None:
        return
    rating_id = n.meta.get("rating_id")
    if rating_id:
        store.set_verdict(int(rating_id), 1 if useful else -1)
    n.meta["verdict"] = 1 if useful else -1
    if useful:
        n.pinned = True
        n.storage_strength += 1.0
    else:
        # A rejected insight should not be reachable again, and its supports
        # should not keep pulling it back.
        n.invalid_at = store.clock.now()
    store.save(n)
