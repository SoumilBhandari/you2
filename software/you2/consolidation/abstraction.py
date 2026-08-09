"""Abstraction: reading a batch and turning it into structure.

One model call per ripple batch. Facts arrive with a stable key, get resolved
against whatever already holds that key, and carry provenance back to the
episodes they came from.

Facts are extracted *here* rather than at encoding time because extracting a
fact requires knowing what it supersedes, and that needs context the moment
does not have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clock import DAY
from ..config import BrainConfig
from ..encoding.binding import resolve_entity
from ..llm import prompts
from ..llm.client import LLMError
from ..llm.rules import extract as rule_extract
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge
from ..inference import parsimony
from . import supersede
from .forgetting import mark_absorbed
from .replay import Batch, mark_replayed

#: Existing fact keys shown to the extractor so it can reuse them. Reuse is
#: the whole game -- a fact under a fresh key can never supersede anything --
#: and it is also where extraction most often fails.
KEY_HINTS = 60


@dataclass(slots=True)
class BatchResult:
    gist: str = ""
    tally: supersede.Tally = field(default_factory=supersede.Tally)
    entities_new: list[int] = field(default_factory=list)
    loops_new: list[int] = field(default_factory=list)
    loops_closed: list[int] = field(default_factory=list)
    episodes: list[int] = field(default_factory=list)
    absorbed: int = 0
    refused: int = 0
    degraded: bool = False
    """True when the model was unreachable and the rule extractor stood in.
    Recorded so a later pass can upgrade what the rules produced."""

    def as_dict(self) -> dict[str, Any]:
        return {"gist": self.gist[:120], "facts": self.tally.as_dict(),
                "entities_new": len(self.entities_new),
                "loops_new": len(self.loops_new),
                "loops_closed": len(self.loops_closed),
                "episodes": len(self.episodes), "absorbed": self.absorbed,
                "refused": self.refused,
                "degraded": self.degraded}


def relevant_keys(store: Store, batch: Batch) -> list[str]:
    """Keys the extractor should try to reuse.

    Facts already attached to this batch's entities first -- those are the
    ones a new statement is most likely to be about -- then the most recently
    touched keys as a general prior.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for entity_id in batch.shared:
        for other, weight, code, _back in store.graph.neighbours(entity_id):
            if weight <= 0.0 or code != list(Edge).index(Edge.ABOUT):
                continue
            soma = store.soma.get(other)
            if soma is None or soma.kind is not Kind.FACT:
                continue
            fact = store.get(other)
            if fact and fact.key and fact.key not in seen and fact.is_live:
                seen.add(fact.key)
                keys.append(fact.key)
    for fact in store.by_kind(Kind.FACT, limit=KEY_HINTS, order="last_access DESC"):
        if fact.key and fact.key not in seen:
            seen.add(fact.key)
            keys.append(fact.key)
    return keys[:KEY_HINTS]


