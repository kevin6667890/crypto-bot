from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

import pytest

from dashboard.market_state_v2 import MarketStateEngineV2, STATE_ENGINE_VERSION


AS_OF = 1_800_000_000
ENGINE = MarketStateEngineV2()


def indicator(value, *, ts=AS_OF, available=True, stale=False, partial=False):
    return {
        "value": value, "source_timestamp": ts if value is not None else None,
        "available": available and value is not None, "stale": stale,
        "partial": partial, "warmup_complete": value is not None,
        "calculation_version": "fixture-v2",
    }


def frame(direction="up", *, volatility="normal", momentum="neutral", confirmed=True):
    sign = 1 if direction == "up" else -1 if direction == "down" else 0
    arrangement = (
        "EMA20_GT_MA60_GT_MA200" if sign > 0 else
        "EMA20_LT_MA60_LT_MA200" if sign < 0 else "MIXED"
    )
    stoch = 50 if momentum == "neutral" else 10 if momentum == "oversold" else 90
    compression = 90 if volatility == "low" else 5
    expansion = 90 if volatility == "high" else 5
    return {
        "candle_close_ts": AS_OF, "confirmed": confirmed,
        "trend": {
            "ema20": indicator(100), "ma60": indicator(98), "ma200": indicator(95),
            "ema20_slope": indicator(sign * 1.0), "ma60_slope": indicator(sign * .8),
            "ma200_slope": indicator(sign * .5),
            "close_distance_to_ema20": indicator(sign * 2.0),
            "close_distance_to_ma60": indicator(sign * 4.0),
            "close_distance_to_ma200": indicator(sign * 8.0),
            "ma_arrangement": indicator(arrangement),
        },
        "momentum": {
            "rsi14": indicator(50 if momentum == "neutral" else 25 if momentum == "oversold" else 75),
            "stoch_rsi": indicator(stoch), "stoch_rsi_k": indicator(stoch),
            "stoch_rsi_d": indicator(stoch), "price_momentum": indicator(sign * 4),
            "momentum_persistence": indicator(sign * .7),
        },
        "volatility": {
            "atr14": indicator(2), "atr_percentage": indicator(3 if volatility == "high" else .5 if volatility == "low" else 1.2),
            "bollinger_upper": indicator(110), "bollinger_mid": indicator(100),
            "bollinger_lower": indicator(90), "bollinger_bandwidth": indicator(4),
            "realized_volatility": indicator(2), "compression_percentile": indicator(compression),
            "expansion_percentile": indicator(expansion),
        },
        "structure": {
            "recent_confirmed_swing_high": indicator(110), "recent_confirmed_swing_low": indicator(90),
            "rolling_high_distance": indicator(0), "rolling_low_distance": indicator(0),
        },
        "volume": {
            "volume": indicator(1000), "volume_moving_average": indicator(800),
            "volume_ratio": indicator(1.4), "candle_body_percentage": indicator(60),
            "upper_wick_percentage": indicator(20), "lower_wick_percentage": indicator(20),
        },
        "quality": {"status": "AVAILABLE", "source_timestamp": AS_OF, "stale": False,
                    "partial": False, "missing": False, "gaps": [], "notes": []},
    }


def context(directions=None, *, volatility="normal", momentum="neutral", price=100.0):
    directions = directions or {name: "up" for name in ("1W", "1D", "4H", "1H", "15m")}
    frames = {name: frame(directions.get(name, "mixed"), volatility=volatility,
                          momentum=momentum) for name in ("1W", "1D", "4H", "1H", "15m")}
    return {
        "version": "market-analysis-context-v2", "instrument": "ETH-USDT-SWAP",
        "as_of": AS_OF, "execution_timeframe": "15m", "price": indicator(price),
        "timeframes": frames,
        "flow": {
            "cvd": {}, "oi": {}, "funding": {}, "basis": {}, "vpvr": {},
            "price_oi_combination": {"state": "INSUFFICIENT_DATA", "data_quality": "MISSING"},
            "price_cvd_combination": {"state": "INSUFFICIENT_DATA", "data_quality": "MISSING"},
        },
        "levels": [],
        "quality": {"overall_status": "PARTIAL", "stale_sources": [],
                    "partial_sources": [], "missing_sources": ["cvd", "oi"], "gaps": []},
    }


