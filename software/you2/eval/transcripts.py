"""A hand-labelled extraction corpus.

The year-long harness measures whether *consolidation* holds. It says nothing
about extraction, because it runs the rule extractor against a corpus the same
rules generated -- a closed loop that can only ever agree with itself. This is
the open loop: sentences written as a person actually says them, labelled by
hand with what a competent reader should take from them, and, just as
important, **what they must not take.**

Three things are being measured, and only the first is obvious.

**Recall and precision** -- did it find the durable claims, and did it invent
anything.

**Key stability** -- the same dimension, stated twice weeks apart in different
words, has to land on the same key. This is the metric that predicts whether
supersession will ever fire, and it is the one every memory system quietly
fails. Sessions here are ordered and the extractor sees the keys it has
already produced, exactly as it does at night.

**Traps** -- questions, hypotheticals, world knowledge, other people's
opinions, and things that were true for an afternoon. Extractors are eager;
a memory that fills with "the user is running late" is worse than an empty
one, because every one of those is a distractor at retrieval time forever.

Labelling rules I held myself to:

  - `facts` are what a careful human would write down, no more. If I had to
    argue for it, it went in `allow` instead.
  - `must` words are content words that have to survive into the claim, not
    the phrasing. Matching is stemmed and unordered.
  - `allow` is neither credited nor penalised: defensible extras.
  - `traps` are scored: extracting one is a precision failure with a name.
  - `dim` is my canonical name for the dimension. It is never shown to the
    extractor -- it exists so two sessions can be checked for agreement
    without caring what key the extractor chose.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GoldFact:
    dim: str
    """Canonical dimension id. Two golds sharing a dim must share a key."""
    must: str
    """Content words that must appear in the extracted claim."""
    why: str = ""


@dataclass(frozen=True, slots=True)
class Session:
    label: str
    day: int
    turns: tuple[str, ...]
    facts: tuple[GoldFact, ...] = ()
    entities: tuple[str, ...] = ()
    loops: tuple[str, ...] = ()
    closed: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    """Defensible extra claims: not required, not penalised."""
    traps: tuple[str, ...] = ()
    """Claims that must NOT be stored. Word sets; all words must appear."""
    entity_traps: tuple[str, ...] = ()
    loop_traps: tuple[str, ...] = ()


CORPUS: tuple[Session, ...] = (

    Session(
        label="the first evening",
        day=0,
        turns=(
            "my name is Soumil by the way",
            "I work at Fenwick, I'm a structural engineer there",
            "I live in Bristol, moved down about four years ago",
        ),
        facts=(
            GoldFact("user/name", "soumil"),
            GoldFact("user/employer", "fenwick", "changes in session 14"),
            GoldFact("user/location", "bristol"),
        ),
        entities=("Fenwick:org",),
        allow=("engineer", "four years"),
    ),

    Session(
        label="the kestrel job",
        day=2,
        turns=(
            "had lunch with David Anderson about the Kestrel contract",
            "Anderson is the project manager on it, he signs everything off",
            "the Kestrel migration keeps slipping, we're still waiting on "
            "their test team",
            "I'll send him the revised numbers by Friday",
        ),
        facts=(
            GoldFact("anderson/role", "anderson project manager"),
        ),
        entities=("David Anderson:person", "Kestrel"),
        loops=("send revised numbers",),
        allow=("kestrel slipping", "test team"),
        entity_traps=("Friday", "Soumil"),
    ),

    Session(
        label="how they take things",
        day=3,
        turns=(
            "we've run out of milk again, I only ever buy semi-skimmed",
            "I prefer meetings in the morning, I'm useless after three",
        ),
        facts=(
            GoldFact("user/pref_milk", "semi-skimmed", "changes in session 10"),
            GoldFact("user/pref_meeting", "meetings morning",
                     "changes in session 11"),
        ),
        allow=("useless after three",),
    ),

    Session(
        label="a question is not a memory",
        day=4,
        turns=(
            "what did I say about the Kestrel deadline?",
            "no, never mind",
        ),
        traps=("kestrel deadline",),
    ),

    Session(
        label="hypotheticals",
        day=5,
        turns=(
            "I might take the Lisbon job if it comes through, not sure yet",
            "if I did I'd have to sell the car",
        ),
        traps=("user lisbon job", "user sell car"),
        loop_traps=("sell the car",),
        allow=("lisbon",),
    ),

    Session(
        label="what the model already knows",
        day=6,
        turns=(
            "Lisbon's in Portugal, obviously",
            "penicillin is an antibiotic, that's not the point — "
            "I'm allergic to it",
        ),
        facts=(
            GoldFact("user/allergy_penicillin", "allergic penicillin"),
        ),
        traps=("lisbon portugal", "penicillin antibiotic"),
    ),

    Session(
        label="the house",
        day=8,
        turns=(
            "the garden wall needs rebuilding before winter",
            "the boiler service is due in October",
            "Marta Nilsen is drawing up plans for the wall",
        ),
        facts=(
            GoldFact("wall/state", "wall needs rebuilding"),
            GoldFact("boiler/service", "boiler due october"),
        ),
        entities=("Marta Nilsen:person",),
        allow=("marta plans",),
    ),

    Session(
        label="true for an afternoon",
        day=9,
        turns=(
            "I'm shattered today, that meeting went on for three hours",
            "running late this morning, the traffic was awful",
        ),
        traps=("user shattered", "user running late", "user traffic"),
    ),

    Session(
        label="family",
        day=12,
        turns=(
            "my sister Priya is moving to Lisbon in the spring",
            "my wife Elena is a paediatrician, she does long shifts",
        ),
        facts=(
            GoldFact("user/sister", "sister priya"),
            GoldFact("user/wife", "wife elena"),
        ),
        entities=("Priya:person", "Elena:person"),
        allow=("paediatrician", "long shifts", "lisbon spring"),
    ),

    Session(
        label="the milk changes",
        day=40,
        turns=(
            "I've switched to oat milk, the semi-skimmed was upsetting my "
            "stomach",
        ),
        facts=(
            GoldFact("user/pref_milk", "oat milk",
                     "same dimension as session 3 — must reuse that key"),
        ),
    ),

    Session(
        label="the meetings change",
        day=45,
        turns=(
            "I've moved my meetings to the afternoon these days, "
            "mornings are for drawings",
        ),
        facts=(
            GoldFact("user/pref_meeting", "meetings afternoon",
                     "same dimension as session 3 — must reuse that key"),
        ),
    ),

    Session(
        label="closing the loop",
        day=46,
        turns=(
            "sent Anderson the revised numbers, that's done",
        ),
        closed=("send revised numbers",),
    ),

    Session(
        label="somebody else's preference",
        day=50,
        turns=(
            "Anderson said he prefers everything in writing, no phone calls",
        ),
        facts=(
            GoldFact("anderson/pref_writing", "anderson writing"),
        ),
        traps=("user prefers writing",),
    ),

    Session(
        label="the job changes",
        day=52,
        turns=(
            "I don't work at Fenwick any more, I'm at Lumen now",
        ),
        facts=(
            GoldFact("user/employer", "lumen",
                     "same dimension as session 1 — must reuse that key"),
        ),
        entities=("Lumen:org",),
        traps=("user works fenwick",),
    ),

    Session(
        label="a commitment and something that is not one",
        day=55,
        turns=(
            "I need to book the boiler service before it gets cold",
            "someone should really fix that gate",
        ),
        loops=("book boiler service",),
        loop_traps=("fix that gate",),
    ),

    Session(
        label="two clauses, one breath",
        day=60,
        turns=(
            "the wall's coming along nicely, though I've decided to go with "
            "brick rather than block",
            "remember that the car insurance renews on the third of March",
        ),
        facts=(
            GoldFact("user/decision_brick", "brick"),
            GoldFact("car/insurance", "insurance renews march"),
        ),
        allow=("wall coming along",),
        traps=("wall brick",),
    ),

    # ---------------------------------------------------------------- harder
    # Written after the rules already scored 1.00 on everything above, and
    # deliberately not tuned against: when one of these fails, the fix has to
    # be a whole construction or it does not go in. A corpus you patch
    # sentence by sentence measures your patience, not your extractor.

    Session(
        label="a correction mid-sentence",
        day=64,
        turns=(
            "I'll call Anderson on Tuesday — no wait, Wednesday, "
            "he's away Tuesday",
        ),
        loops=("call anderson wednesday",),
        traps=("anderson away",),
    ),

    Session(
        label="hyperbole",
        day=66,
        turns=(
            "I'd rather die than sit through another all-hands",
        ),
        facts=(
            GoldFact("user/pref_allhands", "all-hands"),
        ),
        traps=("user die", "user rather die"),
    ),

    Session(
        label="somebody else's opinion about them",
        day=68,
        turns=(
            "Elena thinks I should drop the Kestrel work altogether",
        ),
        traps=("user decided drop kestrel", "user should drop kestrel"),
        allow=("elena kestrel",),
    ),

    Session(
        label="a constraint with a reason",
        day=70,
        turns=(
            "I can't do anything before nine, that's the school run",
        ),
        facts=(
            GoldFact("user/constraint_morning", "before nine"),
        ),
        allow=("school run",),
    ),

    Session(
        label="a preference with no subject",
        day=72,
        turns=(
            "no coriander in anything, ever — it tastes like soap to me",
        ),
        facts=(
            GoldFact("user/pref_coriander", "coriander"),
        ),
    ),

    Session(
        label="somebody else's commitment",
        day=74,
        turns=(
            "Marta said the drawings will be ready next week",
        ),
        loop_traps=("drawings ready",),
        allow=("marta drawings",),
    ),

    Session(
        label="a retraction",
        day=76,
        turns=(
            "forget what I said about the boiler, the landlord handles it",
        ),
        traps=("user boiler service due",),
        allow=("landlord boiler",),
    ),

    Session(
        label="two facts, one breath",
        day=78,
        turns=(
            "Priya's flat is in Alfama and she starts at the hospital in March",
        ),
        facts=(
            GoldFact("priya/location", "priya alfama"),
        ),
        entities=("Alfama:place",),
        allow=("priya hospital march",),
    ),
)


# ------------------------------------------------------------------ accounting

def dimensions() -> dict[str, list[int]]:
    """dim -> indices of the sessions asserting it.

    A dim in more than one session is a key-stability pair: the extractor has
    to notice that the second statement is about the same thing as the first.
    """
    out: dict[str, list[int]] = {}
    for i, s in enumerate(CORPUS):
        for f in s.facts:
            out.setdefault(f.dim, []).append(i)
    return out


def stability_pairs() -> list[str]:
    return [d for d, where in dimensions().items() if len(where) > 1]


def totals() -> dict[str, int]:
    return {
        "sessions": len(CORPUS),
        "utterances": sum(len(s.turns) for s in CORPUS),
        "facts": sum(len(s.facts) for s in CORPUS),
        "entities": sum(len(s.entities) for s in CORPUS),
        "loops": sum(len(s.loops) for s in CORPUS),
        "closures": sum(len(s.closed) for s in CORPUS),
        "traps": sum(len(s.traps) + len(s.entity_traps) + len(s.loop_traps)
                     for s in CORPUS),
        "stability_pairs": len(stability_pairs()),
    }
