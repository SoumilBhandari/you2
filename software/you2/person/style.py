"""Style: how they talk, and therefore how to talk back.

Every assistant sounds the same to everybody. A device that has heard six
months of one person has no excuse for that.

Nothing here is a personality setting or a persona the user picks. It is
measured from their own turns — length, hedging, questions, formality,
profanity, how much they narrate versus state — and turned into instructions
for the reply. Someone who speaks in six-word sentences should not be answered
in paragraphs.

Deliberately conservative. Style is the part of a personal model that is most
tempting to over-fit and most obvious when it is wrong: a device that mimics
your swearing after hearing it twice is unsettling, not personal. Everything
needs sustained evidence, and the instruction generated is always a
*constraint* on the assistant's own voice rather than an impersonation of the
user's.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.embed import tokenize
from ..substrate.neuron import Kind, Tier
from ..substrate.store import Store

MIN_TURNS = 25

_WORD = re.compile(r"[A-Za-z']+")
_HEDGE = re.compile(
    r"\b(maybe|perhaps|probably|possibly|might|sort of|kind of|i think|"
    r"i guess|i suppose|roughly|about|around|fairly|quite)\b", re.I)
_INTENSE = re.compile(
    r"\b(really|very|extremely|absolutely|completely|totally|so|utterly)\b", re.I)
_FORMAL = re.compile(
    r"\b(however|therefore|regarding|furthermore|accordingly|nevertheless|"
    r"consequently|shall|whom)\b", re.I)
_CASUAL = re.compile(
    r"\b(yeah|yep|nah|gonna|wanna|kinda|dunno|cheers|mate|bloody|damn)\b", re.I)
_NARRATIVE = re.compile(r"\b(then|after that|so i|and then|eventually|later)\b", re.I)


@dataclass
class Style:
    turns: int = 0
    words_mean: float = 0.0
    words_median: float = 0.0
    question_rate: float = 0.0
    hedge_rate: float = 0.0
    intensifier_rate: float = 0.0
    formality: float = 0.0
    """[-1, 1]. Negative is casual, positive is formal."""
    narrative_rate: float = 0.0
    vocabulary: float = 0.0
    """Type-token ratio over the sample: how varied their word choice is."""

    @property
    def reliable(self) -> bool:
        return self.turns >= MIN_TURNS

    @property
    def terse(self) -> bool:
        return self.reliable and self.words_median <= 9.0

    @property
    def expansive(self) -> bool:
        return self.reliable and self.words_median >= 24.0

    def instruction(self) -> str:
        """One line of guidance for the reply prompt.

        A constraint on the assistant's voice, never an impersonation of
        theirs. Empty until there is enough evidence, which is the right
        default: the generic voice is fine, and a badly-fitted one is not.
        """
        if not self.reliable:
            return ""
        bits: list[str] = []
        if self.terse:
            bits.append("they speak in short sentences — match that, one or two lines")
        elif self.expansive:
            bits.append("they give context when they talk — a little more is welcome")
        if self.formality < -0.25:
            bits.append("casual register, contractions are fine")
        elif self.formality > 0.25:
            bits.append("they are precise and formal; do not be chatty")
        if self.hedge_rate > 0.35:
            bits.append("they hedge; do not overstate certainty back at them")
        elif self.hedge_rate < 0.08:
            bits.append("they are direct; be direct")
        if self.question_rate > 0.45:
            bits.append("they mostly ask rather than tell; answer the question asked")
        return "; ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {"turns": self.turns, "words_median": round(self.words_median, 1),
                "question_rate": round(self.question_rate, 3),
                "hedge_rate": round(self.hedge_rate, 3),
                "formality": round(self.formality, 3),
                "vocabulary": round(self.vocabulary, 3),
                "reliable": self.reliable, "instruction": self.instruction()}


def build(store: Store, cfg: BrainConfig, now: float,
          window_days: float = 180.0, limit: int = 800) -> Style:
    """Measure style from the person's own turns.

    Episodes only, and only ones the person actually said — a document import
    would otherwise teach the device that they write like a specification,
    which is exactly the kind of over-fit that makes this feel wrong.
    """
    texts: list[str] = []
    for ep in store.by_kind(Kind.EPISODE, limit=limit,
                            tiers=(Tier.HOT, Tier.WARM), live_only=False,
                            order="created_at DESC"):
        if ep.source_ref == "document":
            continue
        if (now - ep.created_at) / DAY > window_days:
            continue
        for turn in ep.text().split(" / "):
            turn = turn.strip()
            if turn:
                texts.append(turn)

    out = Style(turns=len(texts))
    if not texts:
        return out

    lengths = [len(_WORD.findall(t)) for t in texts]
    out.words_mean = statistics.fmean(lengths)
    out.words_median = statistics.median(lengths)
    out.question_rate = sum(1 for t in texts if t.rstrip().endswith("?")) / len(texts)
    out.hedge_rate = sum(1 for t in texts if _HEDGE.search(t)) / len(texts)
    out.intensifier_rate = sum(1 for t in texts if _INTENSE.search(t)) / len(texts)
    out.narrative_rate = sum(1 for t in texts if _NARRATIVE.search(t)) / len(texts)

    formal = sum(1 for t in texts if _FORMAL.search(t))
    casual = sum(1 for t in texts if _CASUAL.search(t))
    total_marked = formal + casual
    out.formality = (formal - casual) / total_marked if total_marked else 0.0

    all_tokens = [w for t in texts for w in tokenize(t)]
    out.vocabulary = len(set(all_tokens)) / len(all_tokens) if all_tokens else 0.0
    return out
