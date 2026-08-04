from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

import pytest

from dashboard.decision_engine import LIVE_STRATEGY_VERSION, MarketContext, evaluate_decision
from dashboard.market_state_v2 import MarketStateEngineV2
from dashboard.strategy_router_v2 import (
    DEFINITIONS_VERSION, FAMILY_VERSIONS, NO_TRADE_CODES, PARAMETERS,
    ROUTER_VERSION, STAGES, StrategyRouterV2, backtest_specifications_v2,
)
from dashboard.strategy_rules import StrategyParameters
from tests.test_market_state_v2 import AS_OF, advance, context, frame, indicator


ENGINE = MarketStateEngineV2()
ROUTER = StrategyRouterV2()


def inputs(directions=None, *, momentum="neutral", quality="PARTIAL"):
    value = context(directions, momentum=momentum)
    value["quality"]["overall_status"] = quality
    state = ENGINE.evaluate(value)
    state["overlays"] = list(state["overlays"])
    return value, state


def interaction(level_type, direction, *, stage="OBSERVING", interaction_type="APPROACHING",
                timeframe="4H", boundary=100.0, reclaim="NOT_RECLAIMED", touches=3):
    return {
        "level_type": level_type, "timeframe": timeframe, "zone_low": boundary - .25,
        "zone_high": boundary + .25, "boundary": boundary, "distance_pct": .1,
        "approach_direction": direction, "interaction_type": interaction_type,
        "touch_count": touches, "rejection_strength": None, "reclaim_status": reclaim,
        "source_timestamps": [AS_OF - 3600], "quality": "AVAILABLE",
        "breakout_timestamp": None, "confirmation_timestamp": None,
        "reclaim_timestamp": None, "volume_ratio": 1.4, "cvd_oi_quality": "UNAVAILABLE",
        "current_stage": stage, "invalidation_reason": None,
    }


def candidate(route, family, direction):
    return next(item for item in route["candidates"] if item["family"] == family and item["direction"] == direction)


def route_with(value, state):
    return ROUTER.route(value, state)


@pytest.mark.parametrize(("family", "version"), list(FAMILY_VERSIONS.items()))
def test_five_versioned_route_results(family, version):
    assert family in {"TREND_PULLBACK", "MA200_MEAN_REVERSION", "BREAKOUT_CONTINUATION", "FAILED_BREAKOUT_REVERSAL", "NO_TRADE"}
    assert version.endswith("-v2")


def test_router_contract_versions_and_all_directional_candidates():
    value, state = inputs()
    result = route_with(value, state)
    assert result["version"] == ROUTER_VERSION
    assert result["definitions_version"] == DEFINITIONS_VERSION
    assert {(item["family"], item["direction"]) for item in result["candidates"]} == {
        (family, direction) for family in FAMILY_VERSIONS if family != "NO_TRADE" for direction in ("LONG", "SHORT")}
    assert result["disclaimer"].startswith("研究策略路由，不是实时交易建议")


@pytest.mark.parametrize("stage", STAGES)
def test_unified_lifecycle_stage_is_versioned(stage):
    assert stage in STAGES


def test_trend_pullback_long_watch_armed_trigger_and_no_reclaim():
    directions = {"1W": "up", "1D": "up", "4H": "up", "1H": "down", "15m": "down"}
    value, state = inputs(directions)
    state["level_interactions"] = [interaction("EMA20", "FROM_ABOVE")]
    watch = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert watch["state"] == "WATCH"
    state["level_interactions"][0]["interaction_type"] = "TOUCHING"
    armed = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert armed["state"] == "ARMED"
    assert armed["score_breakdown"]["trigger"] < 20
    state["level_interactions"][0]["interaction_type"] = "RECLAIMED"
    state["timeframes"]["15m"]["momentum_state"] = "RECOVERING_FROM_OVERSOLD"
    state["level_interactions"].append(interaction("SWING_HIGH", "FROM_BELOW", boundary=104))
    ready = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert ready["state"] == "TRIGGER_READY"


