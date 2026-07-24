from __future__ import annotations

from dataclasses import replace
import inspect
import sqlite3

import pytest

from dashboard.automatic_discovery import DevelopmentData
from dashboard.automatic_discovery_v2 import _execution_payload, _parallel_map
from dashboard.backtest_engine import run_execution_backtest
from dashboard.discovery_checkpoint import DiscoveryCheckpoint, checkpoint_key
from dashboard.discovery_identity import build_parameter_identity
from dashboard.strategy_program import Primitive
from dashboard.strategy_program_runtime import (
    DEVELOPMENT_END_TS, align_higher_timeframe, build_program_features,
)
from dashboard.strategy_program_v2 import (
    CHECKPOINT_SCHEMA_VERSION, SEARCH_BUDGETS, STRATEGY_SEARCH_POLICY_VERSION,
    TEMPORAL_FAMILIES, EconomicGeometry, TemporalSequence, TemporalStage,
    generate_programs, geometry_aware_deduplicate, validate_geometry,
)
from dashboard.temporal_strategy_runtime import (
    TemporalSequenceEvaluator, evaluate_trigger_vector, run_program_backtest,
)
from dashboard.strategy_rules import StrategyParameters

START = 1704067200


def candidates():
    generated = generate_programs()
    return {
        family: next(program for program in generated["programs"]
                     if program.sequence.family == family
                     and program.direction == "LONG"
                     and program.timeframe == "15m"
                     and program.higher_timeframe_context is None
                     and program.geometry.exit_type == "FIXED_R")
        for family in TEMPORAL_FAMILIES
    }


def temporal_rows():
    rows = []
    for index in range(210):
        close = 100 + index * .001
        rows.append({
            "ts": START + index * 900, "open": close - .02, "high": close + .2,
            "low": close - .2, "close": close, "volume": 100, "confirmed": 1,
        })
    return rows


def scripted_fixture(program):
    rows = temporal_rows()
    features = build_program_features(rows)
    # Pin only existing causal OHLCV-derived features; no external data enters.
    for index in range(199, 210):
        features[index].update({
            "warm": True, "atr": 1.0, "bb_width": .02, "recent_high": 100.5,
            "recent_low": 99.5, "ema_20": 100.0, "sma_60": 99.0,
            "sma_200": 98.0, "ema_20_slope": .1, "rsi": 50,
            "candle_return": .001,
        })
    family = program.sequence.family
    if family == TEMPORAL_FAMILIES[0]:
        rows[200].update(open=100, high=100.3, low=99.8, close=100)
        rows[201].update(open=100, high=102, low=99.9, close=101.2)
        rows[202].update(open=101.1, high=101.2, low=100.4, close=100.6)
        rows[203].update(open=100.6, high=101.2, low=100.5, close=100.8)
        features[203]["ema_20"] = 100.7
        rows[204].update(open=100.8, high=101.5, low=100.7, close=101.2)
    elif family == TEMPORAL_FAMILIES[1]:
        rows[200].update(open=100, high=100.2, low=99.6, close=100)
        rows[201].update(open=99.8, high=100, low=98.8, close=99.1)
        rows[202].update(open=99.2, high=100.2, low=99.0, close=99.8)
        rows[203].update(open=99.8, high=101.0, low=99.7, close=100.8)
        rows[204].update(open=100.8, high=101.2, low=100.6, close=101.0)
    elif family == TEMPORAL_FAMILIES[2]:
        rows[200].update(open=99.5, high=101.5, low=99.4, close=101)
        features[200]["candle_return"] = .012
        rows[201].update(open=101, high=101.1, low=99.7, close=99.8)
        features[201]["rsi"] = 40
        rows[202].update(open=99.8, high=100.1, low=99.5, close=100)
        rows[203].update(open=99.8, high=101.0, low=99.7, close=100.7)
        rows[204].update(open=100.7, high=101.2, low=100.5, close=101)
    else:
        rows[200].update(open=100, high=100.7, low=99.3, close=100)
        rows[201].update(open=100, high=101.5, low=99.8, close=101.2)
        rows[202].update(open=101.2, high=101.8, low=101, close=101.5)
        rows[203].update(open=101.5, high=102.2, low=101.4, close=102)
        rows[204].update(open=102, high=102.5, low=101.9, close=102.3)
    return rows, features


def checkpoint_key_for(program, fold=1):
    return checkpoint_key(
        stage="btc_canonical_execution", program_identity=program.entry_identity,
        geometry_identity=program.geometry.identity, instrument="BTC-USDT",
        timeframe=program.timeframe, fold_identity={"fold": fold},
        dataset_fingerprint="fixture", policy_version=STRATEGY_SEARCH_POLICY_VERSION,
    )