def advance(value, seconds=900):
    current = deepcopy(value); current["as_of"] += seconds
    current["price"]["source_timestamp"] += seconds
    for item in current["timeframes"].values():
        if not item:
            continue
        item["candle_close_ts"] += seconds
        if item["quality"].get("source_timestamp") is not None:
            item["quality"]["source_timestamp"] += seconds
        for group in ("trend", "momentum", "volatility", "structure", "volume"):
            for fact in item[group].values():
                if fact["source_timestamp"] is not None:
                    fact["source_timestamp"] += seconds
    return current


@pytest.mark.parametrize(("direction", "expected"), [
    ("up", "TREND_UP"), ("down", "TREND_DOWN"),
])
def test_clear_directional_trends(direction, expected):
    result = ENGINE.evaluate(context({name: direction for name in ("1W", "1D", "4H", "1H", "15m")}))
    assert all(item["primary_state"] == expected for item in result["timeframes"].values())
    assert result["primary_state_code"] == ("HTF_UPTREND_CONTINUATION" if direction == "up" else "HTF_DOWNTREND_CONTINUATION")


@pytest.mark.parametrize(("volatility", "expected"), [
    ("high", "RANGE_HIGH_VOLATILITY"), ("low", "RANGE_LOW_VOLATILITY"),
])
def test_range_volatility_states(volatility, expected):
    value = context({name: "mixed" for name in ("1W", "1D", "4H", "1H", "15m")}, volatility=volatility)
    result = ENGINE.evaluate(value)
    assert result["timeframes"]["4H"]["primary_state"] == expected
    assert result["primary_state_code"] in {"RANGE_ROTATION", "VOLATILITY_TRANSITION"}


def test_mixed_transition_and_conflict():
    value = context({"1W": "up", "1D": "down", "4H": "mixed", "1H": "up", "15m": "down"})
    result = ENGINE.evaluate(value)
    assert result["timeframes"]["4H"]["primary_state"] == "TRANSITION_MIXED"
    assert result["cross_timeframe"]["state"] == "CONFLICTED"


@pytest.mark.parametrize(("higher", "lower", "alignment", "composite"), [
    ("up", "down", "HIGHER_UP_LOWER_PULLBACK", "HTF_UPTREND_PULLBACK"),
    ("down", "up", "HIGHER_DOWN_LOWER_BOUNCE", "HTF_DOWNTREND_BOUNCE"),
])
def test_higher_timeframe_context_preserved_during_lower_move(higher, lower, alignment, composite):
    value = context({"1W": higher, "1D": higher, "4H": higher, "1H": lower, "15m": lower})
    result = ENGINE.evaluate(value)
    assert result["cross_timeframe"]["state"] == alignment
    assert result["primary_state_code"] == composite


@pytest.mark.parametrize(("distance", "overlay"), [
    (.1, "TESTING_MA200_FROM_ABOVE"), (-.1, "TESTING_MA200_FROM_BELOW"),
])
def test_ma200_test_direction_is_atr_scaled(distance, overlay):
    value = context()
    value["timeframes"]["4H"]["trend"]["close_distance_to_ma200"] = indicator(distance)
    result = ENGINE.evaluate(value)
    assert overlay in result["timeframes"]["4H"]["overlays"]
    assert "TESTING_MA200" in result["timeframes"]["4H"]["overlays"]


def test_ma200_reclaim_and_breakdown_candidates_are_not_bounce_claims():
    above = context(); above["timeframes"]["4H"]["trend"]["close_distance_to_ma200"] = indicator(.1)
    below = context(); below["timeframes"]["4H"]["trend"]["close_distance_to_ma200"] = indicator(-.1)
    assert "MA200_RECLAIM_CANDIDATE" in ENGINE.evaluate(above)["timeframes"]["4H"]["overlays"]
    assert "MA200_BREAKDOWN_CANDIDATE" in ENGINE.evaluate(below)["timeframes"]["4H"]["overlays"]


