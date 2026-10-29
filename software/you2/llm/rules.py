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
from typing import Iterable, Sequence

from ..perception.discourse import commitments, is_closure, is_question
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
    r"\bi\s+(?:really\s+|quite\s+|generally\s+|only\s+ever\s+|always\s+|"
    r"usually\s+|mostly\s+|tend\s+to\s+)?"
    r"(prefer|like|love|hate|enjoy|avoid|can't stand|cannot stand|"
    r"don't like|do not like|dislike|buy|drink|take|order|use|wear|"
    r"go for|stick to)\s+(.{2,60}?)(?:[.,;!?]|$)")

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

#: "I'd rather die than sit through another all-hands." Hyperbole is a
#: preference, and the comparative is what carries it: whatever follows
#: `than` is the thing actually disliked. Taking the sentence literally
#: stores a claim about dying.
_RATHER = re.compile(
    r"\bi'?d?\s+rather\s+.{2,40}?\s+than\s+(.{3,50}?)(?:[.,;!?]|$)")

#: A standing inability is a constraint on every future suggestion, which
#: makes it one of the more useful things to know. "I can't stand X" is a
#: preference and belongs to _PREF, so those verbs are excluded here.
_CANNOT = re.compile(
    r"\bi\s+(?:really\s+)?(?:can'?t|cannot|am not able to)\s+"
    r"(?!stand|believe|help|wait|tell|remember|imagine|think|see|say)"
    r"(.{3,50}?)(?:[.,;!?]|$)")

#: "no coriander in anything, ever." A bare negative with no subject and no
#: verb. The quantifier is what makes it a standing rule rather than a
#: refusal of whatever was just offered, so it is required.
_NEVER_WANT = re.compile(
    r"^\s*no\s+([a-z][\w-]{2,25})\b(?=[^.!?]*\b"
    r"(?:ever|anything|everything|at all|any more|anymore|again)\b)",
    re.IGNORECASE)

#: "Priya's flat is in Alfama." A possessive subject names both the owner and
#: the dimension, which is a better key than anything derivable from the
#: predicate.
#: The name class excludes the apostrophe deliberately: `[\w'-]+` swallows
#: "Priya's" whole and then finds no possessive left to match.
_POSSESSIVE_STATE = re.compile(
    r"\b([A-Z][\w-]+)'s\s+([a-z][\w]{2,18}(?:\s+[a-z][\w]{2,18})?)\s+"
    r"(is|are|was|were)\s+(.{3,40}?)(?:[.,;!?]|$)")

#: The verb is captured, not consumed. Dropping it produced "the car
#: insurance on the third of March" from "the car insurance renews on the
#: third of March" -- a fact with a subject, a date, and no claim, which
#: reads as true and says nothing.
_THING_STATE = re.compile(
    r"\bthe\s+([a-z][\w -]{2,30}?)\s+"
    r"(is|are|needs|need|has|have|keeps|renews|expires|runs out|starts|"
    r"ends|arrives|comes up|goes|falls|lands)\s+"
    r"(.{3,50}?)(?:[.,;!?]|$)")

#: A change of practice. Two shapes, and the distinction matters: when the
#: dimension is named ("moved my *meetings* to the afternoon") it is the key,
#: and when it is not ("switched to oat *milk*") the head of the new value is.
_SHIFT_NAMED = re.compile(
    r"\bi(?:'ve| have)?\s+(?:just\s+|now\s+)?"
    r"(?:moved|switched|changed|shifted)\s+my\s+([\w-]{3,20})\s+to\s+"
    r"(.{2,50}?)(?:[.,;!?]|$)")
_SHIFT_BARE = re.compile(
    r"\bi(?:'ve| have)?\s+(?:just\s+|now\s+)?"
    r"(?:moved|switched|changed)\s+(?:over\s+)?to\s+(.{2,50}?)(?:[.,;!?]|$)")

#: Somebody else's preference, which must land under *their* subject. A
#: third-party opinion filed under `user/` is the most damaging extraction
#: error there is: the device tells you what you think, and is wrong.
_OTHER_PREF = re.compile(
    r"\b([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*)\s+"
    r"(?:said|says|told me)?\s*(?:that\s+)?(?:he|she|they)?\s*"
    r"(prefers|likes|hates|wants|needs|refuses|insists on|avoids)\s+"
    r"(.{2,60}?)(?:[.,;!?]|$)")

