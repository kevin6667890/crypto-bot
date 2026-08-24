"""Bounded deterministic facts supplied to report providers."""
from __future__ import annotations

from typing import Any
from datetime import datetime
from .canonical import stable_hash
from .versions import AI_REPORT_FACT_REGISTRY_VERSION,AI_REPORT_NUMERIC_REGISTRY_VERSION
from .report_numeric_semantics import numeric_semantics
from .intelligence_quality import classify_evidence_quality

MAX_FACTS = 160
MAX_KEY_LEVELS = 12
TIMEFRAME_IDS = {"15m": "TF15", "1H": "TF1H", "4H": "TF4H", "1D": "TF1D", "1W": "TF1W"}
TF_RANK={"MULTI":0,"15m":1,"1H":2,"4H":3,"1D":4,"1W":5}
CURRENT_LEVEL_TIMEFRAMES={"15m","1H","4H","1D","MULTI"}

def current_level_relevance(base:dict[str,Any], level:dict[str,Any])->dict[str,Any]:
    """Classify a registry level for a *current* surface without deleting it.

    The registry remains the full evidence record.  This policy only prevents a
    valid but remote weekly/global level from being presented as an immediate
    support or resistance.  The distance limit is volatility-normalized and
    therefore has no instrument-specific price constants.
    """
    frame=next((item for item in base.get("timeframe_structures",[]) if item.get("timeframe")=="15m"),{})
    current=_number(frame.get("last_confirmed_close")) or 0.0
    atr=_number(frame.get("atr")) or current*.005
    price=_number(level.get("representative_price")) or 0.0
    distance_pct=abs(price-current)/current if current else None
    # At least 5%, widening deterministically with the current 15m ATR.
    # The upper bound prevents an anomalous ATR from turning a global reference
    # into a tactical level.
    max_distance_pct=min(.12,max(.05,6*atr/current)) if current else 0.0
    decision=_timestamp(base.get("decision_time"))
    valid_until=_timestamp(level.get("valid_until"))
    fresh=valid_until is None or decision is None or valid_until>=decision
    state=str(level.get("state") or "")
    role=str(level.get("role") or "")
    timeframes=set(level.get("timeframes") or [])
    current_timeframe=bool(timeframes&CURRENT_LEVEL_TIMEFRAMES)
    side_ok=(role=="SUPPORT" and price<=current) or (role=="RESISTANCE" and price>=current) or role=="PIVOT"
    current_eligible=(state in {"ACTIVE","FLIPPED"} and fresh and current_timeframe and side_ok
                      and distance_pct is not None and distance_pct<=max_distance_pct)
    return {"current_price":current,"distance_pct":distance_pct,"max_distance_pct":max_distance_pct,
            "fresh":fresh,"current_eligible":current_eligible,
            "reference_tier":"CURRENT" if current_eligible else "LONG_TERM_REFERENCE"}

def _number(item:Any)->float|None:
    if isinstance(item,dict):item=item.get("value")
    return float(item) if isinstance(item,(int,float)) and not isinstance(item,bool) else None

def _timestamp(value:Any)->float|None:
    if value is None:return None
    if isinstance(value,(int,float)):return float(value)
    try:return datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()
    except ValueError:return None

