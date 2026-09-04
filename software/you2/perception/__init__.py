"""Perception: listening, prosody, discourse, and the encoding gate."""

from __future__ import annotations

from .discourse import Commitment, Discourse, analyse, commitments, valence
from .listening import Attention, Orienting, State, Turn
from .prosody import Baseline, Prosody, extract, text_arousal
from .salience import Salience, evaluate, goal_relevance, novelty, surprise

__all__ = [
    "Attention", "Baseline", "Commitment", "Discourse", "Orienting", "Prosody",
    "Salience", "State", "Turn", "analyse", "commitments", "evaluate",
    "extract", "goal_relevance", "novelty", "surprise", "text_arousal",
    "valence",
]
