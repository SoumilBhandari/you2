"""The substrate: SQLite on disk, graph and vectors in RAM, one object.

Everything above this layer talks to neurons and synapses. Nothing above it
writes SQL.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, NamedTuple, Sequence

import numpy as np

from ..clock import Clock, RealClock
from ..config import DEFAULT, BrainConfig
from .embed import Embedder, HashEmbedder
from .graph import Graph
from .neuron import ALWAYS_HOT, Kind, Neuron, Tier
from .synapse import Edge, hebbian
from .vectors import VectorIndex

_SCHEMA = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "1"


class Soma(NamedTuple):
    """The decay inputs for one neuron, held in RAM for the hot path."""
    last_access: float
    access_count: int
    storage_strength: float
    pinned: bool
    kind: Kind
    tier: Tier

_NEURON_COLS = (
    "id, kind, content, key, gist, created_at, last_access, access_count, "
    "valid_at, invalid_at, superseded_by, storage_strength, salience, "
    "surprise, arousal, valence, confidence, pinned, tier, absorbed, "
    "times_replayed, session_id, source_ref, meta"
)


class Store:
    def __init__(self, cfg: BrainConfig | None = None, clock: Clock | None = None,
                 embedder: Embedder | None = None, path: str | Path | None = None) -> None:
        self.cfg = cfg or DEFAULT
        self.clock = clock or RealClock()
        self.embedder = embedder or HashEmbedder(dim=self.cfg.embed_dim)

        db = Path(path) if path is not None else Path(self.cfg.db_path)
        if str(db) != ":memory:":
            db.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(db)

        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA.read_text())
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES('schema_version', ?) "
            "ON CONFLICT(k) DO NOTHING", (SCHEMA_VERSION,))

        self.graph = Graph()
        self.vectors = VectorIndex(self.conn, dim=self.cfg.embed_dim,
                                   quantize=self.cfg.quantize_vectors)
        self._alias_cache: dict[str, int] | None = None
        #: The soma cache. Spreading activation needs each visited neuron's
        #: decay inputs, and 800 SQLite round-trips per turn does not fit in a
        #: 50 ms retrieval budget. Derived from the DB, rebuilt on load(), and
        #: updated in exactly the three places that write neurons.
        self.soma: dict[int, Soma] = {}
        self.load()

    # ------------------------------------------------------------ lifecycle

    def load(self) -> None:
        """Rebuild the in-RAM structures from disk. Called at boot, and after
        the nightly compaction."""
        edges = self.conn.execute(
            "SELECT src, dst, type, weight FROM edges WHERE weight > 0"
        ).fetchall()
        self.graph.build((r["src"], r["dst"], r["type"], r["weight"]) for r in edges)
        self.vectors.load(hot_ids=self._hot_ids())
        self._alias_cache = None
        self.soma = {
            r["id"]: Soma(r["last_access"], r["access_count"],
                          r["storage_strength"], bool(r["pinned"]),
                          Kind(r["kind"]), Tier(r["tier"]))
            for r in self.conn.execute(
                "SELECT id, last_access, access_count, storage_strength, "
                "pinned, kind, tier FROM neurons WHERE tier != 'tomb'")
        }

    def _cache_soma(self, n: Neuron) -> None:
        if n.tier is Tier.TOMB:
            self.soma.pop(n.id, None)
        else:
            self.soma[n.id] = Soma(n.last_access, n.access_count,
                                   n.storage_strength, n.pinned, n.kind, n.tier)

    def _hot_ids(self) -> set[int]:
        rows = self.conn.execute(
            "SELECT id FROM neurons WHERE tier = 'hot'").fetchall()
        return {r["id"] for r in rows}

    def flush(self) -> None:
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.flush()
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------------- neurons

    def add(self, neuron: Neuron, *, embed_text: str | None = None,
            aliases: Sequence[str] = ()) -> Neuron:
        now = self.clock.now()
        if not neuron.created_at:
            neuron.created_at = now
        if not neuron.last_access:
            neuron.last_access = now
        if neuron.valid_at is None and neuron.kind is Kind.FACT:
            neuron.valid_at = now
        if neuron.storage_strength <= 0.0:
            neuron.storage_strength = self.cfg.storage_strength_init

        row = neuron.to_row()
        row.pop("id")
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        cur = self.conn.execute(
            f"INSERT INTO neurons({cols}) VALUES({marks})", tuple(row.values()))
        neuron.id = int(cur.lastrowid or 0)

        text = embed_text if embed_text is not None else neuron.text()
        if text:
            vec = self.embedder.encode([text])[0]
            self.vectors.put(neuron.id, vec, hot=(neuron.tier is Tier.HOT))

        if aliases:
            self.add_aliases(neuron.id, aliases)
        self._cache_soma(neuron)
        return neuron

    def get(self, neuron_id: int) -> Neuron | None:
        row = self.conn.execute(
            f"SELECT {_NEURON_COLS} FROM neurons WHERE id=?", (neuron_id,)).fetchone()
        return Neuron.from_row(row) if row else None

    def get_many(self, ids: Iterable[int]) -> dict[int, Neuron]:
        ids = list(ids)
        if not ids:
            return {}
        out: dict[int, Neuron] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT {_NEURON_COLS} FROM neurons WHERE id IN ({marks})",
                chunk).fetchall()
            for r in rows:
                out[r["id"]] = Neuron.from_row(r)
        return out

    def save(self, neuron: Neuron, *, reembed: bool = False) -> None:
        row = neuron.to_row()
        nid = row.pop("id")
        assignments = ", ".join(f"{k}=?" for k in row)
        self.conn.execute(f"UPDATE neurons SET {assignments} WHERE id=?",
                          (*row.values(), nid))
        if reembed and neuron.text():
            vec = self.embedder.encode([neuron.text()])[0]
            self.vectors.put(neuron.id, vec, hot=(neuron.tier is Tier.HOT))
        else:
            self.vectors.set_scope(neuron.id, neuron.tier is Tier.HOT)
        self._cache_soma(neuron)

    def touch(self, neuron_id: int, *, storage_strength: float,
              access_count: int, last_access: float) -> None:
        """Record a retrieval, writing only the access columns.

        Deliberately NOT save(): save() writes the whole row from an in-memory
        copy, and the workspace holds neurons loaded before the turn began. A
        repair that invalidated a fact mid-turn was then silently reverted by
        the broadcast at the end of the same turn, because broadcast wrote back
        a pre-repair snapshot. Narrow updates make that class of bug
        impossible rather than merely unlikely.
        """
        self.conn.execute(
            "UPDATE neurons SET storage_strength=?, access_count=?, last_access=? "
            "WHERE id=?", (storage_strength, access_count, last_access, neuron_id))
        prior = self.soma.get(neuron_id)
        if prior is not None:
            self.soma[neuron_id] = prior._replace(
                storage_strength=storage_strength, access_count=access_count,
                last_access=last_access)

    def save_many(self, neurons: Iterable[Neuron]) -> int:
        n = 0
        for neuron in neurons:
            self.save(neuron)
            n += 1
        return n

    def by_key(self, key: str, *, live_only: bool = True) -> Neuron | None:
        sql = f"SELECT {_NEURON_COLS} FROM neurons WHERE key=?"
        if live_only:
            sql += " AND invalid_at IS NULL AND superseded_by IS NULL"
        sql += " ORDER BY created_at DESC LIMIT 1"
        row = self.conn.execute(sql, (key,)).fetchone()
        return Neuron.from_row(row) if row else None

    def history_of(self, key: str) -> list[Neuron]:
        """Every version of a fact, newest first. 'You used to...' is
        valuable, which is why supersession invalidates rather than deletes."""
        rows = self.conn.execute(
            f"SELECT {_NEURON_COLS} FROM neurons WHERE key=? ORDER BY created_at DESC",
            (key,)).fetchall()
        return [Neuron.from_row(r) for r in rows]

    def by_kind(self, kind: Kind, *, limit: int = 1000, live_only: bool = True,
                tiers: Sequence[Tier] | None = None,
                order: str = "created_at DESC") -> list[Neuron]:
        sql = f"SELECT {_NEURON_COLS} FROM neurons WHERE kind=?"
        args: list[Any] = [str(kind)]
        if live_only:
            sql += " AND invalid_at IS NULL AND superseded_by IS NULL"
        if tiers:
            sql += f" AND tier IN ({','.join('?' * len(tiers))})"
            args += [str(t) for t in tiers]
        else:
            sql += " AND tier != 'tomb'"
        sql += f" ORDER BY {order} LIMIT ?"
        args.append(limit)
        return [Neuron.from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def between(self, kind: Kind | None, start: float, end: float,
                limit: int = 5000) -> list[Neuron]:
        sql = (f"SELECT {_NEURON_COLS} FROM neurons "
               "WHERE created_at >= ? AND created_at < ? AND tier != 'tomb'")
        args: list[Any] = [start, end]
        if kind is not None:
            sql += " AND kind=?"
            args.append(str(kind))
        sql += " ORDER BY created_at LIMIT ?"
        args.append(limit)
        return [Neuron.from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def count(self, kind: Kind | None = None, *, live_only: bool = False) -> int:
        sql = "SELECT COUNT(*) c FROM neurons WHERE tier != 'tomb'"
        args: list[Any] = []
        if kind is not None:
            sql += " AND kind=?"
            args.append(str(kind))
        if live_only:
            sql += " AND invalid_at IS NULL AND superseded_by IS NULL"
        return int(self.conn.execute(sql, args).fetchone()["c"])

    def entomb(self, neuron_id: int, *, cascade: bool = True) -> list[int]:
        """User-facing delete. The only unrecoverable state.

        Cascades to facts whose provenance cites this neuron -- 'forget that'
        has to mean the derived claims too, and provenance is what makes that
        traceable.
        """
        killed = [neuron_id]
        if cascade:
            for fact in self.by_kind(Kind.FACT, limit=100000, live_only=False):
                if neuron_id in fact.citations():
                    remaining = [c for c in fact.citations() if c != neuron_id]
                    if not remaining:
                        killed.append(fact.id)
        for nid in killed:
            self.conn.execute(
                "UPDATE neurons SET tier='tomb', content='', gist=NULL, meta=NULL "
                "WHERE id=?", (nid,))
            self.conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (nid, nid))
            self.vectors.drop(nid)
            self.soma.pop(nid, None)
        self.log("entomb", {"root": neuron_id, "removed": killed})
        # Deleting a memory has to reach the conclusions drawn from it, or
        # "forget that" leaves a derivative behind that still asserts it.
        from ..inference.tms import invalidate
        for nid in list(killed):
            invalidate(self, nid, self.clock.now(), reason="entombed")
        return killed

    # -------------------------------------------------------------- aliases

    def add_aliases(self, neuron_id: int, aliases: Sequence[str]) -> None:
        rows = [(a.strip().lower(), neuron_id) for a in aliases if a and a.strip()]
        if rows:
            self.conn.executemany(
                "INSERT INTO aliases(alias, neuron_id) VALUES(?,?) "
                "ON CONFLICT DO NOTHING", rows)
            self._alias_cache = None

    def alias_map(self) -> dict[str, int]:
        if self._alias_cache is None:
            rows = self.conn.execute(
                "SELECT a.alias, a.neuron_id FROM aliases a "
                "JOIN neurons n ON n.id = a.neuron_id WHERE n.tier != 'tomb'"
            ).fetchall()
            self._alias_cache = {r["alias"]: r["neuron_id"] for r in rows}
        return self._alias_cache

    def find_entity(self, name: str) -> Neuron | None:
        nid = self.alias_map().get(name.strip().lower())
        return self.get(nid) if nid else None

    # ---------------------------------------------------------------- edges

    def connect(self, src: int, dst: int, etype: Edge | str = Edge.ASSOC,
                weight: float = 0.1, *, at: float | None = None) -> None:
        if src == dst:
            return
        now = at if at is not None else self.clock.now()
        e = str(Edge(etype))
        self.conn.execute(
            "INSERT INTO edges(src, dst, type, weight, created_at, last_fired, fire_count) "
            "VALUES(?,?,?,?,?,?,1) "
            "ON CONFLICT(src, dst, type) DO UPDATE SET "
            "weight=excluded.weight, last_fired=excluded.last_fired, "
            "fire_count=edges.fire_count+1",
            (src, dst, e, weight, now, now))
        self.graph.upsert(src, dst, e, weight)

    def strengthen(self, src: int, dst: int, a_i: float, a_j: float,
                   etype: Edge | str = Edge.ASSOC) -> float:
        """Hebbian. Neurons that fire together wire together -- soft-bounded,
        so the graph does not saturate into uselessness."""
        if src == dst:
            return 0.0
        if src > dst and Edge(etype) is Edge.ASSOC:
            src, dst = dst, src            # canonical order for symmetric edges
        current = self.graph.weight(src, dst, etype)
        new = hebbian(current, a_i, a_j, self.cfg.hebbian_rate)
        self.connect(src, dst, etype, new)
        return new

    def edges_of(self, neuron_id: int) -> list[tuple[int, Edge, float, bool]]:
        return [(o, Edge(list(Edge)[c]), w, b)
                for o, w, c, b in self.graph.neighbours(neuron_id)]

    def prune_edges(self, w_min: float) -> int:
        cur = self.conn.execute("DELETE FROM edges WHERE weight < ?", (w_min,))
        return cur.rowcount or 0

    def persist_graph(self) -> int:
        """Write the in-RAM graph back to disk. Called at the end of the
        nightly downscaling pass, which is also when the overlay folds in."""
        rows = [(s, d, str(t), w) for s, d, t, w in self.graph.all_edges() if w > 0.0]
        with self.transaction() as conn:
            conn.execute("DELETE FROM edges")
            conn.executemany(
                "INSERT INTO edges(src, dst, type, weight, created_at) "
                "VALUES(?,?,?,?,0) ON CONFLICT(src,dst,type) DO UPDATE SET "
                "weight=excluded.weight", rows)
        return len(rows)

    # -------------------------------------------------------------- vectors

    def embed(self, text: str) -> np.ndarray:
        return self.embedder.encode([text])[0]

    def search(self, query: str | np.ndarray, k: int = 10, *,
               scope: str = "hot", exclude: Iterable[int] | None = None,
               min_score: float = 0.0) -> list[tuple[int, float]]:
        vec = self.embed(query) if isinstance(query, str) else query
        return self.vectors.search(vec, k, scope=scope, exclude=exclude,
                                   min_score=min_score)

    # -------------------------------------------------------------- journal

    def log(self, kind: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO journal(ts, kind, payload) VALUES(?,?,?)",
            (self.clock.now(), kind, json.dumps(payload, separators=(",", ":"),
                                                default=str)))

    def journal(self, kind: str | None = None, limit: int = 100,
                since: float | None = None) -> list[dict[str, Any]]:
        sql = "SELECT ts, kind, payload FROM journal WHERE 1=1"
        args: list[Any] = []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [{"ts": r["ts"], "kind": r["kind"], **json.loads(r["payload"])}
                for r in self.conn.execute(sql, args).fetchall()]

    def rate(self, neuron_id: int | None, surfaced_as: str,
             axes: dict[str, float] | None = None,
             verdict: int | None = None) -> int:
        axes = axes or {}
        cur = self.conn.execute(
            "INSERT INTO ratings(ts, neuron_id, surfaced_as, verdict, "
            "nonobvious, grounded, actionable, novel) VALUES(?,?,?,?,?,?,?,?)",
            (self.clock.now(), neuron_id, surfaced_as, verdict,
             axes.get("nonobvious"), axes.get("grounded"),
             axes.get("actionable"), axes.get("novel")))
        return int(cur.lastrowid or 0)

    def set_verdict(self, rating_id: int, verdict: int) -> None:
        self.conn.execute("UPDATE ratings SET verdict=? WHERE id=?",
                          (verdict, rating_id))

    def precision_by_type(self) -> dict[str, dict[str, float]]:
        """The instrument that eventually answers 'which connection types
        actually earn their place'. Useless for three months, then decisive."""
        rows = self.conn.execute(
            "SELECT surfaced_as, COUNT(*) n, "
            "SUM(CASE WHEN verdict > 0 THEN 1 ELSE 0 END) good, "
            "SUM(CASE WHEN verdict IS NOT NULL THEN 1 ELSE 0 END) rated "
            "FROM ratings GROUP BY surfaced_as").fetchall()
        return {r["surfaced_as"]: {
            "surfaced": r["n"], "rated": r["rated"],
            "precision": (r["good"] / r["rated"]) if r["rated"] else float("nan"),
        } for r in rows}

    # ------------------------------------------------------------- sessions

    def open_session(self, session_id: str) -> None:
        self.conn.execute(
            "INSERT INTO sessions(id, started_at) VALUES(?,?) "
            "ON CONFLICT(id) DO NOTHING", (session_id, self.clock.now()))

    def close_session(self, session_id: str, summary: str | None = None) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, summary=COALESCE(?, summary) WHERE id=?",
            (self.clock.now(), summary, session_id))

    # ---------------------------------------------------------------- stats

    def stats(self) -> dict[str, Any]:
        counts = {str(k): self.count(k) for k in Kind}
        live_facts = self.count(Kind.FACT, live_only=True)
        tiers = {r["tier"]: r["n"] for r in self.conn.execute(
            "SELECT tier, COUNT(*) n FROM neurons GROUP BY tier").fetchall()}
        return {
            "neurons": sum(counts.values()),
            "by_kind": counts,
            "live_facts": live_facts,
            "superseded_facts": counts.get("fact", 0) - live_facts,
            "tiers": tiers,
            "edges": self.graph.edge_count,
            "hops_truncated": self.graph.hops_truncated,
            "graph_mb": round(self.graph.memory_bytes() / 1e6, 2),
            "vectors": len(self.vectors),
            "vectors_hot": self.vectors.hot_count,
            "vectors_mb": round(self.vectors.memory_bytes() / 1e6, 2),
            "db_mb": round(Path(self.path).stat().st_size / 1e6, 2)
            if self.path != ":memory:" and Path(self.path).exists() else 0.0,
        }

    def __repr__(self) -> str:
        return f"Store({self.path!r}, neurons={self.count()}, {self.graph!r})"
