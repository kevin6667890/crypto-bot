from __future__ import annotations

from typing import Any

from .canonical import identity, stable_hash
from .quality import iso
from .structure_timeline import build_timeline
from .swing_structure import confirmed_swings, swing_summary
from .timeframe_facts import build_multi_timeframe_facts
from .orderflow_context_adapter import build_ai3_facts
from .versions import (
    AI_CONTEXT_ADAPTER_VERSION, AI_CONTEXT_SCHEMA_VERSION, AI_MARKET_FACTS_VERSION,
    AI_MARKET_TIMELINE_VERSION, AI_RANGE_COMPRESSION_VERSION, AI_SWING_STRUCTURE_VERSION,
    AI_TIMEFRAME_STRUCTURE_VERSION, SUPPORTED_TIMEFRAMES,
    AI_ORDERFLOW_WINDOW_VERSION, AI_ORDERFLOW_METRICS_VERSION,
    AI_ORDERFLOW_ATTRIBUTION_VERSION, AI_KEY_LEVEL_ENGINE_VERSION,
    AI_KEY_LEVEL_ZONE_VERSION, AI_SCENARIO_TREE_VERSION,
    AI_CONTEXT_ORDERFLOW_ADAPTER_VERSION,
)


def _schema_quality(status: str) -> str:
    return {"COMPLETE": "VALID", "PARTIAL": "PARTIAL", "STALE": "STALE",
            "MISSING": "MISSING", "WARMUP_INCOMPLETE": "PARTIAL",
            "GAP_AFFECTED": "PARTIAL", "INVALID": "UNKNOWN"}.get(status, "UNKNOWN")


def _traced(value: float | None, unit: str, timestamp: int, source: str, derivation: str,
            version: str, quality: str, path: str | None = None) -> dict[str, Any]:
    result = {"value": value, "unit": unit, "timestamp": iso(timestamp), "source": source,
              "status": "DERIVED" if value is not None else "UNAVAILABLE", "quality": quality,
              "derivation": derivation, "version": version}
    if path:
        result["evidence_path"] = path
    return result


def _trend(facts: dict[str, Any], swing_state: str) -> tuple[str, list[str], list[str]]:
    if len(facts["confirmed_bars"]) < 60:
        return "INSUFFICIENT_DATA", [], ["fewer than 60 confirmed bars"]
    close = facts["confirmed_close"]
    ma = facts["moving_averages"]
    slopes = facts["slopes"]
    bull = bear = 0
    support, contradict = [], []
    for name in ("ma20", "ma30", "ma60", "ma200"):
        value = ma[name]["value"]
        if value is None:
            continue
        if close > value:
            bull += 1; support.append(f"close above {name}")
        elif close < value:
            bear += 1; contradict.append(f"close below {name}")
    if facts["ma_ordering"] == "BULLISH":
        bull += 2; support.append("bullish MA ordering")
    elif facts["ma_ordering"] == "BEARISH":
        bear += 2; contradict.append("bearish MA ordering")
    for name in ("ma20", "ma60", "ma200", "ema20"):
        value = slopes[name]["value"]
        bull += int(value is not None and value > 0)
        bear += int(value is not None and value < 0)
    if swing_state == "HH_HL":
        bull += 2; support.append("confirmed HH/HL sequence")
    elif swing_state == "LH_LL":
        bear += 2; contradict.append("confirmed LH/LL sequence")
    if bull >= 8 and bull-bear >= 5:
        result = "STRONG_BULL"
    elif bull >= 5 and bull > bear:
        result = "BULL"
    elif bear >= 8 and bear-bull >= 5:
        result = "STRONG_BEAR"
    elif bear >= 5 and bear > bull:
        result = "BEAR"
    else:
        result = "NEUTRAL"
    if result in {"BEAR", "STRONG_BEAR"}:
        support, contradict = contradict, support
    return result, support, contradict


