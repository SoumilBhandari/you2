"""The person.

Everything below `memory` answers *what did they say*. This answers *who are
they* — and it is the difference between an assistant with a good search index
and one that is actually theirs.

Six things, all derived from what is already in the store, none of them
volunteered by the user and none of them a setting they picked:

    identity    the durable self: name, where, who with, what constrains them
    rhythms     the shape of a week — when they are sharp, when they are flat
    stances     how they feel about each thing, and which way it is moving
    style       how they talk, and therefore how to talk back
    concerns    what is live and weighing on them right now
    arc         what has changed about them lately

Rebuilt nightly, not per query. It is a *stable* prefix — which keeps the KV
cache warm, and is also just true: who someone is does not change between
Tuesday and Wednesday.

**Every claim is evidenced or absent.** The failure mode of a system that
models a person is confidently telling them something about themselves that is
wrong, and it is far worse than saying nothing: it is the moment they stop
believing any of it. So each component carries its sample size, thin evidence
produces an empty section rather than a hedged one, and `explain()` prints
what each line was derived from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..clock import DAY, iso
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from . import rhythms as rhythms_mod
from . import stance as stance_mod
from . import style as style_mod

#: Fact keys that describe the person rather than their circumstances. These
#: go first and never decay out of the block.
IDENTITY_KEYS = ("user/name", "user/location", "user/employer", "user/role")

#: Prefixes that mark a fact as being about the person themselves.
SELF_PREFIX = "user/"

MAX_IDENTITY = 8
MAX_PREFERENCES = 8
MAX_CONCERNS = 5


@dataclass
class Person:
    identity: list[Neuron] = field(default_factory=list)
    preferences: list[Neuron] = field(default_factory=list)
    relationships: list[Neuron] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    rhythms: rhythms_mod.Rhythms = field(default_factory=rhythms_mod.Rhythms)
    stances: dict[int, stance_mod.Stance] = field(default_factory=dict)
    style: style_mod.Style = field(default_factory=style_mod.Style)
    arc: list[str] = field(default_factory=list)
    known_days: float = 0.0
    updated_at: float = 0.0

    # ------------------------------------------------------------- rendering

    def persona(self, now: float | None = None) -> str:
        """The stable block that goes in front of every prompt.

        Not the retrieved memories -- those change every turn and sit after
        the cache breakpoint. This is who the person is, and it is identical
        all day.
        """
        now = now if now is not None else self.updated_at
        out: list[str] = []

        if self.identity:
            out.append("who they are:")
            out.extend(f"  - {n.text()}" for n in self.identity[:MAX_IDENTITY])

        if self.preferences:
            out.append("how they like things:")
            out.extend(f"  - {n.text()}" for n in self.preferences[:MAX_PREFERENCES])

        people = [n for n in self.relationships]
        if people:
            out.append("their people:")
            out.extend(f"  - {n.text()}" for n in people[:5])

        feelings = stance_mod.brief(self.stances, now)
        if feelings:
            out.append("how they are feeling about things:")
            out.extend(f"  - {line}" for line in feelings)

        shape = self.rhythms.brief()
        if shape:
            out.append("their rhythm:")
            out.extend(f"  - {line}" for line in shape)

        if self.concerns:
            out.append("on their mind:")
            out.extend(f"  - {c}" for c in self.concerns[:MAX_CONCERNS])

        instruction = self.style.instruction()
        if instruction:
            out.append(f"how to talk to them: {instruction}")

        return "\n".join(out)

    def explain(self) -> str:
        """What every line was derived from.

        A model of a person that cannot show its evidence is one they have no
        way to correct, and being wrong about someone without being
        challengeable is the worst state this system can be in.
        """
        lines = [
            f"person as of {iso(self.updated_at)}, known {self.known_days:.0f} days",
            f"  identity      {len(self.identity)} facts",
            f"  preferences   {len(self.preferences)} facts",
            f"  relationships {len(self.relationships)} entities",
            f"  rhythms       {self.rhythms.total} episodes over "
            f"{self.rhythms.span_days:.0f} days"
            f"{'' if self.rhythms.reliable else '  (too thin to use)'}",
            f"  stances       {len(self.stances)} entities, "
            f"{sum(1 for s in self.stances.values() if s.reliable)} well-evidenced",
            f"  style         {self.style.turns} turns"
            f"{'' if self.style.reliable else '  (too thin to use)'}",
        ]
        for s in stance_mod.notable(self.stances, self.updated_at):
            lines.append(f"    {s.describe(self.updated_at)}")
        for label, what, z in self.rhythms.notable():
            lines.append(f"    {label}: {what} ({z:+.1f}σ from their own baseline)")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at, "known_days": round(self.known_days, 1),
            "identity": [n.text() for n in self.identity],
            "preferences": [n.text() for n in self.preferences],
            "relationships": [n.text() for n in self.relationships],
            "concerns": self.concerns,
            "rhythms": self.rhythms.as_dict(),
            "stances": [s.as_dict() for s in
                        stance_mod.notable(self.stances, self.updated_at, limit=8)],
            "style": self.style.as_dict(),
            "arc": self.arc,
        }

    def __repr__(self) -> str:
        return (f"Person({len(self.identity)} identity, "
                f"{len(self.preferences)} preferences, "
                f"{len(self.stances)} stances, {self.known_days:.0f}d)")


def build(store: Store, cfg: BrainConfig, now: float) -> Person:
    out = Person(updated_at=now)

    span = store.conn.execute(
        "SELECT MIN(created_at) a FROM neurons").fetchone()
    if span and span["a"]:
        out.known_days = max(0.0, (now - span["a"]) / DAY)

    facts = store.by_kind(Kind.FACT, limit=500, live_only=True)
    facts = [f for f in facts if not f.meta.get("derived")]

    ranked = sorted(facts, key=lambda f: (-f.confidence, -f.storage_strength))
    seen: set[int] = set()

    for key in IDENTITY_KEYS:
        hit = store.by_key(key)
        if hit is not None and hit.id not in seen:
            seen.add(hit.id)
            out.identity.append(hit)
    for f in ranked:
        if f.id in seen or not f.key:
            continue
        if f.key.startswith(SELF_PREFIX) and "pref_" not in f.key \
                and "habit_" not in f.key:
            seen.add(f.id)
            out.identity.append(f)
        if len(out.identity) >= MAX_IDENTITY:
            break

    for f in ranked:
        if f.id in seen or not f.key:
            continue
        if "pref_" in f.key or "habit_" in f.key or "decision_" in f.key:
            seen.add(f.id)
            out.preferences.append(f)
        if len(out.preferences) >= MAX_PREFERENCES:
            break

    out.relationships = [
        n for n in store.by_kind(Kind.ENTITY, limit=200, order="access_count DESC")
        if n.meta.get("kind") == "person"][:6]

    out.rhythms = rhythms_mod.build(store, cfg, now)
    out.stances = stance_mod.build(store, cfg, now)
    out.style = style_mod.build(store, cfg, now)
    out.concerns = _concerns(store, cfg, now, out)
    out.arc = _arc(store, cfg, now)

    store.log("person", {"identity": len(out.identity),
                         "preferences": len(out.preferences),
                         "stances": len(out.stances),
                         "style_turns": out.style.turns,
                         "rhythms_reliable": out.rhythms.reliable})
    return out


def _concerns(store: Store, cfg: BrainConfig, now: float,
              person: Person) -> list[str]:
    """What is actually weighing on them.

    Overdue commitments first, because those are the ones they are carrying
    whether or not they mention them; then whatever they are souring on.
    """
    out: list[str] = []
    for loop in store.by_kind(Kind.LOOP, limit=60, order="created_at ASC"):
        if loop.invalid_at is not None:
            continue
        due = loop.meta.get("expected_by")
        overdue = isinstance(due, (int, float)) and now > due
        out.append(loop.content.strip() + (" (overdue)" if overdue else ""))
        if len(out) >= MAX_CONCERNS:
            break

    for s in stance_mod.notable(person.stances, now, limit=3):
        if s.drifting == "souring" and len(out) < MAX_CONCERNS:
            out.append(f"{s.name} has been going badly")
    return out


def _arc(store: Store, cfg: BrainConfig, now: float,
         window_days: float = 90.0) -> list[str]:
    """What has changed about them lately.

    Supersession makes this free: a fact that replaced another *is* a change
    in the person, dated, with both values kept. No other part of the system
    has to do anything for this to work.
    """
    out: list[str] = []
    for fact in store.by_kind(Kind.FACT, limit=400, live_only=True):
        if fact.meta.get("derived") or not fact.key:
            continue
        if (now - (fact.valid_at or fact.created_at)) / DAY > window_days:
            continue
        history = store.history_of(fact.key)
        if len(history) < 2:
            continue
        previous = history[1]
        out.append(f"was \"{previous.text()}\", now \"{fact.text()}\"")
        if len(out) >= 4:
            break
    return out


def save(store: Store, person: Person) -> None:
    """Persist the model so a reboot does not lose it.

    Stored as one journal entry rather than as neurons: it is entirely derived
    and rebuilt nightly, so making it part of the graph would mean retrieval
    could surface a stale copy of a summary of itself.
    """
    store.conn.execute(
        "INSERT INTO meta(k, v) VALUES('person', ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (json.dumps(person.as_dict(), default=str),))


def load_snapshot(store: Store) -> dict[str, Any] | None:
    row = store.conn.execute(
        "SELECT v FROM meta WHERE k='person'").fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return None