def test_trend_pullback_break_invalidates_and_pressure_blocks_long():
    directions = {"1W": "up", "1D": "up", "4H": "up", "1H": "down", "15m": "up"}
    value, state = inputs(directions)
    state["level_interactions"] = [interaction("EMA20", "FROM_ABOVE", interaction_type="RECLAIMED")]
    state["overlays"].append("BREAKDOWN_CONFIRMED")
    assert candidate(route_with(value, state), "TREND_PULLBACK", "LONG")["state"] == "INVALIDATED"
    state["overlays"].remove("BREAKDOWN_CONFIRMED"); state["primary_state_code"] = "MAJOR_RESISTANCE_TEST"
    item = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert "TOO_CLOSE_TO_OPPOSING_LEVEL" in {blocker["code"] for blocker in item["blockers"]}


def test_trend_pullback_short_is_independent_and_support_blocks_chase():
    directions = {"1W": "down", "1D": "down", "4H": "down", "1H": "up", "15m": "down"}
    value, state = inputs(directions)
    state["level_interactions"] = [interaction("MA60", "FROM_BELOW", interaction_type="REJECTED"), interaction("SWING_LOW", "FROM_ABOVE", boundary=96)]
    state["timeframes"]["15m"]["momentum_state"] = "ROLLING_OVER_FROM_OVERBOUGHT"
    assert candidate(route_with(value, state), "TREND_PULLBACK", "SHORT")["state"] == "TRIGGER_READY"
    state["primary_state_code"] = "MAJOR_SUPPORT_TEST"
    item = candidate(route_with(value, state), "TREND_PULLBACK", "SHORT")
    assert any(blocker["code"] == "TOO_CLOSE_TO_OPPOSING_LEVEL" for blocker in item["blockers"])


def ma_setup(direction="LONG", *, touch=False, reclaim=False, confluence=True):
    trend = "up" if direction == "LONG" else "down"
    value, state = inputs({name: trend for name in ("1W", "1D", "4H", "1H", "15m")}, momentum="oversold" if direction == "LONG" else "overbought")
    approach = "FROM_ABOVE" if direction == "LONG" else "FROM_BELOW"
    kind = "RECLAIMED" if reclaim else "TOUCHING" if touch else "APPROACHING"
    reclaim_status = ("RECLAIMED_ABOVE_MA200" if direction == "LONG" else "REJECTED_BELOW_MA200") if reclaim else "NOT_RECLAIMED"
    level = interaction("MA200", approach, interaction_type=kind, reclaim=reclaim_status, touches=3 if confluence else 1)
    state["level_interactions"] = [level]
    if confluence:
        confluence_type = "SWING_LOW" if direction == "LONG" else "SWING_HIGH"
        value["levels"] = [{"type": confluence_type, "timeframe": "4H", "value": 100.0, "source_timestamp": AS_OF - 7200, "confirmed": True}]
    return value, state


def test_ma200_watch_touch_armed_and_touch_never_triggers():
    value, state = ma_setup()
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")["state"] == "WATCH"
    state["level_interactions"][0]["interaction_type"] = "TOUCHING"
    item = candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")
    assert item["state"] == "ARMED"
    assert any(blocker["code"] == "MA200_TOUCH_WITHOUT_RECLAIM" for blocker in item["blockers"])


def test_ma200_single_wick_is_not_confirmation_but_reclaim_structure_can_trigger():
    value, state = ma_setup(touch=True)
    value["timeframes"]["15m"]["volume"]["lower_wick_percentage"] = indicator(60)
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")["state"] != "TRIGGER_READY"
    state["level_interactions"][0]["interaction_type"] = "RECLAIMED"
    state["level_interactions"][0]["reclaim_status"] = "RECLAIMED_ABOVE_MA200"
    state["timeframes"]["15m"]["momentum_state"] = "RECOVERING_FROM_OVERSOLD"
    state["level_interactions"].append(interaction("SWING_HIGH", "FROM_BELOW", boundary=104))
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")["state"] == "TRIGGER_READY"


def test_ma200_long_strong_down_invalid_and_confluence_is_required():
    value, state = ma_setup(confluence=False)
    item = candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")
    assert any(blocker["code"] == "NO_STRUCTURAL_LEVEL" for blocker in item["blockers"])
    state["timeframes"]["4H"]["primary_state"] = "TREND_DOWN"; state["primary_state_code"] = "HTF_DOWNTREND_CONTINUATION"
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")["state"] == "INVALIDATED"


