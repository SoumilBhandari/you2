"""The night.

Eight idle hours on a mains-powered machine. This is the part of the design
that turns the project's worst constraint into its best asset.

Structured as sleep is structured, because each stage does a different job:

    NREM  1  prioritised replay          what to re-read, and how often
          2  ripple batching             read related conversations together
          3  abstraction                 gist, facts, entities, loops
          4  schema assimilation         fit it to what is already known
          5  supersession                replace, or ask
          6  synaptic downscaling        the one that makes retrieval sharpen
          7  decay and tiering           demote what has faded

    REM   8  remote association          sample distant, structurally similar
          9  structure mapping           shape without content
         10  counterfactual probing      is this region actually grounded?
         11  insight scoring             most candidates die here

NREM dominates early and REM late, because consolidation has to happen before
recombination has anything worth recombining.

Two operational rules:

**Interruptible within 50 ms.** Someone walking in at 3 a.m. and saying
"Friday" must never wait for a consolidation batch to finish. Checked between
batches, and the whole run is checkpointed so an interrupted night resumes
rather than restarts.

**Never silently truncate.** Every cap that drops work logs what it dropped.
A run that skipped half the backlog and reports "consolidated" is how a
memory system quietly stops keeping up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..clock import DAY, iso
from ..config import BrainConfig
from ..discovery import contradiction, openloops, recurrence, scoring
from ..substrate.neuron import Kind
from ..substrate.store import Store
from . import abstraction, dream, forgetting, homeostasis, replay, storage
from .supersede import Tally, open_questions

Interrupt = Callable[[], bool]


@dataclass
class NightReport:
    started_at: float = 0.0
    ended_at: float = 0.0
    cycles: int = 0
    batches: int = 0
    episodes_replayed: int = 0
    facts: Tally = field(default_factory=Tally)
    entities_new: int = 0
    schemas: int = 0
    loops_new: int = 0
    loops_closed: int = 0
    degraded_batches: int = 0
    homeostasis: dict[str, Any] = field(default_factory=dict)
    forgetting: dict[str, Any] = field(default_factory=dict)
    insights: list[int] = field(default_factory=list)
    rejected: int = 0
    bridges: int = 0
    probes: list[dict[str, Any]] = field(default_factory=list)
    inferences: int = 0
    compiled: list[int] = field(default_factory=list)
    stale_derivatives: int = 0
    questions: list[str] = field(default_factory=list)
    interrupted: bool = False
    profile: str = ""
    growth: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    persona: str = ""
    audit_failures: list[int] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return self.ended_at - self.started_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": iso(self.started_at), "cycles": self.cycles,
            "batches": self.batches, "episodes": self.episodes_replayed,
            "facts": self.facts.as_dict(), "entities_new": self.entities_new,
            "schemas": self.schemas, "loops_new": self.loops_new,
            "loops_closed": self.loops_closed,
            "degraded_batches": self.degraded_batches,
            "homeostasis": self.homeostasis, "forgetting": self.forgetting,
            "insights": len(self.insights), "rejected": self.rejected,
            "inferences": self.inferences, "compiled": len(self.compiled),
            "stale_derivatives": self.stale_derivatives,
            "bridges": self.bridges, "questions": len(self.questions),
            "interrupted": self.interrupted, "growth": self.growth,
            "coverage": self.coverage, "storage": self.storage,
            "audit_failures": self.audit_failures,
        }

    def journal(self) -> str:
        """The dream journal. Plain text, and the first thing to read when
        retrieval quality moves."""
        h = self.homeostasis
        lines = [
            f"night of {iso(self.started_at)}  ({self.seconds:.1f}s"
            + (", interrupted" if self.interrupted else "") + ")",
            f"  replayed   {self.episodes_replayed} episodes in {self.batches} batches"
            + (f" ({self.degraded_batches} without the model)"
               if self.degraded_batches else ""),
            f"  facts      {self.facts.new} new, {self.facts.refined} refined, "
            f"{self.facts.reversed_} reversed, {self.facts.restated} restated, "
            f"{self.facts.conflicts} conflicting",
            f"  structure  {self.entities_new} entities, {self.schemas} schemas, "
            f"{self.loops_new} loops opened, {self.loops_closed} closed",
            f"  synapses   {h.get('edges_before', 0)} -> {h.get('edges_after', 0)} "
            f"({h.get('pruned', 0)} pruned, "
            f"{h.get('mass_retained', 1.0):.2f} of weight retained)",
            f"  forgetting {self.forgetting.get('demoted_warm', 0)} to warm, "
            f"{self.forgetting.get('demoted_cold', 0)} to cold, "
            f"{self.forgetting.get('compacted', 0)} compacted",
            f"  discovery  {len(self.insights)} kept, {self.rejected} rejected, "
            f"{self.bridges} bridges considered",
            f"  reasoning  {self.inferences} drawn, {len(self.compiled)} compiled, "
            f"{self.stale_derivatives} derivatives went stale",
            f"  person     {len(self.persona.splitlines())} lines of who they are",
            f"  storage    {self.storage.get('total_mb', 0):.1f} MB, "
            f"{self.storage.get('bytes_per_day', 0) / 1e3:.1f} kB/day, "
            f"~{self.storage.get('projected_10y_mb', 0):.0f} MB at 10y"
            + (f"  [pressure: {', '.join(self.storage['pressure']['applied'])}]"
               if self.storage.get("pressure") else ""),
        ]
        if self.questions:
            lines.append("  to ask:")
            lines.extend(f"    - {q}" for q in self.questions[:5])
        if self.audit_failures:
            lines.append(f"  !! AUDIT FAILED: compacted but unabsorbed: "
                         f"{self.audit_failures}")
        return "\n".join(lines)


def run(store: Store, llm: Any | None = None, cfg: BrainConfig | None = None, *,
        now: float | None = None, interrupt: Interrupt | None = None,
        seed: int | None = None, since: float | None = None) -> NightReport:
    cfg = cfg or store.cfg
    now = now if now is not None else store.clock.now()
    rng = np.random.default_rng(seed)
    report = NightReport(started_at=now)
    stop = interrupt or (lambda: False)

    # ---- NREM / REM cycles -------------------------------------------------
    drawn = replay.sample(store, cfg, now, rng=rng, since=since)
    batches = replay.ripple_batches(store, drawn, cfg)
    report.batches = len(batches)

    # Ceiling, not floor, and the cycle count no longer bounds the work.
    #
    # `len(batches) // cycles` truncates: five batches over four cycles gave
    # one batch per cycle, and the loop then stopped at `cycle >= cycles` with
    # the fifth never consolidated. Batches are ordered chronologically, so
    # **the abandoned batch was always the most recent one** — the material
    # most likely to contain a correction or a change of mind.
    #
    # Measured on the demo month: "actually I prefer meetings in the afternoon
    # these days" was drawn every night, dropped every night, and never
    # reached `times_replayed = 1`. Five nights later the device still held
    # "the user prefers meetings in the morning", and would have held it
    # forever. `report.batches` said 5 the whole time.
    #
    # The NREM/REM alternation is about *interleaving* rest into the work, not
    # about capping it. So the cycle count sets how often REM runs, and the
    # loop ends when the batches are done.
    cycles = max(1, cfg.nrem_rem_cycles)
    per_cycle = max(1, -(-len(batches) // cycles))
    cycle = 0
    index = 0

    while True:
        cycle += 1
        report.cycles = cycle

        # --- NREM -----------------------------------------------------------
        end = min(len(batches), index + per_cycle)
        while index < end:
            if stop():
                report.interrupted = True
                break
            batch = batches[index]
            index += 1
            result = abstraction.consolidate(store, llm, batch, cfg, now)
            report.episodes_replayed += len(batch)
            report.entities_new += len(result.entities_new)
            report.loops_new += len(result.loops_new)
            report.loops_closed += len(result.loops_closed)
            report.degraded_batches += int(result.degraded)
            _merge(report.facts, result.tally)
        store.flush()
        if report.interrupted:
            break

        # --- REM. Not on the first cycle: nothing to recombine yet. ---------
        if cycle > 1 and not stop():
            _rem(store, llm, cfg, now, report, rng)

        if index >= len(batches):
            break

    # ---- consolidation of structure ---------------------------------------
    if not report.interrupted:
        report.schemas = len(abstraction.induce_schema(store, cfg, now))
        _discover(store, cfg, now, report)
        _reason(store, llm, cfg, now, report, rng)

    # ---- homeostasis: always runs, even after an interruption --------------
    # It is the mechanism that keeps retrieval sharp, and skipping it because
    # someone woke the device at 3 a.m. is how a store quietly saturates.
    report.homeostasis = homeostasis.downscale(store, cfg).as_dict()

    forget = forgetting.decay_and_tier(store, cfg, now)
    compacted = forgetting.compact(store, cfg, now)
    merged = forget.as_dict()
    merged["compacted"] = compacted.compacted
    merged["bytes_freed"] = compacted.bytes_freed
    report.forgetting = merged
    report.audit_failures = forgetting.audit(store)
    from ..inference import tms as _tms
    report.audit_failures += _tms.audit(store)

    report.storage = storage.measure(store).as_dict()
    pressure = storage.enforce(store, cfg, now)
    if pressure.applied:
        report.storage["pressure"] = pressure.as_dict()

    report.questions = [q["question"] for q in open_questions(store)]
    # Who they are, rebuilt nightly. Stable all day, which keeps it inside
    # the cacheable prefix and is also just true.
    from ..person import build as build_person, save as save_person
    person = build_person(store, cfg, now)
    save_person(store, person)
    report.persona = person.persona(now)
    report.profile = build_profile(store, cfg, now)
    report.growth = forgetting.growth_curve(store, cfg)
    report.coverage = replay.coverage(store)
    report.ended_at = store.clock.now()

    store.log("night", report.as_dict())
    store.flush()
    return report


def _merge(into: Tally, other: Tally) -> None:
    into.new += other.new
    into.restated += other.restated
    into.refined += other.refined
    into.reversed_ += other.reversed_
    into.conflicts += other.conflicts
    into.distinct += other.distinct
    into.questions.extend(other.questions)


def _rem(store: Store, llm: Any | None, cfg: BrainConfig, now: float,
         report: NightReport, rng: np.random.Generator) -> None:
    if cfg.bridge_every_days > 1 and int(now / DAY) % cfg.bridge_every_days:
        # Not a bridging night. Counterfactual probing still runs -- it is
        # cheap and it is what keeps weakly-grounded regions honest.
        report.probes.extend(dream.counterfactual(store, llm, cfg, now, rng=rng))
        return
    # One per night at most. A wrong analogy is embarrassing in a way a
    # wrong contradiction is not, and volume is what turns a good
    # feature into noise the user learns to ignore.
    bridges = dream.propose(store, llm, cfg, now, limit=1, rng=rng)
    report.bridges += len(bridges)
    for bridge in bridges:
        if not bridge.useful or not bridge.text:
            continue
        scored = scoring.score(store, cfg, now, text=bridge.text,
                               cites=[bridge.a, bridge.b], kind="bridge")
        insight = scoring.surface(store, cfg, now, kind="bridge", text=bridge.text,
                                  cites=[bridge.a, bridge.b], scored=scored,
                                  push=False)   # bridges may never push
        if insight is None:
            report.rejected += 1
        else:
            report.insights.append(insight.id)
            dream.link(store, bridge, insight.id, now)

    report.probes.extend(dream.counterfactual(store, llm, cfg, now, rng=rng))


def _reason(store: Store, llm: Any | None, cfg: BrainConfig, now: float,
            report: NightReport, rng: np.random.Generator) -> None:
    """Inference, and the compilation that makes it cheap to repeat.

    Drawn over what has been active lately rather than over the whole store:
    an inference nobody will ever ask for is not worth deriving, let alone
    storing.
    """
    from ..activation import Cue, availability_fn, compete, spread
    from ..inference import cheap, compilation, expensive, tms

    anchors = [n.id for n in store.by_kind(Kind.FACT, limit=12,
                                           order="last_access DESC")]
    anchors += [n.id for n in store.by_kind(Kind.ENTITY, limit=8,
                                            order="last_access DESC")]
    if not anchors:
        return
    cascade = spread(store.graph, [Cue(a, 1.0) for a in anchors[:12]], cfg,
                     availability=availability_fn(store, cfg, now))
    ws = compete(store, cascade, cfg, now=now,
                 budget=cfg.workspace_tokens * 2)

    drawn = cheap(store, ws, cfg, now)
    drawn += expensive(store, llm, ws, cfg, now)
    report.inferences = len(drawn)

    out = compilation.sweep(store, drawn, cfg, now)
    report.compiled = out.compiled

    # Re-derivation is left for a later pass; what matters tonight is that
    # nothing stale is still being retrieved as if it were current.
    stale = tms.stale(store, limit=200)
    report.stale_derivatives = len(stale)
    for n in stale:
        if n.meta.get("derived"):
            compilation.decompile(store, n.id)
        n.meta.pop("needs_rederivation", None)
        store.save(n)


def _discover(store: Store, cfg: BrainConfig, now: float,
              report: NightReport) -> None:
    """Contradiction, recurrence and open loops. All three may push.

    Each supplies its own non-obviousness, because the axis means "would they
    have worked this out themselves" and that is measured differently per
    type. Graph distance is right for a bridge and wrong for the rest.
    """
    for conflict in contradiction.pending(store, cfg, now, limit=5):
        # Nobody notices themselves contradicting a thing they said in March.
        insight = scoring.surface(store, cfg, now, kind="contradiction",
                                  text=conflict.question, cites=conflict.cites,
                                  nonobvious=1.0)
        (report.insights.append(insight.id) if insight
         else setattr(report, "rejected", report.rejected + 1))

    for pattern in recurrence.detect(store, cfg, now)[:5]:
        # Lift is the measure: at 2.1 the pattern is marginal and they may
        # well have noticed; at 5 they have not.
        insight = scoring.surface(store, cfg, now, kind="recurrence",
                                  text=recurrence.phrase(pattern),
                                  cites=pattern.cites,
                                  nonobvious=min(1.0, (pattern.lift - 1.0) / 3.0))
        (report.insights.append(insight.id) if insight
         else setattr(report, "rejected", report.rejected + 1))

    for s in openloops.stale(store, cfg, now, limit=5):
        cites = [s.loop.id] + s.loop.citations()
        # The longer it has been silent, the less likely it is still in mind.
        insight = scoring.surface(store, cfg, now, kind="openloop",
                                  text=s.text, cites=cites,
                                  nonobvious=min(1.0, s.quiet_days / 21.0))
        if insight is not None:
            report.insights.append(insight.id)
            openloops.nudged(store, s.loop, now)
        else:
            report.rejected += 1


def build_profile(store: Store, cfg: BrainConfig, now: float,
                  budget: int | None = None) -> str:
    """The stable daily profile.

    Rebuilt nightly rather than per query. A stable prefix keeps the KV cache
    warm, so the device lives in the fast case and query-specific retrieval
    only has to add a small addendum *after* the cache breakpoint. Trades a
    little retrieval precision for an order of magnitude of latency, and on
    the local build it is the difference between one second and twenty.
    """
    from ..activation.workspace import estimate_tokens, render

    budget = budget or cfg.workspace_tokens
    lines: list[str] = []
    used = 0

    def take(neurons, header: str) -> None:
        nonlocal used
        chunk: list[str] = []
        for n in neurons:
            line = f"  - {render(n, now)}"
            cost = estimate_tokens(line)
            if used + cost > budget:
                break
            chunk.append(line)
            used += cost
        if chunk:
            lines.append(header + ":")
            lines.extend(chunk)

    take(sorted(store.by_kind(Kind.FACT, limit=200, live_only=True),
                key=lambda n: (-n.confidence, -n.storage_strength))[:14], "known")
    take([n for n in store.by_kind(Kind.LOOP, limit=40) if n.invalid_at is None][:5],
         "open")
    take(sorted(store.by_kind(Kind.ENTITY, limit=200),
                key=lambda n: -n.access_count)[:6], "who/what")

    profile = "\n".join(lines)
    store.log("profile", {"tokens": used, "lines": len(lines)})
    return profile


def due(store: Store, cfg: BrainConfig, now: float) -> bool:
    """Has tonight's run already happened?"""
    from ..clock import day_key, hour_of_day
    hour = hour_of_day(now)
    if not (cfg.night_start_hour <= hour < cfg.night_end_hour):
        return False
    for row in store.journal("night", limit=3):
        if day_key(row["ts"]) == day_key(now):
            return False
    return True