def test_temporal_ast_canonicalization_and_equivalence_are_deterministic():
    program = next(iter(candidates().values()))
    assert program.canonical_ast() == program.canonical_ast()
    assert program.semantic_identity == replace(program).semantic_identity


def test_different_stage_order_changes_identity():
    program = next(iter(candidates().values()))
    reordered = replace(program, sequence=replace(
        program.sequence, stages=tuple(reversed(program.sequence.stages))))
    assert program.semantic_identity != reordered.semantic_identity


def test_phase6a_and_legacy_identities_remain_unchanged():
    assert build_parameter_identity("TREND_BREAKOUT", {}) == "9d409e889c93b62bfab8698db1f3367787dfec9bdd1259f3113e7c9c49a344ab"
    assert build_parameter_identity("TREND_BREAKOUT_V2", {}) == "f21e4bed23f65fb1d81d6e812bfa982d6faced771d41083b23ce2e743e96f773"
    assert build_parameter_identity("TREND_BREAKOUT_V2_1", {}) == "5f92a57854557ff8b63b79ed2be2a49fd01aafafa39237e5a1a079953abcfaec"
    from dashboard import strategy_program
    assert strategy_program.STRATEGY_PROGRAM_SCHEMA_VERSION == "strategy-program-schema-v1"


def test_generation_contains_exactly_four_families_and_enforces_budgets():
    generated = generate_programs()
    assert {p.sequence.family for p in generated["programs"]} == set(TEMPORAL_FAMILIES)
    assert generated["raw_program_count"] <= SEARCH_BUDGETS["raw_structurally_valid"]
    assert len(generated["programs"]) <= SEARCH_BUDGETS["semantic"]
    assert generated["long_only_count"] == generated["short_only_count"]
    assert generated["structural_rejection_count"] > 0


@pytest.mark.parametrize("family", TEMPORAL_FAMILIES)
def test_each_family_executes_lifecycle_trigger_execution_checkpoint_and_resume(family, tmp_path):
    program = candidates()[family]
    rows, features = scripted_fixture(program)
    start, end = rows[200]["ts"], rows[207]["ts"]
    vector, evaluator = evaluate_trigger_vector(
        program, rows, features, start, end, instrument="BTC-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 1},
    )
    assert vector, (family, evaluator.transitions)
    assert all(t["causal_feature_timestamps"]["primary"] == t["timestamp"]
               for t in evaluator.transitions)
    result = run_program_backtest(
        program, rows, features, start, end, instrument="BTC-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 1},
    )
    assert result["program_evaluations"]
    assert result["lifecycle_evidence"]["maximum_triggers_per_sequence"] == 1
    path = tmp_path / f"{family}.db"
    key = checkpoint_key_for(program)
    with DiscoveryCheckpoint(path) as checkpoint:
        checkpoint.register([key]); checkpoint.mark_running(key)
        checkpoint.complete(key, {"vector": list(vector)})
        assert checkpoint.completed(key) == {"vector": list(vector)}
    with DiscoveryCheckpoint(path) as resumed:
        assert resumed.pending("btc_canonical_execution") == []
        assert resumed.completed(key) == {"vector": list(vector)}


def test_expired_stage_cannot_later_trigger():
    program = next(iter(candidates().values()))
    first = program.sequence.stages[0]
    program = replace(program, sequence=replace(program.sequence, stages=(
        replace(first, maximum_bars=0), *program.sequence.stages[1:])))
    rows, features = scripted_fixture(program)
    evaluator = TemporalSequenceEvaluator(
        program, rows, features, instrument="BTC-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 1},
    )
    evaluator.evaluate(200)
    # Remove the immediate transition and present it only after expiry.
    rows[201]["close"] = 100
    evaluator.evaluate(201)
    rows[202]["close"] = 102
    for index in range(202, 207):
        evaluator.evaluate(index)
    assert not evaluator.trigger_evidence
    assert any(t["resulting_state"] == "INVALIDATED" for t in evaluator.transitions)


def test_future_candles_cannot_change_past_transitions():
    program = candidates()[TEMPORAL_FAMILIES[0]]
    rows, features = scripted_fixture(program)
    _, first = evaluate_trigger_vector(
        program, rows[:205], features[:205], rows[200]["ts"], rows[205 - 1]["ts"] + 900,
        instrument="BTC-USDT", dataset_fingerprint="fixture", fold_identity={"fold": 1},
    )
    changed = rows + [{"ts": rows[-1]["ts"] + 900, "open": 1, "high": 999,
                       "low": 0, "close": 1, "volume": 1, "confirmed": 1}]
    changed_features = features + [dict(features[-1])]
    _, second = evaluate_trigger_vector(
        program, changed, changed_features, rows[200]["ts"], rows[205 - 1]["ts"] + 900,
        instrument="BTC-USDT", dataset_fingerprint="fixture", fold_identity={"fold": 1},
    )
    assert first.transitions == second.transitions