#: "Anderson is the project manager." Capitalised subject, role predicate.
#: Deliberately requires an article, so "Anderson is away" does not become a
#: durable claim about who Anderson is.
_ROLE = re.compile(
    r"\b([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)*)\s+is\s+(?:the|a|an|our|my)\s+"
    r"(.{3,45}?)(?:[.,;!?]|$)")

#: Employment stated without the verb "work": "I'm at Lumen now", "I've
#: joined Fenwick". Guarded on capitalisation in the source so "I'm at home"
#: and "I'm at the dentist" do not become employers.
_AT_EMPLOYER = re.compile(
    r"\bi(?:'m| am)\s+(?:now\s+)?at\s+([\w'-]{3,30}?)(?:\s+now)?(?:[.,;!?]|$)")
_JOINED = re.compile(
    r"\bi(?:'ve| have)?\s+(?:just\s+)?(?:joined|started at|moved to)\s+"
    r"([\w'-]{3,30}?)(?:[.,;!?]|$)")

# Episodes join their turns with " / ", so a pattern that spans the join
# invents facts nobody said: "the garden wall is moving along / actually I
# prefer oat milk now" produced the fact `garden_wall/state = "prefer oat milk
# now"`. Clause-splitting first is not an optimisation, it is correctness.
#
# The comma case is the same bug in miniature. "I don't work at Fenwick any
# more, I'm at Lumen now" is two claims, and only the second one is true. So
# a comma splits when a new subject follows it and not otherwise -- splitting
# on every comma would cut "meetings in the morning, useless after three"
# into two fragments that mean nothing apart.
_SPLIT = re.compile(
    r"(?<=[.!?])\s+|\s*/\s*|\s*\|\s*|;\s*|\s+[—–]\s+|\s+-\s+"
    r"|,\s+(?:and\s+|but\s+|though\s+|although\s+|so\s+|then\s+)?"
    r"(?=i\b|i'|the\s|my\s|we\b|we')"
    # A conjunction followed by a fresh subject is a clause boundary too, and
    # without it a second clause runs past the length limit of every pattern
    # here: "Priya's flat is in Alfama and she starts at the hospital in
    # March" matched nothing at all. Bare "and" is left alone — "fish and
    # chips" is not two clauses.
    r"|\s+and\s+(?=i\b|i'|he\b|she\b|they\b|it\b|we\b|the\s|my\s)",
    re.IGNORECASE)

#: A bare pronoun object carries no dimension: "I'm allergic to it" keyed
#: `user/allergic_x` and stored a claim naming no allergen, which is a fact
#: that can never be retrieved and can never be corrected.
_PRONOUN_OBJECT = re.compile(r"^(it|that|this|them|those|these|him|her|one)\b",
                             re.IGNORECASE)


def slug(text: str, limit: int = 3) -> str:
    words = [w for w in tokenize(text)][:limit]
    return "_".join(words) if words else "x"


#: Trailing discourse markers. "my name is Soumil by the way" stored the name
#: as "soumil by the way", which is then said back to them.
_FILLER_TAIL = re.compile(
    r"[\s,]+(by the way|anyway|anyhow|actually|though|i guess|i think|"
    r"i suppose|you know|obviously|of course|to be honest|honestly|"
    r"apparently|as it happens|or something|or whatever|really)\s*$", re.I)


def trim(text: str) -> str:
    out = text.strip(" ,;:-–—")
    while True:
        cut = _FILLER_TAIL.sub("", out)
        if cut == out:
            return cut.strip(" ,;:-–—")
        out = cut


def original_case(word: str, source: str) -> str:
    """Names come back from a lowercased clause. A persona block that calls
    them "soumil" reads as broken in a way that has nothing to do with what
    it knows."""
    m = re.search(rf"\b{re.escape(word)}\b", source, re.IGNORECASE)
    return m.group(0) if m else word


_WORDS = re.compile(r"[A-Za-z][\w'-]*")


