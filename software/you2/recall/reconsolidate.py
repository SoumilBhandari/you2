"""Reconsolidation: corrections land in the moment, not at 3 a.m.

Retrieving a memory makes it labile -- a retrieved trace is re-encoded in the
context of retrieval and can be modified in the process. That is a real
property of memory, and it is exactly the repair path docs/design.md asks for.

**"No, that's out of date" must work immediately.** It is the single most
important trust interaction the device has. Deferring it to the night job
means the device argues with you twice about the same thing, and the second
time is much worse than the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..activation.workspace import Workspace
from ..config import BrainConfig
from ..consolidation.supersede import Resolution, resolve
from ..llm import validate
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge
from ..inference.tms import invalidate

_CORRECTION = re.compile(
    r"\b(no,?\s+(that|it|i)|that'?s (not|wrong|out of date|old|no longer)|"
    r"not any ?more|no longer|i don'?t .* any ?more|you'?re wrong|"
    r"that'?s outdated|actually,?\s+(it|i|that|we)|i stopped|i quit|"
    r"forget (that|what i said)|scratch that)\b", re.IGNORECASE)

#: A correction marker at the *start* of a clause, where it is prefixed to the
#: replacement rather than being a clause of its own.
_LEADING_MARKER = re.compile(
    r"^(?:no|nope|nah|actually|sorry|wait|hang on|hold on|i meant|"
    r"scratch that|correction)\b[,:]?\s*", re.IGNORECASE)

_HARD_DELETE = re.compile(
    r"\b(forget (that|about that|what i said|it)|delete that|"
    r"remove that|never mind that)\b", re.IGNORECASE)


@dataclass(slots=True)
class Repair:
    kind: str                      # corrected | deleted | none
    target: Neuron | None = None
    resolution: Resolution | None = None
    removed: list[int] | None = None

    def __repr__(self) -> str:
        return f"Repair({self.kind}, target={self.target.id if self.target else None})"


def looks_like_correction(text: str) -> bool:
    return bool(_CORRECTION.search(text))


def looks_like_deletion(text: str) -> bool:
    return bool(_HARD_DELETE.search(text))


#: Correcting an episode is meaningless -- it happened. Deleting one is the
#: main case, so the two operations look at different kinds.
CORRECTABLE = (Kind.FACT, Kind.LOOP, Kind.INSIGHT)
DELETABLE = (Kind.FACT, Kind.LOOP, Kind.INSIGHT, Kind.EPISODE)


def target(store: Store, ws: Workspace | None, text: str, *,
           kinds: tuple[Kind, ...] = CORRECTABLE) -> Neuron | None:
    """Which memory is being corrected, or deleted?

    The workspace that was just broadcast, because that is what the device
    said and therefore what the person is reacting to.
    """
    if ws is None or not ws.items:
        return None
    q = store.embed(text)
    best: tuple[float, Neuron] | None = None
    for item in ws.items:
        n = item.neuron
        if n.kind not in kinds:
            continue
        v = store.vectors.get(n.id)
        sim = 0.0
        if v is not None:
            norm = float(np.linalg.norm(v))
            if norm:
                sim = float(np.dot(v / norm, q))
        # Activation matters as much as similarity: a correction often shares
        # no vocabulary with what it corrects ("no, not any more").
        rank = 0.6 * item.activation + 0.4 * max(0.0, sim)
        if best is None or rank > best[0]:
            best = (rank, n)
    return best[1] if best else None


def apply(store: Store, llm: Any | None, ws: Workspace | None, text: str,
          cfg: BrainConfig, now: float) -> Repair:
    """Handle a correction or a deletion, right now."""
    if looks_like_deletion(text):
        victim = target(store, ws, text, kinds=DELETABLE)
        if victim is not None:
            removed = store.entomb(victim.id)
            store.log("repair", {"kind": "deleted", "target": victim.id,
                                 "removed": removed})
            return Repair("deleted", victim, removed=removed)
        return Repair("none")

    if not looks_like_correction(text):
        return Repair("none")

    victim = target(store, ws, text)
    if victim is None:
        return Repair("none")

    if victim.kind is Kind.INSIGHT:
        # Rejecting a surfaced connection is a rating, not a supersession,
        # and it feeds the precision-per-type figure that eventually says
        # which categories to cut.
        from ..discovery.scoring import verdict
        verdict(store, victim.id, useful=False)
        store.log("repair", {"kind": "rejected_insight", "target": victim.id})
        return Repair("corrected", victim)

    if victim.kind is Kind.LOOP:
        victim.invalid_at = now
        victim.meta["closed_reason"] = "user_correction"
        store.save(victim)
        store.log("repair", {"kind": "closed_loop", "target": victim.id})
        return Repair("corrected", victim)

    # Through the same checks as everything else that writes a fact. This
    # path used to bypass them entirely, which is how "no, that is not right"
    # became a stored claim reading "that is not right" and "no, I drink
    # semi-skimmed now" became one reading "I drink semi-skimmed now" — in
    # the first person, under the milk key, ready to be said back to them.
    replacement = _restate(text)
    if replacement is not None:
        replacement = validate.to_third_person(replacement)
        if validate.undurable(replacement):
            replacement = None
    if replacement is None:
        # A retraction with nothing to put in its place. "No, forget that" is
        # a complete instruction and it means exactly what `invalid_at`
        # already encodes, so the fact simply dies.
        #
        # This used to store a fact reading "the user says this is no longer
        # true: <the old claim>", which is the tombstone written as content.
        # It is not a claim about the person, it holds the dimension's key,
        # and the next real statement on that dimension supersedes *it* rather
        # than the thing it was retracting — so the demo month accumulated
        # three versions of `boiler_service/state` saying the same thing.
        victim.invalid_at = now
        victim.meta["retracted"] = True
        victim.meta.pop("question", None)
        store.save(victim)
        invalidate(store, victim.id, now, reason="user_retraction")
        store.log("repair", {"kind": "retracted", "target": victim.id})
        return Repair("corrected", victim)

    key = victim.key or f"user/corrected_{victim.id}"
    resolution = resolve(store, llm, key, replacement,
                         victim.citations() or [victim.id],
                         confidence=0.9, now=now, cfg=cfg)

    # A user correction is authoritative, and the judge's opinion of the
    # relation is beside the point: they have just told us the old claim is
    # wrong. Whatever the judge called it -- conflict, distinct, refinement --
    # the old fact dies here.
    #
    # This matters because a correction usually shares almost no vocabulary
    # with what it corrects ("no, not any more"), so a similarity-based judge
    # reliably answers "distinct", creates a disambiguated key, and leaves the
    # wrong fact live and retrievable. The device then repeats it tomorrow,
    # which is the exact failure the whole repair path exists to prevent.
    if resolution.fact is not None and resolution.fact.id != victim.id:
        victim.invalid_at = now
        victim.superseded_by = resolution.fact.id
        victim.meta.pop("question", None)
        store.save(victim)
        store.connect(resolution.fact.id, victim.id, Edge.SUPERSEDES, 1.0, at=now)
        if resolution.fact.key != victim.key and victim.key:
            # Put the replacement under the key it is actually about, so the
            # next statement on this dimension supersedes it in turn.
            resolution.fact.key = victim.key
        resolution.fact.meta.pop("question", None)
        resolution.fact.confidence = 0.95
        store.save(resolution.fact)
        invalidate(store, victim.id, now, reason="user_correction")
        resolution.relation = "reversal"
    if resolution.fact is not None:
        resolution.fact.pinned = True
        resolution.fact.storage_strength += 1.0
        store.save(resolution.fact)
    store.log("repair", {"kind": "corrected", "target": victim.id,
                         "relation": resolution.relation,
                         "new": resolution.fact.id if resolution.fact else None})
    return Repair("corrected", victim, resolution)


def _restate(text: str) -> str | None:
    """Turn a correction into a standalone claim, or None if it is a bare
    retraction.

    "no, not any more" carries no content on its own. Without this the store
    fills with facts that read "not any more" and mean nothing six months
    later — and inventing a replacement is worse, so None means *nothing goes
    in*, which the caller turns into a plain invalidation.
    """
    # The replacement comes **last**. "No, that's out of date, I'm at Lumen
    # now" — a correction states what is wrong and then what is right, in that
    # order, so taking the last clause with content beats taking the longest.
    # Longest picked "that's out of date" over "I'm at Lumen now" on exactly
    # that sentence, which is the whole content of the correction lost to a
    # tiebreak.
    best: str | None = None
    for clause in re.split(r"[,;.]", text):
        clause = _LEADING_MARKER.sub("", clause.strip()).strip()
        # A marker with nothing attached ("no", "actually") is not the
        # replacement; a marker with a clause attached ("actually I prefer oat
        # milk now") is the whole of it, which is why the marker is stripped
        # here rather than the clause discarded.
        if not clause or _CORRECTION.search(clause):
            continue
        best = clause
    return best
