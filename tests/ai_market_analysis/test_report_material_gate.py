from copy import deepcopy

from dashboard.ai_market_analysis.report_material_gate import material_fingerprint


def base():
    return {
        "instrument": "ETH-USDT-SWAP", "decision_time": "2026-08-22T12:00:00Z",
        "latest_confirmed_market_time": "2026-08-22T12:00:00Z",
        "timeframe_structures": [{
            "timeframe": "4H", "trend_classification": "BULL",
            "structure_classification": "TREND_UP", "confidence": "HIGH",
            "last_confirmed_close": {"value": 64120, "timestamp": 1},
            "deterministic_intelligence": {
                "state": "IMPULSE_UP", "extension_state": "NORMAL",
                "current_price": 64120,
                "momentum": {"state": "MOMENTUM_STABLE", "rsi": 55},
                "volume": {"state": "VOLUME_NORMAL", "current_ratio": 1.01},
            },
        }],
        "timeframe_coverage": {"4H": {"quality": "COMPLETE", "actual_bars": 512}},
        "multi_timeframe_summary": {"dominant_timeframe": "4H", "alignment": "MIXED",
                                    "conflicts": [], "timeframe_states": {"4H": "IMPULSE_UP"},
                                    "extension_states": {"4H": "NORMAL"}},
        "market_timeline": {"current_phase": "IMPULSE", "breakout_direction": "UP"},
        "key_levels": [{"level_id": "one", "representative_price": 64000,
                        "observed_at": 1, "role": "SUPPORT", "state": "ACTIVE",
                        "strength": "STRONG", "timeframes": ["4H"],
                        "confluences": ["CONFIRMED_SWING_LOW"]}],
        "scenario_tree": {"status": "AVAILABLE", "scenarios": [
            {"scenario_id": "s1", "type": "BULLISH_CONTINUATION",
             "direction": "UP", "likelihood": "MEDIUM"}]},
        "order_flow_phases": [],
        "data_quality": {"overall": "VALID", "gaps": [], "stale_sources": [],
                         "missing_sources": []},
    }


def test_clock_price_counts_and_content_ids_are_not_material():
    first = base(); second = deepcopy(first)
    second["decision_time"] = "2026-08-22T16:00:00Z"
    second["latest_confirmed_market_time"] = "2026-08-22T16:00:00Z"
    second["timeframe_structures"][0]["last_confirmed_close"] = {"value": 64180, "timestamp": 2}
    second["timeframe_structures"][0]["deterministic_intelligence"]["current_price"] = 64180
    second["timeframe_structures"][0]["deterministic_intelligence"]["momentum"]["rsi"] = 55.2
    second["timeframe_coverage"]["4H"]["actual_bars"] = 513
    second["key_levels"][0].update({"level_id": "two", "representative_price": 64020, "observed_at": 2})
    second["scenario_tree"]["scenarios"][0]["scenario_id"] = "s2"
    assert material_fingerprint(first)["fingerprint"] == material_fingerprint(second)["fingerprint"]


def test_structure_level_quality_and_flow_transitions_are_material():
    original = material_fingerprint(base())["fingerprint"]
    for mutate in (
        lambda value: value["timeframe_structures"][0].update(structure_classification="BREAKOUT_UP"),
        lambda value: value["key_levels"][0].update(state="BROKEN"),
        lambda value: value["data_quality"].update(overall="PARTIAL", missing_sources=["cvd"]),
        lambda value: value.update(order_flow_phases=[{
            "phase": "CURRENT", "attribution": {"primary": "NEW_LONGS_DOMINANT", "confidence": "HIGH"},
            "metrics": {"quadrant": "PRICE_UP_OI_UP", "volume_regime": "EXPANDING",
                        "quality": {"overall": "VALID", "flow_coverage": {"state": "FLOW_COMPLETE"}}},
        }]),
    ):
        changed = base(); mutate(changed)
        assert material_fingerprint(changed)["fingerprint"] != original
