from __future__ import annotations

from copy import deepcopy

import pytest

from dashboard.thesis_breakout_features import (
    CANONICAL_LEVEL_FEATURES_STATUS,
    FEATURE_PARAMETER_SCHEMAS,
    FAILED_BREAKDOWN_CONFIRMED,
    FAILED_BREAKOUT_CONFIRMED,
    ROLLING_HIGH_BREAKOUT_CONFIRMED,
    ROLLING_LOW_BREAKDOWN_CONFIRMED,
    RollingStructureHistoricalBatchAdapterV1,
    RollingStructureParametersV1,
    RollingStructureValidationError,
    compile_rolling_structure_rows,
)


BASE = 1_700_000_000
WIDTH = 14_400


def candles(closes, *, highs=None, lows=None):
    highs = highs or [value + 1 for value in closes]
    lows = lows or [value - 1 for value in closes]
    return [
        {
            "ts": BASE + index * WIDTH,
            "candle_close_ts": BASE + (index + 1) * WIDTH,
            "open": value,
            "high": highs[index],
            "low": lows[index],
            "close": value,
            "volume": 1,
            "confirmed": True,
        }
        for index, value in enumerate(closes)
    ]


def compile_rows(rows, lookback=5, window=3):
    return compile_rolling_structure_rows(
        rows, {"lookback_bars": lookback, "failure_window_bars": window}
    )


@pytest.mark.parametrize("value", [4, 501, True, 5.0])
def test_lookback_bounds_and_integer_contract(value):
    with pytest.raises(RollingStructureValidationError, match="lookback_bars"):
        RollingStructureParametersV1.from_mapping({"lookback_bars": value})


@pytest.mark.parametrize("value", [0, 21, True, 3.0])
def test_failure_window_bounds_and_integer_contract(value):
    with pytest.raises(RollingStructureValidationError, match="failure_window_bars"):
        RollingStructureParametersV1.from_mapping(
            {"lookback_bars": 5, "failure_window_bars": value}
        )


def test_parameter_keys_are_closed_and_lookback_is_required():
    with pytest.raises(RollingStructureValidationError, match="required"):
        RollingStructureParametersV1.from_mapping({})
    with pytest.raises(RollingStructureValidationError, match="unsupported"):
        RollingStructureParametersV1.from_mapping({"lookback_bars": 5, "sql": "select 1"})


def test_rolling_high_reference_excludes_current_and_close_confirms():
    rows = candles(
        [9, 9, 9, 9, 9, 9.5, 10.1],
        highs=[10, 10, 10, 10, 10, 10.5, 10.2],
        lows=[8] * 7,
    )
    compiled = compile_rows(rows)
    assert compiled[4]["rolling_high_reference"] is None
    assert compiled[5]["rolling_high_reference"] == 10
    # Wick 10.5 crossed the reference, but close 9.5 did not.
    assert compiled[5]["rolling_high_breakout_confirmed"] is False
    # The previous candle is now in the reference window; no current-candle leak.
    assert compiled[6]["rolling_high_reference"] == 10.5
    assert compiled[6]["rolling_high_breakout_confirmed"] is False


def test_rolling_high_and_low_confirm_on_strict_close_cross():
    up = compile_rows(candles([9, 9, 9, 9, 9, 10.01], highs=[10] * 5 + [10.1], lows=[8] * 6))
    assert up[5]["rolling_high_reference"] == 10
    assert up[5]["rolling_high_breakout_confirmed"] is True
    down = compile_rows(candles([11, 11, 11, 11, 11, 9.99], highs=[12] * 6, lows=[10] * 5 + [9.9]))
    assert down[5]["rolling_low_reference"] == 10
    assert down[5]["rolling_low_breakdown_confirmed"] is True


def test_equal_close_is_not_a_confirmed_break():
    up = compile_rows(candles([9, 9, 9, 9, 9, 10], highs=[10] * 6, lows=[8] * 6))
    down = compile_rows(candles([11, 11, 11, 11, 11, 10], highs=[12] * 6, lows=[10] * 6))
    assert up[5]["rolling_high_breakout_confirmed"] is False
    assert down[5]["rolling_low_breakdown_confirmed"] is False


def test_failed_breakout_timestamp_is_failure_confirmation_not_original_break():
    rows = candles(
        [9, 9, 9, 9, 9, 10.2, 10.1, 9.9, 9.8],
        highs=[10, 10, 10, 10, 10, 10.3, 10.2, 10, 10],
        lows=[8, 8, 8, 8, 8, 9.8, 9.8, 9.7, 9.6],
    )
    compiled = compile_rows(rows, window=3)
    assert compiled[5]["rolling_high_breakout_confirmed"] is True
    assert compiled[5]["failed_breakout_confirmed"] is False
    assert compiled[6]["failed_breakout_confirmed"] is False
    assert compiled[7]["failed_breakout_confirmed"] is True
    context = compiled[7]["rolling_structure_event_contexts"][FAILED_BREAKOUT_CONFIRMED]
    assert context["break_timestamp"] == rows[5]["candle_close_ts"]
    assert context["original_breakout_timestamp"] == rows[5]["candle_close_ts"]
    assert context["event_timestamp"] == rows[7]["candle_close_ts"]
    assert context["failure_confirmation_timestamp"] == rows[7]["candle_close_ts"]
    assert context["reference_level"] == 10


