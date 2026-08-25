from __future__ import annotations

import time

import pytest

from dashboard.thesis_event_engine import ThesisValidationError
from dashboard.thesis_event_engine_v2 import (
    ThesisEventEngineV2, _causal_observation_series, compile_thesis_v2, thesis_capabilities_v2,
)
from dashboard.thesis_expression import (
    ExpressionValidationError, feature_contracts_from_capabilities,
    parse_thesis_spec_v2,
)


BASE = 1_700_006_400


def candles(count: int, *, timeframe: str = "4H", close_fn=None):
    width = {"15m": 900, "1H": 3_600, "4H": 14_400, "1D": 86_400}[timeframe]
    start = BASE - BASE % width
    output = []
    for index in range(count):
        close = float(close_fn(index) if close_fn else 100 + (index % 12) - 6)
        output.append({
            "ts": start + index * width, "candle_close_ts": start + (index + 1) * width,
            "open": close, "high": close + 1, "low": close - 1, "close": close,
            "volume": float(100 + (index % 20) * 5), "confirmed": 1,
            "source": "fixture", "source_version": "v1", "_source_store": "historical_candles",
        })
    return output


def runtime(*, derivatives_ready: bool = False):
    readiness = ({key: {"status": "READY"} for key in ("OI", "FUNDING", "BASIS")}
                 if derivatives_ready else None)
    capabilities = thesis_capabilities_v2(readiness)
    return capabilities, feature_contracts_from_capabilities(capabilities)


def spec(raw_expression, rows, *, timeframe="4H", horizons=None, derivatives_ready=False):
    capabilities, registry = runtime(derivatives_ready=derivatives_ready)
    value = parse_thesis_spec_v2({
        "version": "thesis-spec-v2", "instrument": "BTC", "timeframe": timeframe,
        "expression": raw_expression, "forward_horizons": horizons or ["12H", "24H"],
        "requested_as_of": rows[-1]["candle_close_ts"], "assumptions": [], "metadata": {},
    }, registry, supported_instruments=("BTC", "ETH", "SOL"),
       supported_timeframes=("15m", "1H", "4H", "1D"),
       supported_horizons=("4H", "12H", "24H", "3D", "7D"))
    return value, registry


def condition(feature, operator, value, parameters=None):
    return {"node_type": "CONDITION", "feature": feature, "operator": operator,
            "value": value, "parameters": parameters or {}}


def test_or_and_not_execute_as_one_expression_without_branch_event_inflation():
    rows = candles(160)
    expression = {"node_type": "ANY", "children": [
        condition("RSI", "gte", 60),
        {"node_type": "NOT", "child": condition("VOLUME_PERCENTILE", "gte", 95)},
    ]}
    parsed, registry = spec(expression, rows)
    result = ThesisEventEngineV2(registry).run(parsed, rows)
    assert result["status"] == "COMPLETED"
    included = [item for item in result["event_records"] if item["exclusion_status"] == "INCLUDED"]
    timestamps = [item["timestamp"] for item in included]
    assert timestamps == sorted(set(timestamps))
    # Candidate membership is owned by the overall tree transition, not by
    # changes in a branch while the ANY group remains TRUE.
    assert result["raw_candidate_count"] <= len(rows)
    assert all(item["expression_result"]["node_type"] == "ANY" for item in included)


def test_rolling_breakout_uses_prior_window_and_wick_is_not_confirmation():
    values = [100.0] * 35 + [105.0] + [100.0] * 20
    rows = candles(len(values), close_fn=lambda index: values[index])
    # Prior candle wicks far above its close; the breakout reference includes
    # that wick, so a close below it must not confirm.
    rows[34]["high"] = 110.0
    expression = condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True,
                           {"lookback_bars": 20})
    parsed, registry = spec(expression, rows)
    result = ThesisEventEngineV2(registry).run(parsed, rows)
    assert result["status"] == "COMPLETED"
    assert rows[35]["close"] == 105.0
    assert all(item["timestamp"] != rows[35]["candle_close_ts"]
               for item in result["event_records"])


def test_failed_breakout_is_timestamped_at_failure_confirmation():
    values = [100.0] * 35 + [112.0, 111.0, 99.0] + [100.0] * 30
    rows = candles(len(values), close_fn=lambda index: values[index])
    for index in range(35):
        rows[index]["high"] = 101.0
    expression = condition("FAILED_BREAKOUT_CONFIRMED", "eq", True,
                           {"lookback_bars": 20, "failure_window_bars": 3})
    parsed, registry = spec(expression, rows)
    result = ThesisEventEngineV2(registry).run(parsed, rows)
    events = [item for item in result["event_records"] if item["exclusion_status"] == "INCLUDED"]
    assert events
    event = events[0]
    assert event["timestamp"] == rows[37]["candle_close_ts"]
    context = event["event_context"][0]
    assert context["original_breakout_timestamp"] == rows[35]["candle_close_ts"]
    assert context["failure_confirmation_timestamp"] == rows[37]["candle_close_ts"]