def recase(text: str, source: str) -> str:
    """Give every word back the capitalisation it had when it was said.

    Patterns match against a lowercased clause, so everything they capture
    comes back flat: "the user's sister priya is moving to lisbon". These get
    read aloud and printed in the profile, and a device that renders the
    names of the people you love in lower case looks broken for a reason that
    has nothing to do with what it knows.

    Only capitalisation that is *evidence* is copied — a word capitalised
    mid-sentence in the source. A sentence-initial capital says nothing about
    the word, and treating it as a name is how "Actually" becomes a person.
    """
    def fix(m: re.Match[str]) -> str:
        word = m.group(0)
        for found in re.finditer(rf"\b{re.escape(word)}\b", source, re.IGNORECASE):
            got = found.group(0)
            if got[:1].isupper() and found.start() > 0:
                return got
        return word
    return _WORDS.sub(fix, text)


#: A prepositional phrase attaches *after* the head noun, so everything from
#: here on is modifier rather than topic.
_ATTACHMENT = re.compile(
    r"\b(in|on|at|with|for|from|about|during|before|after|over|under|by|"
    r"than|because|since|while|unless|these days|nowadays|any more|anymore|"
    r"right now|at the moment|lately|generally|usually|most of the time)\b")


def topic(phrase: str) -> str:
    """The dimension a claim is about.

    Trims first, because `add()` trims the claim text and every caller built
    the key from the untrimmed object -- so "I prefer oat milk I think" stored
    the right sentence under `user/pref_think`, which can never supersede the
    `user/pref_milk` it was meant to replace.

    Take the **last content word of the phrase up to its first attachment**.
    English noun phrases are head-final -- "cow milk" and "oat milk" both
    reduce to `milk` -- but a prepositional phrase hangs off the *end*, and
    taking the literal last word lets it steal the head:

        "meetings in the morning"             -> morning
        "meetings in the afternoon these days" -> days

    Two different keys for the same dimension, so the later statement sits
    next to the earlier one forever and the profile ends up asserting both.
    Cutting at the attachment gives `meeting` for both, and as a bonus fixes
    "oat milk in my coffee", which previously keyed on `coffee`.
    """
    phrase = trim(phrase)
    head = _ATTACHMENT.split(phrase.lower(), maxsplit=1)[0]
    words = tokenize(head) or tokenize(phrase)
    if not words:
        return "x"
    return stem(words[-1])


# --------------------------------------------------------- nominal ellipsis
#
# "We've run out of milk again — I only ever buy semi-skimmed."
#
# The object of `buy` is a bare modifier: the head noun was said a clause ago
# and dropped, which English does constantly for consumables and products
# ("I only get free-range", "she takes decaf", "we always order large").
# A reader supplies *milk* without noticing.
#
# An extractor that does not costs the whole mechanism this project is built
# on. The claim becomes "the user buys semi-skimmed", which keys on `skimm`;
# six weeks later "I've switched to oat milk" keys on `milk`; the two are
# different dimensions as far as the store can tell, so supersession never
# fires and both sit in the profile forever, contradicting each other.
#
# The detection is deliberately narrow, because a false positive welds a
# wrong noun onto a right claim: fire only on a **single-token object that is
# morphologically an adjective**. "oat milk" is two tokens and keeps its own
# head; "meetings in the morning" heads on a noun. Only the elliptical case
# is left.

_ADJECTIVAL_SUFFIX = ("-", "less", "free", "ish", "ous", "ic", "al", "ive")

#: One-word modifiers that stand in for a noun so routinely that morphology
#: alone would miss them.
_BARE_MODIFIER = frozenset("""
decaf organic large small medium regular diet skimmed semi whole plain
unsalted salted wholemeal white brown black green herbal sparkling still
vegan veggie gluten dairy lactose instant ground filter loose
""".split())

#: Not nouns, so never an antecedent. Kept small on purpose — the point is to
#: skip the obvious verbs in "we've run out of milk", not to be a tagger.
_NOT_A_NOUN = frozenset("""
run out get got have has had buy buys bought take takes took use uses used
go goes went do does did make makes made keep keeps kept need needs come
comes came want wants like likes prefer prefers say says said think thinks
been being was were are is am put puts finish finished ran gone done
""".split())

