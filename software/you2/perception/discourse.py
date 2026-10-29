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



def commitments(text: str, now: float) -> list[Commitment]:
    """Intentions that create an open loop.

    Open loops are the most underrated thing in the connections layer -- a
    device that says "three weeks ago you said you'd look into the Anderson
    contract; it hasn't come up since" does something no phone does.
    """
    out: list[Commitment] = []
    parts = _clauses(text)
    for i, clause in enumerate(parts):
        if len(clause) < 8:
            continue
        low = clause.lower()
        m = _COMMIT.search(low)
        if not m:
            continue
        # The deadline is read from this clause *and everything after it*: a
        # correction arrives as its own clause ("— no wait, Wednesday") and
        # belongs to the commitment it follows. The stored text stays the
        # clause alone, so an open loop reads back as the one thing they said
        # they would do rather than the whole paragraph it was buried in.
        tail = " ".join(parts[i:])
        confidence = 0.75 if m.group(1) in ("i'll", "i will", "remind me to",
                                            "i need to", "i must") else 0.55
        expected = None
        # "I'll call Anderson on Tuesday — no wait, Wednesday." A repair
        # marker makes everything before it obsolete, so the deadline is read
        # from the last segment rather than the first. Taking the first one
        # is not a near miss: it books the reminder on the day they explicitly
        # corrected, which reads as not listening.
        d = _last_deadline(tail.lower())
        text = clause
        if d:
            expected = now + _deadline_days(d) * DAY
            confidence = min(0.95, confidence + 0.15)
            # And the correction goes into the text, not just the date.
            # Storing "you said you'd call Anderson on Tuesday" with a
            # Wednesday reminder attached is worse than either error alone:
            # the loop reads back the day they explicitly took back.
            own = _BARE_DAY.search(low)
            if own and own.group(1) != d:
                said = clause[own.start():own.end()]
                text = re.sub(rf"\b{own.group(1)}\b",
                              d.title() if said[:1].isupper() else d,
                              clause, count=1, flags=re.IGNORECASE)
        # The correction has been applied, so the words that carried it are
        # noise: "I'll do the drawings Thursday, actually make that Thursday"
        # is what happens if they stay.
        cut = _AFTER_REPAIR.search(text)
        if cut is not None and cut.start() > 8:
            text = text[:cut.start()].rstrip(" ,;:—–-")
        out.append(Commitment(text, expected, confidence))
    return out


#: Clause boundaries, for isolating the one thing they committed to. Defined
#: here rather than shared with the extractor's splitter because perception
#: sits below the model boundary and must not import across it.
_CLAUSE = re.compile(
    r"[.;!?]+\s*|\s+[—–]\s+|\s*/\s*|\bbut\b|\band then\b"
    r"|,\s+(?:and\s+|so\s+|then\s+)?(?=i\b|i'|we\b|we')", re.IGNORECASE)


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE.split(text) if c and c.strip()]


_AFTER_REPAIR = re.compile(
    r"\b(?:no wait|wait no|actually|sorry|i meant|scratch that|make that|"
    r"rather|no,)\b", re.IGNORECASE)

_BARE_DAY = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|"
    r"tonight|next week|next month)\b")


def _last_deadline(low: str) -> str | None:
    """The deadline as finally stated, after any self-correction.

    The corrected day is the **first** one after the repair marker, not the
    last: "no wait, Wednesday, he's away Tuesday" ends with a reason, and
    reading the last day in the sentence books the very day being ruled out.

    With no repair marker this falls through to the ordinary pattern, which
    requires by/before/on — a bare weekday elsewhere in a sentence is usually
    when something happened, not when something is due.
    """
    segments = _AFTER_REPAIR.split(low)
    if len(segments) > 1:
        for segment in segments[1:]:
            found = _BARE_DAY.search(segment)
            if found:
                return found.group(1)
    m = _DEADLINE.search(low)
    return m.group(2) if m else None


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


#: A question, with or without the punctuation. ASR does not supply question
#: marks, and auxiliary inversion is the only signal left: "do I work at
#: Lumen" still contains the substring "i work at lumen", so a pattern that
#: matches the latter matches the former too.
_INTERROGATIVE = re.compile(
    r"^\s*(what|when|where|who|whom|whose|why|how|which|did|do|does|is|are|"
    r"am|was|were|can|could|should|would|will|have|has|had|tell me|remind me)\b",
    re.IGNORECASE)


def is_question(text: str) -> bool:
    return text.strip().endswith("?") or bool(_INTERROGATIVE.match(text))


def analyse(text: str, now: float) -> Discourse:
    low = text.lower()
    return Discourse(
        self_reference=self_reference(text),
        explicit=explicit_marker(text),
        valence=valence(text),
        is_question=is_question(text),
        is_closure=is_closure(text),
        commitments=commitments(text, now),
        words=len(_WORD.findall(low)),
    )
