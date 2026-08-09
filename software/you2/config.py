"""Every tunable in the system, in one place.

This system will be tuned for months. Scattering constants through modules
makes that impossible, so nothing outside this file is allowed a magic number:
if a value could reasonably be different, it lives here.

Load order: defaults -> TOML file -> environment overrides (YOU2_*).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BrainConfig:
    # ---- paths -----------------------------------------------------------
    db_path: str = "data/brain.db"
    journal_path: str = "data/journal"

    # ---- substrate -------------------------------------------------------
    embed_dim: int = 384
    """Dimensionality of the embedding space. MiniLM-L6-v2 is 384."""

    quantize_vectors: bool = True
    """int8 with a per-vector scale. 4x smaller, negligible recall loss at
    this precision, and the vector index only picks cue seeds anyway."""

    hot_tier_days: float = 90.0
    """Episodes newer than this stay in the fast-path vector search. Facts,
    entities, schemas and open loops are always hot regardless of age."""

    # ---- activation: base level (ACT-R, plus Bjork two-component) ---------
    base_decay: float = 0.5
    """d0 in B_i = ln(sum (t - t_k)^-d). 0.5 is the ACT-R standard."""

    storage_gain: float = 0.30
    """alpha: how much storage strength grows per retrieval. Scaled by how
    *hard* the retrieval was, so recovering something faded is worth more
    than re-reading something fresh (desirable difficulty)."""

    storage_strength_init: float = 1.0
    retrieval_strength_init: float = 1.0

    # ---- activation: spreading -------------------------------------------
    spread_decay: float = 0.45
    """gamma, per hop. 3 hops leaves 0.09 of source activation, so nothing
    survives that far unless several paths converge on it -- which is
    exactly the convergent evidence worth having."""

    fire_threshold: float = 0.05
    """theta. Below this a neuron does not fire and does not propagate."""

    edge_threshold: float = 0.01
    """Don't even enqueue a contribution smaller than this."""

    visit_budget: int = 800
    """Hard cap on neurons visited per cycle. The guarantee that retrieval
    cost is independent of corpus size."""

    max_hops: int = 4

    fan_dampening: float = 1.0
    """Multiplier on the ln(1 + fan) term in S_ij = w / (1 + k*ln(1+fan)).
    0 disables the fan effect entirely (for ablation)."""

    # ---- activation: workspace -------------------------------------------
    workspace_tokens: int = 400
    """Hard cap on the injected memory block. This is the workspace capacity,
    not just a latency budget -- see docs/brain.md section 4."""

    workspace_max_items: int = 12
    inhibition: float = 0.70
    """lambda. A selected winner multiplies its near-neighbours' activation
    by (1 - lambda), so the block cannot fill with paraphrases."""

    inhibition_radius: float = 0.80
    """Cosine similarity above which a neuron counts as a near-neighbour."""

    # ---- learning --------------------------------------------------------
    hebbian_rate: float = 0.08
    """eta in dw = eta * a_i * a_j * (1 - w). Soft-bounded, or every edge
    saturates to 1.0 and the graph carries no information."""

    hebbian_min_activation: float = 0.10
    """Don't wire together things that barely fired."""

    # ---- sleep: homeostasis ----------------------------------------------
    downscale_rho: float = 0.97
    """Multiplicative, global, nightly. Preserves relative structure exactly
    while pushing accumulated noise below threshold. ~33% annual decay."""

    prune_weight_min: float = 0.02
    """Edges below this after downscaling are deleted."""

    # ---- sleep: replay ---------------------------------------------------
    replay_beta: float = 0.60
    """Exponent on (1 + times_replayed) in the priority denominator. Stops
    the same dramatic Tuesday being re-consolidated every night for a year."""

    replay_batch_min: int = 5
    replay_batch_max: int = 15
    """Ripple batching: replay is compressed and clustered, not sequential.
    One LLM call per batch is ~10x cheaper and extracts better, because a
    fact spanning several conversations is only visible when read together."""

    replay_max_batches: int = 40
    """Per night. Bounds cost and wall clock."""

    nrem_rem_cycles: int = 3
    """NREM must run before REM has anything worth recombining."""

    # ---- sleep: forgetting -----------------------------------------------
    warm_tier_days: float = 365.0
    cold_compaction_days: float = 365.0
    """Absorbed episodes older than this lose their verbatim text. Gist and
    embedding survive. Nothing unabsorbed is ever compacted."""

    never_decay_days: float = 30.0
    """Anything accessed this recently is protected from tier demotion."""

    retrieval_floor: float = 0.02
    """Retrieval strength never goes below this -- forgetting is loss of
    access, not loss of data."""

    # ---- perception ------------------------------------------------------
    dwell_seconds: float = 8.0
    """Sustained attention after a reply. A follow-up inside this window
    needs no wake word."""

    document_salience_discount: float = 0.6
    """Something you wrote in a file is weaker evidence about you than
    something you said out loud."""

    prosody_baseline_halflife: float = 604800.0
    """One week. Arousal is speaker-relative and means nothing until there
    is a running baseline."""

    # ---- salience --------------------------------------------------------
    w_surprise: float = 0.30
    w_arousal: float = 0.20
    w_self_reference: float = 0.15
    w_goal_relevance: float = 0.15
    w_novelty: float = 0.10
    w_explicit: float = 0.10

    salience_floor: float = 0.15
    """Below this: discarded, never written. Greetings, acknowledgements."""

    salience_strong: float = 0.55
    """Above this: flagged for priority replay tonight."""

    # ---- discovery -------------------------------------------------------
    recurrence_min_lift: float = 2.0
    recurrence_min_count: int = 4
    recurrence_min_span_days: float = 21.0
    """The span requirement kills 'noticed a pattern on Wednesday; it was
    one bad week'."""

    openloop_stale_days: float = 14.0
    max_pushes_per_day: int = 2

    insight_min_nonobvious: float = 0.5
    insight_min_actionable: float = 0.4
    insight_min_novel: float = 0.6
    insight_min_citations: int = 2
    """Grounding is binary. An insight that cannot cite two real memories is
    a hallucination with a citation field."""

    bridge_min_hops: int = 3
    bridge_candidates_per_night: int = 60
    bridge_min_alignment: float = 0.45

    # ---- llm -------------------------------------------------------------
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "llama-3.1-8b-instant"
    llm_night_model: str = "llama-3.1-8b-instant"
    """Consolidation can run a smaller, cheaper model than the primary."""
    llm_timeout: float = 30.0
    llm_max_retries: int = 3

    # ---- runtime ---------------------------------------------------------
    night_start_hour: int = 2
    night_end_hour: int = 6
    interrupt_check_seconds: float = 0.05
    """The night job must yield to a wake word within 50 ms."""

    def merged(self, **overrides: Any) -> BrainConfig:
        known = {f.name for f in fields(self)}
        clean = {k: v for k, v in overrides.items() if k in known}
        return replace(self, **clean)

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides: Any) -> BrainConfig:
        """defaults -> TOML -> YOU2_* env vars -> explicit kwargs."""
        cfg = cls()
        if path is not None:
            p = Path(path)
            if p.exists():
                with p.open("rb") as fh:
                    data = tomllib.load(fh)
                # Accept either a flat table or a [brain] section.
                flat = data.get("brain", data)
                cfg = cfg.merged(**_coerce(cls, flat))
        env = {}
        for f in fields(cls):
            raw = os.environ.get(f"YOU2_{f.name.upper()}")
            if raw is not None:
                env[f.name] = raw
        if env:
            cfg = cfg.merged(**_coerce(cls, env))
        if overrides:
            cfg = cfg.merged(**_coerce(cls, overrides))
        return cfg


def _coerce(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """TOML and env give us strings; the dataclass wants real types."""
    types = {f.name: f.type for f in fields(cls)}
    out: dict[str, Any] = {}
    for key, val in data.items():
        target = types.get(key)
        if target is None:
            continue
        if isinstance(val, str):
            if target in ("int", int):
                val = int(val)
            elif target in ("float", float):
                val = float(val)
            elif target in ("bool", bool):
                val = val.strip().lower() in ("1", "true", "yes", "on")
        elif target in ("float", float) and isinstance(val, int):
            val = float(val)
        out[key] = val
    return out


DEFAULT = BrainConfig()