#: A noun is usually introduced. Preferring an introduced token over the
#: merely-last one is what picks *milk* out of "we've run out of milk again"
#: rather than the verb that precedes it.
_INTRODUCER = re.compile(
    r"\b(?:the|a|an|of|my|our|some|any|more|no|out of|that|this)\s+([a-z][\w-]{2,20})\b")


def is_elliptical(phrase: str) -> bool:
    # Whitespace, not `tokenize`: the tokenizer splits on the hyphen, so
    # "semi-skimmed" arrives as two tokens and the one-word test — which is
    # the entire safety of this mechanism — silently never fires.
    words = phrase.strip().lower().split()
    if len(words) != 1:
        return False
    word = words[0].strip(".,;:!?\"'")
    if not word:
        return False
    if word in _BARE_MODIFIER:
        return True
    if "-" in word:
        return True
    last = tokenize(word)[-1] if tokenize(word) else word
    if last in _NOT_A_NOUN or last in _BARE_MODIFIER:
        return last in _BARE_MODIFIER
    if last.endswith("ed"):
        return True
    return any(last.endswith(s) for s in _ADJECTIVAL_SUFFIX if s != "-")


def antecedent_noun(prior: list[str]) -> str:
    """The head noun a bare modifier is standing in for.

    Nearest first, and within a clause the *introduced* noun wins: "we've run
    out of milk again" offers `run`, `out` and `milk` as content words, and
    only `milk` arrives behind a preposition.
    """
    for clause in reversed(prior):
        introduced = [w for w in _INTRODUCER.findall(clause.lower())
                      if w not in _NOT_A_NOUN and w not in _BARE_MODIFIER]
        if introduced:
            return introduced[-1]
        words = [w for w in tokenize(clause)
                 if w not in _NOT_A_NOUN and not w.endswith("ly")]
        if words:
            return words[-1]
    return ""


def restore_head(phrase: str, prior: list[str]) -> str:
    """"semi-skimmed" -> "semi-skimmed milk", when the head is recoverable."""
    if not is_elliptical(phrase):
        return phrase
    head = antecedent_noun(prior)
    if not head or any(stem(head) == stem(w) for w in tokenize(phrase)):
        return phrase
    return f"{phrase.strip()} {head}"


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


#: What a name is, read off the preposition in front of it. Cheap, general,
#: and far better than the previous default of calling every unrecognised
#: capitalised word an organisation -- a confident wrong answer, which is the
#: worst kind for something the device says out loud.
_AS_ORG = re.compile(r"\b(?:at|for|joined|with the|works? at|started at)\s+$", re.I)
_AS_PERSON = re.compile(
    r"\b(?:my\s+(?:wife|husband|partner|sister|brother|mother|father|mum|mom|"
    r"dad|son|daughter|boss|neighbour|neighbor|friend|colleague|landlord|"
    r"doctor|dentist)|with|met|meeting|saw|spoke to|talked to|called|"
    r"lunch with|dinner with|asked|told)\s+$", re.I)
_AS_PLACE = re.compile(r"\b(?:in|to|from|near|around|live in|lives in|"
                       r"moving to|moved to|back in)\s+$", re.I)


def classify(name: str, source: str, head: str | None = None) -> str:
    if head:
        return "project"
    m = re.search(rf"\b{re.escape(name)}\b", source, re.IGNORECASE)
    before = source[: m.start()] if m else ""
    for pattern, kind in ((_AS_PERSON, "person"), (_AS_ORG, "org"),
                          (_AS_PLACE, "place")):
        if pattern.search(before):
            return kind
    if len(name.split()) >= 2:
        return "person"
    return "thing"


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
            kind = classify(m.group(1).strip(), sentence, head)
            existing = out.get(key)
            if existing:
                existing["aliases"] = sorted(set(existing["aliases"]) | aliases)
            else:
                out[key] = {"name": name, "kind": kind, "aliases": sorted(aliases)}
    return list(out.values())


def clauses(text: str) -> list[str]:
    return [c.strip() for c in _SPLIT.split(text) if c and c.strip()]


