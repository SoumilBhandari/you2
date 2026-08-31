"""The global workspace.

Global workspace theory says consciousness is a limited-capacity broadcast:
many processes compete, one coalition wins, and the winner is broadcast to
everything else. That is a startlingly exact description of what a token
budget does to retrieved memory.

So the 400-token cap is not an arbitrary latency compromise -- it is the
workspace capacity, and selection is a competition rather than a truncation.
Three things follow, and all three are load-bearing:

  * Lateral inhibition, so the block carries eight *different* things.
  * A utility multiplier per kind, because a fact is denser signal per token
    than the episode it was extracted from.
  * Broadcast consequences: winning strengthens the winner and wires the
    winners to each other. That Hebbian bump is where the association graph
    actually comes from -- nobody writes those edges by hand and no extraction
    pass produces them. The structure is a by-product of use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from ..clock import DAY, iso
from ..config import BrainConfig
from ..substrate.neuron import BLOCK_ORDER, Kind, Neuron, Tier
from ..substrate.store import Store
from .baselevel import availability, on_retrieval
from .cues import CueSet
from .inhibition import suppress
from .spread import Cascade

#: Candidates pulled from the cascade before the competition. Beyond ~60 the
#: tail cannot win a slot under any weighting, so fetching them is waste.
CANDIDATE_POOL = 60

#: Characters per token. English through a BPE tokeniser runs 3.6-4.2; the
#: low end is used so the estimate errs toward over-counting and the real
#: budget is never exceeded. On device, swap in the model's own tokeniser.
CHARS_PER_TOKEN = 3.6

_HEADERS: dict[Kind, str] = {
    Kind.FACT: "known",
    Kind.LOOP: "open",
    Kind.ENTITY: "who/what",
    Kind.SCHEMA: "patterns",
    Kind.INSIGHT: "noticed",
    Kind.EPISODE: "said before",
    Kind.AFFECT: "mood",
}


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def ago(seconds: float) -> str:
    """Compact relative age. Costs three characters and makes the difference
    between a fact the model treats as current and one it treats as history."""
    d = seconds / DAY
    if d < 1.0:
        return f"{max(1, int(seconds / 3600))}h"
    if d < 14.0:
        return f"{int(d)}d"
    if d < 60.0:
        return f"{int(d / 7)}w"
    if d < 730.0:
        return f"{int(d / 30)}mo"
    return f"{d / 365:.1f}y"


@dataclass(slots=True)
class Item:
    neuron: Neuron
    activation: float
    score: float
    tokens: int
    line: str


@dataclass(slots=True)
class Workspace:
    items: list[Item] = field(default_factory=list)
    tokens: int = 0
    considered: int = 0
    cascade: Cascade | None = None
    cues: CueSet | None = None
    suppressed: list[tuple[int, int, float]] = field(default_factory=list)
    now: float = 0.0

    def ids(self) -> list[int]:
        return [i.neuron.id for i in self.items]

    def neurons(self) -> list[Neuron]:
        return [i.neuron for i in self.items]

    def of_kind(self, kind: Kind) -> list[Neuron]:
        return [i.neuron for i in self.items if i.neuron.kind is kind]

    def context(self) -> list[tuple[int, float]]:
        """Carried into the next turn's cue set. This is how follow-ups and
        pronouns work without re-deriving everything."""
        return [(i.neuron.id, i.activation) for i in self.items]

    def block(self) -> str:
        """The memory block injected into the prompt.

        Ordered by kind rather than by score: a stable ordering keeps the
        prompt prefix cacheable for longer, which is worth more than putting
        the strongest item first.
        """
        if not self.items:
            return ""
        by_kind: dict[Kind, list[Item]] = {}
        for item in self.items:
            by_kind.setdefault(item.neuron.kind, []).append(item)
        out: list[str] = []
        for kind in BLOCK_ORDER:
            group = by_kind.get(kind)
            if not group:
                continue
            out.append(f"{_HEADERS[kind]}:")
            out.extend(f"  - {i.line}" for i in group)
        return "\n".join(out)

    def explain(self) -> str:
        """Why each item is here. `you2 why` prints this, and a retrieval you
        cannot explain is one you cannot tune."""
        lines = [
            f"cues: {self.cues.by_source() if self.cues else {}}",
            f"cascade: {self.cascade!r}" if self.cascade else "cascade: none",
            f"considered {self.considered}, selected {len(self.items)}, "
            f"{self.tokens} tok",
            "",
        ]
        for item in self.items:
            n = item.neuron
            lines.append(f"[{item.score:6.3f}] {n.kind:<8} #{n.id}  {n.text()[:64]}")
            if self.cascade:
                path = self.cascade.path_to(n.id)
                if len(path) > 1:
                    hops = " -> ".join(
                        f"#{p}" + (f"({e})" if e else "") for p, e in path)
                    lines.append(f"           via {hops}")
                elif self.cascade.seeds.get(n.id):
                    lines.append(f"           seed: {self.cascade.seeds[n.id]}")
        if self.suppressed:
            lines.append("")
            lines.append("suppressed by lateral inhibition:")
            for loser, winner, sim in self.suppressed[:8]:
                lines.append(f"  #{loser} <- #{winner} (sim {sim:.2f})")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Workspace({len(self.items)} items, {self.tokens} tok)"


def render(neuron: Neuron, now: float) -> str:
    """One line per neuron. Terse: every character is budget."""
    text = neuron.text().strip().replace("\n", " ")
    if neuron.kind is Kind.FACT:
        since = neuron.valid_at or neuron.created_at
        return f"{text} [{ago(now - since)}]"
    if neuron.kind is Kind.LOOP:
        due = neuron.meta.get("expected_by")
        age = ago(now - neuron.created_at)
        overdue = ""
        if isinstance(due, (int, float)) and now > due:
            overdue = f", {ago(now - due)} overdue"
        return f"{text} (said {age} ago{overdue})"
    if neuron.kind is Kind.ENTITY:
        role = neuron.meta.get("role")
        return f"{text}" + (f" — {role}" if role else "")
    if neuron.kind is Kind.EPISODE:
        return f"{ago(now - neuron.created_at)} ago: {text}"
    if neuron.kind is Kind.INSIGHT:
        return f"{text}"
    if neuron.kind is Kind.AFFECT:
        return f"{ago(now - neuron.created_at)} ago: {text}"
    return text


def compete(store: Store, cascade: Cascade, cfg: BrainConfig, *,
            cues: CueSet | None = None, now: float | None = None,
            include_superseded: bool = False,
            budget: int | None = None) -> Workspace:
    """Run the competition and assemble the block.

    Greedy by score/token under the budget, with inhibition applied after each
    pick. Greedy is within a few percent of optimal for a knapsack this small
    and costs microseconds, which matters when the whole retrieval has 50 ms.
    """
    now = now if now is not None else store.clock.now()
    budget = budget if budget is not None else cfg.workspace_tokens
    ws = Workspace(cascade=cascade, cues=cues, now=now)

    ranked = cascade.ranked(CANDIDATE_POOL)
    if not ranked:
        return ws
    neurons = store.get_many(nid for nid, _ in ranked)

    scores: dict[int, float] = {}
    rendered: dict[int, tuple[str, int]] = {}
    for nid, activation in ranked:
        n = neurons.get(nid)
        if n is None or n.tier is Tier.TOMB:
            continue
        if not include_superseded and not n.is_live:
            continue
        line = render(n, now)
        if not line.strip():
            continue
        rendered[nid] = (line, estimate_tokens(line) + 3)   # +3 for "  - \n"
        scores[nid] = activation * n.utility
    ws.considered = len(scores)

    while scores and len(ws.items) < cfg.workspace_max_items:
        # The same firing threshold that governs the cascade governs entry to
        # the workspace. This is what lets inhibition *exclude* rather than
        # merely reorder: with spare capacity, a suppressed near-duplicate
        # would otherwise still take a slot, and the block fills with
        # paraphrases exactly as it would with no inhibition at all.
        for nid in [k for k, v in scores.items() if v < cfg.fire_threshold]:
            del scores[nid]
        if not scores:
            break
        winner = max(scores, key=lambda k: scores[k] / rendered[k][1])
        line, tokens = rendered[winner]
        score = scores.pop(winner)
        if ws.tokens + tokens > budget:
            # Too expensive for what remains, but something cheaper may fit.
            continue
        n = neurons[winner]
        ws.items.append(Item(n, cascade.activation.get(winner, 0.0), score,
                             tokens, line))
        ws.tokens += tokens
        for loser, sim in suppress(scores, winner, store.vectors.similarities,
                                   radius=cfg.inhibition_radius,
                                   strength=cfg.inhibition):
            ws.suppressed.append((loser, winner, sim))

    assert ws.tokens <= budget, "workspace exceeded its budget"
    return ws


def broadcast(store: Store, ws: Workspace, cfg: BrainConfig,
              *, now: float | None = None) -> None:
    """Winning has consequences. This is what makes it a learning system
    rather than a query planner.

    1. Each winner's access is recorded, with the desirable-difficulty rule.
    2. Every pair of co-active winners gets a Hebbian bump.

    Step 2 is where the association graph comes from. Things remembered
    together become linked, and the link is what makes them get remembered
    together next time.
    """
    now = now if now is not None else store.clock.now()
    if not ws.items:
        return

    for item in ws.items:
        n = item.neuron
        soma = store.soma.get(n.id)
        if soma is None:
            continue
        storage, count, last = on_retrieval(soma, now, cfg)
        n.storage_strength = storage
        n.access_count = count
        n.last_access = last
        store.save(n)

    strong = [i for i in ws.items if i.activation >= cfg.hebbian_min_activation]
    for a in range(len(strong)):
        for b in range(a + 1, len(strong)):
            ia, ib = strong[a], strong[b]
            store.strengthen(ia.neuron.id, ib.neuron.id,
                             min(1.0, ia.activation), min(1.0, ib.activation))

    store.log("broadcast", {
        "ids": ws.ids(), "tokens": ws.tokens, "considered": ws.considered,
        "visited": ws.cascade.visited if ws.cascade else 0,
        "at": iso(now),
    })


def availability_fn(store: Store, cfg: BrainConfig, now: float):
    """Bind the base-level term to this store and this instant."""
    soma = store.soma

    def avail(nid: int) -> float:
        s = soma.get(nid)
        return availability(s, now, cfg) if s is not None else 0.0

    return avail
