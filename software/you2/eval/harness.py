"""The harness: does retrieval quality hold at month 24?

That is the question that defines the product, and there is no benchmark for
it. So this builds the instrument: run a fabricated history through the real
pipeline at an accelerated clock, probing at intervals, and plot precision
against elapsed time.

**If precision falls with elapsed time, consolidation is not working.** One
chart says so. It is the primary regression test of the entire project.

The ablations matter as much as the headline number. Turn off downscaling,
turn off supersession, turn off inhibition, replace spreading activation with
plain top-k -- each should visibly degrade something. If disabling a mechanism
changes nothing, the mechanism is not earning its complexity and should be
deleted. Worth running before every release.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..clock import DAY, SimClock, iso
from ..config import BrainConfig
from ..consolidation import night
from ..llm import FakeLLM
from ..runtime.pipeline import Brain
from ..substrate import HashEmbedder, Kind, Neuron
from .synthetic import Truth, Utterance, entity_seeds, generate


@dataclass
class Snapshot:
    day: int
    at: float
    probe_precision: float = 0.0
    """Fraction of probes whose expected token appears in the retrieved block."""
    stale_leakage: float = 0.0
    """Fraction of probes whose block still contains a superseded value.
    Should be zero, and rising is the classic rot signature."""
    diversity: float = 0.0
    """Distinct kinds per block, normalised. Low means the block is eight
    paraphrases."""
    block_tokens: float = 0.0
    visited: float = 0.0
    neurons: int = 0
    live_facts: int = 0
    superseded_facts: int = 0
    edges: int = 0
    open_loops: int = 0

    def as_row(self) -> str:
        return (f"{self.day:>5} {self.probe_precision:>10.2f} "
                f"{self.stale_leakage:>9.2f} {self.diversity:>10.2f} "
                f"{self.block_tokens:>8.0f} {self.visited:>8.0f} "
                f"{self.neurons:>8} {self.live_facts:>6} "
                f"{self.superseded_facts:>6} {self.edges:>7}")


@dataclass
class Result:
    label: str = "default"
    snapshots: list[Snapshot] = field(default_factory=list)
    nights: int = 0
    utterances: int = 0
    encoded: int = 0
    recurrence_found: bool = False
    recurrence_false_positives: int = 0
    contradiction_resolved: bool = False
    """Either a question was generated, or one claim superseded the other.
    Both are correct outcomes; which one you get depends on the judge, and
    the deterministic rule judge always picks the newer statement."""
    contradiction_asked: bool = False
    loops_open_correct: int = 0
    loops_closed_correct: int = 0
    bridge_found: bool = False
    insights: int = 0
    insights_pushed: int = 0
    insights_rejected: int = 0
    seconds: float = 0.0

    @property
    def final(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None

    @property
    def drift(self) -> float:
        """Change in probe precision from first snapshot to last.

        Negative is the failure the whole design exists to prevent.
        """
        if len(self.snapshots) < 2:
            return 0.0
        return self.snapshots[-1].probe_precision - self.snapshots[0].probe_precision

    def table(self) -> str:
        head = (f"  day  precision@blk   stale%  diversity   tokens  visited "
                f" neurons  facts  super’d  edges")
        return "\n".join([f"[{self.label}]", head]
                         + [s.as_row() for s in self.snapshots])

    def summary(self) -> str:
        f = self.final
        return "\n".join([
            f"[{self.label}]  {self.utterances} utterances, {self.encoded} encoded, "
            f"{self.nights} nights, {self.seconds:.1f}s",
            f"  precision   {self.snapshots[0].probe_precision:.2f} -> "
            f"{f.probe_precision:.2f}   (drift {self.drift:+.2f})" if f else "",
            f"  stale leak  {f.stale_leakage:.2f}" if f else "",
            f"  store       {f.neurons} neurons, {f.live_facts} live facts "
            f"({f.superseded_facts} superseded), {f.edges} edges" if f else "",
            f"  discovery   recurrence={self.recurrence_found} "
            f"(fp={self.recurrence_false_positives}) "
            f"contradiction={'asked' if self.contradiction_asked else ('superseded' if self.contradiction_resolved else 'MISSED')} "
            f"bridge={self.bridge_found} "
            f"insights={self.insights} ({self.insights_pushed} pushed, "
            f"{self.insights_rejected} rejected)",
            f"  loops       {self.loops_open_correct} open kept, "
            f"{self.loops_closed_correct} closed",
        ])


def probe(brain: Brain, truth: Truth, now: float) -> tuple[float, float, float, float, float]:
    """Run the held-out probes and score the retrieved block."""
    hits = 0
    stale = 0
    kinds = 0.0
    tokens = 0.0
    visited = 0.0
    for query, expected in truth.probes:
        ws = brain._retrieve(query, now)
        # What the model is actually given is the nightly profile plus this
        # turn's retrieval. Scoring the block alone under-measures the system
        # by exactly the profile, which is the whole point of building it.
        block = (brain.profile + "\n" + ws.block()).lower()
        tokens += ws.tokens
        visited += ws.cascade.visited if ws.cascade else 0
        kinds += len({i.neuron.kind for i in ws.items})
        if expected.lower() in block:
            hits += 1
        # Leakage means a superseded value presented as *current*. A derived
        # "this has changed: was X, now Y" claim legitimately quotes the old
        # value and is one of the more useful things the system produces, so
        # those lines are excluded rather than counted as rot.
        current_lines = "\n".join(
            line for line in block.splitlines()
            if "(inferred)" not in line and "was \"" not in line)
        # Only count a value as stale once its replacement has actually been
        # stated. Before that instant the "old" value is simply the current
        # one, and counting it measures nothing but the calendar.
        superseded = [tok for key, tok in truth.stale_tokens.items()
                      if tok and now >= truth.changed_at.get(key, float("inf"))]
        if any(tok in current_lines for tok in superseded):
            stale += 1
    n = max(1, len(truth.probes))
    return (hits / n, stale / n, kinds / n / 4.0, tokens / n, visited / n)


def run(*, days: int = 365, seed: int = 0, cfg: BrainConfig | None = None,
        label: str = "default", snapshot_days: Iterable[int] = (30, 90, 180, 365, 730),
        night_every: int = 1, quiet: bool = True,
        on_snapshot: Callable[[Snapshot], None] | None = None) -> Result:
    """One full simulated history."""
    import time as _time
    t0 = _time.perf_counter()

    cfg = cfg or BrainConfig(db_path=":memory:")
    utterances, truth = generate(days, seed=seed)
    clock = SimClock(start=utterances[0].at - DAY)
    llm = FakeLLM(now=clock.now())
    brain = Brain(cfg, clock=clock, llm=llm,
                  embedder=HashEmbedder(dim=cfg.embed_dim))

    # Pre-register entities so entity resolution is not the thing under test.
    from ..encoding.binding import resolve_entity
    for seed_entity in entity_seeds():
        resolve_entity(brain.store, seed_entity, clock.now())

    result = Result(label=label, utterances=len(utterances))
    checkpoints = sorted(d for d in snapshot_days if d <= days)
    next_checkpoint = 0
    day0 = utterances[0].at
    last_night_day = -1

    for u in utterances:
        day = int((u.at - day0) / DAY)

        # Night, before the day's first utterance.
        if day > last_night_day and day % night_every == 0:
            last_night_day = day
            clock.set(day0 + day * DAY + 3 * 3600)
            llm.now = clock.now()
            brain.night(now=clock.now(), seed=seed)
            result.nights += 1

        clock.set(u.at)
        llm.now = u.at
        reply = brain.say(u.text, now=u.at)
        if reply.encoded is not None and getattr(reply.encoded, "episode", None):
            result.encoded += 1

        while (next_checkpoint < len(checkpoints)
               and day >= checkpoints[next_checkpoint]):
            brain.close(u.at)
            brain.load_profile(u.at)
            snap = _snapshot(brain, truth, checkpoints[next_checkpoint], u.at)
            result.snapshots.append(snap)
            if on_snapshot:
                on_snapshot(snap)
            if not quiet:
                print(snap.as_row())
            next_checkpoint += 1

    # Final night, then the discovery checks.
    clock.set(utterances[-1].at + DAY)
    llm.now = clock.now()
    brain.night(now=clock.now(), seed=seed)
    result.nights += 1
    brain.load_profile(clock.now())

    if not result.snapshots or result.snapshots[-1].day != days:
        result.snapshots.append(_snapshot(brain, truth, days, clock.now()))

    _score_discovery(brain, truth, result, clock.now())
    result.seconds = _time.perf_counter() - t0
    brain.close_store()
    return result


def _snapshot(brain: Brain, truth: Truth, day: int, now: float) -> Snapshot:
    precision, stale, diversity, tokens, visited = probe(brain, truth, now)
    stats = brain.store.stats()
    return Snapshot(
        day=day, at=now, probe_precision=precision, stale_leakage=stale,
        diversity=diversity, block_tokens=tokens, visited=visited,
        neurons=stats["neurons"], live_facts=stats["live_facts"],
        superseded_facts=stats["superseded_facts"], edges=stats["edges"],
        open_loops=sum(1 for n in brain.store.by_kind(Kind.LOOP, limit=500)
                       if n.invalid_at is None),
    )


def _score_discovery(brain: Brain, truth: Truth, result: Result,
                     now: float) -> None:
    from ..discovery import detect_recurrence, pending_conflicts

    patterns = detect_recurrence(brain.store, brain.cfg, now)
    for p in patterns:
        if truth.recurrence in p.context.lower():
            result.recurrence_found = True
        else:
            result.recurrence_false_positives += 1

    result.contradiction_asked = any(
        truth.contradiction_key in (c.question or "").lower()
        or truth.contradiction_key in c.older.text().lower()
        for c in pending_conflicts(brain.store, brain.cfg, now, limit=20))
    versions = [n for n in brain.store.by_kind(Kind.FACT, limit=4000,
                                               live_only=False)
                if (n.key or "").lower().startswith(truth.contradiction_fact_key)]
    live = [n for n in versions if n.is_live]
    result.contradiction_resolved = (result.contradiction_asked
                                     or (len(versions) > 1 and len(live) == 1))

    open_now = {n.content for n in brain.store.by_kind(Kind.LOOP, limit=500)
                if n.invalid_at is None}
    closed_now = {n.content for n in brain.store.by_kind(Kind.LOOP, limit=500,
                                                         live_only=False)
                  if n.invalid_at is not None}
    result.loops_open_correct = sum(
        1 for t in truth.open_loops if any(_overlaps(t, o) for o in open_now))
    result.loops_closed_correct = sum(
        1 for t in truth.closed_loops if any(_overlaps(t, c) for c in closed_now))

    insights = brain.store.by_kind(Kind.INSIGHT, limit=500, live_only=False)
    result.insights = len(insights)
    result.insights_pushed = sum(1 for n in insights if n.meta.get("pushed"))
    result.insights_rejected = len(brain.store.journal("insight_rejected", limit=500))
    result.bridge_found = any(n.meta.get("type") == "bridge" for n in insights)


def _overlaps(a: str, b: str) -> bool:
    from ..substrate.embed import tokenize
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return bool(ta) and len(ta & tb) / len(ta) > 0.5


# ------------------------------------------------------------------ ablations

ABLATIONS: dict[str, dict[str, Any]] = {
    "no-downscaling": {"downscale_rho": 1.0, "prune_weight_min": 0.0},
    "no-inhibition": {"inhibition": 0.0},
    "no-fan-effect": {"fan_dampening": 0.0},
    "no-base-level": {"base_decay": 0.0},
    "top-k-only": {"spread_decay": 0.0, "max_hops": 0},
    "uncapped-block": {"workspace_tokens": 4000, "workspace_max_items": 40},
    "no-salience-gate": {"salience_floor": 0.0},
}


def ablate(days: int = 365, seed: int = 0,
           which: Iterable[str] | None = None) -> dict[str, Result]:
    """Run the baseline and each ablation.

    A mechanism whose removal changes nothing is not earning its complexity.
    """
    out: dict[str, Result] = {}
    base = BrainConfig(db_path=":memory:")
    out["baseline"] = run(days=days, seed=seed, cfg=base, label="baseline")
    for name in (which or ABLATIONS):
        cfg = base.merged(**ABLATIONS[name])
        out[name] = run(days=days, seed=seed, cfg=cfg, label=name)
    return out


def compare(results: dict[str, Result]) -> str:
    base = results.get("baseline")
    lines = [f"{'variant':<20} {'prec':>6} {'drift':>7} {'stale':>7} "
             f"{'tokens':>7} {'visited':>8} {'neurons':>8} {'edges':>8}"]
    for name, r in results.items():
        f = r.final
        if f is None:
            continue
        lines.append(f"{name:<20} {f.probe_precision:>6.2f} {r.drift:>+7.2f} "
                     f"{f.stale_leakage:>7.2f} {f.block_tokens:>7.0f} "
                     f"{f.visited:>8.0f} {f.neurons:>8} {f.edges:>8}")
    if base is not None and base.final is not None:
        lines.append("")
        lines.append("deltas vs baseline (a mechanism that changes nothing "
                     "should be deleted):")
        for name, r in results.items():
            if name == "baseline" or r.final is None:
                continue
            d_prec = r.final.probe_precision - base.final.probe_precision
            d_stale = r.final.stale_leakage - base.final.stale_leakage
            d_edges = r.final.edges - base.final.edges
            lines.append(f"  {name:<18} precision {d_prec:+.2f}  "
                         f"stale {d_stale:+.2f}  edges {d_edges:+d}")
    return "\n".join(lines)
