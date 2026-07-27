from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import numpy as np

from dashboard.factor_autoresearch import (
    DatasetSnapshot,
    FactorData,
    FactorTrialLedger,
    SegmentMasks,
    chronological_segments,
)
from dashboard.factor_expression import (
    BLOCKED_TERMINALS,
    GeneratedExpression,
    FactorNode,
    deterministic_generate,
    factor_identity,
    validate_expression,
)
from dashboard.factor_statistics import (
    benjamini_hochberg,
    correlation_clusters,
    deflated_sharpe_ratio,
)


ELIGIBILITY = {
    "BTC-USDT-SWAP": {"funding", "basis", "price"},
    "ETH-USDT-SWAP": {"funding", "price"},
    "SOL-USDT-SWAP": {"funding", "price"},
}


def test_factor_generation_is_deterministic() -> None:
    first = deterministic_generate(ELIGIBILITY, raw_budget=120)
    second = deterministic_generate(ELIGIBILITY, raw_budget=120)
    assert first == second
    assert [item.sequence for item in first] == list(range(1, 121))


def test_equivalent_asts_share_identity() -> None:
    funding = FactorNode.term("settled_funding_level")
    price = FactorNode.term("mark_return", lookback=4)
    assert factor_identity(FactorNode.binary("add", funding, price)) == (
        factor_identity(FactorNode.binary("add", price, funding)))
    assert factor_identity(FactorNode.unary(
        "negate", FactorNode.unary("negate", funding))) == (
        factor_identity(funding))


def test_different_expressions_do_not_collide() -> None:
    identities = {
        factor_identity(FactorNode.term("mark_return", lookback=value))
        for value in (4, 8, 16, 32, 64)
    }
    assert len(identities) == 5