def _structure(facts: dict[str, Any], index: int) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    atr = facts["atr14"]["value"]
    swings = confirmed_swings(facts["confirmed_bars"], facts["timeframe"], atr=atr)
    swing_state = swing_summary(swings)
    timeline = build_timeline(facts, swings)
    trend, supporting, contradicting = _trend(facts, swing_state)
    phase = timeline["current_phase"]
    classification = {
        "BREAKOUT_ATTEMPT": "BREAKOUT_UP" if timeline["direction"] == "UP" else "BREAKOUT_DOWN",
        "BREAKOUT_CONFIRMED": "BREAKOUT_UP" if timeline["direction"] == "UP" else "BREAKOUT_DOWN",
        "IMPULSE": "BREAKOUT_UP" if timeline["direction"] == "UP" else "BREAKOUT_DOWN",
        "POST_BREAKOUT_PULLBACK": "POST_BREAKOUT_PULLBACK", "RETEST": "RETEST",
        "CONTINUATION": "CONTINUATION_UP" if timeline["direction"] == "UP" else "CONTINUATION_DOWN",
        "FAILED_BREAKOUT": "FAILED_BREAKOUT_UP" if timeline["direction"] == "UP" else "FAILED_BREAKOUT_DOWN",
        "REVERSAL": "REVERSAL_UP" if timeline["direction"] == "UP" else "REVERSAL_DOWN",
        "RANGE_BUILDING": "RANGE", "COMPRESSION": "COMPRESSION",
    }.get(phase, "TREND_UP" if trend in {"BULL", "STRONG_BULL"} else
          "TREND_DOWN" if trend in {"BEAR", "STRONG_BEAR"} else "UNCLASSIFIED")
    timestamp = facts["latest_confirmed_bar_timestamp"] or facts["decision_time"]
    quality = _schema_quality(facts["quality"]["status"])
    source = f"{facts['instrument']}:{facts['timeframe']}:confirmed_ohlcv"
    traced = lambda metric, unit, derivation: _traced(
        metric["value"], unit, metric["last_bar_timestamp"] or timestamp, source,
        derivation, metric["calculation_version"], quality)
    live = facts["live_observation"]
    live_out = None if live is None else {
        "open_time": iso(live["open_time"]), "confirmed": False, "close_time": iso(live["close_time"]),
        "open": live["open"], "high": live["high"], "low": live["low"],
        "close": live["close"], "volume": live["volume"], "source": live["source"],
    }
    paths = [f"/timeframe_structures/{index}/last_confirmed_close"]
    confidence = "LOW" if facts["quality"]["status"] in {"GAP_AFFECTED", "STALE", "INVALID"} else (
        "INSUFFICIENT" if trend == "INSUFFICIENT_DATA" else "HIGH" if len(supporting) >= 4 and not contradicting else "MEDIUM")
    structure = {
        "timeframe": facts["timeframe"],
        "last_confirmed_close": _traced(facts["confirmed_close"], "USDT", timestamp, source,
                                         "last confirmed close at causal cutoff", AI_MARKET_FACTS_VERSION, quality),
        "current_incomplete_candle": live_out,
        "moving_averages": {name: traced(metric, "USDT", f"causal {name} over confirmed closes")
                            for name, metric in facts["moving_averages"].items()},
        "slopes": {name: traced(metric, "percent", f"four confirmed-bar slope of {name}")
                   for name, metric in facts["slopes"].items()},
        "price_position": ",".join(name for name, metric in facts["price_to_ma_distance"].items()
                                    if metric["value"] is not None and metric["value"] > 0) or "NOT_AVAILABLE",
        "moving_average_ordering": facts["ma_ordering"], "swing_structure": swing_state,
        "rsi": traced(facts["rsi14"], "index", "causal RSI14"),
        "stoch_rsi": traced(facts["stoch_rsi"]["stoch_rsi"], "index", "causal Stoch RSI14"),
        "atr": traced(facts["atr14"], "USDT", "Wilder ATR14 reused from Market Context V2 feature core"),
        "volume_regime": facts["volume_regime"]["classification"],
        "trend_classification": trend, "structure_classification": classification,
        "nearest_support": None, "nearest_resistance": None,
        "invalidation": {"rule": timeline["events"][-1]["invalidation"] if timeline["events"] else "new confirmed structure evidence required",
                         "evidence_paths": paths},
        "confidence": confidence,
        "supporting_facts": [{"claim": claim, "evidence_paths": paths} for claim in supporting],
        "contradicting_facts": [{"claim": claim, "evidence_paths": paths} for claim in contradicting],
        "source_bar_timestamps": [iso(row["close_time"]) for row in facts["confirmed_bars"]],
    }
    return structure, timeline, swings


