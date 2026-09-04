"""Rule-based extraction: the offline fallback, and the test substrate.

This exists for two reasons and both matter.

**Offline.** On the API build, no network means consolidation cannot run. A
device whose memory silently stops improving when the Wi-Fi drops is worse
than one that degrades visibly. These rules keep facts, entities and open
loops flowing at reduced quality until the network returns, and the night job
marks what it extracted so a later pass can upgrade it.

**Determinism.** The eval harness runs two simulated years and asks whether
retrieval precision holds. That question is about consolidation, supersession,
downscaling and decay -- not about how good an 8B model is at reading. Putting
a stochastic model in the middle of that measurement would make the result
unreproducible and would answer a different question.

---

**The key derivation is the interesting part.** A fact key must capture the
*dimension* a claim is about, not its value, or supersession never fires:
"I drink cow milk" and "I drink oat milk" have to produce the same key or the
store keeps both forever.

The heuristic is to take the **last content word of the object phrase** as the
topic. "cow milk" and "oat milk" both give `milk`; "strong coffee" gives
`coffee`. It works because English noun phrases are head-final -- modifiers
come before the head. It fails on postmodified phrases ("the milk from the
farm shop"), which is a known and acceptable hole: the real extractor is given
the existing key list and told to reuse it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..perception.discourse import commitments, is_closure
from ..substrate.embed import stem, tokenize

# Words that start a sentence and are capitalised for that reason alone.
_SENTENCE_STARTERS = frozenset("""
i i'll i've i'm i'd we we'll we've we're they they'll they're he he's she she's
it it's the a an and but so you my our their this that these those
there here when where what why how if then now yes no ok okay well just also
after before while because although since until during despite though
maybe perhaps actually anyway honestly basically apparently
""".split())

# Capitalised, and never a person, project or place. Without this, "Friday" and
# "March" become entities that accumulate episodes and hub the whole graph.
_NEVER_ENTITY = frozenset("""
monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november
december today tomorrow yesterday tonight morning afternoon evening night
week weekend month year spring summer autumn fall winter christmas easter
ok okay yeah yep nope thanks hi hello hey sorry please
""".split())

_ENTITY_HEAD = re.compile(
    r"\b(?:the\s+)?([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*)"
    r"(?:\s+(contract|project|account|deal|report|review|migration|launch))?")

_COMMON_NOUN_ENTITY = re.compile(
    r"\bthe\s+([a-z][\w-]*(?:\s+[a-z][\w-]*)?)\s+"
    r"(contract|project|wall|boiler|car|garage|roof|deck|kitchen|garden|"
    r"account|migration|deadline|review|rota)\b")

_PREF = re.compile(
    r"\bi\s+(?:really\s+|quite\s+|generally\s+)?"
    r"(prefer|like|love|hate|enjoy|avoid|can't stand|cannot stand|"
    r"don't like|do not like|dislike)\s+(.{2,60}?)(?:[.,;!?]|$)")

#: First person to third person. These facts get read aloud, and "the user
#: don't like meetings" is the kind of thing that makes a device sound broken
#: for a reason that has nothing to do with what it knows.
_THIRD_PERSON: dict[str, str] = {
    "can't stand": "cannot stand", "cannot stand": "cannot stand",
    "don't like": "does not like", "do not like": "does not like",
}


def third_person(verb: str) -> str:
    fixed = _THIRD_PERSON.get(verb)
    if fixed is not None:
        return fixed
    return verb if verb.endswith("s") else verb + "s"

_HABIT = re.compile(
    r"\bi\s+(always|never|usually|tend to|generally|mostly)\s+(.{3,60}?)(?:[.,;!?]|$)")

_STATE = re.compile(
    r"\bi(?:'m| am)\s+(allergic to|scared of|terrible at|good at|bad at|"
    r"useless at|out of|done with)\s+(.{2,50}?)(?:[.,;!?]|$)")

_WORK = re.compile(r"\bi\s+(?:work|working)\s+(?:at|for)\s+(.{2,40}?)(?:[.,;!?]|$)")
_LIVE = re.compile(r"\bi\s+live\s+in\s+(.{2,40}?)(?:[.,;!?]|$)")
_NAME = re.compile(r"\bmy name(?:'s| is)\s+(.{2,30}?)(?:[.,;!?]|$)")

_RELATION = re.compile(
    r"\bmy\s+(wife|husband|partner|sister|brother|mother|father|mum|mom|dad|"
    r"son|daughter|boss|neighbour|neighbor|dog|cat|landlord|doctor|dentist)\s+"
    r"(.{3,60}?)(?:[.,;!?]|$)")

_DECISION = re.compile(
    r"\bi(?:'ve| have)?\s*decided\s+(?:to\s+)?(.{3,60}?)(?:[.,;!?]|$)")

_THING_STATE = re.compile(
    r"\bthe\s+([a-z][\w -]{2,30}?)\s+(?:is|are|needs|need|has|have|keeps)\s+"
    r"(.{3,50}?)(?:[.,;!?]|$)")

# Episodes join their turns with " / ", so a pattern that spans the join
# invents facts nobody said: "the garden wall is moving along / actually I
# prefer oat milk now" produced the fact `garden_wall/state = "prefer oat milk
# now"`. Clause-splitting first is not an optimisation, it is correctness.
_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*/\s*|\s*\|\s*|;\s*")


def slug(text: str, limit: int = 3) -> str:
    words = [w for w in tokenize(text)][:limit]
    return "_".join(words) if words else "x"


def topic(phrase: str) -> str:
    """The dimension a claim is about.

    Last content word: English noun phrases are head-final, so "cow milk" and
    "oat milk" both reduce to `milk` and a later preference can replace an
    earlier one instead of sitting beside it.
    """
    words = tokenize(phrase)
    if not words:
        return "x"
    return stem(words[-1])


@dataclass(slots=True)
class Extracted:
    gist: str = ""
    facts: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    loops: list[dict] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"gist": self.gist, "facts": self.facts, "entities": self.entities,
                "loops": self.loops, "closed": self.closed}


def find_entities(text: str) -> list[dict]:
    out: dict[str, dict] = {}

    for m in _COMMON_NOUN_ENTITY.finditer(text):
        name = f"{m.group(1)} {m.group(2)}".strip()
        canonical = name.title()
        out.setdefault(canonical.lower(), {
            "name": canonical, "kind": "thing",
            "aliases": sorted({name.lower(), canonical.lower(), m.group(1).lower()}),
        })

    for sentence in _SPLIT.split(text):
        for i, m in enumerate(_ENTITY_HEAD.finditer(sentence)):
            name = m.group(1).strip()
            head = m.group(2)
            first_word = name.split()[0].lower()
            # A capitalised word at the start of a sentence is not evidence.
            at_start = m.start() <= 1
            if first_word in _SENTENCE_STARTERS and (at_start or len(name.split()) == 1):
                continue
            if len(name) < 3:
                continue
            parts_lower = [p.lower() for p in name.split()]
            if all(p in _NEVER_ENTITY for p in parts_lower):
                continue
            if len(parts_lower) == 1 and "'" in parts_lower[0]:
                continue        # a contraction that escaped the starter list
            if head:
                name = f"{name} {head}"
            key = name.lower()
            aliases = {key}
            parts = name.split()
            if len(parts) > 1:
                aliases.add(parts[0].lower())
                aliases.add(parts[-1].lower())
            kind = "person" if len(parts) == 2 and not head else \
                ("project" if head else "org")
            existing = out.get(key)
            if existing:
                existing["aliases"] = sorted(set(existing["aliases"]) | aliases)
            else:
                out[key] = {"name": name, "kind": kind, "aliases": sorted(aliases)}
    return list(out.values())


def find_facts(text: str) -> list[dict]:
    facts: list[dict] = []

    def add(key: str, claim: str, confidence: float = 0.7) -> None:
        facts.append({"key": key, "text": claim, "confidence": confidence})

    for clause in _SPLIT.split(text):
        _facts_in_clause(clause.strip().lower(), add)

    # De-duplicate by key, keeping the most confident.
    best: dict[str, dict] = {}
    for f in facts:
        prior = best.get(f["key"])
        if prior is None or f["confidence"] > prior["confidence"]:
            best[f["key"]] = f
    return list(best.values())


def _facts_in_clause(low: str, add) -> None:
    if len(low) < 6:
        return

    m = _NAME.search(low)
    if m:
        add("user/name", f"the user's name is {m.group(1).strip()}", 0.95)

    m = _WORK.search(low)
    if m:
        add("user/employer", f"the user works at {m.group(1).strip()}", 0.85)

    m = _LIVE.search(low)
    if m:
        add("user/location", f"the user lives in {m.group(1).strip()}", 0.85)

    for m in _PREF.finditer(low):
        verb, obj = m.group(1), m.group(2).strip()
        add(f"user/pref_{topic(obj)}",
            f"the user {third_person(verb)} {obj}", 0.8)

    for m in _STATE.finditer(low):
        state, obj = m.group(1), m.group(2).strip()
        add(f"user/{slug(state, 2)}_{topic(obj)}", f"the user is {state} {obj}", 0.85)

    for m in _HABIT.finditer(low):
        adverb, rest = m.group(1), m.group(2).strip()
        add(f"user/habit_{topic(rest)}", f"the user {adverb} {rest}", 0.7)

    for m in _RELATION.finditer(low):
        rel, rest = m.group(1), m.group(2).strip()
        add(f"user/{rel}", f"the user's {rel} {rest}", 0.8)

    for m in _DECISION.finditer(low):
        rest = m.group(1).strip()
        add(f"user/decision_{topic(rest)}", f"the user has decided to {rest}", 0.8)

    for m in _THING_STATE.finditer(low):
        subject, rest = m.group(1).strip(), m.group(2).strip()
        if subject in ("user", "thing", "same", "other", "first", "last"):
            continue
        add(f"{slug(subject, 2)}/state", f"the {subject} {rest}", 0.65)


def extract(texts: Iterable[str], ids: Iterable[int], now: float) -> Extracted:
    texts = list(texts)
    ids = list(ids)
    out = Extracted()
    seen_entities: dict[str, dict] = {}

    for text, nid in zip(texts, ids):
        for f in find_facts(text):
            f["cites"] = [nid]
            out.facts.append(f)
        for e in find_entities(text):
            key = e["name"].lower()
            prior = seen_entities.get(key)
            if prior:
                prior["cites"].append(nid)
                prior["aliases"] = sorted(set(prior["aliases"]) | set(e["aliases"]))
            else:
                e["cites"] = [nid]
                e["note"] = ""
                seen_entities[key] = e
        for c in commitments(text, now):
            days = None
            if c.expected_by is not None:
                days = round((c.expected_by - now) / 86400.0, 1)
            out.loops.append({"text": c.text, "cites": [nid], "days": days,
                              "confidence": c.confidence})
        if is_closure(text):
            out.closed.append(text)

    out.entities = list(seen_entities.values())

    # Merge duplicate keys across the batch, newest wins, citations union.
    merged: dict[str, dict] = {}
    for f in out.facts:
        prior = merged.get(f["key"])
        if prior is None:
            merged[f["key"]] = f
        else:
            prior["cites"] = sorted(set(prior["cites"]) | set(f["cites"]))
            if f["confidence"] >= prior["confidence"]:
                prior["text"] = f["text"]
    out.facts = list(merged.values())

    out.gist = _gist(texts)
    return out


def _gist(texts: list[str]) -> str:
    """Frequency-weighted keyword summary. Not a summary in any real sense --
    it is a retrieval handle, and the real extractor writes a proper one."""
    counts: dict[str, int] = {}
    for t in texts:
        for w in tokenize(t):
            counts[w] = counts.get(w, 0) + 1
    top = sorted(counts, key=lambda w: (-counts[w], w))[:8]
    return ", ".join(top)


def relation(existing_text: str, candidate_text: str) -> str:
    """Rule-based supersession judgement.

    Conservative by design: when the two could both be true, `distinct` is the
    answer. Wrongly declaring a conflict costs a question the user did not
    need to be asked, and asking pointless questions is how the whole feature
    gets ignored.
    """
    a, b = set(tokenize(existing_text)), set(tokenize(candidate_text))
    if not a or not b:
        return "distinct"
    overlap = len(a & b) / max(1, len(a | b))
    if overlap > 0.85:
        return "restatement"
    negated = _negation_flip(existing_text, candidate_text)
    if negated:
        return "reversal"
    if overlap >= 0.4:
        # Same dimension, different value: the newer statement wins.
        return "reversal" if _values_differ(a, b) else "refinement"
    if overlap >= 0.2:
        return "refinement"
    return "distinct"


_NEG = re.compile(r"\b(not|no longer|never|stopped|quit|isn't|doesn't|don't)\b")


def _negation_flip(a: str, b: str) -> bool:
    return bool(_NEG.search(b.lower())) != bool(_NEG.search(a.lower()))


def _values_differ(a: set[str], b: set[str]) -> bool:
    return bool(a - b) and bool(b - a)