def test_major_ma200_test_composes_by_approach_side():
    above = add_level(context(price=100.02), level_type="MA200")
    below = add_level(context(price=99.98), level_type="MA200")
    assert ENGINE.evaluate(above)["primary_state_code"] == "MAJOR_SUPPORT_TEST"
    assert ENGINE.evaluate(below)["primary_state_code"] == "MAJOR_RESISTANCE_TEST"


def test_ma200_breakdown_and_reclaim_require_next_confirmed_snapshot():
    from_above = add_level(context(price=100.02), level_type="MA200")
    below = advance(from_above); below["price"]["value"] = 98
    broken = ENGINE.compare(from_above, below)["current"]["level_interactions"][0]
    assert broken["current_stage"] == "MA200_BREAKDOWN_CONFIRMED"
    from_below = add_level(context(price=99.98), level_type="MA200")
    above = advance(from_below); above["price"]["value"] = 102
    reclaimed = ENGINE.compare(from_below, above)["current"]["level_interactions"][0]
    assert reclaimed["reclaim_status"] == "RECLAIMED_ABOVE_MA200"


def add_level(value, *, level_type="SWING_LOW", timeframe="4H", confirmed=True):
    value["levels"] = [{"type": level_type, "timeframe": timeframe, "value": 100.0,
                        "source_timestamp": AS_OF - 14_400, "distance_pct": 0,
                        "touches": 3, "confirmed": confirmed, "confluence_sources": [],
                        "calculation_version": "fixture"}]
    return value


@pytest.mark.parametrize(("price", "interaction"), [
    (100.35, "APPROACHING"), (100.02, "TOUCHING"), (98.0, "BROKEN"),
])
def test_support_approach_touch_and_confirmed_break(price, interaction):
    result = ENGINE.evaluate(add_level(context(price=price)))
    assert result["level_interactions"][0]["interaction_type"] == interaction


def test_unconfirmed_context_cannot_mark_level_broken():
    value = add_level(context(price=98.0))
    value["timeframes"]["4H"]["confirmed"] = False
    result = ENGINE.evaluate(value)
    assert result["level_interactions"][0]["interaction_type"] != "BROKEN"


def test_resistance_breakout_candidate_records_boundary_metadata():
    result = ENGINE.evaluate(add_level(context(price=102), level_type="SWING_HIGH"))
    interaction = result["level_interactions"][0]
    assert interaction["current_stage"] == "BREAKOUT_CANDIDATE"
    assert interaction["boundary"] == 100
    assert interaction["volume_ratio"] == 1.4
    assert interaction["confirmation_timestamp"] is None


def test_breakout_requires_expansion_and_prior_touch():
    value = add_level(context(price=102), level_type="SWING_HIGH")
    value["levels"][0]["touches"] = 0
    value["timeframes"]["4H"]["volume"]["volume_ratio"] = indicator(.8)
    result = ENGINE.evaluate(value)
    assert "BREAKOUT_CANDIDATE" not in result["overlays"]
    assert result["level_interactions"][0]["current_stage"] == "BOUNDARY_BREACH_OBSERVED"


def test_two_confirmed_closes_confirm_breakout():
    previous = add_level(context(price=102), level_type="SWING_HIGH")
    current = advance(previous); current["price"]["value"] = 103
    compared = ENGINE.compare(previous, current)
    interaction = compared["current"]["level_interactions"][0]
    assert interaction["current_stage"] == "BREAKOUT_CONFIRMED"
    assert interaction["confirmation_timestamp"] == current["as_of"]
    assert "BREAKOUT_CONFIRMED" in compared["current"]["overlays"]
    assert compared["current"]["primary_state_code"] == "HTF_UPTREND_CONTINUATION"
    assert any(item["from_state"] == "BREAKOUT_DEVELOPING" for item in compared["transitions"])


