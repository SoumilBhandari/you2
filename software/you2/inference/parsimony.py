"""Never store what the weights already know.

The language model holds world knowledge -- that penicillin is an antibiotic,
that Lisbon is in Portugal, that migrations slip. Storing any of that in the
personal graph is worse than useless: it costs bytes, and it costs *retrieval
precision*, because every generic fact is one more thing that looks
superficially relevant to some future query.

So the encoding filter is not "is this true" or even "is this important". It
is:

    is this about THIS PERSON, and is it something the model could not have
    guessed?

That question has a cheap, embarrassing-to-not-do answer: mask the
person-specific part and ask the model to fill it in. If it predicts what the
person said, the claim was world knowledge wearing a personal pronoun.

Cheaper still, and enough for most cases, are the structural signals below --
which cost nothing and run first, so the model only sees the ambiguous
remainder.

This is where the "small store, high intelligence" tradeoff actually gets
made. The graph stays small *because* it refuses everything the cortex
already covers, and the system stays smart because the cortex is enormous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import BrainConfig
from ..llm.client import LLMError
from ..substrate.embed import tokenize
from ..substrate.neuron import Kind
from ..substrate.store import Store

#: A claim whose subject is one of these is about the person or their world.
PERSONAL_SUBJECTS = ("user", "my", "our")

#: "X is a/an Y" — a categorical definition, which is exactly what a language
#: model already holds. Note the requirement of an article after the copula:
#: "the boiler is due a service" is not definitional and must not be caught.
_DEFINITIONAL = re.compile(
    r"^\s*(?:the\s+|a\s+|an\s+)?[\w'-]+(?:\s+[\w'-]+){0,2}\s+"
    r"(?:is|are|was|were)\s+(?:a|an|the)\s+\w+", re.IGNORECASE)

#: Universals about people in general.
_UNIVERSAL = re.compile(
    r"\b(all|every|most|no|any)\s+"
    r"(people|humans|human beings|men|women|adults|children|one)\b",
    re.IGNORECASE)

#: Claims with no first-person or named-entity anchor at all.
_HAS_ANCHOR = re.compile(r"\b(the user|user'?s|my|i|we|our)\b", re.IGNORECASE)

MIN_TOKENS = 2


@dataclass(slots=True)
class Verdict:
    keep: bool
    reason: str
    checked_model: bool = False

    def __repr__(self) -> str:
        return f"Verdict({'keep' if self.keep else 'drop'}: {self.reason})"


def structural(store: Store, key: str, text: str,
               cfg: BrainConfig) -> Verdict | None:
    """Free checks. Returns None when the answer is genuinely ambiguous."""
    subject = (key or "").partition("/")[0].lower()
    body = text.strip()

    if len(tokenize(body)) < MIN_TOKENS:
        return Verdict(False, "too short to be a claim")

    # A claim anchored to the user, or to an entity this person actually
    # talks about, is personal by construction.
    if subject in PERSONAL_SUBJECTS or _HAS_ANCHOR.search(body):
        return Verdict(True, "anchored to the person")

    if store.find_entity(subject.replace("_", " ")) is not None:
        return Verdict(True, "about a known entity")

    if _UNIVERSAL.search(body):
        return Verdict(False, "universal claim about people")

    if _DEFINITIONAL.match(body):
        return Verdict(False, "categorical definition the model already holds")

    # Genuinely ambiguous. The caller may spend a model call; the default is
    # to keep, because dropping a real personal fact is much worse than
    # storing a generic one.
    return None


def by_model(llm, text: str) -> Verdict:
    """Ask whether the claim is guessable without knowing the person.

    One call, batched at night, on the small ambiguous remainder. Failure is
    *keep*: dropping a real personal fact is a much worse error than storing a
    generic one, and the asymmetry should be visible in the code.
    """
    if llm is None:
        return Verdict(True, "no model to check with")
    messages = [
        {"role": "system", "content":
            "You judge whether a claim is specific to one particular person "
            "or is general world knowledge anyone would know. Reply with JSON "
            "only."},
        {"role": "user", "content":
            f'Claim: "{text}"\n\n'
            'Is this something you would need to know THIS SPECIFIC PERSON to '
            'know, or is it general knowledge?\n\n'
            'Return JSON: {"personal": true|false, "why": "one clause"}'},
    ]
    try:
        out = llm.structured(messages)
        personal = bool(out.get("personal", True))
        return Verdict(personal,
                       str(out.get("why", "model judgement"))[:80],
                       checked_model=True)
    except (LLMError, TypeError, ValueError, KeyError):
        return Verdict(True, "check failed, keeping")


def worth_storing(store: Store, llm, key: str, text: str, cfg: BrainConfig,
                  *, use_model: bool = False) -> Verdict:
    quick = structural(store, key, text, cfg)
    if quick is not None:
        return quick
    if use_model and cfg.parsimony_use_model:
        return by_model(llm, text)
    return Verdict(True, "ambiguous, keeping")


def audit(store: Store, cfg: BrainConfig, limit: int = 200) -> list[tuple[int, str]]:
    """Facts currently stored that the structural filter would now reject.

    Run after changing the filter: it tells you what a stricter policy would
    have refused, without deleting anything. Storage policy should be
    reversible, and a filter that silently rewrites history is not.
    """
    out: list[tuple[int, str]] = []
    for fact in store.by_kind(Kind.FACT, limit=limit, live_only=True):
        if fact.meta.get("derived"):
            continue
        verdict = structural(store, fact.key or "", fact.text(), cfg)
        if verdict is not None and not verdict.keep:
            out.append((fact.id, verdict.reason))
    return out
