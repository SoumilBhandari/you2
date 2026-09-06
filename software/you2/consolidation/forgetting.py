"""Forgetting: loss of access, not loss of data.

docs/design.md says no framework has a principled decay model. This is one.

Four mechanisms, ranked by how safe they are, and only the last is
destructive:

    retrieval-strength decay   reversible by one access; removes nothing
    synaptic downscaling       partly reversible; removes weak edges only
    tier demotion              reversible; removes fast-path availability
    absorbed compaction        NOT reversible; removes verbatim text

**Absorption gating** is what makes the last one safe. An episode is only
compacted once every claim it carries provably exists elsewhere -- meaning a
fact neuron whose provenance cites this episode. The gist survives; the
verbatim text goes.

The rule is simple and testable: **nothing unabsorbed is ever deleted.** If
consolidation has not understood it yet, it stays.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..activation.baselevel import retrieval_strength
from ..clock import DAY
from ..config import BrainConfig
from ..substrate.neuron import ALWAYS_HOT, Kind, Neuron, Tier
from ..substrate.store import Store

#: Reads that found nothing durable before we accept there is nothing to find.
#: Without this, chatty-but-empty episodes never absorb and never compact, and
#: the store grows linearly in elapsed time -- which is the diagnostic for a
#: memory system with no consolidation.
EMPTY_READS = 3


@dataclass(slots=True)
class Forgetting:
    demoted_warm: int = 0
    demoted_cold: int = 0
    compacted: int = 0
    absorbed: int = 0
    protected: int = 0
    bytes_freed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"demoted_warm": self.demoted_warm, "demoted_cold": self.demoted_cold,
                "compacted": self.compacted, "absorbed": self.absorbed,
                "protected": self.protected, "bytes_freed": self.bytes_freed}


def protected(n: Neuron, now: float, cfg: BrainConfig) -> bool:
    """The small list that prevents the entire class of "it forgot my name".

    Pinned memories, open loops, and anything touched recently. One predicate,
    checked everywhere demotion or compaction happens.
    """
    if n.pinned:
        return True
    if n.kind is Kind.LOOP and n.invalid_at is None:
        return True
    if (now - n.last_access) < cfg.never_decay_days * DAY:
        return True
    return False


def mark_absorbed(store: Store, episode_ids: list[int], now: float, *,
                  reason: str = "extracted") -> int:
    n = 0
    for eid in episode_ids:
        ep = store.get(eid)
        if ep is None or ep.absorbed or ep.kind is not Kind.EPISODE:
            continue
        ep.absorbed = True
        ep.meta["absorbed_reason"] = reason
        ep.meta["absorbed_at"] = now
        store.save(ep)
        n += 1
    return n


def sweep_absorption(store: Store, now: float) -> int:
    """Mark episodes that repeated reads found nothing durable in.

    Three independent passes with no extracted claim is decent evidence there
    is no claim. Recorded with a distinct reason so the decision is auditable
    rather than indistinguishable from a real extraction.
    """
    cited: set[int] = set()
    for fact in store.by_kind(Kind.FACT, limit=100000, live_only=False):
        cited.update(fact.citations())

    n = 0
    for ep in store.by_kind(Kind.EPISODE, limit=100000,
                            tiers=(Tier.HOT, Tier.WARM)):
        if ep.absorbed:
            continue
        if ep.id in cited:
            ep.absorbed = True
            ep.meta["absorbed_reason"] = "extracted"
        elif ep.times_replayed >= EMPTY_READS:
            ep.absorbed = True
            ep.meta["absorbed_reason"] = "no_claims"
        else:
            continue
        ep.meta["absorbed_at"] = now
        store.save(ep)
        n += 1
    return n


def decay_and_tier(store: Store, cfg: BrainConfig, now: float) -> Forgetting:
    """Demote what has faded out of the fast path.

    Nothing is deleted here. Demotion only changes where a memory can be
    reached from, which is what forgetting actually is.
    """
    out = Forgetting()
    out.absorbed = sweep_absorption(store, now)

    for n in store.by_kind(Kind.EPISODE, limit=100000,
                           tiers=(Tier.HOT, Tier.WARM), live_only=False):
        if n.kind in ALWAYS_HOT:
            continue
        if protected(n, now, cfg):
            out.protected += 1
            continue

        age = (now - n.created_at) / DAY
        rs = retrieval_strength(store.soma[n.id], now, cfg) if n.id in store.soma else 0.0

        if n.tier is Tier.HOT and age > cfg.hot_tier_days and rs < 0.5:
            n.tier = Tier.WARM
            store.save(n)
            out.demoted_warm += 1
        elif n.tier is Tier.WARM and age > cfg.warm_tier_days and n.absorbed:
            n.tier = Tier.COLD
            store.save(n)
            out.demoted_cold += 1
    return out


def compact(store: Store, cfg: BrainConfig, now: float) -> Forgetting:
    """Drop verbatim text from cold, absorbed, old episodes.

    The only destructive operation in the system that is not user-initiated,
    and it is gated three ways: cold tier, absorbed, and older than a year.
    """
    out = Forgetting()
    for n in store.by_kind(Kind.EPISODE, limit=100000, tiers=(Tier.COLD,),
                           live_only=False):
        if not n.absorbed or not n.content:
            continue
        if protected(n, now, cfg):
            out.protected += 1
            continue
        if (now - n.created_at) / DAY < cfg.cold_compaction_days:
            continue
        if not n.gist:
            # No gist means nothing would survive. Refuse.
            continue
        out.bytes_freed += len(n.content)
        n.content = ""
        n.meta["compacted_at"] = now
        store.save(n)
        out.compacted += 1
    if out.compacted:
        store.log("compaction", out.as_dict())
    return out


def audit(store: Store) -> list[int]:
    """Every compacted episode that was not absorbed.

    Must always be empty. This is the invariant that makes aggressive
    compaction safe, and it is cheap enough to check on every night run.
    """
    bad: list[int] = []
    for n in store.by_kind(Kind.EPISODE, limit=100000, live_only=False,
                           tiers=(Tier.HOT, Tier.WARM, Tier.COLD)):
        if n.meta.get("compacted_at") and not n.absorbed:
            bad.append(n.id)
    return bad


def growth_curve(store: Store, cfg: BrainConfig) -> dict[str, float]:
    """Neurons per elapsed day, by kind.

    A memory system whose size is linear in elapsed time has no consolidation.
    The growth curve is the diagnostic, and it is worth logging nightly so the
    shape is visible long before retrieval quality moves.
    """
    from ..clock import DAY as _DAY
    rows = store.conn.execute(
        "SELECT MIN(created_at) a, MAX(created_at) b FROM neurons").fetchone()
    span = max(1.0, ((rows["b"] or 0) - (rows["a"] or 0)) / _DAY)
    out = {"days": round(span, 1)}
    for kind in (Kind.EPISODE, Kind.FACT, Kind.ENTITY):
        out[f"{kind}_per_day"] = round(store.count(kind) / span, 3)
    live = store.count(Kind.FACT, live_only=True)
    total = store.count(Kind.FACT)
    out["fact_supersession_ratio"] = round(1.0 - live / total, 3) if total else 0.0
    return out
