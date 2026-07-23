from __future__ import annotations

from dataclasses import replace
import inspect
import sqlite3

import pytest

import dashboard.automatic_discovery as orchestration
from dashboard.automatic_discovery import DevelopmentData, stage_c
from dashboard.discovery_identity import build_parameter_identity
from dashboard.strategy_program import (
    SEARCH_BUDGETS, Primitive, StrategyProgram, build_program_identity,
    generate_programs, neighborhood_variants, semantic_deduplicate, validate_program,
)
from dashboard.strategy_program_runtime import (
    DEVELOPMENT_END_TS, StrategyProgramEvaluator, align_higher_timeframe,
    behavior_deduplicate, build_program_features, evaluate_trigger_vector,
    event_study, run_program_backtest,
)

START = 1704067200


def primitive(kind, name, parameters=None, direction="BOTH"):
    return Primitive.make(kind, name, parameters or {}, direction)


def program(timeframe="15m", direction="LONG", context=None, setup=None):
    return StrategyProgram(
        timeframe=timeframe,
        direction=direction,
        regime=primitive("regime", "aligned_trend", {"mode": "trend"}, direction),
        setup=tuple(setup or (
            primitive("setup", "ema_excursion", {"mode": "trend", "atr_distance": .25}, direction),
        )),
        trigger=primitive("trigger", "reclaim_crossing", {"mode": "trend", "level": "EMA20"}, direction),
        invalidation=primitive("invalidation", "atr_stop", {"atr_multiplier": 1.0}, direction),
        exit=primitive("exit", "fixed_r", {"target_r": 1.5, "minimum_r": 1.25}, direction),
        sizing=primitive("sizing", "paper_accounting_v2", {
            "risk_per_trade": .01, "maximum_notional_fraction": .25,
            "sizing_formula": "requested_risk_divided_by_actual_stop_cost",
        }),
        higher_timeframe_context=context,
    )


def trend_rows(timeframe="15m"):
    step = {"15m": 900, "1H": 3600, "4H": 14400}[timeframe]
    rows = []
    for index in range(221):
        close = 100 + index * .1
        rows.append({"ts": START + index * step, "open": close - .03, "high": close + .35,
                     "low": close - .35, "close": close, "volume": 100, "confirmed": 1})
    base = rows[-1]["close"]
    rows.append({"ts": START + 221 * step, "open": base, "high": base + .2,
                 "low": base - 4.2, "close": base - 4, "volume": 120, "confirmed": 1})
    rows.append({"ts": START + 222 * step, "open": base - 3.8, "high": base + 1,
                 "low": base - 4, "close": base + .8, "volume": 120, "confirmed": 1})
    for index in range(223, 232):
        close = base + .8 + (index - 222) * .5
        rows.append({"ts": START + index * step, "open": close - .2, "high": close + 1,
                     "low": close - .3, "close": close, "volume": 100, "confirmed": 1})
    return rows


def context_rows():
    rows = []
    context_start = START - 260 * 3600
    for index in range(330):
        close = 50 + index * .1
        rows.append({"ts": context_start + index * 3600, "open": close - .02,
                     "high": close + .2, "low": close - .2, "close": close,
                     "volume": 100, "confirmed": 1})
    return rows


def test_generation_is_deterministic_and_budgeted():
    first, second = generate_programs(), generate_programs()
    assert first["selected_program_identities"] == second["selected_program_identities"]
    assert 0 < first["raw_program_count"] <= SEARCH_BUDGETS["raw_structurally_valid"]
    assert len(first["programs"]) <= SEARCH_BUDGETS["semantic"]
    assert first["semantic_duplicate_count"] > 0
    assert first["structural_rejection_count"] > 0


