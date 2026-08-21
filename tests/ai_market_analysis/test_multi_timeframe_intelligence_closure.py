from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.deterministic_intelligence import build_timeframe_intelligence
from dashboard.ai_market_analysis.provider_claim_pack import build_provider_claim_pack
from dashboard.ai_market_analysis.report_fact_registry import current_level_relevance
from dashboard.ai_market_analysis.report_semantic_audit import audit_semantics
from dashboard.ai_market_analysis.structure_timeline import build_timeline
from dashboard.ai_market_analysis.swing_structure import confirmed_swings
from dashboard.ai_market_analysis.timeframe_facts import build_timeframe_facts

from .helpers import BASE, breakout_path, golden_datasets


def _intelligence(rows):
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE + len(rows) * 900)
    swings = confirmed_swings(facts["confirmed_bars"], "15m", atr=facts["atr14"]["value"])
    timeline = build_timeline(facts, swings)
    return build_timeframe_intelligence(facts, timeline, swings)


def test_impulse_followed_by_shallow_pullback_is_tactical_observation():
    rows = breakout_path(tail=[1895, 1910, 1930, 1945, 1938, 1935, 1934])
    value = _intelligence(rows)
    assert value["tactical"]["impulse"]["state"] == "IMPULSE_UP"
    assert value["tactical"]["pullback"]["classification"] == "SHALLOW"
    assert value["state"] == "SHALLOW_PULLBACK"


def test_shallow_drawdown_and_momentum_reset_is_price_resilient():
    rows = [{"open_time": i * 900, "close_time": (i + 1) * 900, "open": 109, "high": 110,
             "low": 108, "close": 109, "volume": 100} for i in range(30)]
    facts = {"timeframe": "15m", "confirmed_bars": rows, "confirmed_close": 109,
             "atr14": {"value": 2}, "rsi14": {"value": 50},
             "recent_rsi_values": [75, 70, 65, 60, 55, 50], "volume_ratio": {"value": 1},
             "price_to_ma_distance": {}, "moving_averages": {"ema20": {"value": 108}}}
    value = build_timeframe_intelligence(facts, {}, [])
    assert value["momentum"]["state"] == "PRICE_RESILIENT_MOMENTUM_RESET"


def test_higher_timeframe_large_atr_distance_is_highly_extended():
    rows = [{"open_time": i, "close_time": i + 1, "open": 109, "high": 111,
             "low": 108, "close": 110, "volume": 100} for i in range(30)]
    for timeframe in ("4H", "1D"):
        facts = {"timeframe": timeframe, "confirmed_bars": rows, "confirmed_close": 110,
                 "atr14": {"value": 2}, "rsi14": {"value": 60}, "recent_rsi_values": [60] * 6,
                 "volume_ratio": {"value": 1}, "price_to_ma_distance": {"ema20": {"value": 10}},
                 "moving_averages": {"ema20": {"value": 100}}}
        assert build_timeframe_intelligence(facts, {}, [])["extension_state"] == "HIGHLY_EXTENDED"


def test_high_volume_rejection_is_supply_evidence_but_not_confirmed_top():
    rows = [{"open_time": i, "close_time": i + 1, "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": 100} for i in range(39)]
    rows.append({"open_time": 39, "close_time": 40, "open": 100, "high": 110,
                 "low": 99, "close": 100.5, "volume": 500})
    facts = {"timeframe": "15m", "confirmed_bars": rows, "confirmed_close": 100.5,
             "atr14": {"value": 2}, "rsi14": {"value": 55}, "recent_rsi_values": [55] * 6,
             "volume_ratio": {"value": 5}, "price_to_ma_distance": {},
             "moving_averages": {"ema20": {"value": 100}}}
    tactical = build_timeframe_intelligence(facts, {}, [])["tactical"]
    assert tactical["supply_evidence"] is True
    assert tactical["top_confirmed"] is False


def test_unavailable_flow_does_not_remove_five_timeframe_price_intelligence():
    datasets, decision = golden_datasets()
    context = build_market_analysis_context(datasets, "ETH-USDT-SWAP", decision, "QUICK", orderflow={})
    assert len(context["timeframe_structures"]) == 5
    assert all(item["deterministic_intelligence"]["state"] for item in context["timeframe_structures"])
    assert context["order_flow_phases"][-1]["metrics"]["quality"]["flow_coverage"]["state"] == "FLOW_UNAVAILABLE"
    assert len(context["scenario_tree"]["scenarios"]) == 3


def test_price_oi_proxy_remains_available_without_claiming_cvd_flow_complete():
    relation = {"fact_id": "PRICE_OI_RELATION", "category": "ORDER_FLOW", "quality": "VALID",
                "value": {"evidence_kind": "PRICE_OI", "state": "PRICE_UP_OI_DOWN", "quality": "VALID", "proxy": True}}
    unavailable = {"fact_id": "FLOW_PHASE_01", "category": "ORDER_FLOW", "quality": "UNAVAILABLE",
                   "value": {"phase": "CURRENT", "flow_quality": "FLOW_UNAVAILABLE", "cvd_status": "UNAVAILABLE"}}
    pack = build_provider_claim_pack({"facts": [relation, unavailable], "numeric_registry": []}, "QUICK")
    assert "PRICE_OI_RELATION" in pack["fact_ids_by_category"]["ORDER_FLOW"]
    assert pack["evidence_status"]["flow_available"] is False


def test_local_and_round_levels_can_be_current_while_far_level_is_reference_only():
    base = {"decision_time": "2026-08-21T00:00:00Z", "timeframe_structures": [{
        "timeframe": "15m", "last_confirmed_close": {"value": 2390}, "atr": {"value": 18}}]}
    local = {"representative_price": 2400, "role": "RESISTANCE", "state": "ACTIVE", "timeframes": ["15m"]}
    round_level = {"representative_price": 2400, "role": "RESISTANCE", "state": "ACTIVE", "timeframes": ["MULTI"]}
    far = {"representative_price": 3000, "role": "RESISTANCE", "state": "ACTIVE", "timeframes": ["1W"]}
    assert current_level_relevance(base, local)["current_eligible"]
    assert current_level_relevance(base, round_level)["current_eligible"]
    assert current_level_relevance(base, far)["reference_tier"] == "LONG_TERM_REFERENCE"


def test_unsupported_liquidation_causality_fails_semantic_audit():
    facts = [{"fact_id": "PRICE_OI_RELATION", "category": "ORDER_FLOW", "quality": "VALID",
              "value": {"state": "PRICE_UP_OI_DOWN", "proxy": True}}]
    claim = {"claim_id": "c", "claim_type": "OPEN_INTEREST",
             "original_text": "\u4ef7\u683c\u4e0a\u6da8\u4f34\u968f OI \u4e0b\u964d\uff0c\u786e\u5b9a\u662f\u7a7a\u5934\u7206\u4ed3\u3002",
             "modality": "FACT", "fact_refs": ["PRICE_OI_RELATION"]}
    assert "UNSUPPORTED_CAUSALITY" in audit_semantics([claim], facts)["failure_codes"]
