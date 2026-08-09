"""Bitemporal supersession, and what to do when it can't be decided.

Supersession is what stops the store rotting. Without it you mention forty
projects over two years and all forty stay equally retrievable, with no notion
that thirty-nine are historical.

The row that matters most is the last one. An unresolvable contradiction
becomes a **question**:

    "In March you said the Anderson deadline was firm; on Tuesday you said it
     had moved. Which is current?"

One question resolves a fact that would otherwise poison retrieval
indefinitely -- and asking is honest in a way that silently picking the newer
record is not. Silently picking newest is what every append-only memory system
does by accident, and it is wrong roughly as often as people misspeak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import BrainConfig
from ..llm import prompts
from ..llm.client import LLMError
from ..llm.rules import relation as rule_relation
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: Weight on a supersedes edge. Conduction for this type is 0.20, so the
#: dead fact is reachable for "what did I used to think" and essentially
#: unreachable in ordinary retrieval.
SUPERSEDE_WEIGHT = 1.0
CONTRADICT_WEIGHT = 0.9
SUPPORT_WEIGHT = 0.7


class Model(Protocol):
    def structured(self, messages: Any, **kw: Any) -> Any: ...


@dataclass(slots=True)
class Resolution:
    relation: str
    fact: Neuron | None = None
    replaced: Neuron | None = None
    question: str | None = None
    why: str = ""

    @property
    def created(self) -> bool:
        return self.relation in ("new", "refinement", "reversal", "conflict", "distinct")


@dataclass(slots=True)
class Tally:
    new: int = 0
    restated: int = 0
    refined: int = 0
    reversed_: int = 0
    conflicts: int = 0
    distinct: int = 0
    questions: list[str] = field(default_factory=list)

    def record(self, r: Resolution) -> None:
        match r.relation:
            case "new":
                self.new += 1
            case "restatement":
                self.restated += 1
            case "refinement":
                self.refined += 1
            case "reversal":
                self.reversed_ += 1
            case "conflict":
                self.conflicts += 1
                if r.question:
                    self.questions.append(r.question)
            case "distinct":
                self.distinct += 1

    def as_dict(self) -> dict[str, int]:
        return {"new": self.new, "restated": self.restated, "refined": self.refined,
                "reversed": self.reversed_, "conflicts": self.conflicts,
                "distinct": self.distinct}


def resolve(store: Store, llm: Model | None, key: str, text: str,
            cites: list[int], confidence: float, now: float,
            cfg: BrainConfig) -> Resolution:
    """Decide what a candidate fact does to the fact already holding its key."""
    existing = store.by_key(key)
    if existing is None:
        return Resolution("new", fact=_create(store, key, text, cites,
                                              confidence, now))

    rel, why, question = _judge(llm, existing, text, now)

    if rel == "restatement":
        # Repetition is evidence. Confidence rises, the record does not.
        existing.confidence = min(1.0, existing.confidence + 0.08 * (1 - existing.confidence))
        existing.access_count += 1
        existing.last_access = now
        existing.meta.setdefault("cites", [])
        existing.meta["cites"] = sorted(set(existing.citations()) | set(cites))
        existing.meta["restated"] = existing.meta.get("restated", 0) + 1
        store.save(existing)
        _cite(store, existing, cites, now)
        return Resolution("restatement", fact=existing, why=why)

    if rel in ("refinement", "reversal"):
        fact = _create(store, key, text, cites, confidence, now)
        # Inherit accumulated learning: the dimension has been discussed
        # before, so the replacement should not start from a cold base.
        fact.storage_strength = max(fact.storage_strength,
                                    existing.storage_strength * 0.8)
        store.save(fact)
        existing.invalid_at = now
        existing.superseded_by = fact.id
        store.save(existing)
        store.connect(fact.id, existing.id, Edge.SUPERSEDES, SUPERSEDE_WEIGHT, at=now)
        return Resolution(rel, fact=fact, replaced=existing, why=why)

    if rel == "conflict":
        # Both stay valid. Retrieval will surface the contradiction, which is
        # the honest behaviour until the user settles it.
        fact = _create(store, key, text, cites, confidence * 0.8, now)
        store.connect(fact.id, existing.id, Edge.CONTRADICTS, CONTRADICT_WEIGHT, at=now)
        q = question or (f"You said \"{existing.text()}\" and later "
                         f"\"{text}\". Which is right?")
        fact.meta["question"] = q
        existing.meta["question"] = q
        store.save(fact)
        store.save(existing)
        store.log("contradiction", {"a": existing.id, "b": fact.id,
                                    "key": key, "question": q})
        return Resolution("conflict", fact=fact, replaced=existing,
                          question=q, why=why)

    # distinct: same key was the wrong call. Give the newcomer its own.
    alt = _disambiguate(store, key)
    return Resolution("distinct",
                      fact=_create(store, alt, text, cites, confidence, now),
                      why=why)


def _judge(llm: Model | None, existing: Neuron, text: str,
           now: float) -> tuple[str, str, str | None]:
    if llm is not None:
        try:
            out = llm.structured(prompts.judge_supersession(existing, text, now))
            rel = str(out.get("relation", "")).strip().lower()
            if rel in ("restatement", "refinement", "reversal", "conflict", "distinct"):
                return rel, str(out.get("why", "")), out.get("question")
        except (LLMError, KeyError, TypeError, ValueError):
            pass
    # Offline, or the model returned something unusable. The rules are
    # conservative and it is better to keep working at lower quality than to
    # stop consolidating because the Wi-Fi dropped.
    return rule_relation(existing.text(), text), "rules", None


def _create(store: Store, key: str, text: str, cites: list[int],
            confidence: float, now: float) -> Neuron:
    fact = store.add(Neuron(
        kind=Kind.FACT, key=key, content=text, created_at=now, last_access=now,
        valid_at=now, confidence=max(0.1, min(1.0, confidence)),
        salience=0.6, storage_strength=2.0,
        meta={"cites": sorted(set(cites))},
    ))
    _cite(store, fact, cites, now)
    return fact


def _cite(store: Store, fact: Neuron, cites: list[int], now: float) -> None:
    """Provenance edges. Carried because a system that knows where it learned
    something is the difference between one you trust and one that is
    confidently wrong at you -- and because 'forget that' needs to be able to
    reach the derived claims."""
    for episode_id in cites:
        if store.soma.get(episode_id) is None:
            continue
        store.connect(fact.id, episode_id, Edge.SUPPORTS, SUPPORT_WEIGHT, at=now)
        for entity_id, weight, code, _back in store.graph.neighbours(episode_id):
            if weight > 0.0 and code == list(Edge).index(Edge.ABOUT):
                soma = store.soma.get(entity_id)
                if soma is not None and soma.kind is Kind.ENTITY:
                    store.connect(fact.id, entity_id, Edge.ABOUT, 0.7, at=now)


def _disambiguate(store: Store, key: str) -> str:
    for i in range(2, 40):
        candidate = f"{key}_{i}"
        if store.by_key(candidate, live_only=False) is None:
            return candidate
    return f"{key}_x"


def open_questions(store: Store, limit: int = 20) -> list[dict[str, Any]]:
    """Unresolved contradictions, for the morning."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in store.by_kind(Kind.FACT, limit=2000):
        q = fact.meta.get("question")
        if not q or q in seen:
            continue
        seen.add(str(q))
        out.append({"question": q, "fact": fact.id, "key": fact.key})
        if len(out) >= limit:
            break
    return out


def settle(store: Store, keep_id: int, drop_id: int, now: float) -> None:
    """The user answered. Apply it, and clear the question from both."""
    keep, drop = store.get(keep_id), store.get(drop_id)
    if keep is None or drop is None:
        return
    drop.invalid_at = now
    drop.superseded_by = keep.id
    drop.meta.pop("question", None)
    keep.meta.pop("question", None)
    keep.confidence = min(1.0, keep.confidence + 0.2)
    # Answering a question about a memory is a retrieval, and a hard one.
    keep.storage_strength += 0.5
    store.save(drop)
    store.save(keep)
    store.connect(keep.id, drop.id, Edge.SUPERSEDES, SUPERSEDE_WEIGHT, at=now)
    store.log("settled", {"keep": keep_id, "drop": drop_id})