def test_equivalent_ast_identity_and_dictionary_order():
    original = program()
    reversed_setup = replace(original, setup=tuple(reversed(original.setup)))
    context = {"fold": 1, "start": 2}
    assert original.semantic_identity == reversed_setup.semantic_identity
    assert build_program_identity(
        original, dataset_fingerprint="d", instrument="BTC-USDT", fold_identity=context
    ) == build_program_identity(
        reversed_setup, dataset_fingerprint="d", instrument="BTC-USDT",
        fold_identity=dict(reversed(list(context.items()))),
    )


def test_semantically_different_programs_do_not_collide():
    assert program().semantic_identity != replace(
        program(), exit=primitive("exit", "fixed_r", {"target_r": 2.0, "minimum_r": 1.25}, "LONG")
    ).semantic_identity


def test_legacy_v1_v2_v21_identities_are_unchanged():
    assert build_parameter_identity("TREND_BREAKOUT", {}) == "9d409e889c93b62bfab8698db1f3367787dfec9bdd1259f3113e7c9c49a344ab"
    assert build_parameter_identity("TREND_BREAKOUT_V2", {}) == "f21e4bed23f65fb1d81d6e812bfa982d6faced771d41083b23ce2e743e96f773"
    assert build_parameter_identity("TREND_BREAKOUT_V2_1", {}) == "5f92a57854557ff8b63b79ed2be2a49fd01aafafa39237e5a1a079953abcfaec"


def test_invalid_combinations_have_exact_codes():
    invalid = replace(
        program(),
        trigger=primitive("trigger", "completed_level_breakout", {"mode": "range"}, "SHORT"),
        invalidation=primitive("invalidation", "atr_stop", {"atr_multiplier": 0}, "LONG"),
        exit=primitive("exit", "fixed_r", {"target_r": 1.0, "minimum_r": 1.25}, "LONG"),
    )
    reasons = validate_program(invalid)
    assert {"DIRECTION_MISMATCH", "INCOMPATIBLE_SETUP_TRIGGER",
            "BREAKOUT_WITHOUT_COMPLETED_LEVEL", "NONFINITE_GEOMETRY",
            "REWARD_RISK_BELOW_MINIMUM"} <= set(reasons)


def test_complexity_and_duplicate_predicates_are_rejected():
    node = primitive("setup", "rsi_reset", {"mode": "trend", "threshold": 40}, "LONG")
    too_many = replace(program(), setup=(program().setup[0], node, node))
    assert {"EXCESSIVE_COMPLEXITY", "DUPLICATE_PREDICATE"} <= set(validate_program(too_many))


def test_one_continuous_setup_triggers_at_most_once():
    rows = trend_rows()
    features = build_program_features(rows)
    vector, evaluator = evaluate_trigger_vector(
        program(), rows, features, rows[200]["ts"], rows[-1]["ts"] + 900,
        instrument="BTC-USDT", dataset_fingerprint="d", fold_identity={"fold": 1},
    )
    assert vector
    counts = {}
    for evidence in evaluator.trigger_evidence.values():
        counts[evidence["setup_id"]] = counts.get(evidence["setup_id"], 0) + 1
    assert max(counts.values()) == 1


def test_state_is_isolated_across_fold_candidate_asset_and_timeframe():
    rows = trend_rows(); features = build_program_features(rows)
    kwargs = dict(instrument="BTC-USDT", dataset_fingerprint="d", fold_identity={"fold": 1})
    first = StrategyProgramEvaluator(program(), rows, features, **kwargs)
    second = StrategyProgramEvaluator(program(), rows, features, **kwargs)
    for index in range(200, 223):
        first.evaluate(index)
    assert first.state != second.state
    assert second.state == "IDLE" and second.transitions == []
    eth = StrategyProgramEvaluator(program(), rows, features, instrument="ETH-USDT",
                                   dataset_fingerprint="d", fold_identity={"fold": 2})
    assert eth.identity != first.identity
    assert program("1H").semantic_identity != program("15m").semantic_identity