def select_relevant_levels(base:dict[str,Any],limit:int=MAX_KEY_LEVELS)->list[dict[str,Any]]:
    decision=datetime.fromisoformat(str(base.get("decision_time")).replace("Z","+00:00")).timestamp() if base.get("decision_time") else float("inf")
    levels={str(x.get("level_id")):x for x in base.get("key_levels",[]) if x.get("level_id") and x.get("state") in {"ACTIVE","FLIPPED"} and (_timestamp(x.get("first_detected")) is None or _timestamp(x.get("first_detected"))<=decision) and (_timestamp(x.get("valid_until")) is None or _timestamp(x.get("valid_until"))>=decision)};chosen=[]
    # Claim-pack LEVEL facts are tactical/current only.  The complete registry
    # stays in ``base.key_levels`` for Research and replay, but a distant weekly
    # level cannot be promoted merely because it is strong or scenario-linked.
    levels={key:value for key,value in levels.items() if current_level_relevance(base,value)["current_eligible"]}
    scenarios=base.get("scenario_tree",{}).get("scenarios",[])
    def add(ids):
        for level_id in sorted({str(x) for x in ids if x in levels}):
            if level_id not in chosen and len(chosen)<limit:chosen.append(level_id)
    add(x for s in scenarios for x in (s.get("trigger") or {}).get("level_ids",[]))
    add(x for s in scenarios for x in s.get("target_level_ids",[]))
    add((s.get("invalidation") or {}).get("level_id") for s in scenarios)
    def sources(level):return set(level.get("confluences",[]))|{str(c.get("source") or c.get("source_family")) for c in level.get("source_candidates",[])}
    add(k for k,v in levels.items() if "BREAKOUT_BOUNDARY" in sources(v))
    add(k for k,v in levels.items() if v.get("state")=="FLIPPED" or "RETEST" in sources(v))
    add(k for k,v in levels.items() if sources(v)&{"IMPULSE_HIGH","IMPULSE_LOW"})
    current=next((_number(f.get("last_confirmed_close")) for f in base.get("timeframe_structures",[]) if f.get("timeframe")=="15m"),None) or 0.0
    for role in ("SUPPORT","RESISTANCE"):
        eligible=[v for v in levels.values() if v.get("role")==role and v.get("state") not in {"INVALIDATED","BROKEN"}]
        if eligible:add([min(eligible,key=lambda v:(abs(float(v.get("representative_price",0))-current),str(v["level_id"]))) ["level_id"]])
    strength={"MAJOR":3,"STRONG":2,"MODERATE":1,"WEAK":0};state={"FLIPPED":4,"ACTIVE":3,"UNCONFIRMED":1,"BROKEN":0,"INVALIDATED":-1}
    rest=sorted((v for k,v in levels.items() if k not in chosen),key=lambda v:(-state.get(v.get("state"),0),-strength.get(v.get("strength"),0),-max((TF_RANK.get(x,0) for x in v.get("timeframes",[])),default=0),abs(float(v.get("representative_price",0))-current),str(v["level_id"])))
    singleton_families={next(iter({c.get("source_family") for c in levels[k].get("source_candidates",[]) if c.get("source_family")})) for k in chosen if len({c.get("source_family") for c in levels[k].get("source_candidates",[]) if c.get("source_family")})==1}
    for value in rest:
        families={c.get("source_family") for c in value.get("source_candidates",[]) if c.get("source_family")}
        if len(families)==1 and next(iter(families)) in singleton_families:continue
        add([value["level_id"]])
        if len(families)==1:singleton_families.update(families)
    return [levels[k] for k in chosen]

def _level_value(level:dict[str,Any])->dict[str,Any]:
    candidates=level.get("source_candidates",[]);timeframes=sorted(set(level.get("timeframes",[])),key=lambda x:(-TF_RANK.get(x,0),x))
    dynamic=any(bool(c.get("dynamic")) for c in candidates);valid=[c.get("valid_until") for c in candidates if c.get("valid_until") is not None];slopes=[c.get("slope") for c in candidates if c.get("slope") is not None]
    return {"level_id":level.get("level_id"),"representative_price":level.get("representative_price"),"zone_low":level.get("zone_low"),"zone_high":level.get("zone_high"),"role":level.get("role"),"state":level.get("state"),"strength":level.get("strength"),"timeframes":timeframes,"primary_timeframe":timeframes[0] if timeframes else None,"dynamic":dynamic,"valid_until":max(valid) if valid else None,"slope":{"values":slopes,"semantic":"MOVING" if dynamic else "STATIC"},"first_detected":level.get("first_detected"),"observed_at":level.get("observed_at"),"source_fact":level.get("source_fact"),"last_tested":level.get("last_tested"),"broken_at":level.get("broken_at"),"flipped_at":level.get("flipped_at"),"invalidation":level.get("invalidation"),"source_types":sorted({str(c.get("source")) for c in candidates if c.get("source")}),"source_families":sorted({str(c.get("source_family")) for c in candidates if c.get("source_family")}),"evidence_paths":sorted(set(level.get("evidence_paths",[]))|{p for c in candidates for p in c.get("evidence_paths",[])}),"quality":level.get("quality")}


