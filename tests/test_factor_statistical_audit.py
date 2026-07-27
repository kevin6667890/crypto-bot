from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dashboard.factor_expression import FactorNode, factor_identity
from dashboard.factor_statistical_audit import (
    FACTOR_STATISTICAL_AUDIT_VERSION,
    PHASE6F_MANIFEST_IDENTITY,
    PHASE6F_RAW_TRIALS,
    FactorStatisticalAudit,
    FrozenExperiment,
    FrozenTrial,
    StatisticalAuditLedger,
    _native_metrics,
    _portfolio_metrics,
    _source_event_ids,
    assign_multiple_testing,
    dependency_adjusted_dsr_sensitivity,
    deterministic_non_overlapping_indices,
    effective_sample_size,
    hac_mean_standard_error,
    horizon_hac_lag,
    is_formally_auditable,
    moving_block_bootstrap_ci,
)
from dashboard.factor_statistics import (
    benjamini_hochberg,
    probabilistic_sharpe_ratio,
)


def test_repeated_unchanged_funding_does_not_increase_independent_count() -> None:
    timestamps = np.arange(65, dtype=np.int64) * 900_000
    data = SimpleNamespace(
        timestamps=timestamps,
        _funding_ts=np.asarray([0, 28_800_000, 57_600_000],
                               dtype=np.int64))
    event_ids, native = _source_event_ids(data, "settled_funding")
    assert native.sum() == 3
    assert np.unique(event_ids[event_ids >= 0]).size == 3
    assert np.sum(event_ids[1:] == event_ids[:-1]) > 50


def test_funding_native_evaluation_occurs_at_settlements() -> None:
    timestamps = np.arange(97, dtype=np.int64) * 900_000
    settlements = np.asarray([0, 28_800_000, 57_600_000], dtype=np.int64)
    data = SimpleNamespace(timestamps=timestamps, _funding_ts=settlements)
    _, native = _source_event_ids(data, "settled_funding")
    np.testing.assert_array_equal(timestamps[native], settlements)


def test_overlapping_forward_returns_are_not_treated_as_iid() -> None:
    timestamps = np.arange(20, dtype=np.int64) * 900_000
    selected = deterministic_non_overlapping_indices(
        timestamps, 3_600_000)
    assert selected.tolist() == [0, 4, 8, 12, 16]
    assert selected.size < timestamps.size


def test_hac_lag_changes_with_horizon() -> None:
    timestamps = np.arange(200, dtype=np.int64) * 900_000
    assert horizon_hac_lag(900_000, timestamps) == 0
    assert horizon_hac_lag(1_800_000, timestamps) == 1
    assert horizon_hac_lag(3_600_000, timestamps) == 3
    assert horizon_hac_lag(86_400_000, timestamps) == 95


def test_non_overlapping_label_sampling_is_deterministic() -> None:
    timestamps = np.asarray([0, 10, 20, 31, 41, 61], dtype=np.int64)
    first = deterministic_non_overlapping_indices(timestamps, 30)
    second = deterministic_non_overlapping_indices(timestamps.copy(), 30)
    assert first.tolist() == [0, 3, 5]
    np.testing.assert_array_equal(first, second)


def test_insufficient_native_sample_reports_non_overlapping_count() -> None:
    timestamps = np.arange(26, dtype=np.int64) * 3_600_000
    scores = np.linspace(-1.0, 1.0, timestamps.size)
    labels = np.linspace(0.01, 0.02, timestamps.size)

    metrics, indices = _native_metrics(
        timestamps, scores, labels, 3_600_000, bootstrap_seed=7)

    assert metrics["status"] == "INSUFFICIENT_SAMPLE"
    assert metrics["non_overlapping_count"] == 26
    assert indices.size == 26


def test_block_bootstrap_is_deterministic_under_fixed_seed() -> None:
    values = np.sin(np.arange(200) / 7)
    first = moving_block_bootstrap_ci(
        values, block_length=10, seed=20260727)
    second = moving_block_bootstrap_ci(
        values, block_length=10, seed=20260727)
    assert first == second
    assert first is not None and first[0] < first[1]


def test_effective_sample_size_never_exceeds_raw() -> None:
    values = np.repeat(np.arange(20, dtype=float), 5)
    assert 1 <= effective_sample_size(values, lag=10) <= len(values)