def consolidate(store: Store, llm: Any | None, batch: Batch,
                cfg: BrainConfig, now: float) -> BatchResult:
    out = BatchResult(episodes=[n.id for n in batch.episodes])
    if not batch.episodes:
        return out

    payload, out.degraded = _read(store, llm, batch, now)

    out.gist = str(payload.get("gist") or "")
    if out.gist:
        for ep in batch.episodes:
            if not ep.gist:
                ep.gist = out.gist
                store.save(ep)
        if batch.episodes[0].session_id:
            store.close_session(batch.episodes[0].session_id, out.gist)

    # ---- entities first: facts want to attach to them ----------------------
    entity_ids: list[int] = []
    for found in payload.get("entities") or []:
        if not isinstance(found, dict) or not found.get("name"):
            continue
        before = store.count(Kind.ENTITY)
        entity = resolve_entity(store, found, now)
        if entity is None:
            continue
        entity_ids.append(entity.id)
        if store.count(Kind.ENTITY) > before:
            out.entities_new.append(entity.id)
        note = str(found.get("note") or "").strip()
        if note and not entity.meta.get("role"):
            entity.meta["role"] = note
            store.save(entity)
        for episode_id in _ids(found.get("cites"), out.episodes):
            store.connect(episode_id, entity.id, Edge.ABOUT, 0.75, at=now)

    # ---- facts -------------------------------------------------------------
    produced: set[int] = set()
    for candidate in payload.get("facts") or []:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("key") or "").strip()
        text = str(candidate.get("text") or "").strip()
        if not key or not text:
            continue
        # Never store what the weights already know. A generic claim costs
        # bytes AND retrieval precision -- it is one more thing that looks
        # superficially relevant to every future query.
        verdict = parsimony.worth_storing(store, llm, key, text, cfg,
                                          use_model=False)
        if not verdict.keep:
            out.refused += 1
            store.log("parsimony", {"key": key, "text": text[:80],
                                    "reason": verdict.reason})
            continue
        cites = _ids(candidate.get("cites"), out.episodes) or list(out.episodes)
        try:
            confidence = float(candidate.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        resolution = supersede.resolve(store, llm, key, text, cites,
                                       confidence, now, cfg)
        out.tally.record(resolution)
        if resolution.fact is not None:
            produced.update(cites)

    # ---- open loops --------------------------------------------------------
    for loop in payload.get("loops") or []:
        if not isinstance(loop, dict):
            continue
        text = str(loop.get("text") or "").strip()
        if len(text) < 6:
            continue
        cites = _ids(loop.get("cites"), out.episodes) or list(out.episodes)
        created = _open_loop(store, text, cites, loop.get("days"), now, entity_ids)
        if created is not None:
            out.loops_new.append(created)

    for closure in payload.get("closed") or []:
        out.loops_closed.extend(_close(store, str(closure), now))

    # ---- bookkeeping -------------------------------------------------------
    if produced:
        out.absorbed = mark_absorbed(store, sorted(produced), now)
    mark_replayed(store, batch.episodes, now)
    store.log("consolidate", out.as_dict())
    return out


def _read(store: Store, llm: Any | None, batch: Batch,
          now: float) -> tuple[dict[str, Any], bool]:
    """Model first, rules as the fallback.

    A device whose memory silently stops improving when the Wi-Fi drops is
    worse than one that degrades visibly, so this never raises.
    """
    if llm is not None:
        try:
            messages = prompts.extract_batch(batch.episodes, now,
                                             relevant_keys(store, batch))
            payload = llm.structured(messages)
            if isinstance(payload, dict):
                return payload, False
        except (LLMError, ValueError, TypeError, KeyError) as exc:
            store.log("consolidate_degraded", {"error": str(exc)[:200],
                                               "episodes": len(batch.episodes)})
    fallback = rule_extract([n.text() for n in batch.episodes],
                            [n.id for n in batch.episodes], now)
    return fallback.as_dict(), True


def _ids(raw: Any, allowed: list[int]) -> list[int]:
    """Citations, filtered to episodes actually in this batch.

    Models cite ids that were never in the prompt. A fact whose provenance is
    invented is worse than no fact -- it is unfalsifiable, and 'forget that'
    can never reach it.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    allow = set(allowed)
    out: list[int] = []
    for item in raw:
        try:
            nid = int(item)
        except (TypeError, ValueError):
            continue
        if nid in allow and nid not in out:
            out.append(nid)
    return out


def _open_loop(store: Store, text: str, cites: list[int], days: Any,
               now: float, entity_ids: list[int]) -> int | None:
    from ..encoding.binding import loop_exists
    if loop_exists(store, text, cites):
        return None
    try:
        horizon = float(days) if days is not None else 14.0
    except (TypeError, ValueError):
        horizon = 14.0
    loop = store.add(Neuron(
        kind=Kind.LOOP, content=text, created_at=now, last_access=now,
        salience=0.6, confidence=0.7, storage_strength=2.0,
        meta={"expected_by": now + max(1.0, horizon) * DAY, "cites": cites},
    ))
    for episode_id in cites:
        store.connect(loop.id, episode_id, Edge.SUPPORTS, 0.8, at=now)
    for entity_id in entity_ids:
        store.connect(loop.id, entity_id, Edge.ABOUT, 0.7, at=now)
    return loop.id


def _close(store: Store, text: str, now: float) -> list[int]:
    from ..encoding.binding import _close_loops
    return _close_loops(store, text, now)


def induce_schema(store: Store, cfg: BrainConfig, now: float,
                  min_members: int = 4) -> list[int]:
    """Promote a repeated fact pattern to a schema.

    A schema is a claim about a *shape* -- "the user's preferences about food
    are strong and stable" -- rather than about a value. Cheap version: fact
    keys sharing a subject and a predicate stem, above a count threshold.

    Schemas matter because schema-consistent material consolidates faster: a
    new fact that fits an existing schema is a strengthening, not a whole new
    structure to build.
    """
    groups: dict[str, list[Neuron]] = {}
    for fact in store.by_kind(Kind.FACT, limit=100000, live_only=True):
        if not fact.key or "/" not in fact.key:
            continue
        subject, _, predicate = fact.key.partition("/")
        stem_key = f"{subject}/{predicate.split('_')[0]}"
        groups.setdefault(stem_key, []).append(fact)

    created: list[int] = []
    for stem_key, members in groups.items():
        if len(members) < min_members:
            continue
        existing = store.by_key(f"schema:{stem_key}")
        subject, _, predicate = stem_key.partition("/")
        text = (f"{subject} has {len(members)} stable {predicate} facts: "
                + "; ".join(m.text() for m in members[:4]))
        if existing is not None:
            existing.content = text
            existing.confidence = min(1.0, 0.5 + 0.1 * len(members))
            existing.meta["members"] = [m.id for m in members]
            store.save(existing, reembed=True)
            continue
        schema = store.add(Neuron(
            kind=Kind.SCHEMA, key=f"schema:{stem_key}", content=text,
            created_at=now, last_access=now, salience=0.5,
            confidence=min(1.0, 0.5 + 0.1 * len(members)), storage_strength=2.5,
            meta={"members": [m.id for m in members]},
        ))
        for m in members:
            store.connect(m.id, schema.id, Edge.PART_OF, 0.7, at=now)
        created.append(schema.id)
    return created
