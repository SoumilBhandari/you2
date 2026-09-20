"""A model that is wrong the way real models are wrong.

`FakeLLM` is clean by construction: it is the rule extractor in a trench
coat, so it produces well-formed keys, third-person claims and honest
citations. That makes it a good substrate for measuring *consolidation* and a
useless one for measuring the thing that stands between an extractor and the
store, because there is nothing for a validator to catch.

So this is the adversary. Every degradation below is a failure I have watched
small instruction-tuned models make on this exact prompt, and each one has a
different consequence:

    first person        gets read back aloud as "the user don't like meetings"
    value in the key    `user/pref_oat_milk` can never supersede
                        `user/pref_milk`, so the store keeps both forever
    camelCase / no      `User/CoffeePreference`, `coffee_preference` — three
    subject             spellings of one dimension, three rows
    invention           a fact that is *plausible* and not in the transcript.
                        The worst one: unfalsifiable, and "forget that"
                        cannot reach it
    ephemera            "the user is running late this morning", stored
                        forever, distracting every retrieval about mornings
    questions as facts  the model echoes the user's question back as a claim
    reported wrappers   "the user said that they prefer X" — a fact about the
                        conversation wearing a fact about the person
    phantom citations   cites an episode id that was never in the prompt
    the assistant       "Friday" becomes an entity and hubs the graph
    complaints as loops "someone should fix the gate" becomes a commitment
                        the device nags about

It is deterministic — degradations are chosen by batch index, not randomly —
so the number it produces is a number you can regress against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..llm import rules
from ..llm.client import Message, Usage

_EPISODE_LINE = re.compile(r"^\[(\d+)\]\s+\(([^)]*)\)\s+(.*)$", re.MULTILINE)

#: Plausible claims that are nowhere in the corpus. Invention is not random
#: noise -- models invent things that *fit*, which is exactly why grounding
#: has to be checked against the source rather than against a vibe.
_INVENTIONS = (
    ("user/commute", "the user cycles to work most days"),
    ("user/pref_restaurant", "the user prefers Italian food"),
    ("anderson/location", "Anderson is based in the London office"),
    ("user/hobby", "the user plays squash on Thursdays"),
)


#: The irregulars have to be listed, or stripping the third-person -s
#: manufactures words ("does" -> "doe") that no model would ever emit and
#: that then fail the grounding check for the wrong reason. An adversary that
#: is unrealistically bad measures nothing.
_UNCONJUGATE = {"does": "do", "is": "am", "has": "have", "was": "was",
                "cannot": "cannot", "will": "will", "would": "would"}


def _first_person(text: str) -> str:
    out = re.sub(r"^the user's\b", "my", text, flags=re.I)
    out = re.sub(r"^the user has\b", "I've", out, flags=re.I)
    out = re.sub(r"^the user is\b", "I'm", out, flags=re.I)

    def verb(m: re.Match[str]) -> str:
        word = m.group(1)
        fixed = _UNCONJUGATE.get(word.lower())
        if fixed is not None:
            return f"I {fixed}"
        return f"I {word[:-1]}" if word.endswith("s") and len(word) > 3 \
            else f"I {word}"

    out = re.sub(r"^the user\s+(\w+)\b", verb, out, flags=re.I)
    return re.sub(r"^the user\b", "I", out, flags=re.I)


def _value_in_key(key: str, text: str) -> str:
    subject, _, predicate = key.partition("/")
    tail = [w for w in rules.tokenize(text)][-2:]
    return f"{subject}/{predicate}_{'_'.join(tail)}" if tail else key


def _camel(key: str) -> str:
    subject, _, predicate = key.partition("/")
    parts = predicate.split("_")
    return (subject.title() + "/" + parts[0]
            + "".join(p.title() for p in parts[1:]))


@dataclass
class SloppyLLM:
    """Same surface as `LLM`. Wrong in the ways that matter."""

    now: float = 0.0
    usage: Usage = field(default_factory=Usage)
    batches: int = 0
    damage: list[str] = field(default_factory=list)

    def complete(self, messages: Sequence[Message], **kw: Any) -> str:
        import json
        return json.dumps(self.structured(messages, **kw), default=str)

    def structured(self, messages: Sequence[Message], **kw: Any) -> Any:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        self.usage.add(sum(len(m["content"]) // 4 for m in messages), 60, 0.0)
        if not system.startswith("You extract structured memory"):
            from ..llm.fake import FakeLLM
            return FakeLLM(now=self.now).structured(messages, **kw)

        ids, texts = [], []
        for m in _EPISODE_LINE.finditer(user):
            ids.append(int(m.group(1)))
            texts.append(m.group(3))
        if not texts:
            return {"gist": "", "facts": [], "entities": [], "loops": [],
                    "closed": []}

        out = rules.extract(texts, ids, self.now).as_dict()
        n = self.batches
        self.batches += 1
        return self._degrade(out, n, texts, ids)

    # ------------------------------------------------------------- the damage

    def _degrade(self, out: dict, n: int, texts: list[str],
                 ids: list[int]) -> dict:
        facts = list(out.get("facts") or [])
        note = self.damage.append

        for i, f in enumerate(facts):
            turn = (n + i) % 5
            if turn == 0:
                f["text"] = _first_person(str(f["text"]))
                note("first person")
            elif turn == 1:
                f["key"] = _value_in_key(str(f["key"]), str(f["text"]))
                note("value in key")
            elif turn == 2:
                f["key"] = _camel(str(f["key"]))
                note("camelCase key")
            elif turn == 3:
                f["key"] = str(f["key"]).partition("/")[2] or str(f["key"])
                note("no subject in key")
            if (n + i) % 4 == 1:
                f["text"] = f"the user said that {f['text']}"
                note("reported wrapper")
            if (n + i) % 7 == 3:
                f["cites"] = [90000 + i]
                note("phantom citation")
            if (n + i) % 6 == 2:
                f["confidence"] = "high"
                note("confidence as a word")

        # Invention: one plausible fabrication every third batch.
        if n % 3 == 2:
            key, text = _INVENTIONS[(n // 3) % len(_INVENTIONS)]
            facts.append({"key": key, "text": text, "cites": list(ids[:1]),
                          "confidence": 0.85})
            note("invented a fact")

        # The user's own words, echoed back as claims.
        for text in texts:
            if text.strip().endswith("?"):
                facts.append({"key": "user/question", "cites": list(ids[:1]),
                              "text": f"the user asked {text.rstrip('?')}",
                              "confidence": 0.6})
                note("question as a fact")
            if re.search(r"\b(today|this morning|right now)\b", text, re.I):
                facts.append({"key": "user/current_state", "cites": list(ids[:1]),
                              "text": f"the user is {text.split(',')[0].strip()}",
                              "confidence": 0.7})
                note("ephemeral state")

        entities = list(out.get("entities") or [])
        if n % 2 == 0:
            entities.append({"name": "Friday", "kind": "person",
                             "aliases": ["friday"], "cites": list(ids[:1])})
            note("the assistant as an entity")

        loops = list(out.get("loops") or [])
        for text in texts:
            if re.search(r"\b(someone|somebody) should\b", text, re.I):
                loops.append({"text": text.strip(), "cites": list(ids[:1]),
                              "days": 7})
                note("a complaint as a commitment")

        return {"gist": out.get("gist", ""), "facts": facts,
                "entities": entities, "loops": loops,
                "closed": out.get("closed") or []}

    def __repr__(self) -> str:
        return f"SloppyLLM({self.batches} batches, {len(self.damage)} defects)"