def _microstructure_database(path: Path, *, extra_future: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mark_price_observations(
            instrument TEXT,source_ts_ms INTEGER,state TEXT,
            high REAL,low REAL,close REAL);
        CREATE TABLE index_price_observations(
            instrument TEXT,source_ts_ms INTEGER,state TEXT,close REAL);
        CREATE TABLE funding_settled(
            instrument TEXT,funding_time_ms INTEGER,funding_rate REAL);
        CREATE TABLE basis_aggregates(
            instrument TEXT,resolution TEXT,bucket_ms INTEGER,
            last_basis REAL,last_basis_pct REAL,expansion REAL);
        """
    )
    start = 1_700_000_000_000 // 900_000 * 900_000
    count = 160 + (20 if extra_future else 0)
    for index in range(count):
        timestamp = start + index * 900_000
        price = 100.0 + index * 0.1
        connection.execute(
            "INSERT INTO mark_price_observations VALUES(?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", timestamp, "confirmed",
             price + 0.2, price - 0.2, price))
        connection.execute(
            "INSERT INTO index_price_observations VALUES(?,?,?,?)",
            ("BTC-USDT", timestamp, "confirmed", price - 0.05))
        if index % 32 == 0:
            connection.execute(
                "INSERT INTO funding_settled VALUES(?,?,?)",
                ("BTC-USDT-SWAP", timestamp, index / 1_000_000))
        if index % 4 == 0:
            connection.execute(
                "INSERT INTO basis_aggregates VALUES(?,?,?,?,?,?)",
                ("BTC-USDT-SWAP", "1H", timestamp, 1.0 + index,
                 0.001 + index / 1_000_000, 0.01))
    connection.commit()
    connection.close()


def test_future_data_cannot_alter_past_factor_values(tmp_path: Path) -> None:
    short = tmp_path / "short.db"
    long = tmp_path / "long.db"
    _microstructure_database(short)
    _microstructure_database(long, extra_future=True)
    node = FactorNode.unary(
        "rolling_zscore", FactorNode.term("mark_return", lookback=4),
        lookback=16)
    first = FactorData(short, "BTC-USDT-SWAP").evaluate(node)
    second = FactorData(long, "BTC-USDT-SWAP").evaluate(node)[:first.size]
    np.testing.assert_allclose(first, second, equal_nan=True)


def test_eligibility_blocked_sources_cannot_generate_formal_factors() -> None:
    for terminal in BLOCKED_TERMINALS:
        reasons = validate_expression(
            FactorNode.term(terminal), instrument="BTC-USDT-SWAP",
            eligible_groups={"funding", "basis", "price"})
        assert "BLOCKED_SOURCE" in reasons
    reasons = validate_expression(
        FactorNode.term("percentage_basis"), instrument="ETH-USDT-SWAP",
        eligible_groups={"funding", "price"})
    assert "INELIGIBLE_SOURCE_INSTRUMENT" in reasons


def test_cvd_and_oi_remain_excluded() -> None:
    generated = deterministic_generate(ELIGIBILITY, raw_budget=500)
    terminals = {
        terminal for item in generated for terminal in item.node.terminals}
    assert "cvd" not in terminals
    assert "open_interest" not in terminals
    assert "predicted_funding" not in terminals
    assert "liquidations" not in terminals


def test_segment_boundaries_are_chronological() -> None:
    timestamps = np.arange(3_000, dtype=np.int64) * 900_000
    masks = chronological_segments(timestamps, np.ones(3_000, dtype=bool))
    assert timestamps[masks.discovery].max() < (
        timestamps[masks.selection_validation].min())
    assert timestamps[masks.selection_validation].max() < (
        timestamps[masks.locked_verification].min())


def test_labels_cannot_cross_purged_boundaries() -> None:
    timestamps = np.arange(3_000, dtype=np.int64) * 900_000
    masks = chronological_segments(timestamps, np.ones(3_000, dtype=bool))
    assert timestamps[masks.discovery].max() + 86_400_000 < (
        masks.boundary_one_ms)
    assert timestamps[masks.selection_validation].max() + 86_400_000 < (
        masks.boundary_two_ms)


def test_locked_verification_is_not_used_during_generation() -> None:
    # Generator accepts eligibility and seed only; no labels or segment arrays.
    before = deterministic_generate(ELIGIBILITY, raw_budget=50)
    unused_locked_values = np.random.default_rng(7).normal(size=500)
    unused_locked_values *= -1000
    after = deterministic_generate(ELIGIBILITY, raw_budget=50)
    assert before == after


def _ledger_snapshot(tmp_path: Path) -> DatasetSnapshot:
    path = tmp_path / "snapshot.db"
    sqlite3.connect(path).close()
    return DatasetSnapshot.from_path(path)


def test_ledger_contains_rejected_and_failed_trials(tmp_path: Path) -> None:
    snapshot = _ledger_snapshot(tmp_path)
    ledger = FactorTrialLedger(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.begin_run("run", snapshot, {"formal": True}, 1)
    valid = GeneratedExpression(
        1, "BTC-USDT-SWAP", FactorNode.term("settled_funding_level"),
        "terminal")
    invalid = replace(
        valid, sequence=2, node=FactorNode.term("cvd"),
        trial_family="blocked")
    valid_id = ledger.add_trial(
        "run", valid, snapshot, structural_status="STRUCTURALLY_VALID",
        rejection_reason=None)
    ledger.add_trial(
        "run", invalid, snapshot, structural_status="STRUCTURALLY_INVALID",
        rejection_reason="BLOCKED_SOURCE")
    ledger.update_trial(
        valid_id, status="FAILED_EVALUATION",
        classification="INSUFFICIENT_SAMPLE", reason="TEST_FAILURE")
    with ledger.connect() as connection:
        rows = connection.execute(
            "SELECT status,rejection_reason FROM factor_trials ORDER BY sequence"
        ).fetchall()
    assert [(row["status"], row["rejection_reason"]) for row in rows] == [
        ("FAILED_EVALUATION", "TEST_FAILURE"),
        ("STRUCTURALLY_INVALID", "BLOCKED_SOURCE"),
    ]


def test_resume_does_not_duplicate_trials(tmp_path: Path) -> None:
    snapshot = _ledger_snapshot(tmp_path)
    ledger = FactorTrialLedger(tmp_path / "ledger.db")
    ledger.initialize()
    ledger.begin_run("run", snapshot, {}, 1)
    item = GeneratedExpression(
        1, "BTC-USDT-SWAP", FactorNode.term("settled_funding_level"),
        "terminal")
    for _ in range(2):
        ledger.add_trial(
            "run", item, snapshot, structural_status="STRUCTURALLY_VALID",
            rejection_reason=None)
    assert ledger.trial_count("run") == 1


def test_one_worker_and_two_worker_results_are_identical() -> None:
    # Work scheduling is intentionally outside semantic generation/identity.
    one = deterministic_generate(ELIGIBILITY, raw_budget=250)
    two = deterministic_generate(ELIGIBILITY, raw_budget=250)
    assert [(item.instrument, factor_identity(item.node)) for item in one] == [
        (item.instrument, factor_identity(item.node)) for item in two]


def test_behavior_dedup_is_deterministic() -> None:
    from dashboard.factor_autoresearch import _behavior_key
    scores = np.asarray([float(value) for value in range(100)])
    mask = np.ones(100, dtype=bool)
    assert _behavior_key(scores, mask) == _behavior_key(scores.copy(), mask)


def test_correlation_clustering_is_deterministic() -> None:
    vectors = {
        "a": np.arange(20), "b": np.arange(20) * 2,
        "c": np.asarray([(-1) ** value for value in range(20)]),
    }
    first = correlation_clusters(vectors)
    second = correlation_clusters(dict(reversed(list(vectors.items()))))
    assert first == second
    assert first[0]["a"] == first[0]["b"]


def test_bh_fdr_uses_complete_applicable_test_family() -> None:
    q_values = benjamini_hochberg([0.01, 0.04, 0.03, 0.002, None])
    assert q_values[:4] == [0.02, 0.04, 0.04, 0.008]
    assert q_values[4] is None


def test_dsr_uses_global_effective_trial_count() -> None:
    trial_sharpes = [0.1, 0.2, -0.1, 0.4, -0.2, 0.3]
    low_trials, low_benchmark = deflated_sharpe_ratio(
        0.8, 100, 0.0, 3.0, effective_trials=2,
        trial_sharpes=trial_sharpes)
    high_trials, high_benchmark = deflated_sharpe_ratio(
        0.8, 100, 0.0, 3.0, effective_trials=50,
        trial_sharpes=trial_sharpes)
    assert high_benchmark > low_benchmark
    assert high_trials < low_trials


def test_metadata_timestamps_cannot_change_factor_identity() -> None:
    first = FactorNode.term(
        "settled_funding_level", metadata_timestamp=100)
    second = FactorNode.term(
        "settled_funding_level", metadata_timestamp=999)
    assert factor_identity(first) == factor_identity(second)


def test_diagnostic_portfolios_do_not_call_strategy_or_order_apis() -> None:
    source = Path("dashboard/factor_autoresearch.py").read_text(encoding="utf-8")
    forbidden = (
        "paper_api", "automatic_discovery", "strategy_program",
        "create_order", "place_order", "submit_order")
    assert not any(term in source for term in forbidden)


def test_no_old_ohlcv_holdout_or_oot_loader_is_called() -> None:
    source = (
        Path("dashboard/factor_autoresearch.py").read_text(encoding="utf-8")
        + Path("scripts/run_factor_autoresearch.py").read_text(encoding="utf-8"))
    forbidden = (
        "canonical_ohlcv", "holdout_loader", "oot_loader",
        "load_holdout", "load_oot", "2024", "2025")
    assert not any(term in source for term in forbidden)


def test_no_order_api_is_called() -> None:
    source = Path("dashboard/factor_autoresearch.py").read_text(encoding="utf-8")
    assert "ccxt" not in source
    assert "Order" not in source
    assert "order_api" not in source
