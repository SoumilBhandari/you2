"""Contradictions: free, and worth pushing.

Falls out of consolidation -- two facts sharing a key with incompatible
values, neither clearly superseding the other, get a `contradicts` edge and a
question. This module is the read side: what is outstanding, and how to phrase
it.

May push. A false positive costs one unnecessary question; a true positive
repairs a fact that would otherwise poison retrieval indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..activation.workspace import ago
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge


@dataclass(slots=True)
class Conflict:
    older: Neuron
    newer: Neuron
    question: str

    @property
    def cites(self) -> list[int]:
        return [self.older.id, self.newer.id]

    def __repr__(self) -> str:
        return f"Conflict(#{self.older.id} vs #{self.newer.id})"


def phrase(older: Neuron, newer: Neuron, now: float) -> str:
    return (f"{ago(now - older.created_at)} ago you said \"{older.text()}\", "
            f"and {ago(now - newer.created_at)} ago \"{newer.text()}\". "
            f"Which is right?")


def pending(store: Store, cfg: BrainConfig, now: float,
            limit: int = 10) -> list[Conflict]:
    out: list[Conflict] = []
    seen: set[tuple[int, int]] = set()
    contradicts = list(Edge).index(Edge.CONTRADICTS)

    for fact in store.by_kind(Kind.FACT, limit=4000, live_only=True):
        for other, weight, code, _back in store.graph.neighbours(fact.id):
            if weight <= 0.0 or code != contradicts:
                continue
            pair = (min(fact.id, other), max(fact.id, other))
            if pair in seen:
                continue
            counterpart = store.get(other)
            if counterpart is None or not counterpart.is_live:
                continue
            seen.add(pair)
            older, newer = sorted((fact, counterpart), key=lambda n: n.created_at)
            question = str(fact.meta.get("question")
                           or counterpart.meta.get("question")
                           or phrase(older, newer, now))
            out.append(Conflict(older, newer, question))
            if len(out) >= limit:
                return out
    return out


def resolve(store: Store, conflict: Conflict, keep_newer: bool,
            now: float) -> None:
    """Apply the user's answer."""
    from ..consolidation.supersede import settle
    keep = conflict.newer if keep_newer else conflict.older
    drop = conflict.older if keep_newer else conflict.newer
    settle(store, keep.id, drop.id, now)
