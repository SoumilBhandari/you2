"""Cues: turning an utterance into starting points.

Getting this right matters more than any parameter in the spread itself. Five
sources, weighted, and the weights encode a claim:

    entity mention (1.2) > semantic kNN (1.0) > conversational context (0.8)
                         > temporal (0.6) > ambient priming (0.2)

Entity match outranking embedding similarity is deliberate. If someone says a
name, that is a far stronger signal than any cosine distance, and treating it
as one avoids the failure where "Anderson" retrieves five semantically
adjacent conversations about other people.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from ..clock import DAY, day_key, part_of_day, weekday
from ..config import BrainConfig
from ..substrate.embed import tokenize
from ..substrate.neuron import Kind
from ..substrate.store import Store
from .spread import Cue

W_SEMANTIC = 1.0
W_ENTITY = 1.2
W_CONTEXT = 0.8
W_TEMPORAL = 0.6
W_AMBIENT = 0.2

SEMANTIC_K = 8
SEMANTIC_MIN = 0.12
AMBIENT_K = 4

#: Explicit time references worth resolving to a window. Deliberately small:
#: over-parsing dates produces confident retrieval of the wrong week.
_WINDOWS: dict[str, tuple[float, float]] = {
    "today": (0.0, 1.0),
    "yesterday": (1.0, 2.0),
    "this morning": (0.0, 1.0),
    "last night": (0.5, 1.5),
    "this week": (0.0, 7.0),
    "last week": (7.0, 14.0),
    "last month": (30.0, 60.0),
    "this month": (0.0, 30.0),
    "last year": (365.0, 730.0),
    "a while ago": (30.0, 365.0),
    "recently": (0.0, 14.0),
    "the other day": (1.0, 5.0),
}

_MONTHS = ("january february march april may june july august september "
           "october november december").split()

#: People say "three weeks ago" far more often than "3 weeks ago".
_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "couple": 2, "few": 3, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12,
}
_AGO = re.compile(
    r"\b(?:a\s+)?(\d+|" + "|".join(_NUMBER_WORDS) + r")\s+"
    r"(?:of\s+)?(day|week|month|year)s?\s+ago")


@dataclass(slots=True)
class CueSet:
    cues: list[Cue] = field(default_factory=list)
    mentions: list[int] = field(default_factory=list)
    """Entity neurons named outright. Used for pronoun binding and for the
    memory-answerable fast path."""
    window: tuple[float, float] | None = None
    text: str = ""

    def __len__(self) -> int:
        return len(self.cues)

    def by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.cues:
            out[c.source] = out.get(c.source, 0) + 1
        return out


def parse_window(text: str, now: float) -> tuple[float, float] | None:
    """Resolve an explicit time reference to a (start, end) unix range."""
    low = text.lower()
    for phrase, (a, b) in _WINDOWS.items():
        if phrase in low:
            return (now - b * DAY, now - a * DAY)
    m = _AGO.search(low)
    if m:
        raw = m.group(1)
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        unit = {"day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}[m.group(2)]
        centre = n * unit
        pad = max(1.0, centre * 0.25)
        return (now - (centre + pad) * DAY, now - max(0.0, centre - pad) * DAY)
    for i, month in enumerate(_MONTHS):
        if re.search(rf"\b(in|since|during|back in)\s+{month}\b", low):
            import datetime as _dt
            today = _dt.datetime.fromtimestamp(now, tz=_dt.timezone.utc)
            year = today.year if (i + 1) <= today.month else today.year - 1
            start = _dt.datetime(year, i + 1, 1, tzinfo=_dt.timezone.utc).timestamp()
            end = start + 31 * DAY
            return (start, min(end, now))
    return None


def find_mentions(store: Store, text: str) -> list[int]:
    """Alias lookup over unigrams and bigrams. A hash lookup, not a model."""
    aliases = store.alias_map()
    if not aliases:
        return []
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text.lower())
    found: list[int] = []
    seen: set[int] = set()
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            key = " ".join(words[i:i + n])
            nid = aliases.get(key)
            if nid is not None and nid not in seen:
                seen.add(nid)
                found.append(nid)
    return found


def build(store: Store, text: str, cfg: BrainConfig, *,
          context: Sequence[tuple[int, float]] = (),
          now: float | None = None) -> CueSet:
    """Assemble the cue set for one utterance.

    `context` is (neuron_id, activation) from the previous workspace, which is
    how pronouns and "that thing we talked about" resolve at all.
    """
    now = now if now is not None else store.clock.now()
    out = CueSet(text=text)
    seen: dict[int, Cue] = {}

    def add(nid: int, weight: float, source: str) -> None:
        if weight <= 0.0:
            return
        existing = seen.get(nid)
        if existing is None:
            cue = Cue(nid, weight, source)
            seen[nid] = cue
            out.cues.append(cue)
        elif weight > existing.weight:
            # Keep the strongest justification, and label it with that source.
            existing.weight = weight
            existing.source = source

    # 1. Entity mentions -- the highest-precision cue available.
    for nid in find_mentions(store, text):
        out.mentions.append(nid)
        add(nid, W_ENTITY, "entity")

    # 2. Semantic kNN over the hot tier.
    for nid, score in store.search(text, k=SEMANTIC_K, scope="hot",
                                   min_score=SEMANTIC_MIN):
        add(nid, W_SEMANTIC * score, "semantic")

    # 3. Conversational context -- what is already in the workspace.
    for nid, activation in context:
        add(nid, W_CONTEXT * min(1.0, activation), "context")

    # 4. Explicit temporal scoping.
    window = parse_window(text, now)
    if window is not None:
        out.window = window
        start, end = window
        for n in store.between(Kind.EPISODE, start, end, limit=40):
            recency = 1.0 - min(1.0, (now - n.created_at) / (5 * 365 * DAY))
            add(n.id, W_TEMPORAL * (0.5 + 0.5 * n.salience) * (0.5 + 0.5 * recency),
                "temporal")

    # 5. Ambient priming. Weak, and deliberately so -- it should tilt a close
    #    call, never decide one.
    for n in store.by_kind(Kind.EPISODE, limit=AMBIENT_K, order="created_at DESC"):
        age_h = (now - n.created_at) / 3600.0
        if age_h > 24.0:
            break
        add(n.id, W_AMBIENT * (1.0 - age_h / 24.0), "ambient")

    return out


def ambient_context(now: float) -> str:
    """A short human-readable stamp for the episode record. Cheap, and it is
    what makes 'you always seem drained after Tuesday meetings' computable."""
    return f"{weekday(now)} {part_of_day(now)} {day_key(now)}"