def test_sequence_state_resets_and_is_isolated():
    program = candidates()[TEMPORAL_FAMILIES[0]]
    rows, features = scripted_fixture(program)
    first = TemporalSequenceEvaluator(
        program, rows, features, instrument="BTC-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 1})
    second = TemporalSequenceEvaluator(
        program, rows, features, instrument="ETH-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 2})
    first.evaluate(200)
    assert first.state != second.state == "IDLE"
    assert first.identity != second.identity
    assert TemporalSequenceEvaluator(
        program, rows, features, instrument="BTC-USDT",
        dataset_fingerprint="fixture", fold_identity={"fold": 1}).state == "IDLE"


def test_context_alignment_is_fully_closed_and_identity_distinguishes_context():
    lower = [{"ts": 900}, {"ts": 2700}, {"ts": 3600}]
    higher = [
        {"ts": 0, "open": 1, "high": 2, "low": .5, "close": 1.5, "volume": 1},
        {"ts": 3600, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 1},
    ]
    aligned = align_higher_timeframe(lower, "15m", higher, "1H")
    assert aligned[0] is None
    assert aligned[1]["context_candle_timestamp"] == 0
    assert aligned[2]["context_candle_close_timestamp"] <= aligned[2]["lower_decision_timestamp"]
    base = next(iter(candidates().values()))
    context = Primitive.make("context", "aligned_trend_context", {
        "timeframe": "1H", "alignment": "last_fully_closed"}, "LONG")
    assert base.entry_identity != replace(base, higher_timeframe_context=context).entry_identity


def test_entry_vector_dedup_retains_bounded_geometry_and_geometry_identity_changes():
    base = next(iter(candidates().values()))
    geometries = [
        EconomicGeometry.make("ATR", 1, "FIXED_R", target)
        for target in (1.5, 2, 2.5, 3)
    ]
    items = [{"instrument": "BTC-USDT", "program": replace(base, geometry=geometry),
              "trigger_vector": (1, 2)} for geometry in geometries]
    selected, evidence = geometry_aware_deduplicate(items)
    assert len(selected) == 3
    assert len({item["program"].geometry.identity for item in selected}) == 3
    assert len(next(iter(evidence.values()))["semantic_aliases"]) == 4


def test_exit_validation_rejects_wrong_reward_and_unbounded_time_stop():
    assert "REWARD_RISK_BELOW_MINIMUM" in validate_geometry(
        EconomicGeometry.make("ATR", 1, "FIXED_R", 1))
    assert "INVALID_TIME_STOP" in validate_geometry(
        EconomicGeometry.make("ATR", 1, "TIME_STOP", None, time_stop_bars=999))


def test_bounded_time_stop_is_executed_by_canonical_provider_path():
    rows = [
        {"ts": START + index * 900, "open": 100, "high": 100.1, "low": 99.9,
         "close": 100, "volume": 100, "confirmed": 1}
        for index in range(6)
    ]
    def provider(_candle, index):
        return {
            "action": "LONG" if index == 0 else "WAIT", "atr": 1.0,
            "stop_distance": 10.0, "target_r": 2.0, "time_stop_bars": 2,
            "score": 0.0, "signal_ts": rows[index]["ts"],
            "signal_id": f"s:{index}", "strategy_version": "test",
            "config_hash": "test", "warmed": True,
        }
    result = run_execution_backtest(
        rows, "BTC-USDT", "15m", StrategyParameters(
            initial_capital=10_000, risk_per_trade=.01, max_notional_fraction=.25,
            trading_fee=0, slippage=0, enable_long=True, enable_short=False,
        ), START, rows[-1]["ts"], signal_provider=provider,
    )
    assert result["trades"][0]["exit_reason"] == "TIME_STOP"
    assert result["trades"][0]["holding_seconds"] == 2 * 900


def test_diagnostic_sparse_lane_cannot_satisfy_formal_retention():
    source = inspect.getsource(__import__(
        "dashboard.automatic_discovery_v2", fromlist=["run_automatic_discovery_v2"]
    ).run_automatic_discovery_v2)
    assert 'classification = "RETAIN_FOR_DIAGNOSTIC_ONLY"' in source
    assert "if item in diagnostic" in source


def test_checkpoint_resume_no_duplicates_and_only_incomplete_reruns(tmp_path):
    program = next(iter(candidates().values()))
    keys = [checkpoint_key_for(program, fold) for fold in (1, 2)]
    path = tmp_path / "checkpoint.db"
    with DiscoveryCheckpoint(path) as checkpoint:
        checkpoint.register(keys)
        checkpoint.mark_running(keys[0]); checkpoint.complete(keys[0], {"fold": 1})
        assert len(checkpoint.pending("btc_canonical_execution")) == 1
    with DiscoveryCheckpoint(path) as checkpoint:
        checkpoint.register(keys)
        assert checkpoint.completed(keys[0]) == {"fold": 1}
        assert [row["fold_identity"] for row in checkpoint.pending(
            "btc_canonical_execution")] == ['{"fold":2}']
        assert checkpoint.progress()["total"] == 2


def test_retry_counts_bounded_and_cancellation_preserves_completed(tmp_path):
    program = next(iter(candidates().values()))
    key = checkpoint_key_for(program)
    cancel = tmp_path / "cancel"
    with DiscoveryCheckpoint(tmp_path / "checkpoint.db", maximum_retries=2,
                             cancel_file=cancel) as checkpoint:
        checkpoint.register([key])
        assert checkpoint.fail(key, "one") == 1
        assert checkpoint.fail(key, "two") == 2
        assert checkpoint.fail(key, "three") == 3
        assert checkpoint.pending("btc_canonical_execution") == []
        # A completed task is immutable under cancellation marking.
        checkpoint.complete(key, {"done": True})
        cancel.write_text("cancel")
        checkpoint.mark_cancelled(key)
        assert checkpoint.completed(key) == {"done": True}
        assert checkpoint.progress()["schema_version"] == CHECKPOINT_SCHEMA_VERSION


def test_one_and_two_worker_order_and_results_are_identical():
    tasks = []
    for program in candidates().values():
        rows, features = scripted_fixture(program)
        tasks.append((program, rows, features))
    def function(task):
        program, rows, features = task
        vector, evaluator = evaluate_trigger_vector(
            program, rows, features, rows[200]["ts"], rows[207]["ts"],
            instrument="BTC-USDT", dataset_fingerprint="fixture",
            fold_identity={"fold": 1},
        )
        return program.semantic_identity, vector, evaluator.transitions
    assert _parallel_map(1, tasks, function) == _parallel_map(2, tasks, function)


def test_matched_exposure_benchmark_is_diagnostic_and_formal_fields_are_unchanged():
    payload = _execution_payload({
        "metrics": {"total_return": 2.0}, "trades": [],
        "program_evidence": {"maximum_candle_timestamp_loaded": START + 900},
    }, {
        "raw_entry_price": 100, "raw_exit_price": 110, "total_return": 9.8,
    }, START, START + 900)
    diagnostics = payload["diagnostics"]
    assert diagnostics["formal_gate_unchanged"] is True
    assert diagnostics["diagnostic_only"] is True
    assert diagnostics["formal_gross_benchmark_excess"] == pytest.approx(-8.0)
    assert diagnostics["formal_net_benchmark_excess"] == pytest.approx(-7.8)
    assert diagnostics["matched_notional_benchmark_return"] == 0


def test_feature_generation_does_not_mutate_raw_ohlcv():
    rows = temporal_rows(); snapshot = [dict(row) for row in rows]
    build_program_features(rows)
    assert rows == snapshot


def test_development_loader_never_reads_cutoff_or_later(tmp_path):
    database = tmp_path / "data.db"
    connection = sqlite3.connect(database)
    connection.execute("""CREATE TABLE historical_candles(
      instrument TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,
      close REAL,volume REAL,confirmed INTEGER,source TEXT)""")
    for timestamp in (DEVELOPMENT_END_TS - 900, DEVELOPMENT_END_TS, DEVELOPMENT_END_TS + 900):
        connection.execute("INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                           ("BTC-USDT", "15m", timestamp, 1, 2, .5, 1.5, 10, 1, "fixture"))
    connection.commit(); connection.close()
    assert [row["ts"] for row in DevelopmentData(database).candles(
        "BTC-USDT", "15m")] == [DEVELOPMENT_END_TS - 900]


def test_no_flow_or_holdout_loader_is_requested():
    source = (
        inspect.getsource(DevelopmentData.candles)
        + inspect.getsource(run_program_backtest)
    ).lower()
    assert "historical_flow" not in source
    assert "cvd" not in source and "open_interest" not in source
    assert "primary_holdout" not in source and "final_oot" not in source
