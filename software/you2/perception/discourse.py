"""What the words themselves signal, without a model call.

Four things are extracted here, and all four feed the salience gate on the
write path -- which means they have to be cheap enough to run after every
turn and robust enough to be worth running.

The self-referential encoding effect is one of the largest and most
replicated results in memory research: material encoded in relation to the
self is remembered far better than the same material encoded semantically. A
lexical scorer plus a small pattern list captures most of it for zero cost,
which is a very good trade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..clock import DAY

# ------------------------------------------------------------- self-reference

_FIRST_PERSON = re.compile(r"\b(i|i'm|i'll|i've|i'd|me|my|mine|myself|we|our)\b")

_PREFERENCE = re.compile(
    r"\b(prefer|prefers|rather|favourite|favorite|love|hate|like|dislike|"
    r"can't stand|cannot stand|enjoy|avoid|always|never|usually|tend to)\b")

_EVALUATION = re.compile(
    r"\b(think|believe|feel|reckon|realised|realized|decided|figured|"
    r"worried|convinced|regret|proud)\b")

_IDENTITY = re.compile(
    r"\b(my name|i am a|i'm a|i work|i live|i grew up|my (wife|husband|partner|"
    r"sister|brother|mother|father|mum|mom|dad|son|daughter|dog|cat))\b")

# ---------------------------------------------------------------- commitments

_COMMIT = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|i should|i need to|i have to|"
    r"i must|i want to|i ought to|remind me to|note to self|todo|to-do)\b")

_DEADLINE = re.compile(
    r"\b(by|before|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|tomorrow|tonight|next week|next month|the end of the (week|month)|"
    r"friday|end of day|eod)\b")

_CLOSURE = re.compile(
    r"\b(done|finished|sorted|handled|sent|fixed|resolved|completed|"
    r"took care of|dealt with|no longer|never mind|dropped it|called off)\b")

# ------------------------------------------------------------------- explicit

_EXPLICIT = re.compile(
    r"\b(remember (this|that)|don't forget|do not forget|note that|"
    r"keep in mind|make a note|important:|for the record|write that down|"
    r"remember i)\b")

# --------------------------------------------------------------- disfluencies

_HESITATION = re.compile(r"\b(um|uh|erm|hmm|like|sort of|kind of|i mean)\b")
_REPAIR = re.compile(r"\b(actually|no wait|sorry|i meant|scratch that|rather)\b")

# ------------------------------------------------------------------- valence
# A compact lexicon. Crude, and honest about it: it exists so valence
# degrades to something directionally right, not to be a sentiment model.

_POSITIVE = frozenset("""
good great excellent brilliant lovely happy pleased delighted glad excited
enjoy enjoyed love loved wonderful fantastic perfect helpful useful proud
relieved calm easy smooth won win success succeeded better best fun looking
forward grateful thanks thankful nice sorted
""".split())

_NEGATIVE = frozenset("""
bad awful terrible horrible hate hated annoying annoyed frustrated frustrating
angry furious sad upset worried anxious stressed stressful exhausted drained
wiped tired sick painful difficult hard struggle struggling failed failing
fail lost worse worst broken useless pointless disappointed dread hopeless
overwhelmed messy stuck late behind problem problems issue issues trouble
mistake wrong delay delayed blocked slipped mess
""".split())

_NEGATORS = frozenset({"not", "no", "never", "isn't", "wasn't", "don't",
                       "didn't", "can't", "won't", "couldn't", "hardly"})

_WORD = re.compile(r"[a-z']+")


@dataclass(slots=True)
class Commitment:
    text: str
    expected_by: float | None = None
    confidence: float = 0.6


@dataclass(slots=True)
class Discourse:
    self_reference: float = 0.0
    explicit: float = 0.0
    valence: float = 0.0
    hesitation: float = 0.0
    is_question: bool = False
    is_closure: bool = False
    commitments: list[Commitment] = field(default_factory=list)
    words: int = 0


def self_reference(text: str) -> float:
    """How much this utterance is about the speaker.

    Weighted rather than binary: "I prefer X" is a far stronger encoding cue
    than "I saw a bird", and both contain "I".
    """
    low = text.lower()
    n_words = max(1, len(_WORD.findall(low)))
    first = len(_FIRST_PERSON.findall(low))
    score = min(0.45, 0.15 * first)
    if _PREFERENCE.search(low) and first:
        score += 0.30
    if _IDENTITY.search(low):
        score += 0.35
    if _EVALUATION.search(low) and first:
        score += 0.15
    # A long anecdote with one "I" is less self-referential than a short
    # declaration that is nothing but.
    density = first / n_words
    score += min(0.15, density * 1.5)
    return max(0.0, min(1.0, score))


def explicit_marker(text: str) -> float:
    """"Remember this." Pinned to 1.0, and pinned memories never decay below
    a floor. When someone tells you to remember something, you remember it."""
    return 1.0 if _EXPLICIT.search(text.lower()) else 0.0


def valence(text: str) -> float:
    """[-1, 1]. Negation-aware over a three-word window, which catches "not
    great" and "never a problem" -- the two failures a bare lexicon makes
    most often."""
    words = _WORD.findall(text.lower())
    if not words:
        return 0.0
    score = 0
    hits = 0
    for i, w in enumerate(words):
        polarity = 1 if w in _POSITIVE else (-1 if w in _NEGATIVE else 0)
        if polarity == 0:
            continue
        window = words[max(0, i - 3):i]
        if any(n in _NEGATORS for n in window):
            polarity = -polarity
        score += polarity
        hits += 1
    if hits == 0:
        return 0.0
    return max(-1.0, min(1.0, score / (hits ** 0.5)))


def hesitation(text: str) -> float:
    words = max(1, len(_WORD.findall(text.lower())))
    n = len(_HESITATION.findall(text.lower())) + 2 * len(_REPAIR.findall(text.lower()))
    return min(1.0, n / (words ** 0.5))


def commitments(text: str, now: float) -> list[Commitment]:
    """Intentions that create an open loop.

    Open loops are the most underrated thing in the connections layer -- a
    device that says "three weeks ago you said you'd look into the Anderson
    contract; it hasn't come up since" does something no phone does.
    """
    out: list[Commitment] = []
    for clause in re.split(r"[.;!?]|\bbut\b|\band then\b", text):
        clause = clause.strip()
        if len(clause) < 8:
            continue
        low = clause.lower()
        m = _COMMIT.search(low)
        if not m:
            continue
        confidence = 0.75 if m.group(1) in ("i'll", "i will", "remind me to",
                                            "i need to", "i must") else 0.55
        expected = None
        d = _DEADLINE.search(low)
        if d:
            expected = now + _deadline_days(d.group(2)) * DAY
            confidence = min(0.95, confidence + 0.15)
        out.append(Commitment(clause, expected, confidence))
    return out


def _deadline_days(phrase: str) -> float:
    phrase = phrase.strip()
    if phrase in ("tomorrow", "tonight"):
        return 1.0
    if phrase in ("end of day", "eod"):
        return 1.0
    if phrase == "next week":
        return 7.0
    if phrase == "next month":
        return 30.0
    if "week" in phrase:
        return 7.0
    if "month" in phrase:
        return 30.0
    return 7.0            # a named weekday: within the week


def is_closure(text: str) -> bool:
    return bool(_CLOSURE.search(text.lower()))


def analyse(text: str, now: float) -> Discourse:
    low = text.lower()
    return Discourse(
        self_reference=self_reference(text),
        explicit=explicit_marker(text),
        valence=valence(text),
        hesitation=hesitation(text),
        is_question=text.strip().endswith("?") or bool(
            re.match(r"^\s*(what|when|where|who|why|how|did|do|does|is|are|was|"
                     r"were|can|could|should|would|have|has|tell me|remind me)\b", low)),
        is_closure=is_closure(text),
        commitments=commitments(text, now),
        words=len(_WORD.findall(low)),
    )