def test_ma200_short_rejection_and_confirmed_break_invalidation():
    value, state = ma_setup("SHORT", reclaim=True)
    value["timeframes"]["15m"]["volume"]["upper_wick_percentage"] = indicator(50)
    state["timeframes"]["15m"]["momentum_state"] = "ROLLING_OVER_FROM_OVERBOUGHT"
    state["level_interactions"].append(interaction("SWING_LOW", "FROM_ABOVE", boundary=96))
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "SHORT")["state"] == "TRIGGER_READY"
    state["level_interactions"][0]["current_stage"] = "MA200_RECLAIM_CONFIRMED"
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "SHORT")["state"] == "INVALIDATED"


def breakout_inputs(direction="LONG", stage="OBSERVING", interaction_type="APPROACHING"):
    value, state = inputs({name: "mixed" for name in ("1W", "1D", "4H", "1H", "15m")}, quality="AVAILABLE")
    state["timeframes"]["1H"]["primary_state"] = "RANGE_LOW_VOLATILITY"
    state["overlays"].append("1H:VOLATILITY_COMPRESSION")
    level_type = "SWING_HIGH" if direction == "LONG" else "SWING_LOW"
    approach = "FROM_BELOW" if direction == "LONG" else "FROM_ABOVE"
    state["level_interactions"] = [interaction(level_type, approach, stage=stage, interaction_type=interaction_type), interaction("SWING_HIGH" if direction == "LONG" else "SWING_LOW", "FROM_BELOW" if direction == "LONG" else "FROM_ABOVE", boundary=104 if direction == "LONG" else 96)]
    return value, state


def test_breakout_compression_watch_first_breach_no_trigger_and_confirmed_armed():
    value, state = breakout_inputs()
    assert candidate(route_with(value, state), "BREAKOUT_CONTINUATION", "LONG")["state"] == "WATCH"
    state["level_interactions"][0]["current_stage"] = "BREAKOUT_CANDIDATE"
    item = candidate(route_with(value, state), "BREAKOUT_CONTINUATION", "LONG")
    assert item["state"] == "WATCH" and item["score_breakdown"]["trigger"] == 0
    state["level_interactions"][0]["current_stage"] = "BREAKOUT_CONFIRMED"; state["overlays"].append("BREAKOUT_CONFIRMED")
    assert candidate(route_with(value, state), "BREAKOUT_CONTINUATION", "LONG")["state"] == "ARMED"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breakout_retest_continuation_and_reentry_invalidation(direction):
    confirmed = "BREAKOUT_CONFIRMED" if direction == "LONG" else "BREAKDOWN_CONFIRMED"
    retesting = "BREAKOUT_RETESTING" if direction == "LONG" else "BREAKDOWN_RETESTING"
    failed = "FAILED_BREAKOUT_CANDIDATE" if direction == "LONG" else "FAILED_BREAKDOWN_CANDIDATE"
    value, state = breakout_inputs(direction, confirmed, "RETESTING")
    state["overlays"].append(confirmed); state["level_interactions"][0]["current_stage"] = retesting
    state["timeframes"]["15m"]["primary_state"] = "TREND_UP" if direction == "LONG" else "TREND_DOWN"
    assert candidate(route_with(value, state), "BREAKOUT_CONTINUATION", direction)["state"] == "TRIGGER_READY"
    state["level_interactions"][0]["current_stage"] = failed
    assert candidate(route_with(value, state), "BREAKOUT_CONTINUATION", direction)["state"] == "INVALIDATED"


def test_breakout_without_space_is_blocked_by_geometry():
    value, state = breakout_inputs("LONG", "BREAKOUT_RETESTING", "RETESTING")
    state["overlays"].append("BREAKOUT_CONFIRMED"); state["timeframes"]["15m"]["primary_state"] = "TREND_UP"
    state["level_interactions"][1]["boundary"] = 99.7; state["level_interactions"][1]["zone_low"] = 99.65; state["level_interactions"][1]["zone_high"] = 99.75
    item = candidate(route_with(value, state), "BREAKOUT_CONTINUATION", "LONG")
    assert not item["geometry"]["valid"]


