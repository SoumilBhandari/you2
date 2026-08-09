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
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store

_CORRECTION = re.compile(
    r"\b(no,?\s+(that|it|i)|that'?s (not|wrong|out of date|old|no longer)|"
    r"not any ?more|no longer|i don'?t .* any ?more|you'?re wrong|"
    r"that'?s outdated|actually,?\s+(it|i|that|we)|i stopped|i quit|"
    r"forget (that|what i said)|scratch that)\b", re.IGNORECASE)

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


def target(store: Store, ws: Workspace | None, text: str) -> Neuron | None:
    """Which memory is being corrected?

    The workspace that was just broadcast, because that is what the device
    said and therefore what the person is reacting to. Facts first: correcting
    an episode is meaningless (it happened), while correcting a fact is the
    whole point.
    """
    if ws is None or not ws.items:
        return None
    q = store.embed(text)
    best: tuple[float, Neuron] | None = None
    for item in ws.items:
        n = item.neuron
        if n.kind not in (Kind.FACT, Kind.LOOP, Kind.INSIGHT):
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
        victim = target(store, ws, text)
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

    key = victim.key or f"user/corrected_{victim.id}"
    resolution = resolve(store, llm, key, _restate(text, victim),
                         victim.citations() or [victim.id],
                         confidence=0.9, now=now, cfg=cfg)
    # A user correction is authoritative. It is not a "conflict" to be asked
    # about later -- they just told us.
    if resolution.relation == "conflict" and resolution.fact is not None:
        victim.invalid_at = now
        victim.superseded_by = resolution.fact.id
        victim.meta.pop("question", None)
        resolution.fact.meta.pop("question", None)
        resolution.fact.confidence = 0.95
        store.save(victim)
        store.save(resolution.fact)
        resolution.relation = "reversal"
    if resolution.fact is not None:
        resolution.fact.pinned = True
        resolution.fact.storage_strength += 1.0
        store.save(resolution.fact)
    store.log("repair", {"kind": "corrected", "target": victim.id,
                         "relation": resolution.relation,
                         "new": resolution.fact.id if resolution.fact else None})
    return Repair("corrected", victim, resolution)


def _restate(text: str, victim: Neuron) -> str:
    """Turn a correction into a standalone claim.

    "no, not any more" carries no content on its own, so a bare negation
    inherits the subject of what it corrects. Without this the store fills
    with facts that read "not any more" and mean nothing six months later.
    """
    cleaned = _CORRECTION.sub("", text).strip(" ,.").strip()
    if len(cleaned) < 8:
        return f"the user says this is no longer true: {victim.text()}"
    return cleaned
