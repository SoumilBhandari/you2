"""The synapse graph, in RAM.

Doing spreading activation as SQL queries per hop costs 10-30 ms on a Pi and
the whole cue-to-block pipeline has ~50 ms, so the adjacency lives in memory:
CSR arrays for the consolidated bulk, a dict overlay for edges created or
changed since the last compaction.

The overlay is not a cache-invalidation problem waiting to happen, because
the nightly downscaling pass already touches every edge -- so it doubles as
the compaction pass, and the overlay is empty every morning by construction.

At ten years of use this is roughly 380k edges after pruning: about 3.5 MB.

Every edge is materialised in *both* directions with a `backward` flag, so a
neighbour lookup is one contiguous slice. Activation must be able to run from
an entity back to the episodes that mention it, or naming a person retrieves
nothing.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Iterator

import numpy as np

from .synapse import CODE_EDGES, EDGE_CODES, Edge

#: (other_id, weight, edge_code, backward)
Neighbour = tuple[int, float, int, bool]


def canonical(a: int, b: int, code: int, backward: bool) -> tuple[int, int, int]:
    """The (src, dst, type) key an adjacency entry came from."""
    return (b, a, code) if backward else (a, b, code)


class Graph:
    __slots__ = ("_idx", "_nodes", "_indptr", "_indices", "_weights",
                 "_etypes", "_back", "_ov", "_shadow", "_edges", "_n_csr",
                 "_codes_present", "hops_truncated")

    def __init__(self) -> None:
        self._idx: dict[int, int] = {}
        self._nodes: np.ndarray = np.zeros(0, dtype=np.int64)
        self._indptr: np.ndarray = np.zeros(1, dtype=np.int64)
        self._indices: np.ndarray = np.zeros(0, dtype=np.int64)
        self._weights: np.ndarray = np.zeros(0, dtype=np.float32)
        self._etypes: np.ndarray = np.zeros(0, dtype=np.int8)
        self._back: np.ndarray = np.zeros(0, dtype=bool)
        # node_id -> list of Neighbour, for edges not yet folded into CSR
        self._ov: dict[int, list[Neighbour]] = {}
        # canonical keys whose CSR entry is stale and superseded by _edges
        self._shadow: set[tuple[int, int, int]] = set()
        # canonical key -> current weight, for every overlay edge
        self._edges: dict[tuple[int, int, int], float] = {}
        self._n_csr = 0
        self._codes_present: set[int] = set()
        #: Times `hops` gave up on its visit budget. Surfaced in `stats()`
        #: rather than swallowed: a cap that drops work says what it dropped,
        #: and a rising count here means the graph is densifying faster than
        #: downscaling is pruning it.
        self.hops_truncated = 0

    # --------------------------------------------------------------- build

    def build(self, edges: Iterable[tuple[int, int, str | Edge, float]]) -> None:
        """Rebuild the CSR from scratch. Clears the overlay."""
        src_l: list[int] = []
        dst_l: list[int] = []
        w_l: list[float] = []
        t_l: list[int] = []
        b_l: list[bool] = []

        for s, d, etype, w in edges:
            code = EDGE_CODES[Edge(etype)]
            src_l.append(s); dst_l.append(d); w_l.append(w); t_l.append(code); b_l.append(False)
            dst_l.append(s); src_l.append(d); w_l.append(w); t_l.append(code); b_l.append(True)

        node_ids = np.unique(np.array(src_l + dst_l, dtype=np.int64)) if src_l \
            else np.zeros(0, dtype=np.int64)
        self._nodes = node_ids
        self._idx = {int(n): i for i, n in enumerate(node_ids)}

        n = len(node_ids)
        m = len(src_l)
        self._indptr = np.zeros(n + 1, dtype=np.int64)
        self._indices = np.zeros(m, dtype=np.int64)
        self._weights = np.zeros(m, dtype=np.float32)
        self._etypes = np.zeros(m, dtype=np.int8)
        self._back = np.zeros(m, dtype=bool)

        if m:
            src_arr = np.fromiter((self._idx[s] for s in src_l), dtype=np.int64, count=m)
            order = np.argsort(src_arr, kind="stable")
            src_sorted = src_arr[order]
            self._indices = np.fromiter((dst_l[i] for i in order), dtype=np.int64, count=m)
            self._weights = np.fromiter((w_l[i] for i in order), dtype=np.float32, count=m)
            self._etypes = np.fromiter((t_l[i] for i in order), dtype=np.int8, count=m)
            self._back = np.fromiter((b_l[i] for i in order), dtype=bool, count=m)
            counts = np.bincount(src_sorted, minlength=n)
            self._indptr[1:] = np.cumsum(counts)

        self._n_csr = m // 2
        self._ov.clear()
        self._shadow.clear()
        self._edges.clear()
        self._codes_present = set(int(c) for c in self._etypes.tolist())

    # -------------------------------------------------------------- mutate

    def upsert(self, src: int, dst: int, etype: str | Edge, weight: float) -> None:
        """Create or update an edge. Lands in the overlay; folded into CSR at
        the next compaction (i.e. tonight)."""
        e = Edge(etype)
        code = EDGE_CODES[e]
        key = (src, dst, code)
        existed = key in self._edges
        self._edges[key] = weight
        self._codes_present.add(code)
        if not existed:
            if self._in_csr(src, dst, code):
                self._shadow.add(key)
            self._ov.setdefault(src, []).append((dst, weight, code, False))
            self._ov.setdefault(dst, []).append((src, weight, code, True))
        else:
            self._rewrite_overlay(src, dst, code, weight)

    def _rewrite_overlay(self, src: int, dst: int, code: int, weight: float) -> None:
        for node, other, back in ((src, dst, False), (dst, src, True)):
            lst = self._ov.get(node)
            if not lst:
                continue
            for i, (o, _w, c, b) in enumerate(lst):
                if o == other and c == code and b is back:
                    lst[i] = (o, weight, c, b)
                    break

    def _in_csr(self, src: int, dst: int, code: int) -> bool:
        i = self._idx.get(src)
        if i is None:
            return False
        lo, hi = self._indptr[i], self._indptr[i + 1]
        seg = self._indices[lo:hi]
        hits = np.nonzero(seg == dst)[0]
        for h in hits:
            p = lo + h
            if self._etypes[p] == code and not self._back[p]:
                return True
        return False

    def weight(self, src: int, dst: int, etype: str | Edge) -> float:
        code = EDGE_CODES[Edge(etype)]
        key = (src, dst, code)
        if key in self._edges:
            return self._edges[key]
        i = self._idx.get(src)
        if i is None:
            return 0.0
        lo, hi = self._indptr[i], self._indptr[i + 1]
        seg = self._indices[lo:hi]
        for h in np.nonzero(seg == dst)[0]:
            p = lo + h
            if self._etypes[p] == code and not self._back[p]:
                return float(self._weights[p])
        return 0.0

    def scale_all(self, rho: float, *, exempt: frozenset[Edge] = frozenset()) -> None:
        """Multiplicative downscaling. Preserves relative structure exactly
        while pushing the accumulated noise floor below threshold -- which is
        the entire reason retrieval sharpens with age instead of blurring."""
        exempt_codes = {EDGE_CODES[e] for e in exempt}
        if self._weights.size:
            if exempt_codes:
                mask = ~np.isin(self._etypes, list(exempt_codes))
                self._weights[mask] *= rho
            else:
                self._weights *= rho
        for key in list(self._edges):
            if key[2] not in exempt_codes:
                self._edges[key] *= rho
                self._rewrite_overlay(*key, self._edges[key])

    def prune(self, w_min: float, *,
              exempt: frozenset[Edge] = frozenset()) -> int:
        """Drop edges that downscaling has pushed below threshold.

        This is where forgetting actually happens at the synapse level, and
        it is safe in a way deleting neurons is not: the memories remain, they
        simply stop pulling each other into the workspace.
        """
        exempt_codes = {EDGE_CODES[e] for e in exempt}
        removed = 0
        if self._weights.size:
            mask = (self._weights < w_min) & (self._weights > 0.0)
            if exempt_codes:
                mask &= ~np.isin(self._etypes, list(exempt_codes))
            for p in np.nonzero(mask)[0]:
                if self._back[p]:
                    continue
                i = int(np.searchsorted(self._indptr, p, side="right") - 1)
                key = (int(self._nodes[i]), int(self._indices[p]), int(self._etypes[p]))
                if key not in self._shadow:
                    self.upsert(key[0], key[1], CODE_EDGES[key[2]], 0.0)
                    removed += 1
        for key, w in list(self._edges.items()):
            if 0.0 < w < w_min and key[2] not in exempt_codes:
                self._edges[key] = 0.0
                self._rewrite_overlay(*key, 0.0)
                removed += 1
        return removed

    # ------------------------------------------------------------ traverse

    def has_type(self, code: int) -> bool:
        """Does the graph hold any edge of this type at all?

        Cheap, conservative and worth having because `neighbours` takes no
        type filter: every caller that wants one walks a node's entire
        adjacency and throws the rest away in Python. `transitive` and
        `inherit` run over all twelve workspace winners on every turn looking
        for PART_OF, CAUSES and INHERITS -- and a store built only from
        conversation holds none of those, so roughly half of all per-turn
        edge traversal was spent proving that for the Nth time.

        Only ever grows within a session, so a false `True` costs exactly the
        work that was being done before and a false `False` is impossible.
        """
        return code in self._codes_present

    def neighbours(self, node: int) -> Iterator[Neighbour]:
        i = self._idx.get(node)
        if i is not None:
            lo, hi = self._indptr[i], self._indptr[i + 1]
            shadow = self._shadow
            for p in range(lo, hi):
                code = int(self._etypes[p])
                back = bool(self._back[p])
                other = int(self._indices[p])
                if shadow and canonical(node, other, code, back) in shadow:
                    continue
                yield other, float(self._weights[p]), code, back
        ov = self._ov.get(node)
        if ov:
            for entry in ov:
                if entry[1] > 0.0:
                    yield entry

    def fan(self, node: int) -> int:
        """Total degree. What the fan effect is computed against."""
        n = 0
        i = self._idx.get(node)
        if i is not None:
            n += int(self._indptr[i + 1] - self._indptr[i])
        ov = self._ov.get(node)
        if ov:
            n += sum(1 for e in ov if e[1] > 0.0)
        return n

    def hops(self, a: int, b: int, max_hops: int = 6,
             budget: int = 4000) -> int:
        """Shortest path length, capped. Returns -1 if unreachable within
        max_hops. Used to score how non-obvious a discovered connection is.

        Bounded in *nodes visited* as well as in depth, which `neighbourhood`
        below has always been and this had no reason not to be. Six hops is a
        depth limit, not a work limit, and every other traversal in this
        system carries a visit budget — that is the first commitment in
        brain.md §1 — so this one was the exception by oversight rather than
        by argument.

        To be accurate about what it buys today: at one simulated year the
        store holds ~1,100 neurons, so the frontier cannot reach 4,000 and
        this never binds. It is a guard for the size the store is meant to
        reach, not a fix for anything measured. The REM cost that *was*
        measured came from calling this twice per pair, which `dream.alignment`
        no longer does.

        Exhausting the budget returns -1, the same as unreachable, and that is
        the honest answer for the one thing this is used for: a pair the
        search could not connect within four thousand nodes is not obviously
        connected.
        """
        if a == b:
            return 0
        seen = {a}
        frontier = deque([(a, 0)])
        while frontier:
            if len(seen) >= budget:
                self.hops_truncated += 1
                return -1
            node, d = frontier.popleft()
            if d >= max_hops:
                continue
            for other, w, _c, _bk in self.neighbours(node):
                if w <= 0.0 or other in seen:
                    continue
                if other == b:
                    return d + 1
                seen.add(other)
                frontier.append((other, d + 1))
        return -1

    def neighbourhood(self, node: int, hops: int = 2,
                      limit: int = 64) -> dict[int, int]:
        """Node -> hop distance, bounded. The subgraph structure mapping
        compares."""
        out = {node: 0}
        frontier = deque([(node, 0)])
        while frontier and len(out) < limit:
            cur, d = frontier.popleft()
            if d >= hops:
                continue
            for other, w, _c, _bk in self.neighbours(cur):
                if w <= 0.0 or other in out:
                    continue
                out[other] = d + 1
                frontier.append((other, d + 1))
                if len(out) >= limit:
                    break
        return out

    def relation_signature(self, node: int) -> dict[int, int]:
        """Multiset of incident edge types. The cheap first filter for
        structural alignment -- content-free by construction, which is the
        point."""
        sig: dict[int, int] = {}
        for _o, w, code, _bk in self.neighbours(node):
            if w > 0.0:
                sig[code] = sig.get(code, 0) + 1
        return sig

    # --------------------------------------------------------------- stats

    @property
    def edge_count(self) -> int:
        live_overlay = sum(1 for w in self._edges.values() if w > 0.0)
        shadowed = len(self._shadow)
        return self._n_csr - shadowed + live_overlay

    @property
    def node_count(self) -> int:
        return len(set(self._idx) | set(self._ov))

    @property
    def overlay_size(self) -> int:
        return len(self._edges)

    def all_edges(self) -> Iterator[tuple[int, int, Edge, float]]:
        """Canonical (src, dst, type, weight) for every live edge."""
        for p in range(self._indices.size):
            if self._back[p]:
                continue
            # find the source node for position p
            i = int(np.searchsorted(self._indptr, p, side="right") - 1)
            src = int(self._nodes[i])
            dst = int(self._indices[p])
            code = int(self._etypes[p])
            if (src, dst, code) in self._shadow:
                continue
            yield src, dst, CODE_EDGES[code], float(self._weights[p])
        for (src, dst, code), w in self._edges.items():
            if w > 0.0:
                yield src, dst, CODE_EDGES[code], w

    def memory_bytes(self) -> int:
        return int(self._indptr.nbytes + self._indices.nbytes
                   + self._weights.nbytes + self._etypes.nbytes
                   + self._back.nbytes)

    def __repr__(self) -> str:
        return (f"Graph(nodes={self.node_count}, edges={self.edge_count}, "
                f"overlay={self.overlay_size}, "
                f"{self.memory_bytes() / 1e6:.1f} MB)")
