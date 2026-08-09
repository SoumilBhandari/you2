"""A fabricated year, with ground truth.

docs/design.md's best idea: "generate six months of synthetic conversation,
load it, run consolidation, and measure whether retrieval quality holds. That
degradation is the failure mode that kills every memory system, and you can
find it on a Tuesday."

The generator plants things we can then check for:

    preferences that CHANGE      -> supersession must fire, and the old value
                                    must stop being retrievable
    commitments and closures     -> open loops must open and shut
    a recurring context/outcome  -> recurrence must find it, and must NOT find
                                    the decoys
    two structurally parallel    -> bridging has something real to find, so
    projects                        "found nothing" is a measurement rather
                                    than an absence of opportunity
    a contradiction              -> a question must be generated
    a lot of ordinary chatter    -> the noise floor everything competes with

Ground truth is emitted alongside, because we generated it. That is the only
way to get a precision figure for a product whose real metric -- "does this
feel like it knows me after two years" -- has no benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from ..clock import DAY, HOUR, weekday

PEOPLE = [
    ("David Anderson", ["david", "anderson", "dave anderson"], "client at Kestrel"),
    ("Priya Raman", ["priya", "raman"], "colleague"),
    ("Marta Nilsen", ["marta", "nilsen"], "the architect"),
    ("Tom Beckwith", ["tom", "beckwith"], "brother-in-law"),
]

PROJECTS = [
    ("the Kestrel migration", ["kestrel", "kestrel migration"]),
    ("the garden wall", ["garden wall", "retaining wall"]),
    ("the Halloway report", ["halloway", "halloway report"]),
]

#: (key, phrasings over time). The second phrasing supersedes the first, which
#: is the single most important thing the harness checks.
#: (key, old phrasing, new phrasing, stale token, current token).
#: The tokens must be unique in the whole corpus, or "stale leakage" measures
#: an unrelated mention instead of a superseded value -- the first version of
#: this file used "kestrel" as the old employer AND as a project name, and
#: reported 0.88 leakage that was entirely the project.
PREFERENCES = [
    ("user/pref_milk", "I prefer dairy milk", "actually I prefer oat milk now",
     "dairy", "oat"),
    ("user/employer", "I work at Fenwick Partners", "I work at Lumen now",
     "fenwick", "lumen"),
    ("user/location", "I live in Ravensmoor", "I live in Ashcombe now",
     "ravensmoor", "ashcombe"),
    ("user/pref_meeting", "I prefer standing meetings",
     "I prefer seated meetings now", "standing", "seated"),
]

CHATTER = [
    "the train was busy again this morning",
    "it rained most of the afternoon",
    "I finally got round to the washing up",
    "the shop had run out of the bread I like",
    "there was a fox in the garden last night",
    "the bins go out tomorrow",
    "I watched a documentary about deep sea vents",
    "the coffee machine is making that noise again",
    "someone parked across the drive again",
    "I need to charge the drill",
    "the neighbours were doing DIY at seven in the morning",
    "the postman came early for once",
]

ORDINARY = [
    "spoke to {person} about {project} for a while",
    "{project} is moving along, slowly",
    "{person} sent over the latest version of {project}",
    "had a look at {project} this evening",
    "{person} is away next week so {project} will wait",
]

#: The planted recurrence: Tuesday planning meetings leave them drained.
#: Two phrasings, because people do not say the same sentence identically for
#: a year -- and because it exercises both the repeat path and the
#: separate-episode path.
RECURRENCE_TEXTS = [
    "the planning meeting left me completely drained again",
    "the planning meeting wiped me out again, awful",
]

#: Structurally parallel, lexically disjoint. Both are "a project blocked by a
#: dependency owned by someone else, slipping repeatedly".
BRIDGE_A = "the Kestrel migration slipped again, still waiting on their test team"
BRIDGE_B = "the garden wall slipped again, still waiting on Marta's drawings"

CONTRADICTION = [
    "the Halloway deadline is completely fixed, it cannot move",
    "the Halloway deadline has moved to the spring",
]

COMMITMENTS = [
    ("I'll send {person} the revised numbers by Friday",
     "sent {person} the revised numbers, that's done"),
    ("I need to book the boiler service", "booked the boiler service, sorted"),
    ("I'll look into {project} costs next week", None),          # never closed
]


@dataclass
class Truth:
    """What the harness is allowed to check against."""
    stale_tokens: dict[str, str] = field(default_factory=dict)
    """key -> a token that must NOT appear in a retrieved block at the end.
    Chosen to be unique in the corpus, so a hit is unambiguously a superseded
    value and not an unrelated mention."""
    current_tokens: dict[str, str] = field(default_factory=dict)
    """key -> a token that must appear."""
    changed_at: dict[str, float] = field(default_factory=dict)
    """key -> when the replacement was stated. Before that instant the old
    value is *current*, and counting it as leakage measures nothing."""
    probes: list[tuple[str, str]] = field(default_factory=list)
    """(query, distinctive token that a correct answer contains)."""
    recurrence: str = ""
    bridge: tuple[str, str] = ("", "")
    contradiction_key: str = ""
    contradiction_fact_key: str = ""
    """Key stem of the fact that must end up with exactly one live
    version. Matching on free text instead picks up every other fact
    mentioning the same project."""
    open_loops: list[str] = field(default_factory=list)
    closed_loops: list[str] = field(default_factory=list)
    total_utterances: int = 0
    span_days: float = 0.0


@dataclass(slots=True)
class Utterance:
    text: str
    at: float
    tag: str = "chatter"


def generate(days: int = 365, *, seed: int = 0, start: float = 1767225600.0,
             per_day: float = 3.0) -> tuple[list[Utterance], Truth]:
    rng = np.random.default_rng(seed)
    truth = Truth(span_days=float(days))
    out: list[Utterance] = []

    def at(day: int, hour: float) -> float:
        return start + day * DAY + hour * HOUR

    # --- preferences, stated early and changed mid-way ----------------------
    for i, (key, old, new, stale_tok, cur_tok) in enumerate(PREFERENCES):
        first_day = 3 + i * 2
        change_day = int(days * 0.55) + i * 3
        out.append(Utterance(old, at(first_day, 9.0), f"pref:{key}:old"))
        out.append(Utterance(new, at(change_day, 9.0), f"pref:{key}:new"))
        truth.stale_tokens[key] = stale_tok
        truth.current_tokens[key] = cur_tok
        truth.changed_at[key] = at(change_day, 9.0)

    # --- the planted recurrence, on actual Tuesdays ------------------------
    # Computed from the calendar, not from `day % 7`. The first version used
    # the modulus and planted every "Tuesday meeting" on a Friday, so the
    # detector correctly found a Friday pattern and the harness scored it as
    # a miss.
    tuesdays = [d for d in range(days) if weekday(at(d, 18.0)) == "tuesday"]
    for k, d in enumerate(tuesdays):
        out.append(Utterance(RECURRENCE_TEXTS[k % len(RECURRENCE_TEXTS)],
                             at(d, 18.0), "recurrence"))
    truth.recurrence = "tuesday"

    # --- decoys: the same complaint, but scattered, so it has no context ----
    for d in rng.choice(days, size=min(8, days), replace=False):
        out.append(Utterance("I am completely drained this evening",
                             at(int(d), 20.0), "decoy"))

    # --- the bridge pair, each repeated so both ends have structure ---------
    for k in range(4):
        out.append(Utterance(BRIDGE_A, at(int(days * 0.2) + k * 21, 11.0), "bridge_a"))
        out.append(Utterance(BRIDGE_B, at(int(days * 0.25) + k * 19, 15.0), "bridge_b"))
    truth.bridge = (BRIDGE_A, BRIDGE_B)

    # --- the contradiction --------------------------------------------------
    out.append(Utterance(CONTRADICTION[0], at(int(days * 0.3), 10.0), "contra_a"))
    out.append(Utterance(CONTRADICTION[1], at(int(days * 0.34), 10.0), "contra_b"))
    truth.contradiction_key = "halloway"
    truth.contradiction_fact_key = "halloway_deadline"

    # --- commitments --------------------------------------------------------
    for i, (opened, closed) in enumerate(COMMITMENTS):
        person = PEOPLE[i % len(PEOPLE)][0]
        project = PROJECTS[i % len(PROJECTS)][0]
        text = opened.format(person=person, project=project)
        day = 10 + i * 30
        out.append(Utterance(text, at(day, 16.0), "loop_open"))
        if closed:
            out.append(Utterance(closed.format(person=person, project=project),
                                 at(day + 6, 16.0), "loop_close"))
            truth.closed_loops.append(text)
        else:
            truth.open_loops.append(text)

    # --- ordinary life, which is most of it ---------------------------------
    n_ordinary = int(days * per_day)
    for _ in range(n_ordinary):
        day = int(rng.integers(0, days))
        hour = float(rng.uniform(7.0, 22.0))
        if rng.random() < 0.45:
            template = ORDINARY[int(rng.integers(0, len(ORDINARY)))]
            person = PEOPLE[int(rng.integers(0, len(PEOPLE)))][0]
            project = PROJECTS[int(rng.integers(0, len(PROJECTS)))][0]
            text = template.format(person=person, project=project)
            tag = "ordinary"
        else:
            text = CHATTER[int(rng.integers(0, len(CHATTER)))]
            tag = "chatter"
        out.append(Utterance(text, at(day, hour), tag))

    out.sort(key=lambda u: u.at)
    truth.total_utterances = len(out)

    # --- probes: what a correct store must still answer at the end ----------
    truth.probes = [
        ("what milk do I prefer", "oat"),
        ("where do I work", "lumen"),
        ("where do I live", "ashcombe"),
        ("what kind of meetings do I prefer", "seated"),
        ("what do I think about David Anderson", "anderson"),
        ("what is happening with the Kestrel migration", "kestrel"),
        ("tell me about the garden wall", "wall"),
        ("what about the Halloway report", "halloway"),
    ]
    return out, truth


def entity_seeds() -> list[dict]:
    """Pre-registered aliases, so entity resolution is not itself the thing
    under test when we are measuring retrieval."""
    out = []
    for name, aliases, role in PEOPLE:
        out.append({"name": name, "kind": "person", "aliases": aliases,
                    "note": role})
    for name, aliases in PROJECTS:
        out.append({"name": name, "kind": "project", "aliases": aliases,
                    "note": ""})
    return out


def stream(utterances: list[Utterance]) -> Iterator[Utterance]:
    yield from utterances