def _bias(trend: str) -> int | None:
    if trend in {"STRONG_BULL", "BULL"}:
        return 1
    if trend in {"STRONG_BEAR", "BEAR"}:
        return -1
    if trend == "INSUFFICIENT_DATA":
        return None
    return 0


def _multi_summary(structures: list[dict[str, Any]], timelines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_tf = {item["timeframe"]: item for item in structures}
    pairs = []
    for lower, higher in zip(("15m", "1H", "4H", "1D"), ("1H", "4H", "1D", "1W")):
        low, high = _bias(by_tf[lower]["trend_classification"]), _bias(by_tf[higher]["trend_classification"])
        if low is None or high is None:
            state = "INSUFFICIENT_DATA"
        elif low == high == 1:
            state = "ALIGNED_BULL"
        elif low == high == -1:
            state = "ALIGNED_BEAR"
        elif low == 1 and high == -1:
            state = "LOWER_TF_BULL_HIGHER_TF_BEAR"
        elif low == -1 and high == 1:
            state = "LOWER_TF_BEAR_HIGHER_TF_BULL"
        else:
            state = "MIXED"
        pairs.append({"lower": lower, "higher": higher, "relationship": state})
    order = {"4H": 5, "1D": 4, "1H": 3, "1W": 2, "15m": 1}
    active = [tf for tf, timeline in timelines.items() if timeline["current_phase"] != "UNCLASSIFIED"]
    dominant = max(active, key=lambda tf: order[tf], default="4H")
    obstacles = []
    for lower, higher in zip(("15m", "1H", "4H", "1D"), ("1H", "4H", "1D", "1W")):
        if timelines[lower]["direction"] == "UP" and _bias(by_tf[higher]["trend_classification"]) == -1:
            obstacles.append(f"{lower} upside structure meets {higher} bearish structure")
        if timelines[lower]["direction"] == "DOWN" and _bias(by_tf[higher]["trend_classification"]) == 1:
            obstacles.append(f"{lower} downside structure remains inside {higher} bullish structure")
    return {"version": AI_TIMEFRAME_STRUCTURE_VERSION, "pair_relationships": pairs,
            "dominant_timeframe": dominant, "higher_timeframe_obstacles": obstacles}


def _schema_event(event: dict[str, Any], tf_index: int) -> dict[str, Any]:
    path = f"/timeframe_structures/{tf_index}/last_confirmed_close"
    return {
        "event_id": event["event_id"], "event_type": event["event_type"],
        "timeframe": event["timeframe"], "start": iso(event["started_at"]),
        "end": iso(event["ended_at"] or event["confirmed_at"]), "direction": event["direction"],
        "confirmation_status": "CONFIRMED",
        "source_bar_timestamps": [iso(item) for item in event["input_bar_timestamps"]],
        "evidence": [{"claim": claim, "evidence_paths": [path]} for claim in event["evidence"]],
        "invalidation": event["invalidation"],
    }


def build_market_analysis_context(datasets: dict[str, list[dict[str, Any]]], instrument: str,
                                  decision_time: int, mode: str = "FULL", *,
                                  orderflow: dict[str, Any] | None = None,
                                  auxiliary: dict[str, Any] | None = None) -> dict[str, Any]:
    facts = build_multi_timeframe_facts(datasets, instrument, decision_time)
    structures, timelines, all_swings = [], {}, {}
    for index, timeframe in enumerate(SUPPORTED_TIMEFRAMES):
        structure, timeline, swings = _structure(facts[timeframe], index)
        structures.append(structure); timelines[timeframe] = timeline; all_swings[timeframe] = swings
    summary = _multi_summary(structures, timelines)
    dominant = summary["dominant_timeframe"]
    timeline = timelines[dominant]
    events = []
    for index, timeframe in enumerate(SUPPORTED_TIMEFRAMES):
        events.extend(_schema_event(item, index) for item in timelines[timeframe]["events"][-12:])
    events = sorted(events, key=lambda item: (item["start"], item["timeframe"], item["event_id"]))[-40:]
    latest = max((item["latest_confirmed_bar_timestamp"] for item in facts.values()
                  if item["latest_confirmed_bar_timestamp"] is not None), default=decision_time)
    fingerprints = {tf: facts[tf]["input_fingerprint"] for tf in SUPPORTED_TIMEFRAMES}
    qualities = {tf: facts[tf]["quality"]["status"] for tf in SUPPORTED_TIMEFRAMES}
    source_versions = {
        "facts": AI_MARKET_FACTS_VERSION, "timeframe_structure": AI_TIMEFRAME_STRUCTURE_VERSION,
        "swing": AI_SWING_STRUCTURE_VERSION, "range_compression": AI_RANGE_COMPRESSION_VERSION,
        "timeline": AI_MARKET_TIMELINE_VERSION, "adapter": AI_CONTEXT_ADAPTER_VERSION,
        "orderflow_window": AI_ORDERFLOW_WINDOW_VERSION,
        "orderflow_metrics": AI_ORDERFLOW_METRICS_VERSION,
        "orderflow_attribution": AI_ORDERFLOW_ATTRIBUTION_VERSION,
        "key_level_engine": AI_KEY_LEVEL_ENGINE_VERSION,
        "key_level_zone": AI_KEY_LEVEL_ZONE_VERSION,
        "scenario_tree": AI_SCENARIO_TREE_VERSION,
        "context_orderflow_adapter": AI_CONTEXT_ORDERFLOW_ADAPTER_VERSION,
    }
    ai3 = build_ai3_facts(facts=facts, timelines=timelines, swings=all_swings, dominant=dominant,
                          instrument=instrument, decision_time=decision_time,
                          orderflow=orderflow, auxiliary=auxiliary)
    ai3_requested = orderflow is not None
    identity_input = {"instrument": instrument, "decision_time": decision_time,
                      "latest_confirmed_market_time": latest, "input_fingerprints": fingerprints,
                      "source_versions": source_versions, "quality": qualities,
                      "orderflow_source_fingerprints": ai3["source_fingerprints"],
                      "phase_window_identities": [p["window_fingerprint"] for p in ai3["order_flow_phases"]],
                      "key_level_identities": [l["level_id"] for l in ai3["key_levels"]],
                      "scenario_identities": [s["scenario_id"] for s in ai3["scenario_tree"]["scenarios"]],
                      "ai3_quality": ai3["quality"]}
    context_id = identity("ctx", identity_input)
    quality_values = set(qualities.values())
    overall = "MISSING" if quality_values == {"MISSING"} else "PARTIAL" if any(
        value != "COMPLETE" for value in quality_values) else "VALID"
    range_fact, breakout, impulse = timeline["range"], timeline["breakout"], timeline["impulse"]
    stamp = latest
    null_or = lambda value, derivation: _traced(value, "USDT", stamp,
        f"{instrument}:{dominant}:confirmed_ohlcv", derivation, AI_MARKET_TIMELINE_VERSION,
        _schema_quality(facts[dominant]["quality"]["status"]))
    breakout_index = next((i for i, item in enumerate(events)
                           if item["timeframe"] == dominant and item["event_type"] == "BREAKOUT_ATTEMPT"), None)
    context = {
        "schema_version": AI_CONTEXT_SCHEMA_VERSION, "context_id": context_id,
        "instrument": instrument, "generated_at": iso(decision_time), "decision_time": iso(decision_time),
        "latest_confirmed_market_time": iso(latest), "requested_analysis_mode": mode,
        "source_versions": source_versions,
        "data_watermarks": {tf: iso(facts[tf]["latest_confirmed_bar_timestamp"] or decision_time)
                            for tf in SUPPORTED_TIMEFRAMES},
        "data_quality": {
            "overall": overall,
            "gaps": [{"source": tf, "start": iso(gap["start"]), "end": iso(gap["end"])}
                     for tf in SUPPORTED_TIMEFRAMES for gap in facts[tf]["quality"]["gaps"]],
            "stale_sources": [tf for tf in SUPPORTED_TIMEFRAMES if facts[tf]["quality"]["source_stale"]],
            "missing_sources": [tf for tf in SUPPORTED_TIMEFRAMES if not facts[tf]["confirmed_bars"]],
            "watermark_mismatches": [],
        },
        "timeframe_structures": structures, "structure_events": events,
        "multi_timeframe_summary": summary,
        "market_timeline": {
            "observation_window": {"start": iso(facts[dominant]["confirmed_bars"][0]["open_time"] if facts[dominant]["confirmed_bars"] else decision_time),
                                   "end": iso(stamp)},
            "compression_start": iso(timeline["compression"]["start"]) if timeline["compression"].get("start") else None,
            "compression_end": iso(timeline["compression"]["end"]) if timeline["compression"].get("end") else None,
            "range_low": null_or(range_fact["low"] if range_fact else None, "bounded range low"),
            "range_high": null_or(range_fact["high"] if range_fact else None, "bounded range high"),
            "breakout_timestamp": iso(breakout["timestamp"]) if breakout and breakout.get("timestamp") else None,
            "breakout_direction": timeline["direction"] if breakout else "NONE",
            "breakout_candle": f"/structure_events/{breakout_index}" if breakout_index is not None else None,
            "breakout_volume_ratio": _traced(facts[dominant]["volume_ratio"]["value"], "ratio", stamp,
                                             f"{instrument}:{dominant}:confirmed_ohlcv", "breakout/current volume ratio",
                                             AI_MARKET_FACTS_VERSION, _schema_quality(facts[dominant]["quality"]["status"])),
            "impulse_high": null_or(impulse["extreme"] if impulse and timeline["direction"] == "UP" else None, "upside impulse extreme"),
            "impulse_low": null_or(impulse["extreme"] if impulse and timeline["direction"] == "DOWN" else None, "downside impulse extreme"),
            "pullback_start": iso(timeline["pullback"]["start"]) if timeline.get("pullback") else None,
            "current_phase": timeline["current_phase"] if timeline["current_phase"] != "EXPIRED" else "UNCLASSIFIED",
        },
        "order_flow_phases": ai3["order_flow_phases"] if ai3_requested else [],
        "phase_transitions": ai3["phase_transitions"] if ai3_requested else [],
        "key_levels": ai3["key_levels"] if ai3_requested else [],
        "scenario_tree": ai3["scenario_tree"] if ai3_requested else {"status": "NOT_IMPLEMENTED", "scenarios": []},
        "position_context": {"source": "NONE", "side": None, "entry": None, "average_cost": None,
                             "quantity": None, "original_thesis": None, "original_timeframe": None,
                             "original_stop": None, "original_targets": [], "realised_exits": [],
                             "remaining_position": None, "current_risk": None,
                             "plan_completed": None, "discipline_warning": None},
        "macro_context": {"status": "NOT_REQUESTED", "items": []},
        "current_core_question": "Which deterministic scenario trigger is confirmed next?" if ai3_requested else "NOT_IMPLEMENTED: scenario synthesis belongs to AI-3",
        "unsupported_claims": ([*[f"key_level_source:{name}:NOT_IMPLEMENTED" for name in ai3["not_implemented_sources"]],
                               "macro_context:NOT_REQUESTED", "position_context:NONE",
                               "AI prose generation:NOT_IMPLEMENTED"] if ai3_requested else
                               ["order_flow_phases:NOT_IMPLEMENTED", "key_levels:NOT_IMPLEMENTED",
                                "scenario_tree:NOT_IMPLEMENTED", "macro_context:NOT_REQUESTED",
                                "AI prose generation:NOT_IMPLEMENTED"]),
        "provenance": {"builder": "dashboard.ai_market_analysis.context_adapter",
                       "builder_version": AI_CONTEXT_ADAPTER_VERSION,
                       "input_snapshot_ids": [*[fingerprints[tf] for tf in SUPPORTED_TIMEFRAMES],
                                              *[ai3["source_fingerprints"][k] for k in sorted(ai3["source_fingerprints"])]],
                       "causal_cutoff": iso(decision_time), "content_hash": stable_hash(identity_input)},
    }
    return context