def test_future_data_cannot_rewrite_breakout_or_failed_membership_before_future():
    values = [100.0] * 35 + [112.0, 111.0, 110.0, 109.0] + [100.0] * 30
    original = candles(len(values), close_fn=lambda index: values[index])
    changed = [dict(item) for item in original]
    changed[38].update({"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0})
    breakout = condition("ROLLING_HIGH_BREAKOUT_CONFIRMED", "eq", True, {"lookback_bars": 20})
    failed = condition("FAILED_BREAKOUT_CONFIRMED", "eq", True,
                       {"lookback_bars": 20, "failure_window_bars": 3})
    for expression in (breakout, failed):
        left_spec, registry = spec(expression, original)
        right_spec, _ = spec(expression, changed)
        left = ThesisEventEngineV2(registry).run(left_spec, original)
        right = ThesisEventEngineV2(registry).run(right_spec, changed)
        cutoff = original[38]["candle_close_ts"]
        assert [item["timestamp"] for item in left["event_records"] if item["timestamp"] < cutoff] == [
            item["timestamp"] for item in right["event_records"] if item["timestamp"] < cutoff]


def test_derivative_missing_blocks_whole_definition_and_is_not_silently_dropped():
    rows = candles(120)
    capabilities, registry = runtime(derivatives_ready=False)
    parsed = parse_thesis_spec_v2({
            "version": "thesis-spec-v2", "instrument": "BTC", "timeframe": "4H",
            "expression": condition("OI_CHANGE_PERCENTILE", "gte", 90),
            "forward_horizons": ["24H"], "requested_as_of": rows[-1]["candle_close_ts"],
            "assumptions": [], "metadata": {},
        }, registry, supported_instruments=("BTC", "ETH", "SOL"),
           supported_timeframes=("15m", "1H", "4H", "1D"),
           supported_horizons=("4H", "12H", "24H", "3D", "7D"))
    with pytest.raises(ThesisValidationError, match="historical feature is unavailable"):
        compile_thesis_v2(parsed, registry)
    assert next(item for item in capabilities["features"]
                if item["code"] == "OI_CHANGE_PERCENTILE")["availability_reason"]


def test_derivative_percentile_is_pit_and_future_change_does_not_rewrite_past():
    rows = candles(120)
    for index, row in enumerate(rows):
        row["open_interest_usd"] = 1_000_000.0 + index * 10_000
        row["_open_interest_usd_source_ts_ms"] = row["candle_close_ts"] * 1000
    expression = condition("OI_CHANGE_PERCENTILE", "gte", 90)
    parsed, registry = spec(expression, rows, derivatives_ready=True)
    left = ThesisEventEngineV2(registry).run(parsed, rows)
    changed = [dict(item) for item in rows]
    changed[-1]["open_interest_usd"] = 1.0
    right = ThesisEventEngineV2(registry).run(parsed, changed)
    cutoff = rows[-1]["candle_close_ts"]
    assert [item["timestamp"] for item in left["event_records"] if item["timestamp"] < cutoff] == [
        item["timestamp"] for item in right["event_records"] if item["timestamp"] < cutoff]


def test_repeated_settlement_is_ranked_once_and_cannot_create_a_new_transition():
    rows = [
        {"funding_rate": value, "_funding_rate_source_ts_ms": source_ts}
        for source_ts, value in [
            *[(index, float(index)) for index in range(31)],
            (30, 30.0), (30, 30.0), (31, 5.0),
        ]
    ]
    values, percentiles = _causal_observation_series(
        rows, value_key="funding_rate", source_timestamp_key="_funding_rate_source_ts_ms",
        min_history=30)
    assert percentiles[30] == percentiles[31] == percentiles[32]
    assert values[30] == values[31] == values[32] == 30.0
    assert percentiles[33] is not None


def test_1d_rejects_subdaily_horizon_but_allows_3d():
    rows = candles(80, timeframe="1D")
    expression = condition("RSI", "lte", 80)
    parsed, registry = spec(expression, rows, timeframe="1D", horizons=["4H"])
    with pytest.raises(ThesisValidationError, match="at least 24H"):
        compile_thesis_v2(parsed, registry)
    parsed, registry = spec(expression, rows, timeframe="1D", horizons=["3D"])
    assert compile_thesis_v2(parsed, registry).forward_horizons == ("3D",)


def test_definition_and_result_hashes_are_deterministic():
    rows = candles(140)
    expression = {"node_type": "ALL", "children": [
        condition("RSI", "lte", 80), condition("VOLUME_PERCENTILE", "gte", 70),
    ]}
    parsed, registry = spec(expression, rows)
    engine = ThesisEventEngineV2(registry)
    first, second = engine.run(parsed, rows), engine.run(parsed, rows)
    assert first["definition_hash"] == second["definition_hash"]
    assert first["result_hash"] == second["result_hash"]


def test_failed_breakout_rejects_a_hidden_default_window():
    rows = candles(80)
    with pytest.raises(ExpressionValidationError, match="failure_window_bars"):
        spec(condition("FAILED_BREAKOUT_CONFIRMED", "eq", True,
                       {"lookback_bars": 20}), rows)
