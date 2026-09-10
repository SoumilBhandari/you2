"""Truth maintenance: what happens to a conclusion when its premise dies.

Every derived thing in this system -- an insight, a compiled inference, a
schema -- is a frozen claim about a state of affairs. When a premise is
superseded, the derivative is silently, invisibly wrong, and it keeps being
retrieved because nothing marked it.

This is not a hypothetical. On 180 days of synthetic history, retrieval
precision was a clean 1.00 while *half* the probes still leaked a superseded
value into the memory block -- and every single leak was a stored insight
quoting a fact that had been replaced months earlier. The fact layer was
working perfectly. The derivatives were rotting.

A justification-based truth maintenance system fixes it in about eighty
lines: every derivative records its premises, and invalidating a premise
invalidates everything justified by it, transitively.

**Invalidate, do not delete.** A derivative whose premise changed may well be
re-derivable -- often to a better conclusion. It is marked stale and left for
the night to re-derive, which is also how a person updates a belief rather
than losing it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: Kinds that are conclusions rather than observations. An episode is never
#: invalidated by a premise change -- it happened.
DERIVATIVE_KINDS: frozenset[Kind] = frozenset(
    {Kind.INSIGHT, Kind.SCHEMA, Kind.FACT})

MAX_DEPTH = 6


@dataclass(slots=True)
class Invalidation:
    root: int
    invalidated: list[int] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Invalidation(#{self.root} -> {len(self.invalidated)} stale)"


def premises(n: Neuron) -> list[int]:
    """What this neuron was concluded from.

    `cites` is provenance for extracted facts; `derived_from` is the premise
    set for a compiled inference. Both are justifications.
    """
    out: list[int] = []
    for field_name in ("cites", "derived_from"):
        for raw in (n.meta.get(field_name) or []):
            try:
                out.append(int(raw))
            except (TypeError, ValueError):
                continue
    return list(dict.fromkeys(out))


def dependents(store: Store, neuron_id: int) -> list[Neuron]:
    """Everything justified by this neuron.

    Walks SUPPORTS edges, which are written whenever a derivative cites a
    premise -- so this is an index lookup rather than a scan of the store.
    """
    out: list[Neuron] = []
    supports = list(Edge).index(Edge.SUPPORTS)
    for other, weight, code, backward in store.graph.neighbours(neuron_id):
        # SUPPORTS runs derivative -> premise, so a dependent is reached
        # backwards from the premise.
        if weight <= 0.0 or code != supports or not backward:
            continue
        soma = store.soma.get(other)
        if soma is None or soma.kind not in DERIVATIVE_KINDS:
            continue
        n = store.get(other)
        if n is not None and n.is_live:
            out.append(n)
    return out


def invalidate(store: Store, root_id: int, now: float, *,
               reason: str = "premise_superseded",
               max_depth: int = MAX_DEPTH) -> Invalidation:
    """Mark everything justified by `root_id` as stale, transitively."""
    out = Invalidation(root=root_id)
    seen = {root_id}
    frontier: deque[tuple[int, int]] = deque([(root_id, 0)])

    while frontier:
        node_id, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for dependent in dependents(store, node_id):
            if dependent.id in seen:
                continue
            seen.add(dependent.id)

            all_premises = premises(dependent)
            live_premises = [p for p in all_premises
                             if p != node_id and _is_live(store, p)]
            # A conclusion resting on many premises survives losing one, with
            # reduced confidence. One resting on a handful does not: an
            # insight built from two facts, one of which was just replaced, is
            # probably about the replaced one.
            survives = (len(live_premises) >= 2
                        and len(live_premises) >= 0.5 * max(1, len(all_premises)))
            if survives:
                dependent.confidence *= 0.7
                dependent.meta["weakened_by"] = node_id
                store.save(dependent)
                continue

            dependent.invalid_at = now
            dependent.meta["stale_reason"] = reason
            dependent.meta["stale_premise"] = node_id
            dependent.meta["needs_rederivation"] = True
            store.save(dependent)
            out.invalidated.append(dependent.id)
            out.by_kind[str(dependent.kind)] = \
                out.by_kind.get(str(dependent.kind), 0) + 1
            frontier.append((dependent.id, depth + 1))

    if out.invalidated:
        store.log("tms_invalidate", {"root": root_id, "reason": reason,
                                     "invalidated": out.invalidated,
                                     "by_kind": out.by_kind})
    return out


def _is_live(store: Store, neuron_id: int) -> bool:
    soma = store.soma.get(neuron_id)
    if soma is None:
        return False
    n = store.get(neuron_id)
    return n is not None and n.is_live


def stale(store: Store, limit: int = 50) -> list[Neuron]:
    """Derivatives waiting to be re-derived."""
    out: list[Neuron] = []
    for kind in DERIVATIVE_KINDS:
        for n in store.by_kind(kind, limit=2000, live_only=False):
            if n.meta.get("needs_rederivation"):
                out.append(n)
                if len(out) >= limit:
                    return out
    return out


def audit(store: Store) -> list[int]:
    """Live derivatives whose premises are all dead.

    Must always be empty. This is the invariant that stops the store filling
    with confident, well-cited, obsolete conclusions -- which is a worse
    failure than forgetting, because it is invisible.
    """
    bad: list[int] = []
    for kind in DERIVATIVE_KINDS:
        for n in store.by_kind(kind, limit=5000, live_only=True):
            justifications = premises(n)
            if not justifications:
                continue
            if not any(_is_live(store, p) for p in justifications):
                bad.append(n.id)
    return bad
