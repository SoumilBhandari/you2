"""The encoding gate.

The usual approach is to ask a model "how important is this?", which produces
flattery: everything a person says sounds important out of context.

Real memory does not work that way. Encoding strength tracks **surprise** --
the gap between what your model of the world predicted and what actually
happened. Prediction errors are what disrupt hippocampal representations and
drive memory updating, and high-error events are both encoded more strongly
and act as event boundaries.

That is computable here, and free. Before encoding an utterance we have
already run retrieval on it for the hot path. If the retrieved memories
predict what was just said, surprise is low -- we knew that. If the utterance
is orthogonal to everything retrieved, surprise is high. **The retrieval we
needed anyway is the predictor.** Salience costs one dot product.

Six terms. Five are computed without a model call; the sixth is a by-product
of a call that already happened.

And note what this is *not*: a decision about what to keep permanently.
Capture stays permissive. **Salience is a replay priority, not a filter** --
which is how the hippocampus does it. Everything gets encoded; what got
tagged is what gets replayed, and what gets replayed is what survives into
long-term structure. Deciding at capture time, when you have the least
context about what matters, is the mistake.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..activation.workspace import Workspace
from ..config import BrainConfig
from ..substrate.neuron import Kind
from ..substrate.store import Store
from .discourse import Discourse, analyse
from .prosody import text_arousal


def _logistic(distance_from_ref: float, sharpness: float) -> float:
    return 1.0 / (1.0 + math.exp(-sharpness * distance_from_ref))


@dataclass(slots=True)
class Salience:
    total: float = 0.0
    surprise: float = 0.0
    arousal: float = 0.0
    self_reference: float = 0.0
    goal_relevance: float = 0.0
    novelty: float = 0.0
    explicit: float = 0.0
    decision: str = "discard"          # discard | weak | strong
    valence: float = 0.0
    discourse: Discourse | None = None
    predicted_by: int | None = None
    """The workspace neuron that best predicted this utterance. Non-obvious
    and useful: it is the thing the new memory should be linked to."""

    @property
    def keep(self) -> bool:
        return self.decision != "discard"

    @property
    def strong(self) -> bool:
        return self.decision == "strong"

    def as_dict(self) -> dict[str, float | str]:
        return {
            "total": round(self.total, 3), "surprise": round(self.surprise, 3),
            "arousal": round(self.arousal, 3),
            "self_reference": round(self.self_reference, 3),
            "goal_relevance": round(self.goal_relevance, 3),
            "novelty": round(self.novelty, 3), "explicit": round(self.explicit, 3),
            "decision": self.decision,
        }

    def __repr__(self) -> str:
        return (f"Salience({self.total:.2f} {self.decision}, "
                f"surprise={self.surprise:.2f}, arousal={self.arousal:.2f}, "
                f"self={self.self_reference:.2f})")


def surprise(store: Store, text: str, ws: Workspace | None,
             cfg: BrainConfig) -> tuple[float, int | None]:
    """Prediction error against the workspace.

    The workspace is what memory predicted would be relevant. If the utterance
    lands inside it, we already knew this.

    An empty workspace is maximum surprise, and correctly so: nothing in
    memory anticipated this at all.
    """
    if ws is None or not ws.items:
        return 1.0, None
    q = store.embed(text)
    best = -1.0
    best_id: int | None = None
    for item in ws.items:
        v = store.vectors.get(item.neuron.id)
        if v is None:
            continue
        n = float(np.linalg.norm(v))
        if n == 0.0:
            continue
        sim = float(np.dot(v / n, q))
        if sim > best:
            best, best_id = sim, item.neuron.id
    if best < 0.0:
        return 1.0, None
    return _logistic(cfg.surprise_ref_sim - best, cfg.surprise_sharpness), best_id


def novelty(store: Store, text: str, cfg: BrainConfig) -> float:
    """Is anything like this in the store at all?

    Distinct from surprise: surprise is "memory failed to predict this in
    context", novelty is "nothing resembling this exists". A recurring
    complaint is unsurprising *and* unnovel; a new topic mid-conversation is
    surprising but may not be novel.
    """
    hits = store.search(text, k=1, scope="hot")
    best = hits[0][1] if hits else 0.0
    return _logistic(cfg.novelty_ref_sim - best, cfg.novelty_sharpness)


def goal_relevance(ws: Workspace | None) -> float:
    """Does this touch an open loop or an active project?

    Read off the cascade, which has already visited them -- so this term is
    free too.
    """
    if ws is None or not ws.items:
        return 0.0
    score = 0.0
    for item in ws.items:
        n = item.neuron
        a = min(1.0, item.activation)
        if n.kind is Kind.LOOP:
            score += 1.0 * a
        elif n.kind is Kind.ENTITY and n.meta.get("status") == "active":
            score += 0.6 * a
        elif n.kind is Kind.SCHEMA:
            score += 0.3 * a
    return min(1.0, score / 0.5)


def evaluate(store: Store, text: str, cfg: BrainConfig | None = None, *,
             workspace: Workspace | None = None,
             arousal: float | None = None,
             engagement: float = 0.0,
             source: str = "speech",
             now: float | None = None) -> Salience:
    """Score one utterance. Nothing here makes a model call."""
    cfg = cfg or store.cfg
    now = now if now is not None else store.clock.now()
    d = analyse(text, now)

    s_surprise, predicted_by = surprise(store, text, workspace, cfg)
    s_novelty = novelty(store, text, cfg)
    s_arousal = arousal if arousal is not None else text_arousal(text)
    s_self = d.self_reference
    s_goal = goal_relevance(workspace)
    s_explicit = d.explicit

    # Engagement is not one of the six terms; it modulates arousal, because
    # coming straight back with a follow-up is behavioural evidence that the
    # topic mattered.
    s_arousal = min(1.0, s_arousal + 0.15 * engagement)

    total = (cfg.w_surprise * s_surprise
             + cfg.w_arousal * s_arousal
             + cfg.w_self_reference * s_self
             + cfg.w_goal_relevance * s_goal
             + cfg.w_novelty * s_novelty
             + cfg.w_explicit * s_explicit)

    # Something you wrote in a file is weaker evidence about you than
    # something you said out loud. Without this, one imported vault swamps
    # two years of conversation.
    if source == "document":
        total *= cfg.document_salience_discount

    # An explicit instruction is not a weighted contribution, it is an
    # instruction. When someone says "remember this", it is remembered.
    if s_explicit >= 1.0:
        total = max(total, cfg.salience_strong + 0.05)

    # Commitments and their resolutions are structurally important regardless
    # of how surprising they are — and a closure is *unsurprising by
    # construction*, because it is about a commitment we already recorded.
    # Left to the weighted score, "sent him the numbers, that's done" scores
    # below the floor, gets discarded, and the open loop it resolves stays
    # open forever. Same shape as the recurring-complaint problem: the gate
    # is right that we knew this, and wrong that it therefore does not matter.
    if d.commitments or d.is_closure:
        total = max(total, cfg.salience_floor + 0.05)

    # Very short acknowledgements are never worth an episode, however
    # surprising the embedder finds them. "ok" against an empty workspace
    # scores 1.0 on surprise, which is exactly the failure this catches.
    if d.words <= 2 and not d.commitments and s_explicit < 1.0:
        total = min(total, cfg.salience_floor * 0.9)

    if total < cfg.salience_floor:
        decision = "discard"
    elif total >= cfg.salience_strong:
        decision = "strong"
    else:
        decision = "weak"

    return Salience(
        total=round(total, 4), surprise=s_surprise, arousal=s_arousal,
        self_reference=s_self, goal_relevance=s_goal, novelty=s_novelty,
        explicit=s_explicit, decision=decision, valence=d.valence,
        discourse=d, predicted_by=predicted_by,
    )