def test_failed_breakdown_timestamp_and_window_boundary_are_inclusive():
    rows = candles(
        [11, 11, 11, 11, 11, 9.8, 9.9, 10, 10.1],
        highs=[12, 12, 12, 12, 12, 10.2, 10.2, 10.3, 10.4],
        lows=[10, 10, 10, 10, 10, 9.7, 9.8, 9.9, 10],
    )
    compiled = compile_rows(rows, window=3)
    assert compiled[5]["rolling_low_breakdown_confirmed"] is True
    assert compiled[8]["failed_breakdown_confirmed"] is True
    context = compiled[8]["rolling_structure_event_contexts"][FAILED_BREAKDOWN_CONFIRMED]
    assert context["break_timestamp"] == rows[5]["candle_close_ts"]
    assert context["failure_confirmation_timestamp"] == rows[8]["candle_close_ts"]


def test_failure_after_window_is_not_confirmed():
    rows = candles(
        [9, 9, 9, 9, 9, 10.2, 10.1, 10.1, 10.1, 9.9],
        highs=[10, 10, 10, 10, 10, 10.3, 10.2, 10.2, 10.2, 10],
        lows=[8, 8, 8, 8, 8, 9.8, 9.8, 9.8, 9.8, 9.7],
    )
    compiled = compile_rows(rows, window=3)
    assert compiled[9]["failed_breakout_confirmed"] is False


def test_future_mutation_does_not_change_past_break_or_failure_membership():
    rows = candles(
        [9, 9, 9, 9, 9, 10.2, 10.1, 9.9, 9.8, 9.7],
        highs=[10, 10, 10, 10, 10, 10.3, 10.2, 10, 10, 10],
        lows=[8, 8, 8, 8, 8, 9.8, 9.8, 9.7, 9.6, 9.5],
    )
    baseline = compile_rows(rows)
    changed = deepcopy(rows)
    for row in changed[8:]:
        row.update(high=1000, low=1, close=500)
    alternate = compile_rows(changed)
    keys = (
        "rolling_high_reference", "rolling_low_reference",
        "rolling_high_breakout_confirmed", "rolling_low_breakdown_confirmed",
        "failed_breakout_confirmed", "failed_breakdown_confirmed",
        "rolling_structure_event_contexts",
    )
    assert [{key: row[key] for key in keys} for row in baseline[:8]] == [
        {key: row[key] for key in keys} for row in alternate[:8]
    ]


def test_unconfirmed_or_unordered_input_fails_closed():
    rows = candles([9] * 6)
    rows[-1]["confirmed"] = False
    with pytest.raises(RollingStructureValidationError, match="confirmed"):
        compile_rows(rows)
    rows = candles([9] * 6)
    rows[-1]["candle_close_ts"] = rows[-2]["candle_close_ts"]
    with pytest.raises(RollingStructureValidationError, match="ordered"):
        compile_rows(rows)


def test_context_exposes_reference_window_and_feature_names_are_distinct():
    rows = candles([9, 9, 9, 9, 9, 10.1], highs=[10] * 5 + [10.2], lows=[8] * 6)
    compiled = compile_rows(rows)
    context = compiled[5]["rolling_structure_event_contexts"][ROLLING_HIGH_BREAKOUT_CONFIRMED]
    assert context["reference_window_start_timestamp"] == rows[0]["candle_close_ts"]
    assert context["reference_window_end_timestamp"] == rows[4]["candle_close_ts"]
    assert context["reference_level"] == 10
    assert ROLLING_HIGH_BREAKOUT_CONFIRMED != ROLLING_LOW_BREAKDOWN_CONFIRMED
    assert CANONICAL_LEVEL_FEATURES_STATUS["status"] == "DEFERRED"


def test_batch_adapter_is_a_closed_v2_engine_integration_seam():
    rows = candles([9, 9, 9, 9, 9, 10.1], highs=[10] * 5 + [10.2], lows=[8] * 6)
    adapter = RollingStructureHistoricalBatchAdapterV1()
    compiled = adapter.compile(rows, {"lookback_bars": 5})
    assert set(adapter.feature_codes) == {
        ROLLING_HIGH_BREAKOUT_CONFIRMED,
        ROLLING_LOW_BREAKDOWN_CONFIRMED,
        FAILED_BREAKOUT_CONFIRMED,
        FAILED_BREAKDOWN_CONFIRMED,
    }
    assert adapter.value(ROLLING_HIGH_BREAKOUT_CONFIRMED, compiled[-1]) is True
    with pytest.raises(RollingStructureValidationError, match="unsupported"):
        adapter.value("PYTHON", compiled[-1])
    assert FEATURE_PARAMETER_SCHEMAS[ROLLING_HIGH_BREAKOUT_CONFIRMED]["lookback_bars"] == {
        "type": "integer", "minimum": 5, "maximum": 500, "required": True,
    }
    assert FEATURE_PARAMETER_SCHEMAS[FAILED_BREAKOUT_CONFIRMED]["failure_window_bars"]["default"] == 3
    assert FEATURE_PARAMETER_SCHEMAS[FAILED_BREAKOUT_CONFIRMED]["failure_window_bars"]["required"] is True
