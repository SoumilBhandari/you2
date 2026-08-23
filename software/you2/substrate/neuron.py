"""A neuron is any unit of memory.

Not "an episodes table, a facts table and a graph" -- one table, one set of
dynamics, a `kind` discriminator. That uniformity is the whole reason
activation can flow from a remembered Tuesday to a person to a project to a
preference without three impedance mismatches in the way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    EPISODE = "episode"   # something that happened
    FACT = "fact"         # a durable claim with identity and a validity window
    ENTITY = "entity"     # a person, project, place, organisation, artefact
    SCHEMA = "schema"     # an induced abstraction over many episodes
    INSIGHT = "insight"   # a discovered connection, with its evidence
    LOOP = "loop"         # an open commitment, question or intention


class Tier(StrEnum):
    HOT = "hot"     # fast path
    WARM = "warm"   # deep recall and nightly work
    COLD = "cold"   # explicit deep recall only; gist + embedding
    TOMB = "tomb"   # user-deleted. The only unrecoverable state.


#: Kinds that stay in the hot tier no matter how old they get. Consolidated
#: knowledge is cheap to hold and expensive to lose from the fast path.
ALWAYS_HOT: frozenset[Kind] = frozenset(
    {Kind.FACT, Kind.ENTITY, Kind.SCHEMA, Kind.LOOP}
)

#: Utility multiplier in the workspace competition. Facts are the densest
#: signal per token and current by construction; episodes are verbose and
#: usually already absorbed into a fact.
UTILITY: dict[Kind, float] = {
    Kind.FACT: 1.4,
    Kind.LOOP: 1.3,
    Kind.ENTITY: 1.1,
    Kind.SCHEMA: 1.0,
    Kind.EPISODE: 0.8,
    Kind.INSIGHT: 0.7,
}

#: Order in which kinds appear in the assembled memory block. Stable ordering
#: keeps the prompt prefix cacheable for longer, which is worth more than
#: ordering by score.
BLOCK_ORDER: tuple[Kind, ...] = (
    Kind.FACT, Kind.LOOP, Kind.ENTITY, Kind.SCHEMA, Kind.INSIGHT,
    Kind.EPISODE,
)


@dataclass(slots=True)
class Neuron:
    id: int = 0
    kind: Kind = Kind.EPISODE
    content: str = ""

    # ---- identity --------------------------------------------------------
    key: str | None = None
    """Stable identity for facts: 'user/coffee_preference'. What makes
    supersession possible at all -- without it a newer statement can only sit
    next to the older one."""

    gist: str | None = None
    """Compacted form. Survives cold-tier compaction when `content` does not."""

    # ---- time ------------------------------------------------------------
    created_at: float = 0.0
    last_access: float = 0.0
    access_count: int = 0

    valid_at: float | None = None
    invalid_at: float | None = None
    """Bitemporal, per Graphiti. A mechanism rather than a policy, and the
    best mechanism anyone has."""

    superseded_by: int | None = None

    # ---- strength (Bjork two-component) ----------------------------------
    storage_strength: float = 1.0
    """How deeply learned. Never decreases. Governs how slowly retrieval
    strength decays."""

    # ---- encoding tags ---------------------------------------------------
    salience: float = 0.0
    surprise: float = 0.0
    arousal: float = 0.0
    valence: float = 0.0
    confidence: float = 1.0
    pinned: bool = False

    # ---- lifecycle -------------------------------------------------------
    tier: Tier = Tier.HOT
    absorbed: bool = False
    """True only when every claim this carries provably exists elsewhere.
    Nothing unabsorbed is ever compacted."""

    times_replayed: int = 0
    session_id: str | None = None
    source_ref: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    # ---- convenience -----------------------------------------------------

    @property
    def utility(self) -> float:
        return UTILITY.get(self.kind, 1.0)

    @property
    def is_live(self) -> bool:
        return self.invalid_at is None and self.superseded_by is None

    def text(self) -> str:
        """What actually goes in the prompt. Cold neurons have no content."""
        return self.content or self.gist or ""

    def citations(self) -> list[int]:
        raw = self.meta.get("cites") or []
        return [int(c) for c in raw]

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id or None,
            "kind": str(self.kind),
            "content": self.content,
            "key": self.key,
            "gist": self.gist,
            "created_at": self.created_at,
            "last_access": self.last_access,
            "access_count": self.access_count,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "superseded_by": self.superseded_by,
            "storage_strength": self.storage_strength,
            "salience": self.salience,
            "surprise": self.surprise,
            "arousal": self.arousal,
            "valence": self.valence,
            "confidence": self.confidence,
            "pinned": int(self.pinned),
            "tier": str(self.tier),
            "absorbed": int(self.absorbed),
            "times_replayed": self.times_replayed,
            "session_id": self.session_id,
            "source_ref": self.source_ref,
            "meta": json.dumps(self.meta, separators=(",", ":")) if self.meta else None,
        }

    @classmethod
    def from_row(cls, row: Any) -> Neuron:
        meta_raw = row["meta"]
        return cls(
            id=row["id"],
            kind=Kind(row["kind"]),
            content=row["content"] or "",
            key=row["key"],
            gist=row["gist"],
            created_at=row["created_at"],
            last_access=row["last_access"],
            access_count=row["access_count"],
            valid_at=row["valid_at"],
            invalid_at=row["invalid_at"],
            superseded_by=row["superseded_by"],
            storage_strength=row["storage_strength"],
            salience=row["salience"],
            surprise=row["surprise"],
            arousal=row["arousal"],
            valence=row["valence"],
            confidence=row["confidence"],
            pinned=bool(row["pinned"]),
            tier=Tier(row["tier"]),
            absorbed=bool(row["absorbed"]),
            times_replayed=row["times_replayed"],
            session_id=row["session_id"],
            source_ref=row["source_ref"],
            meta=json.loads(meta_raw) if meta_raw else {},
        )

    def __repr__(self) -> str:
        body = self.text()
        if len(body) > 48:
            body = body[:45] + "..."
        return f"<{self.kind}#{self.id} {body!r}>"