def test_psr_uses_dependency_adjusted_sample_information() -> None:
    timestamps = np.arange(300, dtype=np.int64) * 900_000
    data = SimpleNamespace(timestamps=timestamps)
    scores = np.sin(np.arange(300) / 11)
    labels = np.repeat(np.asarray([0.01, -0.005, 0.002]), 100)
    metrics, _ = _portfolio_metrics(
        data, scores, np.arange(300), labels, 900_000)
    effective = metrics["effective_return_observations"]
    assert effective <= metrics["raw_return_observations"]
    expected = probabilistic_sharpe_ratio(
        metrics["per_period_dependency_adjusted_sharpe"], 0.0,
        max(3, int(np.floor(effective))),
        metrics["skew"], metrics["kurtosis"])
    assert metrics["psr"] == expected


def test_dsr_uses_all_applicable_attempted_trials() -> None:
    sharpes = [0.01 * ((index % 7) - 3) for index in range(2441)]
    result = dependency_adjusted_dsr_sensitivity(
        0.1, 40.0, 0.0, 3.0, sharpes)
    assert all(
        item["applicable_sharpe_input_count"] == 2441
        for item in result.values())
    assert result["raw_phase6f_trials"]["trial_count"] == 2500


def _fdr_result(
    identity: str, p_value: float, *, lineage: str = "terminal",
) -> dict:
    return {
        "reevaluation_identity": identity,
        "formal_p_value": p_value,
        "source_family": "funding-only",
        "instrument": "BTC-USDT-SWAP",
        "horizon": "1H",
        "lineage": lineage,
        "segment": "DISCOVERY",
    }


def test_bh_uses_declared_complete_family() -> None:
    results = [
        _fdr_result("a", 0.01),
        _fdr_result("b", 0.04),
        _fdr_result("failed", 1.0),
    ]
    summary = assign_multiple_testing(results)
    assert summary["global_family_size"] == 3
    assert all(result["local_family_size"] == 3 for result in results)
    assert results[0]["local_fdr_q"] == 0.03


def test_bonferroni_and_bh_are_reproducible() -> None:
    p_values = [0.001, 0.02, 0.5]
    expected_bh = benjamini_hochberg(p_values)
    results = [
        _fdr_result(str(index), value)
        for index, value in enumerate(p_values)]
    assign_multiple_testing(results)
    ordered = sorted(results, key=lambda item: item["formal_p_value"])
    assert [item["global_fdr_q"] for item in ordered] == expected_bh
    assert ordered[0]["global_bonferroni"] == 0.003


def test_locked_verification_remains_untouched_during_generation() -> None:
    source = Path(
        "dashboard/factor_statistical_audit.py").read_text(encoding="utf-8")
    assert "deterministic_generate" not in source
    assert not hasattr(FactorStatisticalAudit, "generate")


def test_phase6f_factor_identity_remains_unchanged(tmp_path: Path) -> None:
    node = FactorNode.term("settled_funding_level")
    identity = factor_identity(node)
    trial = FrozenTrial(
        1, "trial", identity, "BTC-USDT-SWAP", node, "terminal", (),
        "STRUCTURALLY_VALID", "CLASSIFIED", None)
    assert factor_identity(trial.node) == identity


def test_exact_same_2500_expressions_receive_mappings(tmp_path: Path) -> None:
    node = FactorNode.term("settled_funding_level")
    identity = factor_identity(node)
    trials = tuple(
        FrozenTrial(
            index, f"trial-{index}", identity, "BTC-USDT-SWAP", node,
            "terminal", (), "BUDGET_REJECTED", "BUDGET_REJECTED", None)
        for index in range(1, PHASE6F_RAW_TRIALS + 1))
    experiment = FrozenExperiment(
        tmp_path / "phase6f.db", tmp_path / "snapshot.db",
        tmp_path / "report.json", "phase6f", "dataset", "sha",
        PHASE6F_MANIFEST_IDENTITY, "ledger-sha", trials,
        frozenset(), frozenset(), {})
    ledger = StatisticalAuditLedger(tmp_path / "audit.db")
    ledger.initialize()
    run_id = ledger.start(experiment)
    assert ledger.counts(run_id)["identity_mappings"] == 2500


def test_blocked_cvd_oi_cannot_enter_formal_evaluation() -> None:
    for terminal in ("cvd", "open_interest"):
        node = FactorNode.term(terminal)
        trial = FrozenTrial(
            1, "trial", factor_identity(node), "BTC-USDT-SWAP", node,
            "blocked", (), "STRUCTURALLY_VALID", "GENERATED", None)
        assert not is_formally_auditable(trial)


def test_no_strategy_or_order_api_is_called() -> None:
    source = (
        Path("dashboard/factor_statistical_audit.py").read_text(
            encoding="utf-8")
        + Path("scripts/run_factor_statistical_audit.py").read_text(
            encoding="utf-8"))
    forbidden = (
        "paper_api", "automatic_discovery", "strategy_program",
        "create_order(", "place_order(", "submit_order(", "ccxt.")
    assert not any(term in source for term in forbidden)
