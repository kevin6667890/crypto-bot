"""Bounded deterministic facts supplied to report providers."""
from __future__ import annotations

from typing import Any
from .canonical import stable_hash
from .versions import AI_REPORT_FACT_REGISTRY_VERSION

MAX_FACTS = 160
MAX_KEY_LEVELS = 12
TIMEFRAME_IDS = {"15m": "TF15", "1H": "TF1H", "4H": "TF4H", "1D": "TF1D", "1W": "TF1W"}


def _fact(fact_id: str, category: str, label: str, value: Any, pointer: str, *, unit: str | None = None,
          timestamp: str | int | None = None, source: str = "AI3_CONTEXT", quality: str = "VALID",
          claim_scope: str = "MARKET", priority: int = 50) -> dict[str, Any]:
    display = value
    if isinstance(value, float): display = f"{value:.4f}".rstrip("0").rstrip(".")
    return {"fact_id": fact_id, "category": category, "label": label, "value": value, "unit": unit,
        "timestamp": timestamp, "source": source, "quality": quality, "context_pointer": pointer,
        "display_value": str(display), "allowed_rounding": .51,
        "claim_scope": claim_scope, "provenance": AI_REPORT_FACT_REGISTRY_VERSION,
        "priority": priority}


def build_fact_registry(enriched: dict[str, Any]) -> dict[str, Any]:
    base, facts = enriched["base_context"], []
    decision = enriched["decision_time"]
    quality = base.get("data_quality", {})
    facts.append(_fact("DATA_QUALITY", "WARNING", "数据质量", quality.get("overall", "UNKNOWN"), "/data_quality", timestamp=decision, priority=100))
    for index, warning in enumerate(quality.get("gaps", []) + quality.get("missing_sources", []) + quality.get("watermark_mismatches", [])):
        facts.append(_fact(f"DATA_WARNING_{index+1:02d}", "WARNING", "数据限制", warning, "/data_quality", timestamp=decision, priority=100))
    for index, frame in enumerate(base.get("timeframe_structures", [])):
        tf = frame.get("timeframe"); prefix = TIMEFRAME_IDS.get(tf, f"TF{index}")
        close = frame.get("last_confirmed_close") or {}
        facts.append(_fact(f"{prefix}_SUMMARY", "TIMEFRAME", f"{tf}结构摘要", {"close":close.get("value"),"moving_average_ordering":frame.get("moving_average_ordering"),"swing_structure":frame.get("swing_structure"),"price_position":frame.get("price_position")}, f"/timeframe_structures/{index}", unit=close.get("unit"), timestamp=close.get("timestamp"), quality=close.get("quality", "UNKNOWN"), priority=85))
        for name in ("ma20", "ma60", "ma200"):
            item = (frame.get("moving_averages") or {}).get(name) or {}
            if item.get("value") is not None:
                facts.append(_fact(f"{prefix}_{name.upper()}", "TIMEFRAME", f"{tf} {name.upper()}", item["value"], f"/timeframe_structures/{index}/moving_averages/{name}", unit=item.get("unit"), timestamp=item.get("timestamp"), quality=item.get("quality", "UNKNOWN"), priority=45))
    timeline = base.get("market_timeline", {})
    for fid, key, label in (("STRUCT_RANGE_LOW","range_low","压缩区下沿"),("STRUCT_BREAKOUT_BOUNDARY","range_high","突破边界"),("STRUCT_IMPULSE_HIGH","impulse_high","冲高高点"),("STRUCT_IMPULSE_LOW","impulse_low","脉冲低点")):
        item = timeline.get(key) or {}
        if item.get("value") is not None: facts.append(_fact(fid, "TIMELINE", label, item["value"], f"/market_timeline/{key}", unit=item.get("unit"), timestamp=item.get("timestamp"), priority=96))
    facts.append(_fact("TIMELINE_CURRENT_PHASE", "TIMELINE", "当前阶段", timeline.get("current_phase"), "/market_timeline/current_phase", timestamp=decision, priority=100))
    for index, event in enumerate(base.get("structure_events", [])[-1:]):
        facts.append(_fact(f"EVENT_{index+1:02d}", "TIMELINE", event.get("event_type", "事件"), {k:event.get(k) for k in ("event_type","timeframe","direction","confirmation_status","invalidation")}, f"/structure_events/{len(base.get('structure_events', []))-1+index}", timestamp=event.get("end"), priority=90-index))
    phases = base.get("order_flow_phases", [])
    for index, phase in enumerate(phases[-4:]):
        metrics = phase.get("metrics", {}); cvd = metrics.get("cvd", {}); oi = metrics.get("oi", {})
        attribution=phase.get("attribution") or {}
        value = {"phase": phase.get("phase"), "attribution": {k:attribution.get(k) for k in ("primary","confidence")}, "price_change": metrics.get("price_change"),
                 "price_change_pct": metrics.get("price_change_pct"), "volume": metrics.get("volume"), "volume_regime": metrics.get("volume_regime"),
                 "cvd_delta": cvd.get("signed_delta"), "cvd_status": cvd.get("status"), "oi_change": oi.get("change"), "oi_change_pct": oi.get("change_pct"), "oi_status": oi.get("status"), "quality": phase.get("quality")}
        facts.append(_fact(f"FLOW_PHASE_{index+1:02d}", "ORDER_FLOW", f"{phase.get('phase')}订单流", value,
                           f"/order_flow_phases/{len(phases)-min(4,len(phases))+index}", timestamp=phase.get("end"), quality=phase.get("quality", "UNKNOWN"), priority=95-index))
    for index, transition in enumerate(base.get("phase_transitions", [])[-1:]):
        facts.append(_fact(f"FLOW_TRANSITION_{index+1:02d}", "ORDER_FLOW", "订单流转变", {k:transition.get(k) for k in ("price_change","oi_behavior","cvd_behavior","volume_change","interpretation","confidence","counterevidence")},
            f"/phase_transitions/{len(base.get('phase_transitions', []))-1+index}", timestamp=decision, priority=83-index))
    selected_levels=[(i,level) for i,level in enumerate(base.get("key_levels", [])[:MAX_KEY_LEVELS]) if i in {0,1,2,3,5,7}]
    for display_index, (index, level) in enumerate(selected_levels):
        facts.append(_fact(f"LEVEL_{display_index+1:02d}", "LEVEL", f"{level.get('role')} {level.get('state')}",
            {k:level.get(k) for k in ("level_id","zone_low","zone_high","role","state","strength")},
            f"/key_levels/{index}", unit="USDT", timestamp=level.get("last_tested"), priority=94-display_index))
    for index, scenario in enumerate(base.get("scenario_tree", {}).get("scenarios", [])):
        trigger=scenario.get("trigger") or {}; invalidation=scenario.get("invalidation") or {}
        if not isinstance(trigger,dict):trigger={"rule":str(trigger),"level_ids":scenario.get("trigger_level_ids",[])}
        if not isinstance(invalidation,dict):invalidation={"rule":str(invalidation),"level_id":None,"timeframe":None}
        facts.append(_fact(f"SCENARIO_{index+1:02d}", "SCENARIO", scenario.get("type", "情景"), {"scenario_id":scenario.get("scenario_id"),"type":scenario.get("type"),"direction":scenario.get("direction"),"likelihood":scenario.get("likelihood"),"trigger":{"rule":trigger.get("rule"),"level_ids":trigger.get("level_ids")},"invalidation":{"rule":invalidation.get("rule"),"level_id":invalidation.get("level_id"),"timeframe":invalidation.get("timeframe")},"contradicting_evidence":scenario.get("contradicting_evidence")},
            f"/scenario_tree/scenarios/{index}", timestamp=decision, priority=100))
    pos = enriched["position_context"]
    facts.append(_fact("POSITION_SOURCE", "POSITION", "持仓来源", pos.get("source"), "/position_context/source", timestamp=decision, claim_scope="POSITION", priority=100))
    for fid,key,label in (("POSITION_SIDE","side","方向"),("POSITION_AVERAGE_COST","average_cost","平均成本"),("POSITION_ORIGINAL_QUANTITY","original_quantity","原始数量"),("POSITION_REMAINING_QUANTITY","remaining_quantity","剩余数量"),("POSITION_ORIGINAL_STOP","original_stop","原始止损"),("POSITION_PLAN_COMPLETION","plan_completion_ratio","计划完成度"),("POSITION_WARNINGS","discipline_warnings","纪律警告")):
        if pos.get(key) is not None: facts.append(_fact(fid,"POSITION",label,pos[key],f"/position_context/{key}",unit="USDT" if "COST" in fid or "STOP" in fid else None,timestamp=decision,claim_scope="POSITION",priority=96))
    macro = enriched["macro_context"]
    for index, item in enumerate(macro.get("items", [])):
        facts.append(_fact(f"MACRO_{index+1:02d}", "MACRO", item["title"], {"evidence_id":item["evidence_id"],"category":item["category"],"factual_summary":item["factual_summary"],"publisher":item["publisher"],"published_at":item["published_at"]}, f"/macro_context/items/{index}", timestamp=item["published_at"], source=item["source_type"], claim_scope="MACRO", priority=100))
    if not macro.get("items"):
        facts.append(_fact("MACRO_UNAVAILABLE", "WARNING", "宏观证据", "本次未加入已验证宏观证据。", "/macro_context/warnings", timestamp=decision, priority=100))
    for index, claim in enumerate(base.get("unsupported_claims", [])):
        facts.append(_fact(f"UNSUPPORTED_{index+1:02d}", "WARNING", "禁止断言", claim, f"/unsupported_claims/{index}", timestamp=decision, priority=10))
    facts = facts[:MAX_FACTS]
    numeric = []
    def collect(value: Any, fact_id: str, unit: str | None, key: str = ""):
        if isinstance(value, bool): return
        if isinstance(value, (int,float)):
            if value > 100_000_000 or any(x in key.lower() for x in ("count","timestamp","duration","bucket")): return
            numeric.append({"canonical_value": value, "unit": unit, "exact_display": str(value),
                            "allowed_decimal_places": 4, "allow_percent": unit in {"percent","ratio"},
                            "allow_range": unit == "USDT", "absolute_tolerance": .51, "source_fact_id": fact_id})
        elif isinstance(value, dict):
            for k,v in value.items(): collect(v, fact_id, unit, k)
        elif isinstance(value, list):
            for v in value: collect(v, fact_id, unit, key)
    for fact in facts: collect(fact["value"], fact["fact_id"], fact["unit"])
    # Stable de-duplication.
    unique = {}
    for item in numeric:
        unique.setdefault((str(item["canonical_value"]), item["unit"]),item)
    numeric = [unique[k] for k in sorted(unique)]
    core = {"version": AI_REPORT_FACT_REGISTRY_VERSION, "context_id": enriched["enriched_context_id"],
            "instrument": enriched["instrument"], "decision_time": decision, "facts": facts,
            "numeric_registry": numeric, "allowed_directional_biases": ["BULLISH","NEUTRAL","BEARISH"],
            "max_confidence": "MEDIUM" if quality.get("overall") == "VALID" else "LOW",
            "allowed_market_phases": sorted({str(timeline.get("current_phase") or "UNKNOWN"), "MIXED"})}
    return {**core, "registry_hash": stable_hash(core)}
