"""Scoring extraction against hand-labelled transcripts.

The year-long harness answers "does retrieval hold at month 24". This answers
the question underneath it, which had no instrument at all: **is what goes
into the store correct.**

It runs the corpus in order, threading the keys produced so far back into
each call exactly as the night does, and scores six things:

    recall      of the claims a careful human would write down, how many
    precision   of what it wrote down, how much was asked for
    stability   the same dimension stated twice, weeks apart, in different
                words -- one key, or two? This is the number that decides
                whether supersession ever fires.
    collisions  the opposite failure: two different dimensions sharing a key,
                which silently destroys one of them. Stability without this
                is trivially gamed by mapping everything to one key.
    traps       questions, hypotheticals, world knowledge, other people's
                opinions, things true for an afternoon
    grounding   how much of each claim was actually in the text it cites

Any extractor with the batch signature can be scored, so the rule extractor,
the deterministic fake and a real model over the network are all directly
comparable on one table -- which is the only way to know whether the model is
worth the round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from ..llm import rules, validate
from ..substrate.embed import stem, tokenize
from .transcripts import CORPUS, GoldFact, Session, dimensions, stability_pairs

Payload = dict[str, Any]


class Extractor(Protocol):
    def __call__(self, texts: Sequence[str], ids: Sequence[int], now: float,
                 known_keys: Sequence[str]) -> Payload: ...


# ------------------------------------------------------------------ matching

def covers(text: str, must: str) -> bool:
    """Does the claim carry the content words the gold demands?

    Stemmed, unordered, and substring-tolerant on each word, because
    "semi-skimmed" and "semi skimmed" are the same word and no labelling
    scheme should care.
    """
    have = {stem(w) for w in tokenize(text)}
    flat = " ".join(sorted(have))
    for word in tokenize(must):
        s = stem(word)
        if s in have:
            continue
        if any(s in h or h in s for h in have if len(h) > 3 and len(s) > 3):
            continue
        if s in flat.replace(" ", ""):
            continue
        return False
    return True


# -------------------------------------------------------------------- result

@dataclass
class Score:
    label: str = "rules"
    facts_expected: int = 0
    facts_found: int = 0
    facts_extra: int = 0
    facts_allowed: int = 0
    entities_expected: int = 0
    entities_found: int = 0
    kind_errors: list[str] = field(default_factory=list)
    loops_expected: int = 0
    loops_found: int = 0
    closures_expected: int = 0
    closures_found: int = 0
    trap_hits: list[str] = field(default_factory=list)
    traps_total: int = 0
    stable: int = 0
    stability_total: int = 0
    collisions: list[str] = field(default_factory=list)
    grounding: float = 0.0
    third_person: float = 0.0
    rejected: dict[str, int] = field(default_factory=dict)
    snaps: int = 0
    misses: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)
    seconds: float = 0.0
    calls: int = 0

    @property
    def recall(self) -> float:
        return self.facts_found / max(1, self.facts_expected)

    @property
    def precision(self) -> float:
        kept = self.facts_found + self.facts_extra
        return self.facts_found / kept if kept else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def stability(self) -> float:
        return self.stable / max(1, self.stability_total)

    @property
    def entity_recall(self) -> float:
        return self.entities_found / max(1, self.entities_expected)

    @property
    def loop_recall(self) -> float:
        return self.loops_found / max(1, self.loops_expected)

    @property
    def clean(self) -> bool:
        """No trap fired and no two dimensions share a key. The bar for
        shipping: a system may miss things, but what it stores must be
        right."""
        return not self.trap_hits and not self.collisions

    def row(self) -> str:
        return (f"{self.label:<16} {self.precision:>6.2f} {self.recall:>7.2f} "
                f"{self.f1:>6.2f} {self.stability:>10.2f} "
                f"{len(self.collisions):>6} {len(self.trap_hits):>6} "
                f"{self.entity_recall:>8.2f} {self.loop_recall:>7.2f} "
                f"{self.grounding:>10.2f}")

    def report(self) -> str:
        lines = [
            f"[{self.label}]  {self.calls} batches, {self.seconds:.2f}s",
            f"  facts       {self.facts_found}/{self.facts_expected} found, "
            f"{self.facts_extra} unasked-for, {self.facts_allowed} allowed  "
            f"(P {self.precision:.2f}  R {self.recall:.2f}  F1 {self.f1:.2f})",
            f"  keys        stability {self.stability:.2f} "
            f"({self.stable}/{self.stability_total} dimensions reused a key), "
            f"{len(self.collisions)} collisions, {self.snaps} snapped",
            f"  entities    {self.entities_found}/{self.entities_expected}, "
            f"{len(self.kind_errors)} miskinded",
            f"  loops       {self.loops_found}/{self.loops_expected} open, "
            f"{self.closures_found}/{self.closures_expected} closed",
            f"  traps       {len(self.trap_hits)}/{self.traps_total} fired",
            f"  grounding   {self.grounding:.2f} mean, "
            f"{self.third_person:.2f} third person",
        ]
        if self.rejected:
            worst = sorted(self.rejected.items(), key=lambda kv: -kv[1])
            lines.append("  rejected    " + ", ".join(f"{n}x {r}"
                                                      for r, n in worst[:6]))
        for name, items in (("MISS", self.misses), ("TRAP", self.trap_hits),
                            ("COLLISION", self.collisions),
                            ("KIND", self.kind_errors),
                            ("EXTRA", self.spurious)):
            for item in items[:12]:
                lines.append(f"  {name:<10}  {item}")
        return "\n".join(lines)


# ------------------------------------------------------------------ adapters

def rule_extractor(texts: Sequence[str], ids: Sequence[int], now: float,
                   known_keys: Sequence[str]) -> Payload:
    return rules.extract(list(texts), list(ids), now).as_dict()


def model_extractor(llm: Any) -> Extractor:
    """Any client with `.structured()`: the real one, or the fake."""
    from ..llm import prompts
    from ..llm.client import LLMError
    from ..substrate.neuron import Kind, Neuron

    def call(texts: Sequence[str], ids: Sequence[int], now: float,
             known_keys: Sequence[str]) -> Payload:
        episodes = [Neuron(id=i, kind=Kind.EPISODE, content=t, created_at=now,
                           last_access=now) for i, t in zip(ids, texts)]
        try:
            out = llm.structured(prompts.extract_batch(episodes, now, known_keys))
        except (LLMError, ValueError, TypeError, KeyError) as exc:
            return {"error": str(exc)[:200]}
        return out if isinstance(out, dict) else {}

    return call


# --------------------------------------------------------------------- runner

def run(extractor: Extractor = rule_extractor, *, label: str = "rules",
        corpus: Sequence[Session] = CORPUS, validated: bool = True,
        ground_floor: float = validate.GROUND_FLOOR,
        verbose: bool = False) -> Score:
    """Live through the corpus once, in order, scoring as it goes."""
    import time as _time

    t0 = _time.perf_counter()
    score = Score(label=label)
    known: list[str] = []
    keys_for_dim: dict[str, set[str]] = {}
    seen_for_dim: dict[str, int] = {}
    dim_for_key: dict[str, set[str]] = {}
    grounds: list[float] = []
    third: list[float] = []
    next_id = 1

    for session in corpus:
        ids = list(range(next_id, next_id + len(session.turns)))
        next_id += len(session.turns)
        sources = dict(zip(ids, session.turns))
        joined = " ".join(session.turns)

        payload = extractor(session.turns, ids, session.day * 86400.0, known)
        score.calls += 1
        if validated:
            cleaned = validate.clean(payload, sources=sources, known_keys=known,
                                     ground_floor=ground_floor)
            facts = cleaned.facts
            entities = cleaned.entities
            loops = cleaned.loops
            closed = cleaned.closed
            score.snaps += len(cleaned.snaps)
            for r in cleaned.rejected:
                score.rejected[r.reason] = score.rejected.get(r.reason, 0) + 1
        else:
            facts = [f for f in (payload.get("facts") or [])
                     if isinstance(f, dict) and f.get("key") and f.get("text")]
            entities = [e for e in (payload.get("entities") or [])
                        if isinstance(e, dict)]
            loops = [l for l in (payload.get("loops") or []) if isinstance(l, dict)]
            closed = [str(c) for c in (payload.get("closed") or [])]

        # ---- facts ---------------------------------------------------------
        matched: set[int] = set()
        for gold in session.facts:
            score.facts_expected += 1
            hit = _first(facts, gold, matched)
            if hit is None:
                score.misses.append(f"{session.label}: {gold.dim} ({gold.must})")
                continue
            score.facts_found += 1
            key = str(hit["key"])
            keys_for_dim.setdefault(gold.dim, set()).add(key)
            seen_for_dim[gold.dim] = seen_for_dim.get(gold.dim, 0) + 1
            dim_for_key.setdefault(key, set()).add(gold.dim)

        for i, fact in enumerate(facts):
            text = str(fact.get("text") or "")
            grounds.append(validate.grounding(text, joined,
                                              str(fact.get("quote") or "")))
            third.append(0.0 if _first_person(text) else 1.0)
            if i in matched:
                continue
            if any(covers(text, allowed) for allowed in session.allow):
                score.facts_allowed += 1
                continue
            score.facts_extra += 1
            score.spurious.append(f"{session.label}: {fact.get('key')} = {text}")

        # ---- traps ---------------------------------------------------------
        score.traps_total += (len(session.traps) + len(session.entity_traps)
                              + len(session.loop_traps))
        for trap in session.traps:
            for fact in facts:
                if covers(str(fact.get("text") or ""), trap):
                    score.trap_hits.append(
                        f"{session.label}: stored “{fact.get('text')}”")
                    break
        for trap in session.entity_traps:
            for entity in entities:
                if trap.lower() in str(entity.get("name", "")).lower():
                    score.trap_hits.append(
                        f"{session.label}: “{entity.get('name')}” is not an entity")
                    break
        for trap in session.loop_traps:
            for loop in loops:
                if covers(str(loop.get("text") or ""), trap):
                    score.trap_hits.append(
                        f"{session.label}: opened “{loop.get('text')}”")
                    break

        # ---- entities, loops, closures -------------------------------------
        for spec in session.entities:
            name, _, want_kind = spec.partition(":")
            score.entities_expected += 1
            hit = next((e for e in entities
                        if _same_entity(name, str(e.get("name", "")))), None)
            if hit is None:
                score.misses.append(f"{session.label}: entity {name}")
                continue
            score.entities_found += 1
            if want_kind and str(hit.get("kind")) != want_kind:
                score.kind_errors.append(
                    f"{session.label}: {name} is a {hit.get('kind')}, "
                    f"not a {want_kind}")
        for want in session.loops:
            score.loops_expected += 1
            if any(covers(str(l.get("text") or ""), want) for l in loops):
                score.loops_found += 1
            else:
                score.misses.append(f"{session.label}: loop “{want}”")
        for want in session.closed:
            score.closures_expected += 1
            if any(covers(c, want) for c in closed):
                score.closures_found += 1
            else:
                score.misses.append(f"{session.label}: closure “{want}”")

        known = sorted({*known, *(str(f["key"]) for f in facts)})
        if verbose:
            print(f"  {session.label:<38} facts={len(facts)} "
                  f"loops={len(loops)} keys={len(known)}")

    # ---- key discipline ----------------------------------------------------
    # A dimension counts as stable only when *every* statement of it was
    # extracted AND they agree on a key. Scoring agreement alone would let a
    # recall failure look like discipline, and it is the same failure either
    # way: the second statement never reaches the first, so nothing is ever
    # superseded.
    asserted = dimensions()
    for dim in stability_pairs():
        score.stability_total += 1
        keys = keys_for_dim.get(dim, set())
        seen = seen_for_dim.get(dim, 0)
        if len(keys) == 1 and seen == len(asserted[dim]):
            score.stable += 1
        elif seen < len(asserted[dim]):
            score.misses.append(
                f"unstable: {dim} stated {len(asserted[dim])}x, "
                f"extracted {seen}x")
        else:
            score.misses.append(f"unstable: {dim} -> {sorted(keys)}")
    for key, dims in dim_for_key.items():
        if len(dims) > 1:
            score.collisions.append(f"{key} holds {sorted(dims)}")

    score.grounding = sum(grounds) / len(grounds) if grounds else 0.0
    score.third_person = sum(third) / len(third) if third else 0.0
    score.seconds = _time.perf_counter() - t0
    return score


def _first(facts: Sequence[dict], gold: GoldFact,
           matched: set[int]) -> dict | None:
    for i, fact in enumerate(facts):
        if i in matched:
            continue
        if covers(str(fact.get("text") or ""), gold.must):
            matched.add(i)
            return fact
    return None


_FIRST_PERSON_CLAIM = __import__("re").compile(r"\b(i|i'm|i've|i'll|my|me)\b",
                                               __import__("re").I)


def _first_person(text: str) -> bool:
    return bool(_FIRST_PERSON_CLAIM.search(text))


def _same_entity(want: str, got: str) -> bool:
    a, b = set(tokenize(want)), set(tokenize(got))
    return bool(a) and bool(b) and (a <= b or b <= a)


# ------------------------------------------------------------------ compare

def compare(scores: Sequence[Score]) -> str:
    head = (f"{'extractor':<16} {'prec':>6} {'recall':>7} {'F1':>6} "
            f"{'stability':>10} {'colls':>6} {'traps':>6} {'entity':>8} "
            f"{'loops':>7} {'grounded':>10}")
    return "\n".join([head] + [s.row() for s in scores])


def suite(llm: Any | None = None, *, ground_floor: float = validate.GROUND_FLOOR
          ) -> list[Score]:
    """The standard run: rules alone, rules validated, and a model if given.

    "rules raw" is an ablation of the validator in the same spirit as the
    retrieval ablations -- if cleaning changes nothing, it is not earning its
    place.
    """
    from .sloppy import SloppyLLM

    out = [
        run(rule_extractor, label="rules raw", validated=False),
        run(rule_extractor, label="rules", ground_floor=ground_floor),
        run(model_extractor(SloppyLLM()), label="sloppy raw", validated=False),
        run(model_extractor(SloppyLLM()), label="sloppy", ground_floor=ground_floor),
    ]
    if llm is not None:
        out.append(run(model_extractor(llm), label="model",
                       ground_floor=ground_floor))
    return out