def _fact(fact_id: str, category: str, label: str, value: Any, pointer: str, *, unit: str | None = None,
          timestamp: str | int | None = None, source: str = "AI3_CONTEXT", quality: str = "VALID",
          claim_scope: str = "MARKET", priority: int = 50) -> dict[str, Any]:
    display = label if isinstance(value,(dict,list)) else value
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
    dimensions = classify_evidence_quality(base, enriched.get("macro_context"))
    canonical = base.get("canonical_market_snapshot")
    if canonical:
        facts.append(_fact(
            "CANONICAL_MARKET_SNAPSHOT", "WARNING", "Canonical market evidence",
            {key: canonical.get(key) for key in (
                "snapshot_identity", "version", "instrument", "as_of",
                "causal_cutoff", "quality", "fact_count",
            )},
            "/canonical_market_snapshot", timestamp=decision,
            source="CanonicalMarketSnapshot", priority=100,
        ))
    # Compatibility identifier; its value is CORE quality, never the worst
    # optional/enhanced source.
    facts.append(_fact("DATA_QUALITY", "WARNING", "核心市场数据", dimensions.get("core_quality", "UNKNOWN"), "/evidence_quality/core_quality", timestamp=decision, priority=100))
    facts.append(_fact("ANALYSIS_AVAILABILITY", "WARNING", "分析可用性", dimensions.get("analysis_availability", "ANALYSIS_UNAVAILABLE"), "/evidence_quality/analysis_availability", timestamp=decision, priority=100))
    facts.append(_fact("EVIDENCE_FLOW_QUALITY", "WARNING", "订单流覆盖", dimensions.get("flow_quality", "FLOW_UNAVAILABLE"), "/evidence_quality/flow_quality", timestamp=decision, priority=85))
    facts.append(_fact("LONG_TERM_QUALITY", "WARNING", "长期结构", dimensions.get("long_term_quality", "UNAVAILABLE"), "/evidence_quality/long_term_quality", timestamp=decision, priority=80))
    for index, warning in enumerate(quality.get("gaps", []) + quality.get("missing_sources", []) + quality.get("watermark_mismatches", [])):
        source = str(warning.get("source") if isinstance(warning, dict) else warning)
        priority = 100 if source in {"15m", "1H", "4H", "1D"} else 70
        facts.append(_fact(f"DATA_WARNING_{index+1:02d}", "WARNING", "数据限制", warning, "/data_quality", timestamp=decision, priority=priority))
    for index, frame in enumerate(base.get("timeframe_structures", [])):
        tf = frame.get("timeframe"); prefix = TIMEFRAME_IDS.get(tf, f"TF{index}")
        close = frame.get("last_confirmed_close") or {}
        facts.append(_fact(f"{prefix}_SUMMARY", "TIMEFRAME", f"{tf}结构摘要", {"close":close.get("value"),"moving_average_ordering":frame.get("moving_average_ordering"),"swing_structure":frame.get("swing_structure"),"price_position":frame.get("price_position")}, f"/timeframe_structures/{index}", unit=close.get("unit"), timestamp=close.get("timestamp"), quality=close.get("quality", "UNKNOWN"), priority=100))
        for name in ("ma20", "ma60", "ma200"):
            item = (frame.get("moving_averages") or {}).get(name) or {}
            if item.get("value") is not None:
                facts.append(_fact(f"{prefix}_{name.upper()}", "TIMEFRAME", f"{tf} {name.upper()}", item["value"], f"/timeframe_structures/{index}/moving_averages/{name}", unit=item.get("unit"), timestamp=item.get("timestamp"), quality=item.get("quality", "UNKNOWN"), priority=45))
        intelligence = frame.get("deterministic_intelligence") or {}
        if intelligence:
            facts.append(_fact(
                f"{prefix}_STRUCTURE", "TIMEFRAME", f"{tf} deterministic structure",
                {"timeframe": tf, "role": frame.get("role"), "state": intelligence.get("state"),
                 "extension_state": intelligence.get("extension_state"),
                 "momentum_state": (intelligence.get("momentum") or {}).get("state"),
                 "tactical_state": (intelligence.get("tactical") or {}).get("state"),
                 "volume_states": (intelligence.get("volume") or {}).get("states", []),
                 "supply_evidence": (intelligence.get("tactical") or {}).get("supply_evidence", False),
                 "top_confirmed": (intelligence.get("tactical") or {}).get("top_confirmed", False)},
                f"/timeframe_structures/{index}/deterministic_intelligence", unit=None,
                timestamp=close.get("timestamp"), quality=close.get("quality", "UNKNOWN"), priority=80,
            ))
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
        value["flow_coverage"] = metrics.get("quality", {}).get("flow_coverage", {})
        value["flow_quality"] = value["flow_coverage"].get("state", "FLOW_UNAVAILABLE")
        facts.append(_fact(f"FLOW_PHASE_{index+1:02d}", "ORDER_FLOW", f"{phase.get('phase')}订单流", value,
                           f"/order_flow_phases/{len(phases)-min(4,len(phases))+index}", timestamp=phase.get("end"), quality=phase.get("quality", "UNKNOWN"), priority=(98 if phase.get("phase") == "CURRENT" else 89-index)))
    current_phase = next((phase for phase in reversed(phases) if phase.get("phase") == "CURRENT"), phases[-1] if phases else None)
    if current_phase:
        metrics = current_phase.get("metrics") or {}; oi = metrics.get("oi") or {}
        phase_index = phases.index(current_phase)
        facts.append(_fact(
            "PRICE_OI_RELATION", "ORDER_FLOW", "Price and OI bounded relation",
            {"evidence_kind": "PRICE_OI", "state": metrics.get("quadrant", "INSUFFICIENT_DATA"), "proxy": True,
             "price_change_pct": metrics.get("price_change_pct"), "oi_start": oi.get("start"),
             "oi_end": oi.get("end"), "absolute_change": oi.get("absolute_change"),
             "percentage_change": oi.get("percentage_change"), "slope": oi.get("slope"),
             "observation_count": oi.get("observation_count"), "quality": oi.get("status")},
            f"/order_flow_phases/{phase_index}/metrics", timestamp=current_phase.get("end"),
            quality=oi.get("status", "UNKNOWN"), priority=88,
        ))
        coverage = (metrics.get("quality") or {}).get("flow_coverage") or {}; cvd = metrics.get("cvd") or {}
        facts.append(_fact(
            "FLOW_CONFIRMATION", "ORDER_FLOW", "CVD bounded confirmation eligibility",
            {"phase": "CURRENT", "state": coverage.get("state", "FLOW_UNAVAILABLE"),
             "flow_quality": coverage.get("state", "FLOW_UNAVAILABLE"),
             "coverage_ratio": coverage.get("coverage_ratio"), "cvd_delta": cvd.get("signed_delta"),
             "cvd_status": cvd.get("status"), "confirmation": (
                 "CONFIRMING" if coverage.get("state") == "FLOW_COMPLETE" and cvd.get("signed_delta") is not None
                 else "LIMITED_CONFIRMATION" if coverage.get("state") == "FLOW_PARTIAL_USABLE" and cvd.get("signed_delta") is not None
                 else "NOT_ELIGIBLE")},
            f"/order_flow_phases/{phase_index}/metrics", timestamp=current_phase.get("end"),
            quality=coverage.get("state", "FLOW_UNAVAILABLE"), priority=88,
        ))
    for index, transition in enumerate(base.get("phase_transitions", [])[-1:]):
        facts.append(_fact(f"FLOW_TRANSITION_{index+1:02d}", "ORDER_FLOW", "订单流转变", {k:transition.get(k) for k in ("price_change","oi_behavior","cvd_behavior","volume_change","interpretation","confidence","counterevidence")},
            f"/phase_transitions/{len(base.get('phase_transitions', []))-1+index}", timestamp=decision, priority=93-index))
    all_levels=base.get("key_levels",[]);selected_levels=select_relevant_levels(base)
    level_indexes={level.get("level_id"):index for index,level in enumerate(all_levels)}
    for display_index, level in enumerate(selected_levels):
        index=level_indexes[level["level_id"]]
        facts.append(_fact(f"LEVEL_{display_index+1:02d}", "LEVEL", f"{level.get('role')} {level.get('state')}",
            _level_value(level),
            f"/key_levels/{index}", unit="USDT", timestamp=level.get("last_tested"), priority=(99-display_index if display_index<3 else 90-display_index)))
    current_frame = next((frame for frame in base.get("timeframe_structures", []) if frame.get("timeframe") == "15m"), {})
    intelligence = current_frame.get("deterministic_intelligence") or {}; tactical = intelligence.get("tactical") or {}
    tactical_ts = (current_frame.get("last_confirmed_close") or {}).get("timestamp")
    for fact_id, key, label in (("TACTICAL_LOCAL_HIGH", "local_high", "Tactical local high"),
                                ("TACTICAL_LOCAL_LOW", "local_low", "Tactical local low")):
        value = intelligence.get(key)
        if value is not None:
            facts.append(_fact(fact_id, "TIMEFRAME", label, value,
                               f"/timeframe_structures/0/deterministic_intelligence/{key}",
                               unit="USDT", timestamp=tactical_ts, priority=86))
    # A missing long-horizon registry level must not hide a valid current
    # tactical boundary. These are registered LEVEL facts, derived directly
    # from the existing 15m local swing facts; no price is invented.
    if not selected_levels:
        for fact_id, key, role in (("LEVEL_TACTICAL_RESISTANCE", "local_high", "RESISTANCE"),
                                   ("LEVEL_TACTICAL_SUPPORT", "local_low", "SUPPORT")):
            price = intelligence.get(key)
            if not isinstance(price, (int, float)):
                continue
            level_id = stable_hash({"kind": "TACTICAL_LOCAL", "role": role, "price": price, "timestamp": tactical_ts})
            value = {"level_id": level_id, "representative_price": price, "zone_low": price,
                     "zone_high": price, "role": role, "state": "ACTIVE", "strength": "MEDIUM",
                     "timeframes": ["15m"], "primary_timeframe": "15m", "dynamic": False,
                     "valid_until": None, "observed_at": tactical_ts, "source_fact": key,
                     "quality": "VALID"}
            facts.append(_fact(fact_id, "LEVEL", f"Tactical {role.lower()}", value,
                               f"/timeframe_structures/0/deterministic_intelligence/{key}",
                               unit="USDT", timestamp=tactical_ts, priority=95))
    for fact_id, key, label in (("TACTICAL_IMPULSE", "impulse", "Tactical impulse"),
                                ("TACTICAL_PULLBACK", "pullback", "Tactical pullback"),
                                ("TACTICAL_COMPRESSION", "compression", "Tactical compression")):
        if tactical.get(key):
            facts.append(_fact(fact_id, "TIMEFRAME", label, tactical[key],
                               f"/timeframe_structures/0/deterministic_intelligence/tactical/{key}",
                               timestamp=tactical_ts, priority=86))
    momentum = intelligence.get("momentum") or {}
    if momentum.get("base_state") == "MOMENTUM_RESET":
        facts.append(_fact("MOMENTUM_RESET", "TIMEFRAME", "Momentum reset", momentum,
                           "/timeframe_structures/0/deterministic_intelligence/momentum", timestamp=tactical_ts, priority=87))
    if momentum.get("state") == "PRICE_RESILIENT_MOMENTUM_RESET":
        facts.append(_fact("PRICE_RESILIENT_MOMENTUM_RESET", "TIMEFRAME", "Price-resilient momentum reset", momentum,
                           "/timeframe_structures/0/deterministic_intelligence/momentum", timestamp=tactical_ts, priority=89))
    mtf = base.get("multi_timeframe_summary") or {}
    facts.append(_fact("TIMEFRAME_EXTENSION", "TIMEFRAME", "Multi-timeframe extension",
                       {"states": mtf.get("extension_states", {}),
                        "higher_timeframes_extended": mtf.get("higher_timeframes_extended", [])},
                       "/multi_timeframe_summary/extension_states", timestamp=decision, priority=89))
    facts.append(_fact("TIMEFRAME_ALIGNMENT", "TIMEFRAME", "Multi-timeframe alignment",
                       {"alignment": mtf.get("alignment"), "conflicts": mtf.get("conflicts", []),
                        "dominant_context": mtf.get("dominant_context")},
                       "/multi_timeframe_summary", timestamp=decision, priority=89))
    volume = intelligence.get("volume") or {}
    volume_ids = {"IMPULSE_VOLUME_EXPANSION": "VOLUME_EXPANSION",
                  "POST_IMPULSE_VOLUME_CONTRACTION": "VOLUME_CONTRACTION",
                  "HIGH_VOLUME_REJECTION": "VOLUME_REJECTION"}
    for state_name in volume.get("states", []):
        if state_name in volume_ids:
            facts.append(_fact(volume_ids[state_name], "TIMEFRAME", state_name, volume,
                               "/timeframe_structures/0/deterministic_intelligence/volume",
                               timestamp=tactical_ts, priority=86))
    if tactical.get("supply_evidence"):
        facts.append(_fact("SUPPLY_EVIDENCE_PRESENT", "TIMEFRAME", "Supply or rejection evidence",
                           {"present": True, "top_confirmed": bool(tactical.get("top_confirmed")),
                            "basis": "HIGH_VOLUME_REJECTION_WITH_FOLLOW_THROUGH_CHECK"},
                           "/timeframe_structures/0/deterministic_intelligence/tactical",
                           timestamp=tactical_ts, priority=88))
    psychological = [level for level in all_levels if "PSYCHOLOGICAL_LEVEL" in {
        str(candidate.get("source")) for candidate in level.get("source_candidates", [])}]
    current_price = _number((current_frame.get("last_confirmed_close") or {}).get("value"))
    if psychological and current_price is not None:
        level = min(psychological, key=lambda item: abs(float(item.get("representative_price", 0)) - current_price))
        facts.append(_fact("ROUND_LEVEL", "TIMEFRAME", "Nearest meaningful round level",
                           {"level_id": level.get("level_id"), "price": level.get("representative_price"),
                            "role": level.get("role"),
                            "current_eligible": current_level_relevance(base, level)["current_eligible"]},
                           f"/key_levels/{all_levels.index(level)}", unit="USDT", timestamp=decision, priority=85))
    for index, scenario in enumerate(base.get("scenario_tree", {}).get("scenarios", [])):
        trigger=scenario.get("trigger") or {}; invalidation=scenario.get("invalidation") or {}
        if not isinstance(trigger,dict):trigger={"rule":str(trigger),"level_ids":scenario.get("trigger_level_ids",[])}
        if not isinstance(invalidation,dict):invalidation={"rule":str(invalidation),"level_id":None,"timeframe":None}
        facts.append(_fact(f"SCENARIO_{index+1:02d}", "SCENARIO", scenario.get("type", "情景"), {"scenario_id":scenario.get("scenario_id"),"type":scenario.get("type"),"direction":scenario.get("direction"),"likelihood":scenario.get("likelihood"),"trigger":{"rule":trigger.get("rule"),"level_ids":trigger.get("level_ids",[])},"confirmation":scenario.get("confirmation"),"expected_path":scenario.get("expected_path",[]),"targets":scenario.get("target_level_ids",scenario.get("targets",[])),"invalidation":{"rule":invalidation.get("rule"),"level_id":invalidation.get("level_id"),"timeframe":invalidation.get("timeframe")},"volume_confirmation":scenario.get("volume_confirmation"),"cvd_confirmation":scenario.get("cvd_confirmation"),"oi_confirmation":scenario.get("oi_confirmation"),"funding_basis_confirmation":scenario.get("funding_basis_confirmation"),"contradicting_evidence":scenario.get("contradicting_evidence",scenario.get("contradictory_evidence",[])),"required_data_quality":scenario.get("required_data_quality"),"source_event_ids":scenario.get("source_event_ids",[]),"source_phase_ids":scenario.get("source_phase_ids",[]),"source_level_ids":scenario.get("source_level_ids",[]),"version":scenario.get("version")},
            f"/scenario_tree/scenarios/{index}", timestamp=decision, priority=100))
    scenarios = base.get("scenario_tree", {}).get("scenarios", [])
    if scenarios:
        primary = scenarios[0]; trigger = primary.get("trigger") or {}; invalidation = primary.get("invalidation") or {}
        facts.append(_fact("TACTICAL_TRIGGER", "TIMEFRAME", "Tactical confirmation trigger",
                           {"rule": trigger.get("rule"), "level_ids": trigger.get("level_ids", []),
                            "scenario_id": primary.get("scenario_id")},
                           "/scenario_tree/scenarios/0/trigger", timestamp=decision, priority=89))
        facts.append(_fact("TACTICAL_INVALIDATION", "TIMEFRAME", "Tactical invalidation",
                           {"rule": invalidation.get("rule"), "level_id": invalidation.get("level_id"),
                            "timeframe": invalidation.get("timeframe"), "scenario_id": primary.get("scenario_id")},
                           "/scenario_tree/scenarios/0/invalidation", timestamp=decision, priority=89))
    elif current_frame:
        facts.append(_fact("TACTICAL_TRIGGER", "TIMEFRAME", "Tactical confirmation trigger",
                           {"rule": "confirmed close above local high", "price": intelligence.get("local_high"),
                            "timeframe": "15m"},
                           "/timeframe_structures/0/deterministic_intelligence/local_high",
                           timestamp=tactical_ts, priority=88))
        facts.append(_fact("TACTICAL_INVALIDATION", "TIMEFRAME", "Tactical invalidation",
                           {"rule": "confirmed close below local low", "price": intelligence.get("local_low"),
                            "timeframe": "15m"},
                           "/timeframe_structures/0/deterministic_intelligence/local_low",
                           timestamp=tactical_ts, priority=88))
    pos = enriched["position_context"]
    facts.append(_fact("POSITION_SOURCE", "POSITION", "持仓来源", pos.get("source"), "/position_context/source", timestamp=decision, claim_scope="POSITION", priority=100))
    for fid,key,label in (("POSITION_SIDE","side","方向"),("POSITION_AVERAGE_COST","average_cost","平均成本"),("POSITION_ORIGINAL_QUANTITY","original_quantity","原始数量"),("POSITION_REMAINING_QUANTITY","remaining_quantity","剩余数量"),("POSITION_ORIGINAL_STOP","original_stop","原始止损"),("POSITION_ORIGINAL_TIMEFRAME","original_timeframe","原始周期"),("POSITION_ORIGINAL_THESIS","original_thesis","原始逻辑"),("POSITION_PLAN_COMPLETION","plan_completion_ratio","计划完成度"),("POSITION_WARNINGS","discipline_warnings","纪律警告")):
        if pos.get(key) is not None: facts.append(_fact(fid,"POSITION",label,pos[key],f"/position_context/{key}",unit="USDT" if "COST" in fid or "STOP" in fid else None,timestamp=decision,claim_scope="POSITION",priority=96))
    macro = enriched["macro_context"]
    for index, item in enumerate(macro.get("items", [])):
        facts.append(_fact(f"MACRO_{index+1:02d}", "MACRO", item["title"], {"evidence_id":item["evidence_id"],"category":item["category"],"factual_summary":item["factual_summary"],"publisher":item["publisher"],"published_at":item["published_at"]}, f"/macro_context/items/{index}", timestamp=item["published_at"], source=item["source_type"], claim_scope="MACRO", priority=100))
    if not macro.get("items"):
        facts.append(_fact("MACRO_UNAVAILABLE", "WARNING", "宏观背景", "本轮未纳入宏观背景。", "/macro_context/warnings", timestamp=decision, priority=30))
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
                            "allow_range": unit == "USDT", "absolute_tolerance": .51, "source_fact_id": fact_id,
                            **numeric_semantics(fact_id, key, unit)})
        elif isinstance(value, dict):
            for k,v in value.items(): collect(v, fact_id, unit, k)
        elif isinstance(value, list):
            for v in value: collect(v, fact_id, unit, key)
    for fact in facts: collect(fact["value"], fact["fact_id"], fact["unit"])
    # Stable de-duplication.
    unique = {}
    for item in numeric:
        unique.setdefault((str(item["canonical_value"]), item["unit"]),item)
    numeric = [unique[k] for k in sorted(unique, key=lambda item: (item[0], str(item[1])))]
    core = {"version": AI_REPORT_FACT_REGISTRY_VERSION,"numeric_registry_version":AI_REPORT_NUMERIC_REGISTRY_VERSION, "context_id": enriched["enriched_context_id"],
            "instrument": enriched["instrument"], "decision_time": decision, "facts": facts,
            "numeric_registry": numeric, "allowed_directional_biases": ["BULLISH","NEUTRAL","BEARISH"],
            "max_confidence": ("HIGH" if dimensions.get("core_quality") == "COMPLETE"
                               else "MEDIUM" if dimensions.get("core_quality") == "USABLE"
                               else "LOW"),
            "allowed_market_phases": sorted({str(timeline.get("current_phase") or "UNKNOWN"), "MIXED"})}
    return {**core, "registry_hash": stable_hash(core)}