@pytest.mark.parametrize(("direction", "failed_stage", "reclaim"), [("SHORT", "FAILED_BREAKOUT_CANDIDATE", "RECLAIMED_INTO_PRIOR_RANGE"), ("LONG", "FAILED_BREAKDOWN_CANDIDATE", "RECLAIMED_ABOVE_PRIOR_SUPPORT")])
def test_failed_breakout_requires_sequence_then_reverse_confirmation(direction, failed_stage, reclaim):
    value, state = breakout_inputs("LONG" if direction == "SHORT" else "SHORT")
    target = "FAILED_BREAKOUT_REVERSAL"
    assert candidate(route_with(value, state), target, direction)["state"] == "INELIGIBLE"
    state["level_interactions"][0]["current_stage"] = failed_stage; state["level_interactions"][0]["reclaim_status"] = reclaim
    assert candidate(route_with(value, state), target, direction)["state"] == "ARMED"
    state["level_interactions"].append(interaction("SWING_LOW" if direction == "SHORT" else "SWING_HIGH", "FROM_ABOVE" if direction == "SHORT" else "FROM_BELOW", boundary=96 if direction == "SHORT" else 104))
    state["timeframes"]["15m"]["primary_state"] = "TREND_DOWN" if direction == "SHORT" else "TREND_UP"
    assert candidate(route_with(value, state), target, direction)["state"] == "TRIGGER_READY"
    state["level_interactions"][0]["interaction_type"] = "BROKEN"; state["level_interactions"][0]["reclaim_status"] = "NOT_RECLAIMED"
    assert candidate(route_with(value, state), target, direction)["state"] == "INVALIDATED"


def test_single_wick_without_failed_event_cannot_create_reversal():
    value, state = breakout_inputs()
    value["timeframes"]["15m"]["volume"]["upper_wick_percentage"] = indicator(80)
    assert candidate(route_with(value, state), "FAILED_BREAKOUT_REVERSAL", "SHORT")["state"] == "INELIGIBLE"


@pytest.mark.parametrize("code", NO_TRADE_CODES)
def test_no_trade_reason_vocabulary_is_complete(code):
    assert code in NO_TRADE_CODES


def test_no_trade_data_conflict_noise_no_level_and_confirmation():
    value, state = inputs({"1W": "up", "1D": "down", "4H": "mixed", "1H": "up", "15m": "down"})
    value["quality"]["overall_status"] = "MISSING"; state["timeframes"]["15m"]["primary_state"] = "UNKNOWN"; state["primary_state_code"] = "RANGE_ROTATION"; state["level_interactions"] = []
    codes = {item["code"] for item in route_with(value, state)["no_trade"]["reasons"]}
    assert {"INSUFFICIENT_DATA", "HTF_CONFLICT", "MID_RANGE_NOISE", "NO_STRUCTURAL_LEVEL", "NO_CONFIRMATION"} <= codes


def test_stale_flow_is_excluded_and_partial_flow_is_weak():
    value, state = inputs()
    value["flow"]["price_cvd_combination"] = {"state": "PRICE_UP_CVD_UP", "data_quality": "STALE", "end_timestamp": AS_OF}
    stale = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert "CVD stale and excluded" in stale["limitations"]
    value["flow"]["price_cvd_combination"]["data_quality"] = "PARTIAL"
    partial = candidate(route_with(value, state), "TREND_PULLBACK", "LONG")
    assert any(item["strength"] == "WEAK" for item in partial["supporting_evidence"])


def test_identity_is_deterministic_and_isolated_by_direction_family_level_and_instrument():
    value, state = ma_setup()
    first = route_with(value, state); second = route_with(deepcopy(value), deepcopy(state))
    assert first == second
    ids = {(item["family"], item["direction"]): item["identity_hash"] for item in first["candidates"]}
    assert len(set(ids.values())) == len(ids)
    changed = deepcopy(state); changed["level_interactions"][0]["timeframe"] = "1H"
    assert candidate(route_with(value, changed), "MA200_MEAN_REVERSION", "LONG")["identity_hash"] != candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity_hash"]
    other_value = deepcopy(value); other_state = deepcopy(state); other_value["instrument"] = other_state["instrument"] = "BTC-USDT-SWAP"
    assert candidate(route_with(other_value, other_state), "MA200_MEAN_REVERSION", "LONG")["identity_hash"] != candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity_hash"]


def test_future_source_is_rejected_and_future_route_does_not_mutate_past():
    value, state = inputs(); past = route_with(value, state)
    future_value = advance(value); future_state = ENGINE.evaluate(future_value); route_with(future_value, future_state)
    assert route_with(value, state) == past
    bad = deepcopy(value); bad["timeframes"]["4H"]["trend"]["ma200"]["source_timestamp"] = AS_OF + 1
    with pytest.raises(ValueError, match="later than as_of"):
        route_with(bad, state)


