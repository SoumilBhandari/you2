"""Storage: what it costs, and what to do when it costs too much.

The whole point of putting the world knowledge in the weights is that the
personal store stays small. That claim needs measuring, not asserting, so
this reports actual bytes per component and projects forward.

**The growth curve is the diagnostic.** A memory system whose size is linear
in elapsed time has no consolidation -- it is accumulating. What we want is
sublinear: episodes grow linearly and then compact, facts grow and then
supersede, edges grow and then get pruned, and the derived layer grows with
*use* rather than time. If `bytes_per_day` is not falling over the first
year, something in the night is not running.

**Enforcement escalates rather than refuses.** A memory device that starts
turning memories away is broken. Over budget means forgetting harder: shrink
the hot window, compact sooner, prune more. Every step is logged, because
"why does it not remember last March any more" needs an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import DAY
from ..config import BrainConfig
from ..substrate.store import Store

#: Order in which pressure is applied. Earliest is least destructive.
LADDER = ("shrink_hot_window", "compact_sooner", "prune_harder", "demote_cold")


@dataclass
class Storage:
    db_bytes: int = 0
    graph_bytes: int = 0
    vector_bytes: int = 0
    by_table: dict[str, int] = field(default_factory=dict)
    neurons: int = 0
    span_days: float = 0.0
    derived_facts: int = 0

    @property
    def total_bytes(self) -> int:
        return self.db_bytes + self.graph_bytes + self.vector_bytes

    @property
    def bytes_per_neuron(self) -> float:
        return self.db_bytes / self.neurons if self.neurons else 0.0

    @property
    def bytes_per_day(self) -> float:
        return self.db_bytes / self.span_days if self.span_days else 0.0

    def project(self, years: float = 10.0) -> float:
        """Naive linear projection in MB.

        Deliberately naive: it is an *upper* bound, because consolidation
        makes real growth sublinear. If the real curve tracks this line, the
        night is not doing its job.
        """
        return self.bytes_per_day * 365.0 * years / 1e6

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_mb": round(self.db_bytes / 1e6, 2),
            "graph_mb": round(self.graph_bytes / 1e6, 2),
            "vectors_mb": round(self.vector_bytes / 1e6, 2),
            "total_mb": round(self.total_bytes / 1e6, 2),
            "neurons": self.neurons,
            "bytes_per_neuron": round(self.bytes_per_neuron),
            "bytes_per_day": round(self.bytes_per_day),
            "span_days": round(self.span_days, 1),
            "derived_facts": self.derived_facts,
            "projected_10y_mb": round(self.project(10.0), 1),
            "by_table": {k: round(v / 1e6, 2) for k, v in self.by_table.items()},
        }

    def __repr__(self) -> str:
        return (f"Storage({self.total_bytes / 1e6:.1f} MB, "
                f"{self.bytes_per_neuron:.0f} B/neuron, "
                f"~{self.project(10.0):.0f} MB at 10y)")


def measure(store: Store) -> Storage:
    out = Storage()
    out.neurons = store.count()
    out.graph_bytes = store.graph.memory_bytes()
    out.vector_bytes = store.vectors.memory_bytes()

    try:
        rows = store.conn.execute(
            "SELECT name, SUM(pgsize) b FROM dbstat GROUP BY name").fetchall()
        out.by_table = {r["name"]: int(r["b"]) for r in rows}
    except Exception:      # noqa: BLE001 -- dbstat is optional
        out.by_table = {}

    # Pages *in use*, not the file size. SQLite never returns pages to the
    # filesystem -- there is no VACUUM anywhere in this project and auto_vacuum
    # is off -- so st_size cannot fall, however much content is deleted. The
    # pressure ladder compares this against the budget to decide when to stop
    # climbing, so measuring the file meant the ladder could never observe its
    # own effect: it ran every rung every time, compacting episodes and
    # halving the tier windows, and then pruned the association graph on a
    # budget it had already met. Page bytes do fall, so the ladder can now
    # stop when it has done enough.
    if out.by_table:
        out.db_bytes = sum(out.by_table.values())
    elif store.path != ":memory:" and Path(store.path).exists():
        out.db_bytes = Path(store.path).stat().st_size
    else:
        # dbstat is not always compiled in; fall back to a content estimate so
        # the number is never silently zero.
        out.db_bytes = _estimate(store)

    span = store.conn.execute(
        "SELECT MIN(created_at) a, MAX(created_at) b FROM neurons").fetchone()
    if span and span["a"] and span["b"]:
        out.span_days = max(1.0, (span["b"] - span["a"]) / DAY)

    out.derived_facts = int(store.conn.execute(
        "SELECT COUNT(*) n FROM derivations WHERE compiled_id IS NOT NULL"
    ).fetchone()["n"])
    return out


def _estimate(store: Store) -> int:
    row = store.conn.execute(
        "SELECT SUM(LENGTH(COALESCE(content,'')) + LENGTH(COALESCE(gist,'')) "
        "+ LENGTH(COALESCE(meta,'')) + 160) b FROM neurons").fetchone()
    content = int(row["b"] or 0)
    edges = store.graph.edge_count * 48
    vectors = len(store.vectors) * (store.cfg.embed_dim + 24)
    return content + edges + vectors


@dataclass
class Pressure:
    over_by_mb: float = 0.0
    applied: list[str] = field(default_factory=list)
    freed_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"over_by_mb": round(self.over_by_mb, 2), "applied": self.applied,
                "freed_mb": round(self.freed_bytes / 1e6, 3)}


def enforce(store: Store, cfg: BrainConfig, now: float) -> Pressure:
    """Forget harder if over budget. Never refuse to remember.

    Each rung is more destructive than the last, and the ladder stops as soon
    as the store is back under. Everything is logged: "why does it not
    remember last March any more" has to have an answer.
    """
    out = Pressure()
    measured = measure(store)
    budget = cfg.max_db_mb * 1e6
    if measured.total_bytes <= budget:
        return out
    out.over_by_mb = (measured.total_bytes - budget) / 1e6

    from . import forgetting

    before_rung = measured.total_bytes
    for rung in LADDER:
        if rung == "shrink_hot_window":
            tightened = cfg.merged(hot_tier_days=max(14.0, cfg.hot_tier_days * 0.6))
            out.freed_bytes += _apply(store, tightened, now, forgetting.decay_and_tier)
        elif rung == "compact_sooner":
            tightened = cfg.merged(cold_compaction_days=max(
                90.0, cfg.cold_compaction_days * 0.5))
            before = measure(store).db_bytes
            forgetting.compact(store, tightened, now)
            out.freed_bytes += max(0, before - measure(store).db_bytes)
        elif rung == "prune_harder":
            from .homeostasis import downscale
            tightened = cfg.merged(prune_weight_min=cfg.prune_weight_min * 2.5)
            downscale(store, tightened)
        elif rung == "demote_cold":
            tightened = cfg.merged(warm_tier_days=max(90.0, cfg.warm_tier_days * 0.5))
            out.freed_bytes += _apply(store, tightened, now, forgetting.decay_and_tier)

        out.applied.append(rung)
        after = measure(store).total_bytes
        if after <= budget:
            break
        # A rung that frees nothing will not free anything on the next pass
        # either, and the rungs below it are strictly more destructive. The
        # clearest case is `compact_sooner`: blanking an episode's content
        # leaves the row, so no page empties and not one byte comes back
        # without a VACUUM this project never runs. Climbing past a no-op on
        # to `prune_harder` cost ~64% of the association graph for nothing.
        if before_rung - after <= 0:
            store.log("storage_pressure_stalled",
                      {"rung": rung, "bytes": after, "budget": int(budget)})
            break
        before_rung = after

    store.log("storage_pressure", out.as_dict())
    return out


def _apply(store: Store, cfg: BrainConfig, now: float, fn) -> int:
    before = measure(store).total_bytes
    fn(store, cfg, now)
    return max(0, before - measure(store).total_bytes)


def breakdown(store: Store) -> str:
    """Human-readable, for `you2 stats`."""
    m = measure(store)
    lines = [
        f"{m.total_bytes / 1e6:>8.2f} MB total "
        f"({m.neurons} neurons over {m.span_days:.0f} days)",
        f"{m.db_bytes / 1e6:>8.2f} MB  sqlite on disk",
        f"{m.vector_bytes / 1e6:>8.2f} MB  vector index in RAM",
        f"{m.graph_bytes / 1e6:>8.2f} MB  synapse graph in RAM",
        "",
        f"{m.bytes_per_neuron:>8.0f} B    per neuron",
        f"{m.bytes_per_day / 1e3:>8.2f} kB   per day of use",
        f"{m.project(10.0):>8.1f} MB   projected at 10 years "
        f"(linear upper bound — consolidation should beat it)",
    ]
    if m.by_table:
        lines.append("")
        for name, size in sorted(m.by_table.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"{size / 1e6:>8.2f} MB  {name}")
    return "\n".join(lines)
