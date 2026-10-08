"""Prompts.

Two rules run through all of them.

**Never invent.** Every fact carries provenance, and a fact whose provenance
is a hallucination is worse than no fact -- it is unfalsifiable and it
poisons retrieval forever. So extraction is instructed to quote or drop.

**Facts need a stable key.** `subject/predicate`. Without it a later
statement can only sit *next* to the earlier one, and supersession -- the
mechanism that stops the store rotting -- is impossible. Getting the model to
produce consistent keys is most of the work in these prompts.
"""

from __future__ import annotations

from typing import Sequence

from ..substrate.neuron import Neuron

SYSTEM_EXTRACT = """\
You extract structured memory from a person's conversations with their \
assistant. You are precise and you never invent.

WHAT TO RECORD
Durable claims only: preferences, relationships, commitments, constraints, \
identity, roles, recurring circumstances, standing facts about their things.

WHAT TO IGNORE — returning nothing is the right answer for most turns:
- questions they asked ("what did I say about X?")
- hypotheticals and things they are only considering ("I might…", "if I…")
- states that are true for an afternoon ("I'm shattered today", "running \
late") — unless marked as recurring ("every Tuesday", "always")
- world knowledge anyone would know ("Lisbon is in Portugal")
- what the assistant said, and anything about the conversation itself
- someone else's opinion attributed to the user. If Elena thinks they should \
quit, that is a fact about Elena.

KEYS
Every fact needs a stable key, subject/predicate, lowercase snake_case:
  user/milk_preference        anderson/role        car/insurance_renewal
The key names the DIMENSION, never the value:
  RIGHT  user/milk_preference  = "the user drinks oat milk"
  WRONG  user/prefers_oat_milk = "the user drinks oat milk"
A key holding the value can never be replaced when they change their mind, \
and this is the single most damaging mistake you can make here. When a key \
below already covers the dimension, reuse it EXACTLY.

WRITING THE CLAIM
- Third person, present tense, one short sentence. "the user prefers X", \
never "I prefer X".
- Name the thing the claim is about even when they left it implicit: "I only \
ever buy semi-skimmed" said just after "we're out of milk" is about MILK, \
and the claim must say so or it can never be matched to what they say next.
- `quote` is the exact span you read it from, copied verbatim. If you cannot \
copy a span that supports the claim, do not make the claim.

ENTITIES are people, projects, organisations, places and recurring objects. \
Give each a canonical name and any aliases actually used. Not days of the \
week, not the assistant, not the user themselves.
Reply with JSON only."""

SYSTEM_ANSWER = """\
You are Friday, a desk assistant that knows this person well.

- Answer from the memory block when it is relevant. It is the only thing you \
know about them.
- If the memory block does not cover it, say so plainly rather than guessing \
about their life. Guessing is the one unrecoverable failure.
- Spoken output: short sentences, no lists, no markdown, no preamble. Two or \
three sentences unless asked for more.
- Do not narrate your memory ("I recall that...", "According to my notes"). \
Just know it."""

SYSTEM_JUDGE = """\
You compare a new claim against an existing one about the same subject and \
decide their relationship. You are conservative: when the two could both be \
true, say so rather than declaring a conflict.
Reply with JSON only."""

SYSTEM_INSIGHT = """\
You state a connection between memories in one sentence, to be read aloud.

- Say the connection, not the reasoning.
- Reference the specific memories concretely, with when they happened.
- If the connection is not actually interesting, say so -- returning \
useful=false is the correct answer most of the time.
Reply with JSON only."""


def _fmt(neurons: Sequence[Neuron], now: float) -> str:
    from ..activation.workspace import ago
    return "\n".join(
        f"[{n.id}] ({ago(now - n.created_at)} ago) {n.text()}" for n in neurons)


