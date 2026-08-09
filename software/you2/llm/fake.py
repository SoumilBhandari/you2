"""A deterministic stand-in for the model.

Dispatches on the system prompt and answers with the rule extractor. The
whole test suite and the two-year eval harness run through this, which means
they are fast, offline, and byte-identical on every machine.

What that does and does not validate is worth being explicit about, because
it is easy to overclaim:

  it validates    the consolidation machinery -- supersession firing on
                  matching keys, downscaling improving precision, decay and
                  tiering, replay prioritisation, discovery scoring, the
                  whole write path.
  it does not     extraction quality. How well an 8B model reads a
                  conversation is a separate question, measured separately,
                  against real transcripts.

Conflating those two is how a memory system ends up with a green test suite
and a store full of garbage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from . import rules
from .client import Message, Usage

_EPISODE_LINE = re.compile(r"^\[(\d+)\]\s+\(([^)]*)\)\s+(.*)$", re.MULTILINE)


@dataclass
class FakeLLM:
    """Same surface as `LLM`, no network."""

    now: float = 0.0
    usage: Usage = field(default_factory=Usage)
    scripted: list[str] = field(default_factory=list)
    """Popped in order by `complete`, if non-empty. For tests that need an
    exact reply."""
    seen: list[list[Message]] = field(default_factory=list)

    # ---------------------------------------------------------------- public

    def complete(self, messages: Sequence[Message], **kw: Any) -> str:
        self.seen.append(list(messages))
        self.usage.add(sum(len(m["content"]) // 4 for m in messages), 20, 0.0)
        if self.scripted:
            return self.scripted.pop(0)
        return self._dispatch(messages, as_text=True)

    def structured(self, messages: Sequence[Message], **kw: Any) -> Any:
        self.seen.append(list(messages))
        self.usage.add(sum(len(m["content"]) // 4 for m in messages), 40, 0.0)
        if self.scripted:
            import json
            return json.loads(self.scripted.pop(0))
        return self._dispatch(messages, as_text=False)

    def stream(self, messages: Sequence[Message], **kw: Any) -> Iterator[str]:
        text = self.complete(messages, **kw)
        for word in text.split(" "):
            yield word + " "

    # -------------------------------------------------------------- dispatch

    def _dispatch(self, messages: Sequence[Message], *, as_text: bool) -> Any:
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""

        if system.startswith("You extract structured memory"):
            result = self._extract(user)
            return _dump(result) if as_text else result
        if system.startswith("You compare a new claim"):
            result = self._judge(user)
            return _dump(result) if as_text else result
        if system.startswith("You state a connection"):
            result = self._insight(user)
            return _dump(result) if as_text else result
        if system.startswith("You are Friday"):
            return self._answer(messages)
        return "{}" if as_text else {}

    # --------------------------------------------------------------- handlers

    def _extract(self, user: str) -> dict:
        ids, texts = [], []
        for m in _EPISODE_LINE.finditer(user):
            ids.append(int(m.group(1)))
            texts.append(m.group(3))
        if not texts:
            return {"gist": "", "facts": [], "entities": [], "loops": [], "closed": []}

        out = rules.extract(texts, ids, self.now)
        # Honour the "reuse existing keys" instruction: if a listed key is a
        # near match for one we derived, adopt it. Real models do this badly
        # and it is the single largest source of failed supersession, so the
        # fake should not be unrealistically good at it either -- exact
        # dimension match only.
        known = set(re.findall(r"^- (\S+)$", user, re.MULTILINE))
        for f in out.facts:
            if f["key"] in known:
                continue
            subject, _, predicate = f["key"].partition("/")
            for k in known:
                ks, _, kp = k.partition("/")
                if ks == subject and kp.split("_")[-1] == predicate.split("_")[-1]:
                    f["key"] = k
                    break
        return out.as_dict()

    def _judge(self, user: str) -> dict:
        if "false variant" in user.lower() or "FALSE variant" in user:
            m = re.search(r'Claim:\s+"([^"]*)"', user)
            claim = m.group(1) if m else ""
            return {"false_variant": "not " + claim}
        quoted = re.findall(r'"([^"]{3,})"', user)
        if len(quoted) < 2:
            return {"relation": "distinct", "why": "could not parse"}
        rel = rules.relation(quoted[0], quoted[1])
        out = {"relation": rel, "why": "rule-based"}
        if rel == "conflict":
            out["question"] = f"Is it still true that {quoted[0]}?"
        return out

    def _insight(self, user: str) -> dict:
        kind = ""
        m = re.search(r"Connection type:\s*(\S+)", user)
        if m:
            kind = m.group(1)
        lines = [t for _i, _a, t in _EPISODE_LINE.findall(user)]
        if len(lines) < 2:
            return {"useful": False, "text": "", "why_not": "not enough evidence"}
        return {"useful": True,
                "text": f"{kind}: {lines[0][:70]} — and — {lines[1][:70]}",
                "why_not": ""}

    def _answer(self, messages: Sequence[Message]) -> str:
        block = ""
        for m in messages:
            if m["role"] == "system" and m["content"].startswith("What you know"):
                block = m["content"]
        utterance = messages[-1]["content"]
        if block:
            first = next((line.strip(" -") for line in block.splitlines()[2:]
                          if line.strip().startswith("-")), "")
            return f"About that: {first}." if first else f"Noted: {utterance}"
        return f"Noted: {utterance}"


def _dump(obj: object) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"), default=str)
