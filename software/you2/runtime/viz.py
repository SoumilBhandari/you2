"""Turning a cascade into something you can watch.

The payoff for having built retrieval as an explainable graph traversal: the
data structure that exists so `you2 why` can print a path is *already* an
animation. Nodes, edges, firing order, activation, and what lateral inhibition
suppressed — that is a scene graph.

**The layout is the explanation.** Concentric rings by hop distance from the
cue, with the workspace at the centre. Activation ignites at the rim and
travels inward, winners arrive in the middle, suppressed neighbours visibly
dim. On a round 800×800 display that reads as *thought converging*, and it is
not a metaphor laid over the data — it is the data.

Nothing here is load-bearing: strip the whole module and the brain is
identical. It reads state; it never creates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..activation.spread import Cascade
from ..activation.workspace import Workspace
from ..substrate.neuron import Kind
from ..substrate.store import Store
from ..substrate.synapse import CODE_EDGES

#: Beyond this the ring is unreadable at 800 px and the animation is mush.
MAX_NODES = 90
MAX_EDGES = 160

#: One colour per kind, so a glance says what *sort* of memory lit up. Values
#: are hues; the UI owns saturation and lightness.
HUES: dict[str, int] = {
    "episode": 210,   # blue    — what happened
    "fact": 45,       # amber   — what is true
    "entity": 285,    # violet  — who and what
    "schema": 160,    # green   — patterns
    "insight": 330,   # pink    — discovered
    "loop": 20,       # orange  — open
    "affect": 0,      # red     — felt
}


def label(text: str, limit: int = 42) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(slots=True)
class Scene:
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    order: list[int]
    workspace: list[int]
    tokens: int
    visited: int
    exhausted: bool
    query: str
    inferences: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes, "edges": self.edges, "order": self.order,
            "workspace": self.workspace, "tokens": self.tokens,
            "visited": self.visited, "exhausted": self.exhausted,
            "query": self.query, "inferences": self.inferences,
        }


def scene(store: Store, ws: Workspace, *, query: str = "") -> Scene:
    """Build the frame for one retrieval."""
    cascade: Cascade | None = ws.cascade
    if cascade is None:
        return Scene([], [], [], [], ws.tokens, 0, True, query, [])

    ranked = cascade.ranked(MAX_NODES)
    keep = {nid for nid, _a in ranked}
    winners = set(ws.ids())
    keep |= winners                       # a winner is always drawn
    suppressed = {loser for loser, _w, _s in ws.suppressed}

    neurons = store.get_many(keep)
    peak = max((a for _n, a in ranked), default=1.0) or 1.0

    nodes: list[dict[str, Any]] = []
    for nid in keep:
        n = neurons.get(nid)
        if n is None:
            continue
        activation = cascade.activation.get(nid, 0.0)
        nodes.append({
            "id": nid,
            "kind": str(n.kind),
            "hue": HUES.get(str(n.kind), 210),
            "label": label(n.text()),
            "hop": cascade.hops.get(nid, 3),
            "a": round(min(1.0, activation / peak), 4),
            "raw": round(activation, 4),
            "seed": cascade.seeds.get(nid),
            "winner": nid in winners,
            "suppressed": nid in suppressed,
            "pinned": bool(n.pinned),
            "derived": bool(n.meta.get("derived")),
        })

    edges: list[dict[str, Any]] = []
    for nid in keep:
        step = cascade.via.get(nid)
        if step is None:
            continue
        src, etype = step
        if src not in keep:
            continue
        edges.append({
            "from": src, "to": nid, "type": str(etype),
            "order": _order_index(cascade, src),
        })
    # Then the strongest remaining structural edges among drawn nodes, so the
    # picture shows a network rather than a bare spanning tree.
    drawn = {(e["from"], e["to"]) for e in edges}
    for nid in keep:
        if len(edges) >= MAX_EDGES:
            break
        for other, weight, code, backward in store.graph.neighbours(nid):
            if other not in keep or weight < 0.25 or backward:
                continue
            if (nid, other) in drawn or (other, nid) in drawn:
                continue
            drawn.add((nid, other))
            edges.append({"from": nid, "to": other,
                          "type": str(CODE_EDGES[code]), "order": -1})
            if len(edges) >= MAX_EDGES:
                break

    inferences = [{"claim": label(i.claim, 90), "kind": i.kind,
                   "confidence": round(i.confidence, 3),
                   "premises": list(i.premises)}
                  for i in (ws.inferences or [])]

    return Scene(
        nodes=nodes, edges=edges,
        order=[n for n in cascade.fired if n in keep],
        workspace=ws.ids(), tokens=ws.tokens, visited=cascade.visited,
        exhausted=cascade.exhausted, query=query, inferences=inferences,
    )


def _order_index(cascade: Cascade, nid: int) -> int:
    try:
        return cascade.fired.index(nid)
    except ValueError:
        return -1


def constellation(store: Store, limit: int = 120) -> dict[str, Any]:
    """The idle view: the shape of the whole store, dimmed.

    Not a retrieval — a portrait. It is what makes the device feel like it
    holds something even when nobody is talking to it, and it costs one query.
    """
    picked: list[dict[str, Any]] = []
    for kind, take in ((Kind.ENTITY, 24), (Kind.FACT, 30), (Kind.SCHEMA, 8),
                       (Kind.LOOP, 8), (Kind.EPISODE, 40), (Kind.INSIGHT, 10)):
        for n in store.by_kind(kind, limit=take, order="last_access DESC"):
            picked.append({
                "id": n.id, "kind": str(n.kind), "hue": HUES.get(str(n.kind), 210),
                "label": label(n.text(), 30), "a": round(min(1.0, n.confidence), 3),
                "pinned": bool(n.pinned),
            })
    picked = picked[:limit]
    ids = {p["id"] for p in picked}
    links: list[dict[str, Any]] = []
    for p in picked:
        for other, weight, code, backward in store.graph.neighbours(p["id"]):
            if other in ids and not backward and weight >= 0.3:
                links.append({"from": p["id"], "to": other, "w": round(weight, 3)})
                if len(links) >= 220:
                    break
    stats = store.stats()
    return {"nodes": picked, "edges": links,
            "neurons": stats["neurons"], "synapses": stats["edges"],
            "facts": stats["live_facts"]}


def night_frame(report: Any, stage: str = "") -> dict[str, Any]:
    """A watchable summary of a night, for the dreaming view."""
    h = getattr(report, "homeostasis", {}) or {}
    return {
        "stage": stage,
        "episodes": getattr(report, "episodes_replayed", 0),
        "batches": getattr(report, "batches", 0),
        "facts_new": getattr(getattr(report, "facts", None), "new", 0),
        "facts_reversed": getattr(getattr(report, "facts", None), "reversed_", 0),
        "edges_before": h.get("edges_before", 0),
        "edges_after": h.get("edges_after", 0),
        "pruned": h.get("pruned", 0),
        "compiled": len(getattr(report, "compiled", []) or []),
        "insights": len(getattr(report, "insights", []) or []),
        "questions": list(getattr(report, "questions", []) or [])[:3],
        "journal": report.journal() if hasattr(report, "journal") else "",
    }