def extract_batch(episodes: Sequence[Neuron], now: float,
                  known_keys: Sequence[str] = ()) -> list[dict[str, str]]:
    """One call per replay batch, not per episode.

    Replay in the hippocampus is compressed and clustered rather than
    sequential, and here that is operationally decisive too: batching is ~10x
    cheaper, and the extraction is *better*, because a fact spanning several
    conversations is only visible when they are read together.
    """
    keys = ""
    if known_keys:
        keys = ("\nKeys already in use — reuse the exact key when the new "
                "statement is about the same thing:\n"
                + "\n".join(f"- {k}" for k in sorted(set(known_keys))[:60]))
    user = f"""\
Conversations from this person, read together:

{_fmt(episodes, now)}
{keys}

Return JSON:
{{
  "gist": "one sentence covering what these were about",
  "facts": [
    {{"key": "user/dimension", "text": "short third-person claim",
      "quote": "the exact words you read it from",
      "cites": [episode ids this came from], "confidence": 0.0-1.0}}
  ],
  "entities": [
    {{"name": "Canonical Name", "kind": "person|project|place|org|thing",
      "aliases": ["as actually said"], "note": "one clause, or empty",
      "cites": [episode ids]}}
  ],
  "loops": [
    {{"text": "what they committed to", "cites": [ids], "days": estimated days until due or null}}
  ],
  "closed": [ "text of any commitment these conversations resolve" ]
}}
Empty lists are correct and expected. Do not pad."""
    return [{"role": "system", "content": SYSTEM_EXTRACT},
            {"role": "user", "content": user}]


def judge_supersession(existing: Neuron, candidate_text: str,
                       now: float) -> list[dict[str, str]]:
    from ..activation.workspace import ago
    user = f"""\
Existing claim, recorded {ago(now - existing.created_at)} ago:
  "{existing.text()}"

New claim, from today:
  "{candidate_text}"

Both are about the same subject. Which is it?

  restatement — the same claim, no new information
  refinement  — the new one is more specific but consistent
  reversal    — the new one contradicts and replaces the old one
  conflict    — they contradict and you cannot tell which is current
  distinct    — they are actually about different things

Return JSON: {{"relation": "...", "why": "one clause",
"question": "a question to ask the user, only if relation is conflict"}}"""
    return [{"role": "system", "content": SYSTEM_JUDGE},
            {"role": "user", "content": user}]


def state_insight(kind: str, evidence: Sequence[Neuron], now: float,
                  hint: str = "") -> list[dict[str, str]]:
    user = f"""\
Connection type: {kind}
{hint}

Supporting memories:
{_fmt(evidence, now)}

Return JSON:
{{"useful": true|false,
  "text": "one sentence, spoken aloud, or empty if not useful",
  "why_not": "one clause if useful is false"}}"""
    return [{"role": "system", "content": SYSTEM_INSIGHT},
            {"role": "user", "content": user}]


def answer(memory_block: str, history: Sequence[dict[str, str]],
           utterance: str) -> list[dict[str, str]]:
    """The hot-path prompt.

    Ordered [stable persona] + [stable memory] + [volatile tail] so that the
    prefix stays cacheable, per docs/design.md section 4.3. The volatile part
    goes last, always.
    """
    messages = [{"role": "system", "content": SYSTEM_ANSWER}]
    if memory_block:
        messages.append({"role": "system",
                         "content": "What you know about them:\n" + memory_block})
    messages.extend(history)
    messages.append({"role": "user", "content": utterance})
    return messages


def dream_probe(fact: Neuron) -> list[dict[str, str]]:
    """Counterfactual probing: generate a plausible-but-false variant and see
    whether retrieval rejects it. A schema-robustness test, and the closest
    thing here to actual dreaming."""
    user = f"""\
Claim: "{fact.text()}"

Write one plausible-sounding but FALSE variant of this claim — same subject, \
same shape, different content. It should be the kind of thing someone might \
wrongly believe.

Return JSON: {{"false_variant": "..."}}"""
    return [{"role": "system", "content": SYSTEM_JUDGE},
            {"role": "user", "content": user}]
