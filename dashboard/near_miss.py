"""Explainable near-miss detection and counterfactual outcome tracking."""

from __future__ import annotations

from typing import Any

try:
    from gate_analysis import decision_gates
except ImportError:
    from .gate_analysis import decision_gates

DISCLAIMER = "If this condition changed, the signal might pass at the rule layer; it does not imply a profitable outcome."
NON_CRITICAL = {"final_entry_allowed", "oi_context"}


def distance_gaps(decision: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    ind = decision.get("indicator_values") or {}; close = float((decision.get("decision_input_summary") or {}).get("close") or 0)
    ema, rsi, volume, fast, slow = ind.get("ema"), ind.get("rsi"), ind.get("volume_ratio"), ind.get("fast_ma"), ind.get("slow_ma")
    ema_distance = abs(close - float(ema)) / float(ema) if ema else None
    rsi_low = max(0.0, float(parameters.get("rsi_min", 35)) - float(rsi)) if rsi is not None else None
    rsi_high = max(0.0, float(rsi) - float(parameters.get("rsi_max", 68))) if rsi is not None else None
    bias = decision.get("bias")
    structure_gap = None if None in (fast, slow) else (max(0.0, float(slow) - float(fast)) if bias == "LONG" else max(0.0, float(fast) - float(slow)) if bias == "SHORT" else abs(float(fast) - float(slow)))
    frames = (decision.get("timeframe_context") or {}).get("frame_biases") or {}
    required = (decision.get("timeframe_context") or {}).get("required_frames") or []
    aligned = sum(frames.get(frame) == bias for frame in required)
    flow = decision.get("flow_context") or {}
    return {"score_gap": max(0.0, float(parameters.get("minimum_score", 75)) - float(decision.get("score", 0))), "ema_distance_gap": max(0.0, (ema_distance or 0) - float(parameters.get("ema_pullback_distance", .0045))) if ema_distance is not None else None, "rsi_lower_gap": rsi_low, "rsi_upper_gap": rsi_high, "volume_ratio_gap": max(0.0, float(parameters.get("minimum_volume_ratio", 1)) - float(volume)) if volume is not None else None, "fast_slow_ma_gap": structure_gap, "higher_timeframe_alignment_gap": max(0, len(required) - aligned), "flow_alignment_state": "Unavailable" if not flow.get("available") else "Aligned" if "flow_alignment" not in (decision.get("failed_gates") or []) else "Misaligned", "distance_to_ema": ema_distance, "cvd_direction": "UP" if float(flow.get("cvd_delta") or 0) > 0 else "DOWN" if float(flow.get("cvd_delta") or 0) < 0 else "FLAT", "oi_change": flow.get("oi_change_pct")}


def identify_near_miss(decision: dict[str, Any], parameters: dict[str, Any], max_failed_gates: int = 2, max_score_gap: float = 10.0) -> dict[str, Any] | None:
    gates = decision_gates(decision); failed = [g["key"] for g in gates if g.get("applicable", True) and g.get("blocking", True) and not g["passed"] and g["key"] not in NON_CRITICAL]
    passed = [g["key"] for g in gates if g.get("applicable", True) and g["passed"]]
    gaps = distance_gaps(decision, parameters); risk = decision.get("risk_context") or {}
    close_to_bias = decision.get("bias") in {"LONG", "SHORT"} or (gaps["fast_slow_ma_gap"] is not None and gaps["fast_slow_ma_gap"] <= abs(float((decision.get("indicator_values") or {}).get("slow_ma") or 1)) * .003)
    if not decision.get("warmed") or not risk.get("allowed", True) or not close_to_bias or len(failed) > max_failed_gates or gaps["score_gap"] > max_score_gap or decision.get("entry_allowed"):
        return None
    return {"signal_id": decision.get("signal_id"), "instrument": decision.get("instrument"), "candle_close_ts": decision.get("candle_close_ts"), "strategy_version": decision.get("strategy_version"), "config_hash": decision.get("config_hash"), "bias": decision.get("bias"), "score": decision.get("score"), "minimum_score": parameters.get("minimum_score", 75), **gaps, "failed_gates": failed, "passed_gates": passed, "indicator_values": decision.get("indicator_values") or {}, "timeframe_context": decision.get("timeframe_context") or {}, "flow_context": decision.get("flow_context") or {}, "risk_context": risk, "regime": decision.get("regime", "Unknown"), "final_rejection_reason": decision.get("rejection_reason"), "what_prevented_entry": ", ".join(failed), "what_would_have_changed": DISCLAIMER}


def counterfactual_outcome(near_miss: dict[str, Any], candles: list[dict[str, Any]], parameters: dict[str, Any], window_bars: int = 96) -> dict[str, Any]:
    side = near_miss.get("bias"); indicators = near_miss.get("indicator_values") or {}; atr = indicators.get("atr")
    if side not in {"LONG", "SHORT"} or not candles or not atr:
        return {"status": "INSUFFICIENT_DATA", "counterfactual": True}
    entry = float(candles[0].get("open", candles[0]["close"])); risk = float(atr) * float(parameters.get("stop_loss_atr_multiplier", 1)); rr = float(parameters.get("risk_reward_ratio", 2))
    stop = entry - risk if side == "LONG" else entry + risk; one_r = entry + risk if side == "LONG" else entry - risk; two_r = entry + risk * 2 if side == "LONG" else entry - risk * 2
    favorable = adverse = 0.0; first = None; first_ts = None
    for candle in candles[:window_bars]:
        high, low = float(candle["high"]), float(candle["low"])
        favorable = max(favorable, high - entry if side == "LONG" else entry - low); adverse = max(adverse, entry - low if side == "LONG" else high - entry)
        hit_stop = low <= stop if side == "LONG" else high >= stop
        hit_1r = high >= one_r if side == "LONG" else low <= one_r
        hit_2r = high >= two_r if side == "LONG" else low <= two_r
        if first is None and (hit_stop or hit_1r or hit_2r):
            first = "STOP" if hit_stop else "2R" if hit_2r else "1R"; first_ts = candle.get("ts")
    return {"status": "COMPLETED", "counterfactual": True, "entry": entry, "stop": stop, "target_1r": one_r, "target_2r": two_r, "first_trigger": first or "NONE", "first_trigger_ts": first_ts, "mfe_r": favorable / risk if risk else None, "mae_r": adverse / risk if risk else None, "window_bars": min(window_bars, len(candles)), "paper_pnl_included": False, "disclaimer": DISCLAIMER}