def test_boundary_retest_is_distinct_from_confirmation():
    previous = add_level(context(price=102), level_type="SWING_HIGH")
    current = advance(previous); current["price"]["value"] = 100.05
    interaction = ENGINE.compare(previous, current)["current"]["level_interactions"][0]
    assert interaction["interaction_type"] == "RETESTING"
    assert interaction["confirmation_timestamp"] is None


def test_confirmed_reentry_is_failed_breakout_candidate():
    previous = add_level(context(price=102), level_type="SWING_HIGH")
    current = advance(previous); current["price"]["value"] = 99
    compared = ENGINE.compare(previous, current)
    interaction = compared["current"]["level_interactions"][0]
    assert compared["current"]["primary_state_code"] == "FAILED_BREAKOUT_DEVELOPING"
    assert interaction["interaction_type"] == "RECLAIMED"
    assert interaction["reclaim_timestamp"] == current["as_of"]


def test_two_confirmed_closes_confirm_breakdown_and_reclaim_is_separate():
    previous = add_level(context(price=98), level_type="SWING_LOW")
    maintained = advance(previous); maintained["price"]["value"] = 97
    assert "BREAKDOWN_CONFIRMED" in ENGINE.compare(previous, maintained)["current"]["overlays"]
    reclaimed = advance(previous); reclaimed["price"]["value"] = 101
    interaction = ENGINE.compare(previous, reclaimed)["current"]["level_interactions"][0]
    assert interaction["reclaim_status"] == "RECLAIMED_ABOVE_PRIOR_SUPPORT"


def test_stoch_extremes_only_add_momentum_overlays():
    oversold = ENGINE.evaluate(context(momentum="oversold"))
    overbought = ENGINE.evaluate(context(momentum="overbought"))
    assert oversold["timeframes"]["15m"]["primary_state"] == "TREND_UP"
    assert "MOMENTUM_OVERSOLD" in oversold["timeframes"]["15m"]["overlays"]
    assert overbought["timeframes"]["15m"]["primary_state"] == "TREND_UP"
    assert "MOMENTUM_OVERBOUGHT" in overbought["timeframes"]["15m"]["overlays"]


@pytest.mark.parametrize(("volatility", "overlay"), [
    ("low", "4H:VOLATILITY_COMPRESSION"),
    ("high", "4H:VOLATILITY_EXPANSION"),
])
def test_causal_compression_and_expansion_overlays(volatility, overlay):
    assert overlay in ENGINE.evaluate(context(volatility=volatility))["overlays"]


def test_compression_release_candidate_requires_expansion_volume():
    result = ENGINE.evaluate(context(volatility="high"))
    assert "4H:COMPRESSION_RELEASE_CANDIDATE" in result["overlays"]


def test_compression_to_expansion_emits_confirmed_transition():
    previous = context(volatility="low")
    current = advance(previous)
    for item in current["timeframes"].values():
        item["volatility"]["compression_percentile"]["value"] = 5
        item["volatility"]["expansion_percentile"]["value"] = 90
        item["volatility"]["atr_percentage"]["value"] = 3
    transitions = ENGINE.compare(previous, current)["transitions"]
    assert any(item["from_state"] == "VOLATILITY_COMPRESSION" and
               item["to_state"] == "VOLATILITY_EXPANSION" and
               item["confirmation_status"] == "CONFIRMED" for item in transitions)


@pytest.mark.parametrize(("quality", "expected"), [
    ("MISSING", "FLOW_UNAVAILABLE"), ("STALE", "FLOW_STALE"), ("PARTIAL", "FLOW_PARTIAL"),
])
def test_flow_quality_gates_confirmation(quality, expected):
    value = context()
    value["flow"]["price_oi_combination"] = {"state": "PRICE_UP_OI_UP", "data_quality": quality}
    value["flow"]["price_cvd_combination"] = {"state": "PRICE_UP_CVD_UP", "data_quality": quality}
    assert expected in ENGINE.evaluate(value)["overlays"]