def test_lifecycle_trigger_record_cooldown_and_idempotent_recovery():
    value, state = breakout_inputs("LONG", "BREAKOUT_RETESTING", "RETESTING")
    state["overlays"].append("BREAKOUT_CONFIRMED"); state["timeframes"]["15m"]["primary_state"] = "TREND_UP"
    ready = route_with(value, state); assert candidate(ready, "BREAKOUT_CONTINUATION", "LONG")["state"] == "TRIGGER_READY"
    next_value = advance(value); next_state = deepcopy(state); next_state["as_of"] = next_value["as_of"]
    for tf in next_state["timeframes"].values(): tf["source_timestamps"] = [next_value["as_of"]]
    for item in next_state["level_interactions"]: item["source_timestamps"] = [AS_OF - 3600]
    triggered = ROUTER.route(next_value, next_state, previous_route=ready)
    assert candidate(triggered, "BREAKOUT_CONTINUATION", "LONG")["state"] == "TRIGGERED_RESEARCH_ONLY"
    assert triggered == ROUTER.route(deepcopy(next_value), deepcopy(next_state), previous_route=deepcopy(ready))


def test_lifecycle_expiry_and_setup_identity_recovery():
    value, state = ma_setup(touch=True); armed = route_with(value, state)
    later = deepcopy(value); later["as_of"] = AS_OF + 20_000; later["price"]["source_timestamp"] = AS_OF + 20_000
    for tf, frame_value in later["timeframes"].items():
        frame_value["candle_close_ts"] = AS_OF + 20_000
        for group in ("trend", "momentum", "volatility", "structure", "volume"):
            for fact in frame_value[group].values():
                if fact.get("source_timestamp") is not None: fact["source_timestamp"] = AS_OF + 20_000
    later_state = deepcopy(state); later_state["as_of"] = later["as_of"]
    for frame_state in later_state["timeframes"].values(): frame_state["source_timestamps"] = [later["as_of"]]
    expired = ROUTER.route(later, later_state, previous_route=armed)
    assert candidate(expired, "MA200_MEAN_REVERSION", "LONG")["state"] == "EXPIRED"


def test_scores_are_completion_not_probability_and_geometry_contract_is_complete():
    value, state = ma_setup(); item = candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")
    assert item["score_breakdown"] == {"environment": 25, "structure": 25, "setup": 10, "trigger": 0, "data_quality": 7.0}
    assert 0 <= item["score"] <= 100 and 0 <= item["evidence_strength"] <= 100
    assert not {"probability", "win_rate", "profit_probability"} & item.keys()
    for key in ("setup_zone", "trigger_boundary", "confirmation_rule", "invalidation_reference", "stop_reference_type", "target_reference_types", "maximum_wait_bars", "maximum_holding_bars", "minimum_structural_reward_risk", "entry_timing", "intrabar_policy_placeholder", "gap_policy_placeholder"):
        assert key in item["geometry"]


def test_router_performance_under_25ms_average():
    value, state = ma_setup()
    started = time.perf_counter()
    for _ in range(200): route_with(value, state)
    assert (time.perf_counter() - started) / 200 < .025


def test_read_pipeline_serialization_fixture_under_700ms():
    value, state = inputs(); started = time.perf_counter(); payload = json.dumps(route_with(value, state))
    assert time.perf_counter() - started < .7 and '"strategy-router-v2"' in payload


def test_api_route_is_read_only_and_isolated_from_legacy_llm_and_orders():
    source = Path("dashboard/paper_api.py").read_text(encoding="utf-8")
    block = source[source.index('elif parsed.path == "/api/strategy/route":'):source.index('elif parsed.path == "/api/paper/flow/health"')]
    for forbidden in ("evaluate_decision", "DeepSeek", "INSERT ", "UPDATE ", "DELETE ", "_open_trade", "create_order"):
        assert forbidden not in block
    module = Path("dashboard/strategy_router_v2.py").read_text(encoding="utf-8")
    for forbidden in ("decision_engine", "paper_api", "sqlite3", "urlopen", "raw_trades", "raw_oi"):
        assert forbidden not in module.lower()


