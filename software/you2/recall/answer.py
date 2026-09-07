"""Questions the memory can answer by itself.

"What did I say about X", "when did I last...", "what am I working on", "who
is X". These are resolvable from the workspace alone and templated into a
spoken answer.

Three reasons this matters more than it looks:

  * It is a bigger fraction of real usage than you would guess on a device
    whose entire pitch is that it remembers.
  * It is the best possible offline story. No network, no model, still useful.
  * It is the fastest path in the system -- about 20 ms end to end, against
    620-780 ms for the full loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..activation.workspace import Workspace, ago
from ..clock import DAY, human
from ..config import BrainConfig
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store

_WHAT_ABOUT = re.compile(
    r"\b(what (did|have) i (say|said|told you|mention)|"
    r"what do you (know|remember)|tell me (what|about)|"
    r"remind me (what|about))\b", re.IGNORECASE)

_WHEN_LAST = re.compile(
    r"\b(when (did|was) (i|the last)|how long (since|ago)|"
    r"last time i)\b", re.IGNORECASE)

_WORKING_ON = re.compile(
    r"\b(what am i (working on|doing|supposed to)|"
    r"what'?s (on my plate|outstanding|open|pending)|"
    r"what do i (need to|have to) do|my (open )?(loops|todos|commitments))\b",
    re.IGNORECASE)

_WHO_IS = re.compile(r"\bwho(?:'s| is)\s+(.{2,40}?)\s*\??$", re.IGNORECASE)

_ABOUT_ME = re.compile(
    r"\b(what do you know about me|tell me about myself|"
    r"what have you learned)\b", re.IGNORECASE)


@dataclass(slots=True)
class Answer:
    text: str
    kind: str
    cites: list[int]
    confident: bool = True

    def __repr__(self) -> str:
        return f"Answer({self.kind}, {len(self.cites)} cites)"


def _list(items: list[str], limit: int = 4) -> str:
    items = [i.strip() for i in items if i.strip()][:limit]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def answerable(text: str) -> bool:
    return bool(_WHAT_ABOUT.search(text) or _WHEN_LAST.search(text)
                or _WORKING_ON.search(text) or _WHO_IS.search(text)
                or _ABOUT_ME.search(text))


def answer(store: Store, ws: Workspace, text: str, cfg: BrainConfig,
           now: float, recent: Sequence[str] = ()) -> Answer | None:
    """Try to answer from the workspace alone. None means hand it to the model.

    `recent` is the exchange still in progress — turns that have been said and
    have not yet been written, because an episode is a bounded exchange and is
    only sealed when the next one starts. Without it the device cannot answer
    about something said thirty seconds ago, which is the first thing anyone
    tries and the last thing a memory device should fail at.

    It is a working-memory buffer in the ordinary sense: small, current,
    consulted before long-term storage, and gone once the material it holds
    has been consolidated properly.
    """
    if _WORKING_ON.search(text):
        return _open_loops(store, now)
    if _ABOUT_ME.search(text):
        return _about_me(store, ws, now)
    m = _WHO_IS.search(text)
    if m:
        who = _who(store, m.group(1), now)
        if who is not None:
            return who
    if _WHEN_LAST.search(text):
        return _when_last(store, ws, text, now)
    if _WHAT_ABOUT.search(text):
        return _what_about(store, ws, text, now, recent)
    return None


def _from_working_memory(topic: str, recent: Sequence[str]) -> str | None:
    """The most recent unwritten turn that is about the topic."""
    if not topic:
        return None
    from ..substrate.embed import stem, tokenize
    wanted = {stem(w) for w in tokenize(topic)}
    if not wanted:
        return None
    for said in reversed(list(recent)):
        if wanted & {stem(w) for w in tokenize(said)}:
            return said
    return None


def _open_loops(store: Store, now: float) -> Answer:
    loops = [n for n in store.by_kind(Kind.LOOP, limit=40,
                                      order="created_at ASC")
             if n.invalid_at is None]
    if not loops:
        return Answer("Nothing open that I know of.", "openloops", [])
    parts = []
    for loop in loops[:4]:
        due = loop.meta.get("expected_by")
        tail = ""
        if isinstance(due, (int, float)) and now > due:
            tail = f" ({ago(now - due)} overdue)"
        parts.append(f"{loop.content.strip()}{tail}")
    more = len(loops) - 4
    text = "You've got " + _list(parts) + ("." if more <= 0 else f", and {more} more.")
    return Answer(text, "openloops", [n.id for n in loops[:4]])


def _about_me(store: Store, ws: Workspace, now: float) -> Answer:
    facts = sorted(store.by_kind(Kind.FACT, limit=60, live_only=True),
                   key=lambda n: (-n.confidence, -n.storage_strength))[:5]
    if not facts:
        return Answer("Not much yet — we've only just started.", "aboutme", [])
    return Answer(_list([f.text() for f in facts], limit=5) + ".",
                  "aboutme", [f.id for f in facts])


def _who(store: Store, name: str, now: float) -> Answer | None:
    entity = store.find_entity(name.strip(" ?"))
    if entity is None:
        return None
    facts = []
    for other, weight, code, _back in store.graph.neighbours(entity.id):
        soma = store.soma.get(other)
        if weight > 0.0 and soma is not None and soma.kind is Kind.FACT:
            n = store.get(other)
            if n is not None and n.is_live:
                facts.append(n)
    facts.sort(key=lambda n: -n.confidence)
    role = entity.meta.get("role")
    lead = f"{entity.content}" + (f" — {role}" if role else "")
    if facts:
        return Answer(f"{lead}. {_list([f.text() for f in facts[:3]])}.",
                      "who", [entity.id] + [f.id for f in facts[:3]])
    return Answer(f"{lead}. First came up {ago(now - entity.created_at)} ago.",
                  "who", [entity.id])


#: The thing a question is asking about, when it names one.
_TOPIC = re.compile(
    r"\b(?:about|regarding|on the subject of|to do with|with regard to)\s+"
    r"(.{2,60}?)\s*\??$", re.IGNORECASE)
_LAST_TOPIC = re.compile(
    r"\b(?:when (?:did|was) i last|last time i)\s+\w+\s+(.{2,50}?)\s*\??$",
    re.IGNORECASE)


def topic_of(text: str) -> str:
    m = _TOPIC.search(text) or _LAST_TOPIC.search(text)
    if not m:
        return ""
    from ..substrate.embed import tokenize
    return " ".join(tokenize(m.group(1)))


def _on_topic(store: Store, topic: str, items: list[Neuron]) -> list[Neuron]:
    """Only the material actually about the thing asked.

    Retrieval always returns *something* — that is what spreading activation
    does, and the ambient cues alone guarantee a non-empty workspace. So
    "resolvable from the workspace alone" quietly became "answer with
    whatever is in the workspace", and asking about a project this store had
    never heard of produced a confident sentence about the user's sister and
    cilantro.

    That is confabulation, and it is the worst failure this device can have:
    stated in the user's own vocabulary, with no model involved to hedge it,
    on the fastest path in the system. Saying "nothing on that" is not a
    degraded answer — it is the correct one, and it is the thing that makes
    every other answer worth believing.
    """
    if not topic:
        return items
    from ..substrate.embed import stem, tokenize
    wanted = {stem(w) for w in tokenize(topic)}
    if not wanted:
        return items

    related: set[int] = set()
    anchor = store.find_entity(topic)
    if anchor is not None:
        related = {anchor.id}
        related |= {o for o, w, _c, _b in store.graph.neighbours(anchor.id)
                    if w > 0.0}

    # Naming the thing beats being connected to it. Graph adjacency is what
    # makes "spoke to Priya about it for a while" answer a question about the
    # Kestrel migration, and it is indispensable for that — but on a small
    # store nearly everything is connected to nearly everything, so using it
    # alongside direct matches let a question about Lisbon return the boiler
    # service. Adjacency is the fallback, not a peer.
    named: list[Neuron] = []
    adjacent: list[Neuron] = []
    for n in items:
        if wanted & {stem(w) for w in tokenize(n.text())}:
            named.append(n)
        elif n.id in related:
            adjacent.append(n)
    return named or adjacent


def _when_last(store: Store, ws: Workspace, text: str,
               now: float) -> Answer | None:
    episodes = _on_topic(store, topic_of(text),
                         [n for n in ws.neurons() if n.kind is Kind.EPISODE])
    if not episodes:
        return None
    latest = max(episodes, key=lambda n: n.created_at)
    return Answer(
        f"{ago(now - latest.created_at)} ago — {human(latest.created_at)}. "
        f"You said: {latest.text()[:140]}",
        "whenlast", [latest.id])


def _what_about(store: Store, ws: Workspace, text: str, now: float,
                recent: Sequence[str] = ()) -> Answer | None:
    topic = topic_of(text)
    useful = _on_topic(store, topic, [
        n for n in ws.neurons()
        if n.kind in (Kind.FACT, Kind.EPISODE, Kind.LOOP) and n.text()])
    if not useful:
        # Before giving up, the exchange still in progress. It has been said
        # and not yet written, and "you just told me" is a true answer.
        just_said = _from_working_memory(topic, recent)
        if just_said is not None:
            return Answer(f"Just now you said: {just_said[:160]}",
                          "whatabout", [])
        if topic:
            return Answer(f"Nothing about {topic}, as far as I know.",
                          "whatabout", [])
        return Answer("Nothing on that, as far as I know.", "whatabout", [])
    facts = [n for n in useful if n.kind is Kind.FACT]
    if facts:
        return Answer(_list([f.text() for f in facts], limit=3) + ".",
                      "whatabout", [f.id for f in facts[:3]])
    latest = max(useful, key=lambda n: n.created_at)
    return Answer(f"{ago(now - latest.created_at)} ago you said: "
                  f"{latest.text()[:160]}", "whatabout", [latest.id])


def deep(store: Store, text: str, cfg: BrainConfig, now: float,
         k: int = 8) -> list[Neuron]:
    """Explicit deep recall: search the cold tier too.

    The escape hatch that makes tiering safe. A memory whose retrieval
    strength has bottomed out is unreachable by the ordinary cascade, and
    still findable when someone deliberately goes looking.
    """
    hits = store.search(text, k=k, scope="all")
    return [n for n in store.get_many(nid for nid, _ in hits).values()]