def test_execution_timeframe_missing_is_insufficient_but_weekly_missing_is_not():
    missing_execution = context(); missing_execution["timeframes"]["15m"] = {}
    assert ENGINE.evaluate(missing_execution)["primary_state_code"] == "INSUFFICIENT_DATA"
    missing_weekly = context(); missing_weekly["timeframes"]["1W"] = {}
    assert ENGINE.evaluate(missing_weekly)["primary_state_code"] != "INSUFFICIENT_DATA"


def test_unconfirmed_higher_timeframe_is_unavailable():
    value = context(); value["timeframes"]["1D"]["confirmed"] = False
    result = ENGINE.evaluate(value)
    assert result["timeframes"]["1D"]["primary_state"] == "UNKNOWN"


def test_future_source_timestamp_is_rejected():
    value = context(); value["timeframes"]["4H"]["trend"]["ma200"]["source_timestamp"] = AS_OF + 1
    with pytest.raises(ValueError, match="later than as_of"):
        ENGINE.evaluate(value)


def test_deterministic_identity_and_past_is_unchanged_by_future_context():
    value = context()
    first = ENGINE.evaluate(value)
    assert first == ENGINE.evaluate(deepcopy(value))
    future = advance(value)
    ENGINE.evaluate(future)
    assert first == ENGINE.evaluate(value)


def test_state_transition_compare_is_bounded_and_deterministic():
    previous = context({name: "mixed" for name in ("1W", "1D", "4H", "1H", "15m")}, volatility="low")
    current = advance(previous)
    add_level(current, level_type="SWING_HIGH"); current["price"]["value"] = 102
    compared = ENGINE.compare(previous, current)
    assert compared == ENGINE.compare(deepcopy(previous), deepcopy(current))
    assert len(compared["transitions"]) <= 6


def test_supporting_conflicting_and_unavailable_evidence_are_returned():
    value = context(); value["timeframes"]["4H"]["trend"]["ma60_slope"] = indicator(-.5)
    value["timeframes"]["4H"]["momentum"]["price_momentum"] = indicator(None)
    result = ENGINE.evaluate(value)["timeframes"]["4H"]
    assert result["supporting_evidence"]
    assert "MA60_FALLING" in result["conflicting_evidence"]
    assert "PRICE_MOMENTUM_UNAVAILABLE" in result["unavailable_evidence"]


def test_evidence_strength_is_bounded_and_not_named_probability():
    result = ENGINE.evaluate(context())
    assert 0 <= result["evidence_strength"] <= 100
    assert not {"probability", "win_rate", "success_rate"} & result.keys()


def test_engine_performance_fixture_under_50ms_average():
    value = context()
    started = time.perf_counter()
    for _ in range(100):
        ENGINE.evaluate(value)
    assert (time.perf_counter() - started) / 100 < .05


def test_read_api_pipeline_fixture_under_600ms_with_serialization():
    value = context()
    started = time.perf_counter()
    payload = json.dumps(ENGINE.evaluate(value))
    assert (time.perf_counter() - started) < .6
    assert '"version": "market-state-engine-v2"' in payload


def test_api_is_read_only_and_isolated_from_decision_and_llm():
    source = Path("dashboard/paper_api.py").read_text(encoding="utf-8")
    state_block = source[source.index('elif parsed.path == "/api/market/state":'):source.index('elif parsed.path == "/api/paper/flow/health"')]
    assert "evaluate_decision" not in state_block
    assert "ask_copilot" not in state_block and "DeepSeek" not in state_block
    assert not any(token in state_block for token in ("INSERT ", "UPDATE ", "DELETE ", "create_order"))


def test_openapi_contract_and_generated_types_cover_state_v2():
    schema = json.loads(Path("frontend/openapi/openapi.json").read_text(encoding="utf-8"))
    assert schema["paths"]["/api/market/state"]["get"]["operationId"] == "getMarketStateV2"
    assert "/api/market/state/compare" in schema["paths"]
    for name in ("MarketStateSnapshotV2", "TimeframeStateV2", "StateEvidenceV2",
                 "StateTransitionV2", "LevelInteractionV2", "CrossTimeframeAlignmentV2"):
        assert name in schema["components"]["schemas"]
    assert STATE_ENGINE_VERSION == "market-state-engine-v2"
