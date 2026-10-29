"""Inference: concluding things the person never said.

A second brain that only recalls what you told it is a filing cabinet. A brain
concludes.

**The division of labour that makes this affordable.** The language model is a
very high-parameter net that already holds world knowledge, language, and
reasoning operators. We do not need to store any of that, and we should not
try: the weights are the cortex, rented and replaceable. What the graph holds
is the *person* -- episodes, personal semantics, the connectome -- which is
small, and which no model has. Inference is running the weights over the
graph.

That framing settles the storage question directly:

    never store what the weights already know   (see parsimony.py)
    never store what can be re-derived cheaply  (this module)
    do store what gets re-derived over and over (compile.py)

**Inference is lazy.** Materialising every implication is a combinatorial
explosion and it is also just wrong: people do not hold "my sister's husband's
job is in Lisbon" as a stored fact, they work it out when asked. So nothing
here writes to the store on its own.

**Except when it stops being worth re-deriving.** A conclusion reached three
times gets compiled into a stored fact with its premises attached -- which is
ACT-R's production compilation, and is how a derivation you keep repeating
becomes a thing you just know. Bounded, self-limiting, and it is the only
thing in the system that grows with *use* rather than with elapsed time.

**Two tiers by cost:**

    graph      transitive, inherit, contrast, gap -- microseconds, no model.
               Safe in the hot path; they ride along in the workspace.
    model      abduce, project, transfer -- a call each. Night job, or an
               explicit question. Never on the critical path.

**Derived claims are labelled as derived** in the memory block. Without that
the model asserts an inference back to the person as something they told it,
which is the fastest way to lose their trust in the whole device.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..activation.workspace import Workspace, ago
from ..clock import DAY
from ..config import BrainConfig
from ..llm.client import LLMError
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge

#: How much confidence survives one step of each rule. Deduction over an
#: asserted structural edge is nearly lossless; abduction is a guess.
RELIABILITY: dict[str, float] = {
    "transitive": 0.85,
    "inherit": 0.70,
    "contrast": 0.95,
    "gap": 0.55,
    "abduce": 0.50,
    "project": 0.45,
    "transfer": 0.40,
}

#: Edge types that compose. `part_of` genuinely does; `assoc` emphatically
#: does not, and chaining it is how "you mentioned coffee here and also here"
#: gets dressed up as reasoning.
TRANSITIVE: dict[Edge, str] = {
    Edge.PART_OF: "is part of",
    Edge.CAUSES: "leads to",
}

CHEAP = ("transitive", "inherit", "contrast", "gap")
EXPENSIVE = ("abduce", "project", "transfer")


@dataclass(slots=True)
class Inference:
    claim: str
    kind: str
    premises: list[int] = field(default_factory=list)
    confidence: float = 0.0
    derivation: list[str] = field(default_factory=list)
    cost: str = "graph"

    @property
    def signature(self) -> str:
        """Stable identity for a derivation, so repeats can be counted.

        Keyed on premises and rule rather than on wording: the same conclusion
        reached the same way must hash the same even if the phrasing drifts.
        """
        body = f"{self.kind}|{','.join(str(p) for p in sorted(self.premises))}"
        return hashlib.blake2b(body.encode(), digest_size=12).hexdigest()

    def render(self) -> str:
        return f"{self.claim} (inferred, {self.confidence:.0%})"

    def as_dict(self) -> dict[str, Any]:
        return {"claim": self.claim, "kind": self.kind, "premises": self.premises,
                "confidence": round(self.confidence, 3),
                "derivation": self.derivation, "cost": self.cost}

    def __repr__(self) -> str:
        return f"Inference({self.kind}, {self.confidence:.2f}, {self.claim[:48]!r})"


# ------------------------------------------------------------- graph rules

def transitive(store: Store, anchors: Sequence[int], cfg: BrainConfig,
               max_depth: int = 3) -> list[Inference]:
    """Chain composable edges. A part_of B part_of C, therefore A part_of C.

    Only over edge types that actually compose. Chaining plain association is
    not inference; it is spreading activation with a straight face.
    """
    out: list[Inference] = []
    for anchor in anchors:
        start = store.get(anchor)
        if start is None:
            continue
        for edge_type, phrase in TRANSITIVE.items():
            code = list(Edge).index(edge_type)
            frontier: list[tuple[int, float, list[int]]] = [(anchor, 1.0, [anchor])]
            for depth in range(max_depth):
                nxt: list[tuple[int, float, list[int]]] = []
                for node, strength, path in frontier:
                    for other, weight, c, backward in store.graph.neighbours(node):
                        if c != code or backward or weight <= 0.0 or other in path:
                            continue
                        conf = strength * weight * RELIABILITY["transitive"]
                        if conf < cfg.inference_min_confidence:
                            continue
                        nxt.append((other, conf, path + [other]))
                        if depth >= 1:
                            end = store.get(other)
                            if end is None:
                                continue
                            out.append(Inference(
                                claim=f"{start.text()} {phrase} {end.text()}",
                                kind="transitive", premises=path + [other],
                                confidence=conf, cost="graph",
                                derivation=[f"#{a} -{edge_type}-> #{b}"
                                            for a, b in zip(path, path[1:] + [other])],
                            ))
                frontier = nxt
                if not frontier:
                    break
    return out


def inherit(store: Store, anchors: Sequence[int], cfg: BrainConfig) -> list[Inference]:
    """Properties of a whole apply to its parts, with reduced confidence.

    Defeasible on purpose -- that is what the confidence discount is for. A
    fact about "the Kestrel migration" usually applies to a sub-task of it,
    and sometimes does not.
    """
    out: list[Inference] = []
    part_of = list(Edge).index(Edge.PART_OF)
    about = list(Edge).index(Edge.ABOUT)

    for anchor in anchors:
        child = store.get(anchor)
        if child is None or child.kind not in (Kind.ENTITY, Kind.FACT):
            continue
        for parent_id, weight, code, backward in store.graph.neighbours(anchor):
            if code != part_of or backward or weight <= 0.0:
                continue
            parent = store.get(parent_id)
            if parent is None:
                continue
            for fact_id, w2, c2, back2 in store.graph.neighbours(parent_id):
                if c2 != about or not back2 or w2 <= 0.0:
                    continue
                fact = store.get(fact_id)
                if fact is None or fact.kind is not Kind.FACT or not fact.is_live:
                    continue
                conf = weight * w2 * fact.confidence * RELIABILITY["inherit"]
                if conf < cfg.inference_min_confidence:
                    continue
                out.append(Inference(
                    claim=f"{fact.text()} — so probably also for {child.text()}",
                    kind="inherit", premises=[fact.id, parent_id, anchor],
                    confidence=conf, cost="graph",
                    derivation=[f"#{anchor} part_of #{parent_id}",
                                f"#{fact_id} about #{parent_id}"],
                ))
    return out


def contrast(store: Store, anchors: Sequence[int], cfg: BrainConfig,
             now: float) -> list[Inference]:
    """How a belief has moved.

    "You have changed your mind about this twice in a year" is not stored
    anywhere and is not retrievable by any similarity search -- it is a fact
    about the *shape of the history* of a key, which only exists because
    supersession keeps the old versions.
    """
    out: list[Inference] = []
    seen: set[str] = set()
    for anchor in anchors:
        n = store.get(anchor)
        if n is None or n.kind is not Kind.FACT or not n.key or n.key in seen:
            continue
        seen.add(n.key)
        history = store.history_of(n.key)
        if len(history) < 2:
            continue
        # The current value is the live one, not whichever row sorts first.
        current = store.by_key(n.key) or history[0]
        oldest = history[-1]
        if current.id == oldest.id:
            continue
        span = ((current.valid_at or current.created_at)
                - (oldest.valid_at or oldest.created_at)) / DAY
        if span < 14.0:
            continue
        # Distinct positions, not rows. Two rows saying the same thing are one
        # opinion recorded twice, and counting them is how "changed 9 times"
        # was said about a preference stated twice.
        positions = len({h.text().strip().lower() for h in history})
        if positions < 2:
            continue
        out.append(Inference(
            claim=(f"this has changed {positions - 1} time"
                   f"{'s' if positions > 2 else ''} in {ago(span * DAY)}: "
                   f"was \"{oldest.text()}\", now \"{current.text()}\""),
            kind="contrast", premises=[current.id, oldest.id],
            confidence=RELIABILITY["contrast"], cost="graph",
            derivation=[f"#{h.id} @ {ago(now - h.created_at)}" for h in history[:4]],
        ))
    return out


def gap(store: Store, anchors: Sequence[int], cfg: BrainConfig) -> list[Inference]:
    """Pattern completion: a schema member missing what the rest share.

    The hippocampus completes patterns from partial cues; here the useful
    version is noticing the hole rather than filling it. A gap is a *question*,
    not a claim -- which is why its reliability is the lowest of the graph
    rules and why it is phrased as one.
    """
    out: list[Inference] = []
    for anchor in anchors:
        schema = store.get(anchor)
        if schema is None or schema.kind is not Kind.SCHEMA:
            continue
        members = [store.get(m) for m in (schema.meta.get("members") or [])]
        members = [m for m in members if m is not None and m.is_live]
        if len(members) < 3:
            continue
        subjects = {(m.key or "").partition("/")[0] for m in members}
        if len(subjects) != 1:
            continue
        out.append(Inference(
            claim=(f"there are {len(members)} recorded here but none about "
                   f"{_missing_hint(schema)} — worth asking"),
            kind="gap", premises=[m.id for m in members[:4]],
            confidence=RELIABILITY["gap"] * min(1.0, len(members) / 6.0),
            cost="graph",
            derivation=[f"schema #{schema.id} with {len(members)} members"],
        ))
    return out


def _missing_hint(schema: Neuron) -> str:
    predicate = (schema.key or "").rpartition("/")[2] or "this"
    return f"other {predicate}"


# ------------------------------------------------------------- model rules

def abduce(store: Store, llm: Any, ws: Workspace, cfg: BrainConfig,
           now: float, observation: str) -> list[Inference]:
    """The best explanation for something surprising.

    Abduction is where a memory system stops being a lookup and starts being
    useful: "the migration keeps slipping" plus "you are the only reviewer"
    plus "you have had no free Fridays since March" is an explanation nobody
    stored.
    """
    if llm is None or not ws.items:
        return []
    evidence = [i.neuron for i in ws.items][:6]
    if len(evidence) < 2:
        return []
    from ..llm import prompts
    try:
        out = llm.structured(prompts.state_insight(
            "explanation", evidence, now,
            hint=f"What best explains this: \"{observation}\"? Only answer "
                 f"from the memories given; say useful=false if they do not "
                 f"explain it."))
    except (LLMError, TypeError, ValueError, KeyError):
        return []
    if not out.get("useful") or not out.get("text"):
        return []
    conf = RELIABILITY["abduce"] * min(1.0, len(evidence) / 4.0)
    return [Inference(claim=str(out["text"]), kind="abduce",
                      premises=[n.id for n in evidence], confidence=conf,
                      cost="model", derivation=["abduction over the workspace"])]


def project(store: Store, llm: Any, cfg: BrainConfig, now: float) -> list[Inference]:
    """What is likely to matter next.

    Open loops plus schemas plus the calendar. Deliberately the least
    confident rule here: a prediction stated too firmly is worse than no
    prediction.
    """
    if llm is None:
        return []
    loops = [n for n in store.by_kind(Kind.LOOP, limit=20) if n.invalid_at is None]
    schemas = store.by_kind(Kind.SCHEMA, limit=6)
    evidence = loops[:4] + schemas[:3]
    if len(evidence) < 2:
        return []
    from ..llm import prompts
    try:
        out = llm.structured(prompts.state_insight(
            "what's coming", evidence, now,
            hint="What is likely to need attention in the next week or two? "
                 "One thing only, and only if the memories actually support it."))
    except (LLMError, TypeError, ValueError, KeyError):
        return []
    if not out.get("useful") or not out.get("text"):
        return []
    return [Inference(claim=str(out["text"]), kind="project",
                      premises=[n.id for n in evidence],
                      confidence=RELIABILITY["project"], cost="model",
                      derivation=["projection over open loops and schemas"])]


# ----------------------------------------------------------------- the API

def cheap(store: Store, ws: Workspace, cfg: BrainConfig,
          now: float) -> list[Inference]:
    """Everything derivable from the graph alone, over what just lit up.

    Microseconds. Safe in the hot path, which is the point: an inference that
    costs a model call cannot ride along with every turn, and one that costs
    nothing can.
    """
    anchors = ws.ids()
    if not anchors:
        return []
    out: list[Inference] = []
    out += contrast(store, anchors, cfg, now)
    out += transitive(store, anchors, cfg, max_depth=cfg.inference_max_depth)
    out += inherit(store, anchors, cfg)
    out += gap(store, anchors, cfg)
    return dedupe(out, cfg)


def expensive(store: Store, llm: Any, ws: Workspace, cfg: BrainConfig,
              now: float, *, observation: str = "") -> list[Inference]:
    """Model-backed inference. Night job, or an explicit question."""
    out: list[Inference] = []
    if observation:
        out += abduce(store, llm, ws, cfg, now, observation)
    out += project(store, llm, cfg, now)
    return dedupe(out, cfg)


def dedupe(items: Iterable[Inference], cfg: BrainConfig) -> list[Inference]:
    best: dict[str, Inference] = {}
    for item in items:
        if item.confidence < cfg.inference_min_confidence:
            continue
        prior = best.get(item.signature)
        if prior is None or item.confidence > prior.confidence:
            best[item.signature] = item
    return sorted(best.values(), key=lambda i: -i.confidence)[:cfg.inference_max_items]
