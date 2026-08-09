"""Binding: writing an experience into the substrate.

Everything here runs **after the spoken reply**. Never in the critical path.
The hot path acquires no new blocking calls, ever.

**No model call at all on the write path.** docs/brain.md originally put
entity extraction here as "the only LLM call in the write path"; moving it to
the rule extractor is strictly better and the reasoning is worth keeping:
the write path then works with no network, adds no latency variance, and
cannot fail a turn. The night pass reads the same episodes with the real
model anyway and upgrades what the rules got wrong -- so the model still does
the hard reading, just where there is time for it.

Facts are deliberately not extracted here either. Extracting a fact requires
knowing what it supersedes, and that requires context this moment does not
have. That happens at night, over a batch, where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import DAY
from ..config import BrainConfig
from ..llm import rules
from ..perception.discourse import Commitment
from ..substrate.neuron import Kind, Neuron
from ..substrate.store import Store
from ..substrate.synapse import Edge
from .segment import Episode

#: Weight of a fresh episode->entity edge. Structural, so it is exempt from
#: nightly downscaling and starts high.
ABOUT_WEIGHT = 0.75

#: Consecutive episodes in a session.
NEXT_WEIGHT = 0.35

#: Initial association between two entities named in the same breath.
CO_MENTION = 0.25

DEFAULT_LOOP_DAYS = 14.0

#: How much of a commitment must be echoed for an utterance to close it.
CLOSE_ECHO = 0.40

#: And to count as restating one that is already open.
DEDUP_ECHO = 0.75


#: Similarity above which a low-salience utterance is treated as a repeat of
#: an existing episode rather than as nothing at all.
REPEAT_SIMILARITY = 0.82


@dataclass(slots=True)
class Encoded:
    episode: Neuron | None = None
    entities: list[Neuron] = field(default_factory=list)
    loops: list[Neuron] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""
    repeated: int | None = None

    def __repr__(self) -> str:
        if self.repeated is not None:
            return f"Encoded(repeat of #{self.repeated})"
        if self.skipped:
            return f"Encoded(skipped: {self.reason})"
        return (f"Encoded(#{self.episode.id if self.episode else '-'}, "
                f"{len(self.entities)} entities, {len(self.loops)} loops)")


def encode(store: Store, episode: Episode, cfg: BrainConfig | None = None, *,
           previous: int | None = None, source: str = "speech") -> Encoded:
    """Write one episode and everything it binds to."""
    cfg = cfg or store.cfg
    now = episode.ended_at or store.clock.now()
    salience = episode.salience

    text = episode.text
    if not text.strip():
        return Encoded(skipped=True, reason="empty")

    if salience < cfg.salience_floor:
        # Before discarding: is this the same thing said again?
        #
        # This matters more than it looks. The salience gate scores a
        # recurring complaint as low-surprise and low-novelty — correctly, we
        # *do* already know it — and then drops it. But "this happened again"
        # is precisely the evidence the recurrence detector needs, so a
        # straight discard makes the fourth Tuesday in a row invisible and
        # the whole category undetectable.
        #
        # Repetition strengthens the existing trace rather than laying down a
        # new one, and the occurrence is counted. That is also closer to what
        # actually happens: you do not form a fresh memory of the commute you
        # have made four hundred times.
        repeat = _record_repeat(store, text, now, episode)
        if repeat is not None:
            return Encoded(skipped=True, reason="repeat", repeated=repeat)
        return Encoded(skipped=True, reason=f"salience {salience:.2f}")

    strong = salience >= cfg.salience_strong
    ep = store.add(Neuron(
        kind=Kind.EPISODE,
        content=text,
        created_at=episode.started_at or now,
        last_access=now,
        salience=salience,
        surprise=episode.surprise,
        arousal=episode.arousal,
        valence=episode.valence,
        session_id=episode.session_id,
        source_ref=source,
        # Arousal raises initial storage strength directly: emotional
        # modulation of consolidation, and the reason the things you felt
        # strongly about are the ones you still have.
        storage_strength=1.0 + 0.5 * episode.arousal,
        pinned=any(s.explicit >= 1.0 for s in episode.saliences),
        meta={"boundary": episode.boundary_reason,
              "turns": len(episode.turns),
              "priority_replay": strong},
    ))
    out = Encoded(episode=ep)

    if previous is not None:
        store.connect(previous, ep.id, Edge.NEXT, NEXT_WEIGHT, at=now)

    # ---- entities ---------------------------------------------------------
    entity_ids: list[int] = []
    for found in rules.find_entities(text):
        entity = resolve_entity(store, found, now)
        if entity is None:
            continue
        entity_ids.append(entity.id)
        out.entities.append(entity)
        store.connect(ep.id, entity.id, Edge.ABOUT, ABOUT_WEIGHT, at=now)

    for i in range(len(entity_ids)):
        for j in range(i + 1, len(entity_ids)):
            store.strengthen(entity_ids[i], entity_ids[j], 0.6, 0.6)

    # ---- open loops -------------------------------------------------------
    for s in episode.saliences:
        if s.discourse is None:
            continue
        for c in s.discourse.commitments:
            loop = _open_loop(store, c, ep.id, entity_ids, now)
            if loop is not None:
                out.loops.append(loop)
        if s.discourse.is_closure:
            out.closed.extend(_close_loops(store, text, now))

    store.log("encode", {
        "episode": ep.id, "salience": round(salience, 3),
        "surprise": round(episode.surprise, 3), "boundary": episode.boundary_reason,
        "entities": [e.id for e in out.entities], "loops": [l.id for l in out.loops],
        "strong": strong,
    })
    return out


def _record_repeat(store: Store, text: str, now: float,
                   episode: Episode) -> int | None:
    """Log a re-occurrence against the episode it repeats.

    Cheap: one vector search and one update. The timestamps accumulate in
    `meta["repeats"]`, and the recurrence detector expands them back into
    occurrences, so a pattern stated the same way twenty times is still a
    pattern of twenty.
    """
    hits = store.search(text, k=1, scope="hot", min_score=REPEAT_SIMILARITY)
    if not hits:
        return None
    match = store.get(hits[0][0])
    if match is None or match.kind is not Kind.EPISODE:
        return None

    repeats = list(match.meta.get("repeats") or [])
    repeats.append(round(now, 1))
    match.meta["repeats"] = repeats[-64:]
    match.access_count += 1
    match.last_access = now
    # Repetition is evidence, so it raises storage strength -- but with
    # diminishing returns, or a daily commute would out-rank a wedding.
    match.storage_strength += 0.15 / (1.0 + len(repeats)) ** 0.5
    match.arousal = max(match.arousal, episode.arousal)
    store.save(match)
    store.log("repeat", {"episode": match.id, "n": len(repeats)})
    return match.id


def resolve_entity(store: Store, found: dict, now: float) -> Neuron | None:
    """Match against existing entities by alias first, then by embedding.

    Alias-first is deliberate. Embedding-first merges "David Anderson" and
    "Dave Andrews", and an entity merge is very hard to undo once episodes
    have bound to it.
    """
    name = str(found.get("name", "")).strip()
    if len(name) < 2:
        return None
    aliases = [str(a) for a in found.get("aliases", [])] + [name]

    for alias in aliases:
        hit = store.find_entity(alias)
        if hit is not None:
            new = [a for a in aliases if a.lower() not in store.alias_map()]
            if new:
                store.add_aliases(hit.id, new)
            return hit

    hits = store.search(name, k=3, scope="hot", min_score=0.90)
    for nid, _score in hits:
        candidate = store.get(nid)
        if candidate is not None and candidate.kind is Kind.ENTITY:
            store.add_aliases(candidate.id, aliases)
            return candidate

    return store.add(
        Neuron(kind=Kind.ENTITY, content=name, created_at=now, last_access=now,
               confidence=0.7, storage_strength=1.5,
               meta={"kind": found.get("kind", "thing"), "status": "active"}),
        aliases=aliases)


def _open_loop(store: Store, c: Commitment, episode_id: int,
               entity_ids: list[int], now: float) -> Neuron | None:
    # Don't reopen something already open and unresolved.
    for existing in store.by_kind(Kind.LOOP, limit=200):
        if existing.invalid_at is None:
            if _echo(store, existing.content, c.text) > DEDUP_ECHO:
                return None

    expected = c.expected_by if c.expected_by is not None else now + DEFAULT_LOOP_DAYS * DAY
    loop = store.add(Neuron(
        kind=Kind.LOOP, content=c.text.strip(), created_at=now, last_access=now,
        confidence=c.confidence, salience=0.6, storage_strength=2.0,
        meta={"expected_by": expected, "cites": [episode_id]},
    ))
    store.connect(loop.id, episode_id, Edge.SUPPORTS, 0.8, at=now)
    for eid in entity_ids:
        store.connect(loop.id, eid, Edge.ABOUT, ABOUT_WEIGHT, at=now)
    return loop


def _close_loops(store: Store, text: str, now: float) -> list[int]:
    """Resolve the open loop this utterance settles.

    Only the best match closes, not everything above threshold. "That's
    sorted" after discussing three things is genuinely ambiguous, and wrongly
    closing a live commitment is the expensive error: the device then never
    reminds you about the thing you actually forgot. Leaving one open costs a
    redundant nudge, which is cheap.

    A loop is closed, not deleted -- "you said you'd do X, and you did" is
    worth being able to say, and the recurrence detector needs the record.
    """
    best: tuple[float, Neuron] | None = None
    for loop in store.by_kind(Kind.LOOP, limit=200):
        if loop.invalid_at is not None:
            continue
        score = _echo(store, loop.content, text)
        if score > CLOSE_ECHO and (best is None or score > best[0]):
            best = (score, loop)
    if best is None:
        return []
    score, loop = best
    loop.invalid_at = now
    loop.meta["closed_by_text"] = text[:160]
    loop.meta["closed_score"] = round(score, 3)
    store.save(loop)
    return [loop.id]


def _echo(store: Store, loop_text: str, text: str) -> float:
    """How much of `loop_text` is echoed by `text`.

    Containment, not Jaccard. A closure statement is almost always shorter
    and differently framed than the commitment it resolves -- "I'll look into
    the Anderson contract by Friday" against "the Anderson contract is sorted"
    is a Jaccard of 0.29 and an obvious match to any reader. Containment asks
    the right question: how much of the commitment does this utterance
    mention?

    Backed up by embedding similarity, so a paraphrase with no shared tokens
    still lands.
    """
    import numpy as np

    from ..substrate.embed import tokenize
    a, b = set(tokenize(loop_text)), set(tokenize(text))
    if not a or not b:
        return 0.0
    shared = a & b
    lexical = len(shared) / len(a)
    if len(shared) < 2:
        # One shared word is a coincidence, not a resolution.
        lexical *= 0.5
    embedded = float(np.dot(store.embed(loop_text), store.embed(text)))
    return max(lexical, embedded)