def test_higher_timeframe_alignment_uses_only_fully_closed_candles():
    lower = [{"ts": 900}, {"ts": 2700}, {"ts": 3600}]
    higher = [
        {"ts": 0, "open": 1, "high": 2, "low": .5, "close": 1.5, "volume": 1},
        {"ts": 3600, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 1},
    ]
    aligned = align_higher_timeframe(lower, "15m", higher, "1H")
    assert aligned[0] is None
    assert aligned[1]["context_candle_timestamp"] == 0
    assert aligned[2]["context_candle_timestamp"] == 0
    assert aligned[2]["context_candle_close_timestamp"] <= aligned[2]["lower_decision_timestamp"]


def test_future_context_and_future_primary_candles_cannot_change_past_signal():
    rows = trend_rows(); features = build_program_features(rows)
    cutoff = 223
    vector_a, _ = evaluate_trigger_vector(
        program(), rows[:cutoff], features[:cutoff], rows[200]["ts"], rows[cutoff - 1]["ts"] + 900,
        instrument="BTC-USDT", dataset_fingerprint="d", fold_identity={"fold": 1},
    )
    changed = rows + [{"ts": rows[-1]["ts"] + 900, "open": 1, "high": 2, "low": .5,
                       "close": 1, "volume": 1, "confirmed": 1}]
    vector_b, _ = evaluate_trigger_vector(
        program(), changed, build_program_features(changed), rows[200]["ts"],
        rows[cutoff - 1]["ts"] + 900, instrument="BTC-USDT",
        dataset_fingerprint="d", fold_identity={"fold": 1},
    )
    assert vector_a == vector_b


def test_semantic_deduplication_is_deterministic():
    item = program()
    duplicate = replace(item, setup=tuple(reversed(item.setup)))
    first, aliases = semantic_deduplicate([duplicate, item])
    second, _ = semantic_deduplicate([item, duplicate])
    assert [value.semantic_identity for value in first] == [value.semantic_identity for value in second]
    assert len(first) == 1 and len(next(iter(aliases.values()))) == 2


def test_identical_trigger_vectors_are_behavior_deduplicated_by_complexity():
    simple = program()
    complex_program = replace(simple, higher_timeframe_context=primitive(
        "context", "aligned_trend_context",
        {"mode": "trend", "timeframe": "1H", "alignment": "last_fully_closed"}, "LONG"
    ))
    items = [
        {"instrument": "BTC-USDT", "program": complex_program, "trigger_vector": (1, 2)},
        {"instrument": "BTC-USDT", "program": simple, "trigger_vector": (1, 2)},
    ]
    selected, aliases = behavior_deduplicate(items)
    assert selected[0]["program"] == simple
    assert len(next(iter(aliases.values()))) == 2


def test_event_labels_stay_inside_fold_and_do_not_change_signals():
    rows = trend_rows(); features = build_program_features(rows)
    start, end = rows[200]["ts"], rows[228]["ts"]
    vector, _ = evaluate_trigger_vector(
        program(), rows, features, start, end, instrument="BTC-USDT",
        dataset_fingerprint="d", fold_identity={"fold": 1},
    )
    before = tuple(vector)
    study = event_study(program(), vector, rows, features, start, end)
    after, _ = evaluate_trigger_vector(
        program(), rows, features, start, end, instrument="BTC-USDT",
        dataset_fingerprint="d", fold_identity={"fold": 1},
    )
    assert before == after
    assert all(label["outcome_timestamp"] < end for event in study["events"]
               for label in event["labels"].values())
    assert study["diagnostic_labels_only"]


def test_btc_selection_code_has_no_eth_or_sol_dependency():
    source = inspect.getsource(orchestration._btc_pre_neighborhood)
    assert "eth" not in source.lower() and "sol" not in source.lower()


def test_cross_asset_confirmation_does_not_retune(monkeypatch):
    candidate = {"program": program()}
    calls = []
    monkeypatch.setattr(orchestration, "_backtest_asset",
                        lambda data, candidate_program, instrument:
                        calls.append((candidate_program.semantic_identity, instrument)) or {"metrics": {}})
    result = stage_c(object(), [candidate])
    assert result[0] is candidate
    assert calls == [(program().semantic_identity, "ETH-USDT"),
                     (program().semantic_identity, "SOL-USDT")]