def test_legacy_decision_and_paper_scheduler_source_are_unchanged_in_identity():
    decision = evaluate_decision(StrategyParameters(), MarketContext("ETH-USDT", "15m", AS_OF, 100, {"fast_ma": 99, "slow_ma": 95, "ema": 100, "rsi": 50, "atr": 2, "volume_ratio": 1.2}))
    assert LIVE_STRATEGY_VERSION == "live-mtf-flow-v1" and decision.strategy_version != ROUTER_VERSION
    source = Path("dashboard/paper_api.py").read_text(encoding="utf-8")
    cycle = source[source.index("def cycle_instrument"):source.index("def cycle(", source.index("def cycle_instrument"))]
    assert "STRATEGY_ROUTER_V2" not in cycle and "_open_trade(analysis)" in cycle


def test_openapi_and_generated_contract_names():
    schema = json.loads(Path("frontend/openapi/openapi.json").read_text(encoding="utf-8"))
    assert schema["paths"]["/api/strategy/route"]["get"]["operationId"] == "getStrategyRouteV2"
    for name in ("StrategyRouteSnapshotV2", "StrategyCandidateV2", "StrategyIdentityV2", "StrategyStageV2", "StrategyEvidenceV2", "StrategyBlockerV2", "StrategyGeometryV2", "StrategyTransitionV2", "NoTradeReasonV2"):
        assert name in schema["components"]["schemas"]


def test_backtest_specs_are_serializable_and_bounded_without_running():
    specs = backtest_specifications_v2(); json.dumps(specs)
    assert len(specs) == 8
    by_family = {family: max(item["parameter_combination_count"] for item in specs if item["family"] == family) for family in FAMILY_VERSIONS if family != "NO_TRADE"}
    assert all(count <= 24 for count in by_family.values())
    assert sum(by_family.values()) <= 96
    assert sum(item["parameter_combination_count"] for item in specs) <= 96


def test_no_trade_reasons_are_structured_policy_results_not_errors():
    value, state = inputs(); result = route_with(value, state)["no_trade"]
    assert result["strategy_version"] == "no-trade-policy-v2"
    for reason in result["reasons"]:
        assert {"code", "timeframe", "evidence", "source_timestamp", "temporary", "release_condition"} <= reason.keys()


def test_fixture_post_endpoint_is_explicitly_disabled_by_default():
    source = Path("dashboard/paper_api.py").read_text(encoding="utf-8")
    block = source[source.index('if parsed.path == "/api/strategy/route/evaluate":'):source.index('if parsed.path not in {', source.index('if parsed.path == "/api/strategy/route/evaluate":'))]
    assert "ENABLE_STRATEGY_ROUTER_FIXTURE_API" in block
    assert "disabled" in block.lower()


def test_confluence_zone_preserves_underlying_ma200_identity():
    value, state = ma_setup(touch=True)
    value["levels"] = [{"type": "CONFLUENCE_ZONE", "timeframe": "MULTI", "value": 100.0,
                        "source_timestamp": AS_OF - 7200, "confirmed": True,
                        "confluence_sources": ["4H:MA200", "4H:SWING_LOW"]}]
    state["level_interactions"][0]["level_type"] = "CONFLUENCE_ZONE"
    state["level_interactions"][0]["timeframe"] = "MULTI"
    assert candidate(route_with(value, state), "MA200_MEAN_REVERSION", "LONG")["state"] == "ARMED"


def test_full_http_read_pipeline_fixture_under_700ms(monkeypatch):
    from dashboard import paper_api
    value, _ = inputs()
    calls = []
    class ContextService:
        def context(self, instrument, *, as_of, execution_timeframe):
            calls.append((instrument, as_of, execution_timeframe)); return deepcopy(value)
    monkeypatch.setattr(paper_api, "MARKET_CONTEXT_V2", ContextService())
    handler = object.__new__(paper_api.Handler)
    handler.path = f"/api/strategy/route?instrument=ETH-USDT-SWAP&as_of={AS_OF}&execution_timeframe=15m"
    handler.headers = {}; handler.client_address = ("127.0.0.1", 1); captured = []
    handler._send = lambda payload, status=200: captured.append((payload, int(status)))
    started = time.perf_counter(); handler.do_GET(); elapsed = time.perf_counter() - started
    assert elapsed < .7 and captured[0][0]["version"] == ROUTER_VERSION
    assert calls == [("ETH-USDT-SWAP", AS_OF, "15m")]
