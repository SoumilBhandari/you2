"""The vector index, in two tiers.

Brute-force cosine over everything is 20-60 ms on an A76 at ten years of use,
and retrieval has ~50 ms for the whole cue-to-block pipeline. So the index is
split:

    hot   -- <=90 days of episodes, plus every fact, entity, schema and open
             loop. 5-15k vectors, held dequantised as float32, searched on
             every turn in under 5 ms.
    cold  -- archived episodes. int8, searched only on explicit deep recall
             or at night.

That split is not a compromise made under protest. Recent-plus-consolidated
being the fast path is how recall actually behaves, and pushing raw old
episodes out of it is a large part of why precision holds as the store grows.

The index only ever picks *cue seeds*. The graph does the real retrieval, so
a little quantisation error here costs almost nothing downstream.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

import numpy as np

_GROW = 4096          # capacity growth step
_BLOCK = 4096         # dequantisation block size for cold search


class VectorIndex:
    __slots__ = ("_conn", "dim", "_quantize", "_ids", "_pos", "_q8", "_scales",
                 "_hot", "_n", "_hot_ids", "_hot_mat", "_hot_dirty")

    def __init__(self, conn: sqlite3.Connection, dim: int = 384,
                 quantize: bool = True) -> None:
        self._conn = conn
        self.dim = dim
        self._quantize = quantize
        self._ids = np.zeros(_GROW, dtype=np.int64)
        self._pos: dict[int, int] = {}
        self._q8 = np.zeros((_GROW, dim), dtype=np.int8)
        self._scales = np.zeros(_GROW, dtype=np.float32)
        self._hot = np.zeros(_GROW, dtype=bool)
        self._n = 0
        self._hot_ids = np.zeros(0, dtype=np.int64)
        self._hot_mat = np.zeros((0, dim), dtype=np.float32)
        self._hot_dirty = True

    # ---------------------------------------------------------- quantising

    @staticmethod
    def quantize(vec: np.ndarray) -> tuple[np.ndarray, float]:
        peak = float(np.max(np.abs(vec))) if vec.size else 0.0
        if peak <= 0.0:
            return np.zeros(vec.shape, dtype=np.int8), 0.0
        scale = peak / 127.0
        q = np.clip(np.rint(vec / scale), -127, 127).astype(np.int8)
        return q, scale

    # -------------------------------------------------------------- writes

    def put(self, neuron_id: int, vec: np.ndarray, *, hot: bool = True,
            persist: bool = True) -> None:
        vec = np.asarray(vec, dtype=np.float32).reshape(-1)
        if vec.size != self.dim:
            raise ValueError(f"expected dim {self.dim}, got {vec.size}")

        q, scale = self.quantize(vec)
        idx = self._pos.get(neuron_id)
        if idx is None:
            self._ensure(self._n + 1)
            idx = self._n
            self._pos[neuron_id] = idx
            self._ids[idx] = neuron_id
            self._n += 1
        self._q8[idx] = q
        self._scales[idx] = scale
        self._hot[idx] = hot
        self._hot_dirty = True

        if persist:
            self._conn.execute(
                "INSERT INTO vectors(neuron_id, dim, scale, vec) VALUES(?,?,?,?) "
                "ON CONFLICT(neuron_id) DO UPDATE SET dim=excluded.dim, "
                "scale=excluded.scale, vec=excluded.vec",
                (neuron_id, self.dim, scale, q.tobytes()),
            )

    def drop(self, neuron_id: int, *, persist: bool = True) -> None:
        idx = self._pos.pop(neuron_id, None)
        if idx is not None:
            last = self._n - 1
            if idx != last:
                self._ids[idx] = self._ids[last]
                self._q8[idx] = self._q8[last]
                self._scales[idx] = self._scales[last]
                self._hot[idx] = self._hot[last]
                self._pos[int(self._ids[idx])] = idx
            self._n -= 1
            self._hot_dirty = True
        if persist:
            self._conn.execute("DELETE FROM vectors WHERE neuron_id=?", (neuron_id,))

    def set_scope(self, neuron_id: int, hot: bool) -> None:
        idx = self._pos.get(neuron_id)
        if idx is not None and bool(self._hot[idx]) != hot:
            self._hot[idx] = hot
            self._hot_dirty = True

    def _ensure(self, need: int) -> None:
        cap = self._ids.shape[0]
        if need <= cap:
            return
        new_cap = max(need, cap * 2, _GROW)
        self._ids = np.resize(self._ids, new_cap)
        q8 = np.zeros((new_cap, self.dim), dtype=np.int8)
        q8[: self._n] = self._q8[: self._n]
        self._q8 = q8
        self._scales = np.resize(self._scales, new_cap)
        hot = np.zeros(new_cap, dtype=bool)
        hot[: self._n] = self._hot[: self._n]
        self._hot = hot

    # --------------------------------------------------------------- reads

    def get(self, neuron_id: int) -> np.ndarray | None:
        idx = self._pos.get(neuron_id)
        if idx is None:
            return None
        return self._q8[idx].astype(np.float32) * self._scales[idx]

    def has(self, neuron_id: int) -> bool:
        return neuron_id in self._pos

    def __len__(self) -> int:
        return self._n

    @property
    def hot_count(self) -> int:
        return int(self._hot[: self._n].sum())

    def _refresh_hot(self) -> None:
        if not self._hot_dirty:
            return
        mask = self._hot[: self._n]
        self._hot_ids = self._ids[: self._n][mask].copy()
        sel = self._q8[: self._n][mask].astype(np.float32)
        sel *= self._scales[: self._n][mask][:, None]
        self._hot_mat = sel
        self._hot_dirty = False

    def search(self, query: np.ndarray, k: int = 10, *, scope: str = "hot",
               exclude: Iterable[int] | None = None,
               min_score: float = 0.0) -> list[tuple[int, float]]:
        """Top-k by cosine. Vectors are stored L2-normalised, so a dot
        product is the cosine."""
        q = np.asarray(query, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(q))
        if norm == 0.0 or self._n == 0:
            return []
        q = q / norm
        blocked = set(exclude or ())

        if scope == "hot":
            self._refresh_hot()
            ids, scores = self._hot_ids, self._hot_mat @ q
        else:
            ids = self._ids[: self._n]
            scores = np.empty(self._n, dtype=np.float32)
            for start in range(0, self._n, _BLOCK):
                stop = min(start + _BLOCK, self._n)
                blk = self._q8[start:stop].astype(np.float32)
                blk *= self._scales[start:stop][:, None]
                scores[start:stop] = blk @ q

        if ids.size == 0:
            return []
        take = min(len(ids), k + len(blocked))
        top = np.argpartition(-scores, take - 1)[:take] if take < len(ids) \
            else np.arange(len(ids))
        top = top[np.argsort(-scores[top])]

        out: list[tuple[int, float]] = []
        for i in top:
            nid = int(ids[i])
            if nid in blocked:
                continue
            score = float(scores[i])
            if score < min_score:
                break
            out.append((nid, score))
            if len(out) >= k:
                break
        return out

    def similarity(self, a: int, b: int) -> float:
        va, vb = self.get(a), self.get(b)
        if va is None or vb is None:
            return 0.0
        na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))

    def similarities(self, target: int, others: Sequence[int]) -> np.ndarray:
        """Vectorised one-to-many, for lateral inhibition."""
        v = self.get(target)
        if v is None or not others:
            return np.zeros(len(others), dtype=np.float32)
        n = float(np.linalg.norm(v))
        if n == 0.0:
            return np.zeros(len(others), dtype=np.float32)
        v = v / n
        rows = [self._pos.get(o) for o in others]
        out = np.zeros(len(others), dtype=np.float32)
        valid = [(i, r) for i, r in enumerate(rows) if r is not None]
        if not valid:
            return out
        idxs = np.array([r for _, r in valid], dtype=np.int64)
        mat = self._q8[idxs].astype(np.float32) * self._scales[idxs][:, None]
        norms = np.clip(np.linalg.norm(mat, axis=1), 1e-9, None)
        scores = (mat @ v) / norms
        for (i, _), s in zip(valid, scores):
            out[i] = s
        return out

    # ---------------------------------------------------------- durability

    def load(self, hot_ids: Iterable[int] | None = None) -> int:
        """Rebuild the in-memory index from the database."""
        self._pos.clear()
        self._n = 0
        self._hot_dirty = True
        hot_set = set(hot_ids) if hot_ids is not None else None

        rows = self._conn.execute(
            "SELECT neuron_id, dim, scale, vec FROM vectors"
        ).fetchall()
        self._ensure(len(rows) + 1)
        for nid, dim, scale, blob in rows:
            if dim != self.dim:
                continue
            q = np.frombuffer(blob, dtype=np.int8)
            if q.size != self.dim:
                continue
            idx = self._n
            self._pos[nid] = idx
            self._ids[idx] = nid
            self._q8[idx] = q
            self._scales[idx] = scale
            self._hot[idx] = True if hot_set is None else (nid in hot_set)
            self._n += 1
        return self._n

    def memory_bytes(self) -> int:
        self._refresh_hot()
        return int(self._q8[: self._n].nbytes + self._scales[: self._n].nbytes
                   + self._hot_mat.nbytes)

    def __repr__(self) -> str:
        return (f"VectorIndex(n={self._n}, hot={self.hot_count}, "
                f"dim={self.dim}, {self.memory_bytes() / 1e6:.1f} MB)")
