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


def _derived_from(n: Neuron) -> set[int]:
    out: set[int] = set()
    for raw in (n.meta.get("derived_from") or []):
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
    return out


def _required(store: Store, n: Neuron, premise_id: int) -> bool:
    """Is this premise load-bearing, or one piece of evidence among several?

    `derived_from` is a rule's premise set and every member is necessary —
    a transitive chain is only as good as its weakest link, literally.
    `cites` is evidence for an extracted claim, and evidence is redundant by
    nature.

    One exception, and it is not a special case so much as the definition
    working correctly. A claim about a *change* — "was dairy milk, now oat
    milk" — takes both versions as premises, and the older one being dead is
    the whole content of the claim rather than a hole in it. So a premise
    superseded **by another premise of the same derivation** is not a broken
    link. Without this the stricter audit flags every arc claim the system
    produces, which is 228 nights out of a simulated year: an invariant that
    fires constantly teaches people to ignore it.
    """
    return premise_id in _derived_from(n) and not about_the_change(
        store, n, premise_id)


def about_the_change(store: Store, n: Neuron, premise_id: int) -> bool:
    """Is this derivative *about* the supersession that just happened?

    True when the premise was replaced by another premise of the same
    derivation — the signature of an arc claim, "was X, now Y". Those two
    facts are its subject, so the older one dying is what makes the claim
    true rather than what breaks it.
    """
    kin = _derived_from(n)
    if premise_id not in kin:
        return False
    premise = store.get(premise_id)
    return premise is not None and premise.superseded_by in kin


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

            if about_the_change(store, dependent, node_id):
                # "Was dairy milk, now oat milk." This derivative exists
                # *because* of the supersession, so it survives it untouched
                # — and it cannot be rescued by the redundancy rule below,
                # which needs two live premises and an arc claim only ever
                # has one.
                continue

            all_premises = premises(dependent)
            live_premises = [p for p in all_premises
                             if p != node_id and _is_live(store, p)]
            # A conclusion resting on many premises survives losing one, with
            # reduced confidence. One resting on a handful does not: an
            # insight built from two facts, one of which was just replaced, is
            # probably about the replaced one.
            #
            # But that reasoning only holds for *evidence*, where the premises
            # are partially redundant. A rule application is conjunctive: "the
            # test suite is part of the Kestrel contract" was derived by
            # chaining through the Kestrel migration, so killing the middle
            # link destroys the conclusion outright even though two of its
            # three premises are still perfectly alive. The heuristic said it
            # survived, and it was left live with 0.7 confidence — a confident,
            # well-cited claim resting on nothing, which is the exact failure
            # this module exists to prevent, produced by the module itself.
            #
            # The two cases are already distinguishable: `derived_from` is a
            # rule's premise set, `cites` is evidence.
            survives = (_required(store, dependent, node_id) is False
                        and len(live_premises) >= 2
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
    """Live derivatives that are no longer supported.

    Must always be empty. This is the invariant that stops the store filling
    with confident, well-cited, obsolete conclusions -- which is a worse
    failure than forgetting, because it is invisible.

    Two ways to fail it, and the second was invisible to this check for as
    long as it existed. A claim resting on *evidence* is unsupported when all
    of it is gone. A claim produced by a *rule* is unsupported the moment any
    single premise dies, because rule premises are conjunctive -- so an audit
    that only asked "are they all dead" reported a clean store while a
    compiled chain sat in it with its middle link removed.
    """
    bad: list[int] = []
    for kind in DERIVATIVE_KINDS:
        for n in store.by_kind(kind, limit=5000, live_only=True):
            required = [p for p in premises(n) if _required(store, n, p)]
            if required and not all(_is_live(store, p) for p in required):
                bad.append(n.id)
                continue
            justifications = premises(n)
            if not justifications:
                continue
            if not any(_is_live(store, p) for p in justifications):
                bad.append(n.id)
    return bad