def _antecedent(prior: list[str]) -> str:
    """What a bare pronoun in this clause refers back to.

    The **first** content word of the nearest preceding clause, not the last.
    English clauses are subject-initial and a pronoun in a following clause
    almost always picks up the subject: in "penicillin is an antibiotic, but
    I'm allergic to it", the last content word is *antibiotic* and the right
    answer is *penicillin*. One rule, and it is right far more often than the
    recency heuristic that looks obvious.
    """
    for clause in reversed(prior):
        words = tokenize(clause)
        if words and not _PRONOUN_OBJECT.match(words[0]):
            return words[0]
    return ""


def _resolve(obj: str, prior: list[str]) -> str:
    """Both kinds of anaphora an object phrase can carry.

    A pronoun says *there is a referent, go and find it*; ellipsis says
    nothing at all and relies on the reader noticing an absence. They are the
    same repair — recover the noun from prior discourse — so they resolve in
    one place.
    """
    obj = obj.strip()
    if _PRONOUN_OBJECT.match(obj):
        antecedent = _antecedent(prior)
        return _PRONOUN_OBJECT.sub(antecedent, obj, count=1) if antecedent else obj
    return restore_head(obj, prior)


def find_facts(text: str, context: Sequence[str] = ()) -> list[dict]:
    """`context` is the earlier episodes of the same batch.

    Batching exists so that a claim spanning several turns is visible; an
    antecedent one turn back is exactly that case, and it costs nothing to
    let the reference reach it.
    """
    facts: list[dict] = []

    def add(key: str, claim: str, confidence: float = 0.7) -> None:
        facts.append({"key": key, "text": recase(trim(claim), text),
                      "confidence": confidence})

    earlier: list[str] = []
    for previous in context:
        earlier.extend(clauses(previous))
    parts = clauses(text)
    for i, clause in enumerate(parts):
        _facts_in_clause(clause, earlier + parts[:i], text, add)

    # De-duplicate by key, keeping the most confident.
    best: dict[str, dict] = {}
    for f in facts:
        prior = best.get(f["key"])
        if prior is None or f["confidence"] > prior["confidence"]:
            best[f["key"]] = f
    return list(best.values())


