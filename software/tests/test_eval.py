"""The harness itself, and the ablation deltas that must keep holding.

These are regression tests for the *claims*, not for the code. If someone
tunes a constant and the fan effect stops mattering, this fails and says so.

Short horizons on purpose: a full simulated year is ~46 s per variant, which
is the right thing to run before a release and the wrong thing to run on
every commit.
"""

from __future__ import annotations

import pytest

from you2 import BrainConfig
from you2.eval import ABLATIONS, Result, ablate, compare, generate, run


# ------------------------------------------------------------------ generator

def test_synthetic_history_plants_what_it_claims():
    utterances, truth = generate(180, seed=0)
    assert len(utterances) > 300
    assert utterances == sorted(utterances, key=lambda u: u.at)

    tags = {u.tag for u in utterances}
    assert {"recurrence", "bridge_a", "bridge_b", "contra_a", "loop_open"} <= tags

    # Every stale token must be unique in the corpus, or "leakage" measures an
    # unrelated mention. This bit us once: "kestrel" was both the old employer
    # and a project name, and reported 0.88 leakage that was entirely the
    # project.
    corpus = " ".join(u.text.lower() for u in utterances)
    for key, token in truth.stale_tokens.items():
        assert corpus.count(token) <= 3, f"{token!r} is not distinctive enough"
        assert token not in truth.current_tokens[key]


def test_recurrence_is_planted_on_actual_tuesdays():
    from you2.clock import weekday
    utterances, truth = generate(180, seed=0)
    days = {weekday(u.at) for u in utterances if u.tag == "recurrence"}
    assert days == {truth.recurrence}


def test_generation_is_deterministic():
    a, _ = generate(90, seed=3)
    b, _ = generate(90, seed=3)
    assert [(u.text, u.at) for u in a] == [(u.text, u.at) for u in b]


# -------------------------------------------------------------------- harness

@pytest.mark.timeout(300)
def test_a_short_run_produces_a_usable_curve():
    result = run(days=120, seed=0, snapshot_days=(30, 120))
    assert len(result.snapshots) >= 2
    assert result.encoded > 50
    assert result.nights > 50
    assert result.final is not None
    assert "precision" in result.summary()
    assert result.table().count("\n") >= 3


@pytest.mark.timeout(300)
def test_precision_does_not_fall_with_elapsed_time():
    """The primary regression test of the entire project.

    If precision falls as the store grows, consolidation is not working — and
    that is exactly how every memory system that has shipped fails.
    """
    result = run(days=180, seed=0, snapshot_days=(60, 180))
    assert result.drift >= 0.0, result.table()


@pytest.mark.timeout(300)
def test_superseded_values_never_reach_the_block():
    result = run(days=180, seed=1, snapshot_days=(90, 180))
    assert result.final.stale_leakage == 0.0, result.table()


@pytest.mark.timeout(300)
def test_planted_ground_truth_is_found():
    result = run(days=180, seed=0, snapshot_days=(180,))
    assert result.recurrence_found, "the planted Tuesday pattern was missed"
    assert result.recurrence_false_positives <= 2
    assert result.contradiction_resolved
    assert result.loops_closed_correct >= 1
    assert result.loops_open_correct >= 1


@pytest.mark.timeout(300)
def test_bridging_reports_as_untested_not_as_working():
    """The fake declines to judge analogies, because a rule-based judge
    genuinely cannot. Reporting `bridge=False` is the true statement, and an
    earlier version that returned useful=true unconditionally manufactured 78
    meaningless bridges over a simulated year."""
    result = run(days=180, seed=0, snapshot_days=(180,))
    assert not result.bridge_found


# ------------------------------------------------------------------ ablations

@pytest.mark.timeout(600)
def test_each_mechanism_earns_its_place():
    """A mechanism whose removal changes nothing should be deleted.

    Asserts the *claims* rather than exact numbers, so tuning a constant does
    not fail this but removing a mechanism's effect does.
    """
    results = ablate(days=150, seed=0,
                     which=("no-fan-effect", "no-inhibition", "top-k-only",
                            "no-base-level", "uncapped-block"))
    base = results["baseline"].final

    # The fan effect: without hub dampening the cascade floods.
    assert results["no-fan-effect"].final.visited > base.visited * 3
    assert results["no-fan-effect"].final.focus < base.focus - 0.2

    # Inhibition buys diversity, and specifically diversity.
    assert results["no-inhibition"].final.diversity < base.diversity - 0.2

    # Plain top-k is maximally on-topic and minimally diverse -- naive RAG's
    # failure mode, measured.
    topk = results["top-k-only"].final
    assert topk.diversity < base.diversity - 0.2
    assert topk.visited < base.visited

    # Base-level decay is the only thing that keeps superseded material
    # unreachable.
    assert results["no-base-level"].final.stale_leakage > base.stale_leakage

    # The block cap costs nothing and saves a great deal.
    assert results["uncapped-block"].final.block_tokens > base.block_tokens * 2

    text = compare(results)
    assert "deltas vs baseline" in text


def test_every_ablation_is_a_real_config_key():
    fields = set(BrainConfig().__dataclass_fields__)
    for name, overrides in ABLATIONS.items():
        for key in overrides:
            assert key in fields, f"{name} sets unknown config key {key!r}"
