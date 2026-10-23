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
from .neuron import Kind, Neuron, Tier
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
        self._txn_depth = 0
        #: The soma cache. Spreading activation needs each visited neuron's
        #: decay inputs, and 800 SQLite round-trips per turn does not fit in a
        #: 50 ms retrieval budget. Derived from the DB, rebuilt on load(), and
        #: updated in exactly the three places that write neurons.
        self.soma: dict[int, Soma] = {}
        self.load()
        self._check_embedder()

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

    def _hot_scope(self, neuron: Neuron) -> bool:
        """Should this neuron be a candidate for a semantic cue seed?

        Tier alone is not enough. `decay_and_tier` only walks episodes, so a
        superseded fact stays Tier.HOT forever, while `workspace.compete`
        refuses anything not live -- so those seeds are spent on candidates
        that are structurally forbidden from winning. Measured on the demo:
        3 of the 8 seeds were already dead at night 0, 5 at night 1, 7 at
        night 2, at which point the live fact was not in the seed set at all
        and `why` reported `considered 4, selected 1`.
        """
        return neuron.tier is Tier.HOT and neuron.is_live

    def _hot_ids(self) -> set[int]:
        rows = self.conn.execute(
            "SELECT id FROM neurons WHERE tier = 'hot' "
            "AND invalid_at IS NULL AND superseded_by IS NULL").fetchall()
        return {r["id"] for r in rows}

    def flush(self) -> None:
        """Commit, unless a multi-statement transaction is open.

        The guard is not defensive tidiness. `persist_graph` deletes every
        edge and re-inserts them inside one transaction, and it runs on the
        night thread while turns run on another. A commit landing in that
        window would make the empty edge table durable and put it beyond that
        transaction's rollback — and `store.load()` rebuilds the graph from
        exactly those rows. This must stay correct before anything starts
        committing per turn.
        """
        if self._txn_depth:
            return
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._txn_depth += 1
        try:
            yield self.conn
        except Exception:
            self._txn_depth -= 1
            if self._txn_depth == 0:
                self.conn.rollback()
            raise
        else:
            self._txn_depth -= 1
            if self._txn_depth == 0:
                self.conn.commit()

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
            self.vectors.put(neuron.id, vec, hot=self._hot_scope(neuron))

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
            self.vectors.put(neuron.id, vec, hot=self._hot_scope(neuron))
        else:
            self.vectors.set_scope(neuron.id, self._hot_scope(neuron))
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
        victim = self.get(neuron_id)
        said = (victim.content or "") if victim else ""
        gist = (victim.gist or "") if victim else ""
        session = victim.session_id if victim else None

        killed = [neuron_id]
        if cascade:
            for fact in self.by_kind(Kind.FACT, limit=100000, live_only=False):
                if neuron_id in fact.citations():
                    remaining = [c for c in fact.citations() if c != neuron_id]
                    if not remaining:
                        killed.append(fact.id)
            killed.extend(self._echoes(neuron_id, said))
            killed.extend(self._orphaned_entities(killed))
        for nid in killed:
            self.conn.execute(
                "UPDATE neurons SET tier='tomb', content='', gist=NULL, meta=NULL "
                "WHERE id=?", (nid,))
            self.conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (nid, nid))
            # Aliases are a separate table and a separate index. Leaving them
            # behind leaves the name itself resolvable.
            self.conn.execute("DELETE FROM aliases WHERE neuron_id=?", (nid,))
            self.vectors.drop(nid)
            self.soma.pop(nid, None)
        self._alias_cache = None
        self._scrub(said, gist, session)
        self.log("entomb", {"root": neuron_id, "removed": killed})
        # The one unrecoverable state, and the device says "Forgotten." out
        # loud. Without this the row is still on disk at tier='hot' with its
        # content verbatim after a kill, and search returns it.
        self.flush()
        # Deleting a memory has to reach the conclusions drawn from it, or
        # "forget that" leaves a derivative behind that still asserts it.
        from ..inference.tms import invalidate
        for nid in list(killed):
            invalidate(self, nid, self.clock.now(), reason="entombed")
        return killed

    def _echoes(self, victim_id: int, said: str) -> list[int]:
        """Live episodes that quote the memory being deleted.

        Asking about something before deleting it leaves the question behind,
        and the question contains the same words: "what did I say about the
        HIV test" survives "forget that" and keeps the entity alive with it.
        Memory questions are encoded on purpose — what someone tries to
        recall is evidence about them — but not once they have asked for the
        subject to be gone.

        Two shared content words, which is narrow: an unrelated episode from
        the same evening shares none.
        """
        from .embed import stem, tokenize

        mine = {stem(w) for w in tokenize(said)}
        if len(mine) < 2:
            return []
        out: list[int] = []
        for row in self.conn.execute(
                "SELECT id, content FROM neurons "
                "WHERE kind='episode' AND tier != 'tomb'"):
            if row["id"] == victim_id or not row["content"]:
                continue
            if len(mine & {stem(w) for w in tokenize(row["content"])}) >= 2:
                out.append(int(row["id"]))
        return out

    def _scrub(self, said: str, gist: str, session: str | None) -> None:
        """Remove derived text that was computed *from* the deleted memory.

        A gist is written per replay batch and shared by every episode in it,
        so the deleted episode's words end up in its batch-mates' `gist`
        column, in `sessions.summary`, and in the `consolidate` journal entry.
        Someone says "my HIV test came back positive", then "forget that", and
        the word is still in three places.

        Nulling rather than rewriting: a gist is derived and the next night
        re-derives it. Losing one costs a retrieval handle for a day. Keeping
        one costs the only promise this device makes.
        """
        if gist:
            self.conn.execute("UPDATE neurons SET gist=NULL WHERE gist=?", (gist,))
        if session:
            self.conn.execute(
                "UPDATE sessions SET summary=NULL WHERE id=?", (session,))

        # Session id alone is not enough: a replay batch can span sessions,
        # and `consolidate` writes the batch gist to the *first* episode's
        # session. So the summary carrying the deleted words is often filed
        # under a session the deleted episode was never part of.
        #
        # Scrub by content instead, on exactly the words only this memory
        # contributed — a word that survives elsewhere is not this memory's
        # to erase.
        from .embed import tokenize
        mine = set(tokenize(said)) | set(tokenize(gist))
        if mine:
            for row in self.conn.execute(
                    "SELECT content, gist FROM neurons WHERE tier != 'tomb'"):
                mine -= set(tokenize(row["content"] or ""))
                if not mine:
                    break
        for word in list(mine)[:40]:
            self.conn.execute(
                "UPDATE sessions SET summary=NULL WHERE instr(summary, ?) > 0",
                (word,))
        for needle in (t for t in (said, gist) if t):
            self.conn.execute(
                "DELETE FROM journal WHERE instr(payload, ?) > 0", (needle,))
            # A journal payload holds a truncated copy, so the head of the
            # utterance has to be matched too.
            head = needle[:60]
            if len(head) >= 12:
                self.conn.execute(
                    "DELETE FROM journal WHERE instr(payload, ?) > 0", (head,))

    def _check_embedder(self) -> None:
        """Refuse to mix two vector spaces in one index.

        The only guard in `VectorIndex.load()` is dimensionality, and that
        does not discriminate: `HashEmbedder` and MiniLM-L6-v2 are both 384.
        So swapping the embedder — HANDOFF's own next-work item, described as
        a one-line change — would leave every existing neuron holding a hash
        vector while every new query landed in MiniLM space, with cosine
        between them meaning nothing. Cue seeding, surprise, novelty, the 0.80
        inhibition radius and the entity-resolution floor would all degrade at
        once, in a way almost impossible to attribute.

        Re-embedding rather than refusing to open, because a memory device
        that will not start is worse than one that spends a few seconds
        rebuilding an index it can rebuild exactly.
        """
        from .embed import fingerprint

        try:
            current = fingerprint(self.embedder)
        except Exception:                              # noqa: BLE001
            return
        row = self.conn.execute(
            "SELECT v FROM meta WHERE k='embedder'").fetchone()
        stored = row["v"] if row else None
        if stored == current:
            return
        if stored is not None and len(self.soma):
            self.reembed_all()
            self.log("embedder_changed", {"from": stored, "to": current})
        self.conn.execute(
            "INSERT INTO meta(k, v) VALUES('embedder', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (current,))

    def reembed_all(self, batch: int = 512) -> int:
        """Recompute every stored vector from the text it describes."""
        done = 0
        rows = self.conn.execute(
            "SELECT id FROM neurons WHERE tier != 'tomb'").fetchall()
        ids = [int(r["id"]) for r in rows]
        for start in range(0, len(ids), batch):
            for nid in ids[start:start + batch]:
                n = self.get(nid)
                if n is None:
                    continue
                text = n.text()
                if text.strip():
                    self.vectors.put(nid, self.embed(text))
                    done += 1
        self.conn.commit()
        return done

    def _orphaned_entities(self, killed: Sequence[int]) -> list[int]:
        """Entities that exist only because of the memory being deleted.

        "Forget that" cascaded to facts and stopped there, so the entity
        extracted from the episode survived — with the most sensitive word in
        the utterance as its name, searchable, aliased, and ready to
        accumulate. Someone says "my HIV test came back positive" and then
        "forget that", and a node called HIV is what remains.

        An entity named in other live memories is not the user's to lose and
        stays. One with nothing left attached is a fragment of the deleted
        memory wearing a label.
        """
        dying = set(killed)
        orphans: list[int] = []
        about = list(Edge).index(Edge.ABOUT)
        candidates: set[int] = set()
        for nid in dying:
            for other, weight, code, _back in self.graph.neighbours(nid):
                if weight <= 0.0 or code != about:
                    continue
                soma = self.soma.get(other)
                if soma is not None and soma.kind is Kind.ENTITY:
                    candidates.add(other)
        carriers = (Kind.EPISODE, Kind.FACT, Kind.LOOP, Kind.INSIGHT)
        for entity_id in candidates:
            survives = False
            for other, weight, code, _back in self.graph.neighbours(entity_id):
                # ABOUT only. A memory *carries* an entity when it is about
                # it; an ASSOC edge is co-activation, written by `broadcast`
                # whenever two things happened to win the same workspace.
                # Counting those as carriers meant that merely *asking* about
                # a memory before deleting it wired an unrelated episode to
                # its entity and kept the name alive — "forget that" left a
                # node called HIV in the store.
                if weight <= 0.0 or other in dying or code != about:
                    continue
                soma = self.soma.get(other)
                if soma is None or soma.kind not in carriers:
                    continue
                n = self.get(other)
                if n is not None and n.tier is not Tier.TOMB:
                    survives = True
                    break
            if not survives:
                orphans.append(entity_id)
        return orphans

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

    def journal_count(self, kind: str, since: float | None = None) -> int:
        """How many, not how many fit in the window.

        `journal()` takes a limit, and reporting `len()` of a limited query as
        a count is how the eval came to print "500 rejected" after every run —
        the limit, reported as a measurement, in a summary whose whole purpose
        is to be believed.
        """
        sql = "SELECT COUNT(*) FROM journal WHERE kind=?"
        args: list[Any] = [kind]
        if since is not None:
            sql += " AND ts >= ?"
            args.append(since)
        return int(self.conn.execute(sql, args).fetchone()[0])

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