def _facts_in_clause(clause: str, prior: list[str], whole: str, add) -> None:
    """`clause` keeps its original case: three of these patterns need it."""
    low = clause.lower()
    if len(low) < 6:
        return

    # A question is not a claim. Auxiliary inversion leaves the declarative
    # substring intact -- "do I work at Lumen?" still contains "i work at
    # lumen" -- so every first-person pattern below matches a question about
    # a fact exactly as it matches the fact. The validator could not catch
    # it either: `undurable` tests the *rewritten* claim, and "the user works
    # at Lumen" can never end in a question mark.
    if is_question(clause):
        return

    m = _NAME.search(low)
    if m:
        name = trim(m.group(1))
        add("user/name",
            f"the user's name is {original_case(name, whole)}", 0.95)

    m = _WORK.search(low)
    if m:
        add("user/employer", f"the user works at {m.group(1).strip()}", 0.85)
    else:
        for pattern, conf in ((_JOINED, 0.8), (_AT_EMPLOYER, 0.7)):
            m = pattern.search(low)
            if m and _is_proper(m.group(1), whole):
                add("user/employer", f"the user works at {m.group(1).strip()}", conf)
                break

    m = _LIVE.search(low)
    if m:
        add("user/location", f"the user lives in {m.group(1).strip()}", 0.85)

    for m in _PREF.finditer(low):
        verb, obj = m.group(1), _resolve(m.group(2).strip(), prior)
        add(f"user/pref_{topic(obj)}",
            f"the user {third_person(verb)} {obj}", 0.8)

    for m in _SHIFT_NAMED.finditer(low):
        dim, value = m.group(1).strip(), m.group(2).strip()
        add(f"user/pref_{topic(dim)}",
            f"the user has moved {dim} to {value}", 0.8)
    for m in _SHIFT_BARE.finditer(low):
        value = _resolve(m.group(1).strip(), prior)
        add(f"user/pref_{topic(value)}", f"the user has switched to {value}", 0.8)

    for m in _STATE.finditer(low):
        state, obj = m.group(1), _resolve(m.group(2).strip(), prior)
        add(f"user/{slug(state, 2)}_{topic(obj)}", f"the user is {state} {obj}", 0.85)

    for m in _HABIT.finditer(low):
        adverb, rest = m.group(1), _resolve(m.group(2).strip(), prior)
        add(f"user/habit_{topic(rest)}", f"the user {adverb} {rest}", 0.7)

    for m in _RELATION.finditer(low):
        rel, rest = m.group(1), m.group(2).strip()
        add(f"user/{rel}", f"the user's {rel} {rest}", 0.8)

    for m in _DECISION.finditer(low):
        rest = m.group(1).strip()
        add(f"user/decision_{topic(rest)}", f"the user has decided to {rest}", 0.8)

    for m in _RATHER.finditer(low):
        obj = _resolve(m.group(1).strip(), prior)
        add(f"user/pref_{topic(obj)}", f"the user dislikes {obj}", 0.7)

    for m in _CANNOT.finditer(low):
        obj = m.group(1).strip()
        add(f"user/cannot_{topic(obj)}", f"the user cannot {obj}", 0.75)

    m = _NEVER_WANT.search(clause)
    if m:
        obj = m.group(1).strip().lower()
        add(f"user/pref_{topic(obj)}", f"the user does not want {obj}", 0.75)

    for m in _POSSESSIVE_STATE.finditer(clause):
        who, thing, verb, rest = (m.group(1).strip(), m.group(2).strip(),
                                  m.group(3).strip(), m.group(4).strip())
        if _skip_subject(who):
            continue
        add(f"{_subject_key(who)}/{topic(thing)}",
            f"{who}'s {thing} {verb} {rest}", 0.7)

    for m in _THING_STATE.finditer(low):
        subject, verb, rest = (m.group(1).strip(), m.group(2).strip(),
                               m.group(3).strip())
        if subject in ("user", "thing", "same", "other", "first", "last"):
            continue
        add(f"{slug(subject, 2)}/state", f"the {subject} {verb} {rest}", 0.65)

    # ---- other people, from the original casing ----------------------------
    for m in _OTHER_PREF.finditer(clause):
        who, verb, obj = (m.group(1).strip(), m.group(2).strip(),
                          m.group(3).strip())
        if _skip_subject(who):
            continue
        add(f"{_subject_key(who)}/pref_{topic(obj)}",
            f"{who} {verb} {obj}", 0.75)

    for m in _ROLE.finditer(clause):
        who, role = m.group(1).strip(), m.group(2).strip()
        if _skip_subject(who) or _PRONOUN_OBJECT.match(role):
            continue
        add(f"{_subject_key(who)}/role", f"{who} is the {role}", 0.75)


def _skip_subject(name: str) -> bool:
    parts = [p.lower() for p in name.split()]
    return (not parts or parts[0] in _SENTENCE_STARTERS
            or all(p in _NEVER_ENTITY for p in parts))


def _subject_key(name: str) -> str:
    """Surname for people, because that is what gets said next time.

    "David Anderson is the project manager" and "Anderson prefers everything
    in writing" have to reach the same subject or the two claims never meet.
    """
    return tokenize(name)[-1] if tokenize(name) else slug(name, 2)


def _is_proper(word: str, source: str) -> bool:
    """Capitalised somewhere in the original text, and not sentence-initial.

    "I'm at Lumen now" is an employer; "I'm at home" is not, and no amount of
    pattern work distinguishes them without looking at the capitalisation the
    speaker (or the transcriber) already provided.
    """
    for m in re.finditer(rf"\b{re.escape(word)}\b", source, re.IGNORECASE):
        if m.group(0)[:1].isupper() and m.start() > 0:
            return True
    return False


def extract(texts: Iterable[str], ids: Iterable[int], now: float) -> Extracted:
    texts = list(texts)
    ids = list(ids)
    out = Extracted()
    seen_entities: dict[str, dict] = {}

    for i, (text, nid) in enumerate(zip(texts, ids)):
        for f in find_facts(text, context=texts[:i]):
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
