"""Between the extractor and the store.

Nothing an extractor produces is trusted. Not the model's, not the rules'.
Both are wrong in different ways and this is where both get caught, because
a bad fact is not a small cost: it is a permanent distractor at retrieval
time, it is confidently stated back to the person, and it is unfalsifiable
once its provenance is a hallucination.

Six checks, in this order, because each is cheaper than the next:

1. **shape**      a key is `subject/predicate`, lowercase snake_case. Models
                  return `User/Coffee`, `coffeePreference`, `coffee`, and
                  `the user's coffee preference`. All four are repairable.
2. **person**     first person becomes third. These get read aloud, and "the
                  user don't like meetings" sounds broken for a reason that
                  has nothing to do with what it knows.
3. **durability** an ephemeral state, a hypothetical, a question, or a
                  report about the conversation itself is not a memory.
4. **grounding**  the claim's content words have to be in the text it cites.
                  This is the only real defence against invention: a model
                  that cannot point at the words did not read them.
5. **key**        snap to a key already in use when the claim is about the
                  same dimension. This is the whole of supersession. A fact
                  under a fresh key can never replace anything, and models
                  put the *value* in the key -- `user/pref_oat_milk` --
                  which guarantees it.
6. **dedup**      one claim per key per batch, highest confidence wins.

Everything rejected is returned with a reason and journalled. A filter you
cannot audit is a filter you cannot tune, and this one will be tuned for
months.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..substrate.embed import stem, tokenize

# --------------------------------------------------------------------- shape

_KEY_OK = re.compile(r"^[a-z][a-z0-9_]*/[a-z0-9][a-z0-9_]*$")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_JUNK = re.compile(r"[^a-z0-9_/]+")

#: Predicate words that name a *relation* rather than a dimension. Stripped
#: when comparing two keys, so `coffee_preference` and `pref_coffee` compare
#: equal -- which they should, and which no model will ever spell the same.
_RELATION_WORDS = frozenset("""
pref prefs prefer prefers preference preferences like likes liking
dislike dislikes habit habits routine state status condition
fact facts info information detail details note notes about attribute
value setting config profile data property trait
""".split())

_KEY_STOP = frozenset("""
the a an of for to in on at is are was were has have does do user users
their his her its my our
""".split())

MAX_KEY_LEN = 48


def normalise_key(raw: str, text: str = "") -> str | None:
    """Repair a key into `subject/predicate`, or give up.

    Giving up is rare and deliberate: a fact with an unusable key is dropped
    rather than filed under a guess, because a wrong key is worse than a
    missing fact -- it silently blocks the real one from ever superseding.
    """
    key = _CAMEL.sub("_", str(raw or "")).strip().lower()
    key = key.replace("'s", "").replace("'", "")
    key = re.sub(r"[\s\-.:]+", "_", key)
    key = _JUNK.sub("", key)
    key = re.sub(r"_+", "_", key).strip("_")
    if not key:
        return None

    subject, sep, predicate = key.partition("/")
    if not sep:
        # `coffee_preference` with no subject at all. If the claim is about
        # the person, that is what it is about; otherwise the first token is
        # doing subject duty.
        low = text.lower()
        if re.search(r"\b(the user|user'?s)\b", low) or not key.count("_"):
            subject, predicate = "user", key
        else:
            subject, _, predicate = key.partition("_")
    predicate = predicate.replace("/", "_").strip("_")
    subject = subject.strip("_")

    subject = "_".join(w for w in subject.split("_") if w) or "user"
    predicate = "_".join(w for w in predicate.split("_")
                         if w and w not in _KEY_STOP)
    if not predicate:
        return None
    if subject in ("i", "me", "my", "myself", "self"):
        subject = "user"
    out = f"{subject}/{predicate}"[:MAX_KEY_LEN].rstrip("_")
    return out if _KEY_OK.match(out) else None


def dimension_tokens(predicate: str) -> frozenset[str]:
    """What the predicate is *about*, with the relation words removed."""
    return frozenset(
        stem(w) for w in predicate.split("_")
        if w and w not in _RELATION_WORDS and w not in _KEY_STOP)


# ------------------------------------------------------------------- person

_PRONOUNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*i\s+am\b", re.I), "the user is"),
    (re.compile(r"^\s*i'm\b", re.I), "the user is"),
    (re.compile(r"^\s*i've\b", re.I), "the user has"),
    (re.compile(r"^\s*i'll\b", re.I), "the user will"),
    (re.compile(r"^\s*i'd\b", re.I), "the user would"),
    (re.compile(r"\bi'm\b", re.I), "the user is"),
    (re.compile(r"\bi've\b", re.I), "the user has"),
    (re.compile(r"\bi'll\b", re.I), "the user will"),
    (re.compile(r"\bi'd\b", re.I), "the user would"),
    (re.compile(r"\bmyself\b", re.I), "themselves"),
    (re.compile(r"^\s*my\b", re.I), "the user's"),
    (re.compile(r"\bmy\b", re.I), "the user's"),
    (re.compile(r"^\s*i\b", re.I), "the user"),
    (re.compile(r"\bi\b"), "the user"),
)

#: After "the user", these need agreement. Deliberately a list rather than a
#: rule: English third-person -s is regular, and the irregulars are exactly
#: the verbs that show up in personal claims.
_IRREGULAR_AGREE = {"be": "is", "am": "is", "are": "is", "have": "has",
                    "do": "does", "go": "goes", "can": "can", "will": "will",
                    "would": "would", "should": "should", "must": "must",
                    "cannot": "cannot", "was": "was", "were": "was"}

_NO_AGREE = frozenset("""
is has does was were had will would should must can could may might goes
prefers likes loves hates enjoys avoids wants needs works lives drinks takes
uses buys keeps thinks feels no not never always usually often the a an
""".split())

#: Bare verbs that take third-person -s after "the user". A closed list on
#: purpose: inflecting anything that follows turns the malformed "the user
#: coffee" into the worse "the user coffees", so an unrecognised word is left
#: exactly as it came.
_BARE_VERBS = frozenset("""
prefer like love hate enjoy avoid dislike want need work live drink eat take
use buy keep think feel own owe play run walk cycle drive cook sleep read
write watch visit meet call send book start stop finish get make wear order
travel commute study teach train hope plan remember forget worry care
""".split())

_AFTER_USER = re.compile(r"\bthe user(?:'s)?\s+([a-z']+)", re.I)


def to_third_person(text: str) -> str:
    out = text.strip()
    for pattern, replacement in _PRONOUNS:
        out = pattern.sub(replacement, out)
    out = re.sub(r"\bthe user's\s+(is|are|am)\b", "the user is", out, flags=re.I)

    def agree(m: re.Match[str]) -> str:
        verb = m.group(1)
        low = verb.lower()
        if low in _NO_AGREE or "'" in low:
            return m.group(0)
        fixed = _IRREGULAR_AGREE.get(low)
        if fixed is None and low in _BARE_VERBS:
            fixed = low + ("es" if low.endswith(("s", "x", "z", "ch", "sh"))
                           else "s")
        if fixed is None:
            # Only inflect what is plausibly the main verb: a following noun
            # ("the user coffee") is a malformed claim, not a conjugation
            # problem, and inflecting it makes it worse.
            return m.group(0)
        return m.group(0).replace(verb, fixed)

    out = _AFTER_USER.sub(agree, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:1].lower() + out[1:] if out[:1].isupper() and \
        not out.startswith(("I ", "I'")) else out


_REPORTED = re.compile(
    r"^\s*(?:the user|they|he|she)\s+(?:said|says|told (?:me|friday|the assistant)|"
    r"mentioned that|stated that|explained that|noted that)\s+(?:that\s+)?",
    re.I)

_META = re.compile(
    r"^\s*the user\s+(asked|is asking|wondered|wonders|enquired|inquired|"
    r"queried|wanted to know|checked|is checking|discussed|talked)\b", re.I)


def unwrap_reported(text: str) -> str:
    """"the user said he prefers X" is a fact about X wearing a wrapper."""
    out = _REPORTED.sub("", text).strip()
    if out and out is not text:
        out = re.sub(r"^\s*(?:he|she|they)\b", "the user", out, flags=re.I)
        out = to_third_person(out)
    return out or text


# --------------------------------------------------------------- durability

_EPHEMERAL = re.compile(
    r"\b(today|tonight|this morning|this afternoon|this evening|"
    r"right now|at the moment|just now|earlier today|currently|at present|"
    r"for now|so far today|this minute)\b", re.I)

#: States that are true for an afternoon. Rejected only when nothing marks
#: them as recurring: "drained again after every Tuesday meeting" is a real
#: pattern and the most valuable thing in the corpus.
_TRANSIENT = re.compile(
    r"\b(tired|shattered|knackered|exhausted|drained|wiped|hungry|thirsty|"
    r"running late|late for|ill|unwell|sick|busy|swamped|annoyed|stressed|"
    r"in a meeting|on a call|on the train|on the way|about to)\b", re.I)

_RECURRING = re.compile(
    r"\b(always|usually|every|each|keeps|again|tends|generally|often|never|"
    r"repeatedly|most (?:days|weeks|mornings|evenings)|after every|whenever)\b",
    re.I)

_HYPOTHETICAL = re.compile(
    r"(^\s*if\b|\bthe user (might|may|is considering|is thinking (about|of)|"
    r"would (?:have to|need to|like to)|could)\b|\bnot sure\b|\bunsure\b|"
    r"\bif (?:it|they|this|that) (?:comes|works|happens|goes)\b|"
    r"\bhypothetical|\bperhaps\b|\bpossibly\b)", re.I)

_WORLD = re.compile(
    r"^\s*(?:the\s+)?[A-Za-z][\w'-]*(?:\s+[\w'-]+){0,2}\s+"
    r"(?:is|are|was|were)\s+(?:a|an|the)\s+\w+", re.I)

_ASSISTANT = re.compile(r"^\s*(?:friday|the assistant|the device|you)\b", re.I)

MIN_CONTENT = 2


def undurable(text: str) -> str | None:
    """Why this claim is not a memory, or None if it is one."""
    body = text.strip()
    if not body:
        return "empty"
    if body.endswith("?"):
        return "a question, not a claim"
    if _META.search(body):
        return "about the conversation, not the person"
    if _ASSISTANT.match(body):
        return "about the assistant"
    if _HYPOTHETICAL.search(body):
        return "hypothetical"
    if _EPHEMERAL.search(body):
        return "true today, not tomorrow"
    if _TRANSIENT.search(body) and not _RECURRING.search(body):
        return "a passing state"
    if len(tokenize(body)) < MIN_CONTENT:
        return "too short to be a claim"
    return None


# ---------------------------------------------------------------- grounding

#: Words a claim acquires from being *written as a claim*, which are
#: therefore not evidence that it was read from anything.
_BOILERPLATE = frozenset("""
user users the a an is are was were has have had does do did will would
their his her its them they he she it that this to of in on at for with
from by and or but not no prefers prefer likes like
""".split())

#: Measured rather than chosen. On the extraction corpus run through a model
#: that invents (`eval/sloppy.py`), the two populations do not overlap at all:
#: every fabricated claim scores **0.00** and every genuine one **0.50 or
#: above**. A floor of 0.5 would sit exactly on the lowest true positive, so
#: it goes in the gap instead, where a slightly looser paraphrase still
#: survives and nothing unsupported does.
#:
#: What this catches is *wholesale* invention, which is the common failure.
#: It cannot catch embellishment -- a real fact with an invented detail
#: welded on scores well, because most of its words are genuinely there.
#: That is what the `quote` field is for: a model that returns the span it
#: read gets checked against the span, and cannot embellish inside it.
GROUND_FLOOR = 0.34


def grounding(text: str, source: str, quote: str = "") -> float:
    """How much of the claim is actually in the text it cites.

    Not a string match: extraction paraphrases, and it should. This asks
    whether the *content* of the claim can be found in the source, stemmed
    and unordered.

    A `quote` makes this **stricter, not looser**, and getting that direction
    right matters. A verbatim span proves the model read something real; it
    proves nothing about whether the claim follows from it. So a quote is
    first checked against the source — a fabricated one scores zero outright,
    which is the worst case there is — and then the claim is graded against
    the quote alone rather than the whole batch. A model that copies a real
    sentence and then asserts something it does not say gets caught by the
    narrowing, and could not be caught any other way.
    """
    if quote:
        needle = " ".join(tokenize(quote))
        haystack = " ".join(tokenize(source))
        if not needle or needle not in haystack:
            return 0.0
        source = quote
    claim = {stem(w) for w in tokenize(text) if w not in _BOILERPLATE}
    if not claim:
        return 0.0
    have = {stem(w) for w in tokenize(source)}
    return len(claim & have) / len(claim)


# ------------------------------------------------------------ key canonicals

@dataclass(slots=True)
class Snap:
    frm: str
    to: str
    why: str


def head_noun(text: str, source: str) -> str | None:
    """The common noun a claim is *about*, if it has one.

    "only ever buys semi-skimmed milk" and "has switched to oat milk" are the
    same dimension and no key derivation will ever spell them the same, so
    the dimension has to come from the claim rather than from the key.
    English noun phrases are head-final, so it is the last content word
    before the first prepositional attachment.

    Proper nouns are excluded, and that exclusion is the whole safety of this
    mechanism: "works at Fenwick" and "is at Lumen now" would otherwise be
    two different dimensions rather than two values of one, which is the
    exact error it exists to prevent.
    """
    from .rules import topic
    body = re.sub(r"^\s*the user'?s?\s*", "", text.strip(), flags=re.I)
    words = tokenize(body)
    while words and words[0] in _NO_AGREE:
        words = words[1:]
    if not words:
        return None
    head = topic(" ".join(words))
    if not head or len(head) < 3:
        return None
    # Case-insensitively, or a capitalised name never reaches the
    # capitalisation test at all: `head` has been stemmed and lowercased, so
    # a case-sensitive search for "lumen" in "I'm at Lumen now" finds nothing
    # and the guard silently passes every name it exists to catch.
    found = list(re.finditer(rf"\b({re.escape(head)}\w*)\b", source, re.I))
    if not found:
        return head           # stemming moved it; no capitalisation to read
    for m in found:
        if m.group(1)[:1].isupper() and m.start() > 0:
            return None       # capitalised mid-sentence: a name, not a dimension
    return head


def canonical_key(key: str, text: str, source: str,
                  known: Iterable[str]) -> tuple[str, Snap | None]:
    """Reuse an existing key when the claim is about the same dimension.

    Two signals, both requiring the subject to match:

      containment  the dimension tokens of one predicate contain the other's.
                   Catches `pref_milk` against `prefers_oat_milk` -- the
                   model putting the value in the key.
      head noun    both claims are about the same common noun. Catches
                   `pref_semi_skimmed` against `pref_oat_milk`, where the
                   keys share nothing at all.
    """
    known = [k for k in known if k and "/" in k]
    if key in known:
        return key, None
    subject, _, predicate = key.partition("/")
    mine = dimension_tokens(predicate)
    head = head_noun(text, source)

    best: tuple[int, str, str] | None = None
    for other in known:
        osub, _, opred = other.partition("/")
        if osub != subject:
            continue
        theirs = dimension_tokens(opred)
        if mine and theirs and (mine <= theirs or theirs <= mine):
            extra = len(mine ^ theirs)
            if extra <= 2:
                score = 100 - extra
                if best is None or score > best[0]:
                    best = (score, other, "same dimension tokens")
        if head and head in {stem(w) for w in opred.split("_")}:
            if best is None or best[0] < 50:
                best = (50, other, f"both about “{head}”")
    if best is None:
        return key, None
    return best[1], Snap(key, best[1], best[2])


# --------------------------------------------------------------------- entry

_BAD_ENTITY = frozenset("""
i me my myself we us you he she it they them user the assistant friday
someone somebody anyone everyone nobody thing something anything today
tomorrow yesterday tonight monday tuesday wednesday thursday friday saturday
sunday january february march april may june july august september october
november december morning afternoon evening night week weekend month year
""".split())

_ENTITY_KINDS = frozenset({"person", "project", "place", "org", "thing"})


@dataclass(slots=True)
class Rejection:
    what: str
    text: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"what": self.what, "text": self.text[:80], "reason": self.reason}


@dataclass(slots=True)
class Clean:
    gist: str = ""
    facts: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    loops: list[dict] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    snaps: list[Snap] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"gist": self.gist, "facts": self.facts,
                "entities": self.entities, "loops": self.loops,
                "closed": self.closed}

    def report(self) -> dict[str, Any]:
        return {"kept": len(self.facts), "rejected": len(self.rejected),
                "repaired": len(self.repaired), "snapped": len(self.snaps),
                "reasons": sorted({r.reason for r in self.rejected})}


def clean(payload: dict[str, Any], *, sources: dict[int, str],
          known_keys: Sequence[str] = (), ground_floor: float = GROUND_FLOOR,
          check_grounding: bool = True) -> Clean:
    """Everything an extractor produced, filtered down to what is storable."""
    out = Clean(gist=str(payload.get("gist") or "")[:300])
    all_text = " ".join(sources.values())
    known = list(known_keys)

    for raw in payload.get("facts") or []:
        if not isinstance(raw, dict):
            out.rejected.append(Rejection("fact", str(raw)[:60], "not an object"))
            continue
        text = str(raw.get("text") or raw.get("claim") or "").strip()
        if not text:
            out.rejected.append(Rejection("fact", "", "no text"))
            continue

        original = text
        text = unwrap_reported(to_third_person(text))
        if text != original:
            out.repaired.append(f"{original} -> {text}")

        why = undurable(text)
        if why:
            out.rejected.append(Rejection("fact", text, why))
            continue

        cites = [c for c in _as_ids(raw.get("cites")) if c in sources]
        source = " ".join(sources[c] for c in cites) or all_text
        if check_grounding:
            score = grounding(text, source, str(raw.get("quote") or ""))
            if score < ground_floor:
                out.rejected.append(
                    Rejection("fact", text, f"not in the source ({score:.2f})"))
                continue

        key = normalise_key(str(raw.get("key") or ""), text)
        if key is None:
            out.rejected.append(Rejection("fact", text, "unusable key"))
            continue
        if key != str(raw.get("key") or "").strip():
            out.repaired.append(f"{raw.get('key')} -> {key}")
        key, snap = canonical_key(key, text, source, known)
        if snap is not None:
            out.snaps.append(snap)

        try:
            confidence = float(raw.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        out.facts.append({"key": key, "text": text, "cites": cites,
                          "confidence": max(0.05, min(1.0, confidence))})
        if key not in known:
            known.append(key)

    out.facts = _dedup(out.facts, out.rejected)

    for raw in payload.get("entities") or []:
        if not isinstance(raw, dict):
            continue
        name = re.sub(r"^\s*(the|a|an)\s+", "", str(raw.get("name") or "").strip(),
                      flags=re.I).strip(" .,'\"")
        if len(name) < 3 or name.lower() in _BAD_ENTITY:
            out.rejected.append(Rejection("entity", name, "not a name"))
            continue
        if all(p.lower() in _BAD_ENTITY for p in name.split()):
            out.rejected.append(Rejection("entity", name, "not a name"))
            continue
        kind = str(raw.get("kind") or "thing").lower()
        aliases = [str(a).strip().lower() for a in (raw.get("aliases") or [])
                   if str(a).strip() and str(a).strip().lower() not in _BAD_ENTITY]
        out.entities.append({
            "name": name, "kind": kind if kind in _ENTITY_KINDS else "thing",
            "aliases": sorted(set(aliases) | {name.lower()}),
            "note": str(raw.get("note") or "")[:120],
            "cites": _as_ids(raw.get("cites")),
        })
    out.entities = _merge_entities(out.entities, out.facts, out.rejected)

    for raw in payload.get("loops") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if len(text) < 6:
            continue
        if text.endswith("?"):
            out.rejected.append(Rejection("loop", text, "a question"))
            continue
        if not _is_commitment(text):
            out.rejected.append(Rejection("loop", text, "nobody committed to it"))
            continue
        days = raw.get("days")
        try:
            days = None if days is None else max(0.5, min(365.0, float(days)))
        except (TypeError, ValueError):
            days = None
        out.loops.append({"text": text, "cites": _as_ids(raw.get("cites")),
                          "days": days,
                          "confidence": _confidence(raw.get("confidence"))})

    for raw in payload.get("closed") or []:
        text = str(raw).strip()
        if len(text) >= 4:
            out.closed.append(text)
    return out


#: A commitment has a first-person subject and a modal. "someone should fix
#: the gate" is a complaint, and turning complaints into open loops is how a
#: reminder system becomes something you stop listening to.
_COMMITTED = re.compile(
    r"\b(i'?l?l|i will|i'm going to|i am going to|i should|i need to|"
    r"i have to|i must|i want to|i ought to|remind me|note to self|"
    r"the user (?:will|needs to|has to|is going to|intends to|plans to|"
    r"promised to|committed to|agreed to|said they would))\b", re.I)

_UNCOMMITTED = re.compile(
    r"^\s*(someone|somebody|anyone|people|they|we)\b", re.I)


def _is_commitment(text: str) -> bool:
    if _UNCOMMITTED.match(text):
        return False
    if _COMMITTED.search(text):
        return True
    # A bare imperative -- "book the boiler service" -- is what a model
    # returns when asked for the commitment rather than the sentence.
    return bool(re.match(r"^\s*[a-z]+\s+(the|a|an|my|his|her|their|it)\b", text,
                         re.I)) and not _UNCOMMITTED.match(text)


_OWN_NAME = re.compile(r"\bthe user'?s? name is\s+([\w'-]+)", re.I)


def _merge_entities(entities: list[dict], facts: list[dict],
                    rejected: list[Rejection]) -> list[dict]:
    """Fold "Anderson" into "David Anderson", and drop the person themselves.

    Both are the same failure: a name that should be one node becoming two.
    The user's own name is the worse case — it becomes an entity that every
    episode attaches to, and a node connected to everything is connected to
    nothing.
    """
    own = {m.group(1).lower() for f in facts
           for m in [_OWN_NAME.search(str(f.get("text") or ""))] if m}

    kept: list[dict] = []
    for e in sorted(entities, key=lambda e: -len(str(e["name"]).split())):
        name = str(e["name"])
        low = name.lower()
        if low in own:
            rejected.append(Rejection("entity", name, "this is the user"))
            continue
        mine = set(low.split())
        host = next((k for k in kept if mine <= set(str(k["name"]).lower().split())),
                    None)
        if host is not None:
            host["aliases"] = sorted(set(host["aliases"]) | set(e["aliases"]) | {low})
            host["cites"] = sorted(set(host["cites"]) | set(e["cites"]))
            if not host.get("note"):
                host["note"] = e.get("note", "")
            continue
        kept.append(e)
    return kept


def _confidence(raw: Any, default: float = 0.7) -> float:
    try:
        return max(0.05, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return default


def _as_ids(raw: Any) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for item in raw:
        try:
            nid = int(item)
        except (TypeError, ValueError):
            continue
        if nid not in out:
            out.append(nid)
    return out


def _dedup(facts: list[dict], rejected: list[Rejection]) -> list[dict]:
    best: dict[str, dict] = {}
    for f in facts:
        prior = best.get(f["key"])
        if prior is None:
            best[f["key"]] = f
            continue
        keep, drop = (f, prior) if f["confidence"] > prior["confidence"] \
            else (prior, f)
        keep["cites"] = sorted(set(keep["cites"]) | set(drop["cites"]))
        best[f["key"]] = keep
        rejected.append(Rejection("fact", drop["text"],
                                  f"duplicate of {keep['key']}"))
    return list(best.values())