def test_neighborhood_variants_change_exactly_one_parameter():
    variants = neighborhood_variants(program())
    assert 0 < len(variants) <= SEARCH_BUDGETS["neighborhood_per_program"]
    assert all(len(orchestration._changed_parameters(program(), variant)) == 1 for variant in variants)


def test_feature_generation_does_not_mutate_raw_ohlcv():
    rows = trend_rows(); snapshot = [dict(row) for row in rows]
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
    rows = DevelopmentData(database).candles("BTC-USDT", "15m")
    assert [row["ts"] for row in rows] == [DEVELOPMENT_END_TS - 900]


def test_runtime_never_requests_historical_cvd_or_oi():
    source = inspect.getsource(DevelopmentData.candles).lower()
    assert "historical_flow" not in source and "cvd" not in source and "open_interest" not in source


def test_generation_and_execution_budgets_are_enforced():
    generated = generate_programs()
    assert generated["raw_program_count"] <= 600
    assert len(generated["programs"]) <= 200
    assert SEARCH_BUDGETS == {
        "raw_structurally_valid": 600, "semantic": 200, "event_study": 90,
        "btc_backtest": 45, "cross_asset": 12, "neighborhood_per_program": 5,
    }


def test_every_generated_primitive_contract_evaluates_without_missing_parameters():
    rows = trend_rows(); features = build_program_features(rows)
    for candidate in generate_programs()["programs"]:
        aligned = [{
            "context_candle_timestamp": row["ts"],
            "context_candle_close_timestamp": row["ts"],
            "lower_decision_timestamp": row["ts"],
            "candle": row, "feature": feature,
        } for row, feature in zip(rows, features)]
        evaluator = StrategyProgramEvaluator(
            candidate, rows, features, instrument="BTC-USDT",
            dataset_fingerprint="d", fold_identity={"fold": 1},
            aligned_context=aligned,
        )
        evaluator.evaluate(220)


def test_repeated_planning_produces_identical_development_identities():
    identities = []
    for _ in range(2):
        identities.append([
            build_program_identity(value, dataset_fingerprint="d", instrument="BTC-USDT",
                                   fold_identity={"fold_set": "development-five-fold-v1"})
            for value in generate_programs()["programs"]
        ])
    assert identities[0] == identities[1]


@pytest.mark.parametrize("timeframe", ("15m", "1H", "4H"))
def test_each_primary_timeframe_runs_features_context_lifecycle_execution(timeframe):
    rows = trend_rows(timeframe)
    candidate = program(timeframe)
    aligned = None
    if timeframe == "15m":
        context = primitive(
            "context", "aligned_trend_context",
            {"mode": "trend", "timeframe": "1H", "alignment": "last_fully_closed"}, "LONG"
        )
        candidate = program(timeframe, context=context)
        higher = context_rows()
        aligned = align_higher_timeframe(
            rows, timeframe, higher, "1H", build_program_features(higher)
        )
    features = build_program_features(rows)
    result = run_program_backtest(
        candidate, rows, features, rows[200]["ts"], rows[-1]["ts"] + {"15m": 900, "1H": 3600, "4H": 14400}[timeframe],
        instrument="BTC-USDT", dataset_fingerprint="fixture",
        fold_identity={"fold": 1}, aligned_context=aligned,
    )
    assert result["program_evidence"]["canonical_ast"]["timeframe"] == timeframe
    assert result["lifecycle_evidence"]["transitions"]
    assert result["program_evaluations"]
    assert result["signal_count"] >= 1
    assert result["trades"]
    if timeframe == "15m":
        assert all(value["context_candle_close_timestamp"] <= value["lower_decision_timestamp"]
                   for value in result["program_evaluations"].values())
