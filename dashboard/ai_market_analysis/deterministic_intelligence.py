"""Deterministic, explainable multi-timeframe market observations.

The functions in this module consume only confirmed OHLCV-derived facts and
confirmed swing/timeline state.  They deliberately produce observations, not
trading instructions or narrative causality.
"""
from __future__ import annotations

from typing import Any

from .versions import AI_DETERMINISTIC_INTELLIGENCE_VERSION


TIMEFRAME_ROLES = {
    "15m": "TACTICAL",
    "1H": "SETUP_CONTEXT",
    "4H": "PRIMARY_ENVIRONMENT",
    "1D": "MEDIUM_TERM_DIRECTION",
    "1W": "LONG_TERM_STRUCTURE",
}


def _finite(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator not in (None, 0) else None


def _volume_ratio(rows: list[dict[str, Any]], selected: list[dict[str, Any]]) -> float | None:
    if not rows or not selected:
        return None
    selected_ids = {int(row["open_time"]) for row in selected}
    baseline = [float(row["volume"]) for row in rows[-40:] if int(row["open_time"]) not in selected_ids]
    if not baseline:
        return None
    return _ratio(sum(float(row["volume"]) for row in selected) / len(selected), sum(baseline) / len(baseline))


def _impulse(facts: dict[str, Any], timeline: dict[str, Any]) -> dict[str, Any] | None:
    rows = facts.get("confirmed_bars", [])
    atr = _finite((facts.get("atr14") or {}).get("value"))
    if len(rows) < 6 or not atr:
        return None
    source = timeline.get("impulse") or {}
    start_ts = source.get("start") or source.get("timestamp")
    extreme_ts = source.get("extreme_time")
    direction = timeline.get("direction") if source else None
    segment = [row for row in rows if start_ts is not None and int(row["open_time"]) >= int(start_ts)
               and (extreme_ts is None or int(row["open_time"]) <= int(extreme_ts))]
    if not segment:
        # A bounded fallback catches a recent OHLCV impulse even when the older
        # breakout timeline has no eligible range boundary.
        segment = rows[-12:]
        start = float(segment[0]["open"])
        up = max(float(row["high"]) for row in segment) - start
        down = start - min(float(row["low"]) for row in segment)
        direction = "UP" if up >= down else "DOWN"
        extreme_index = max(range(len(segment)), key=lambda i: float(segment[i]["high"])) if direction == "UP" else min(
            range(len(segment)), key=lambda i: float(segment[i]["low"])
        )
        segment = segment[:extreme_index + 1]
    if len(segment) < 2:
        return None
    start_price = float(segment[0]["open"])
    if direction == "UP":
        end_price = max(float(row["high"]) for row in segment)
        end_row = max(segment, key=lambda row: float(row["high"]))
        displacement = end_price - start_price
    else:
        end_price = min(float(row["low"]) for row in segment)
        end_row = min(segment, key=lambda row: float(row["low"]))
        displacement = start_price - end_price
    normalized = displacement / atr
    if normalized < 1.0:
        return None
    total_range = max(float(row["high"]) for row in segment) - min(float(row["low"]) for row in segment)
    signed_close = float(end_row["close"]) - start_price
    if direction == "DOWN":
        signed_close = -signed_close
    return {
        "state": f"IMPULSE_{direction}", "direction": direction,
        "start": int(segment[0]["open_time"]), "end": int(end_row["close_time"]),
        "start_price": start_price, "end_price": end_price,
        "price_displacement_pct": displacement / start_price * 100 if start_price else None,
        "atr_normalized_displacement": normalized,
        "impulse_volume_ratio": _volume_ratio(rows, segment), "bars": len(segment),
        "close_efficiency": signed_close / total_range if total_range else None,
    }


def _pullback(rows: list[dict[str, Any]], impulse: dict[str, Any] | None, atr: float | None) -> dict[str, Any] | None:
    if not impulse or not atr:
        return None
    after = [row for row in rows if int(row["open_time"]) >= int(impulse["end"])]
    if not after:
        return None
    displacement = abs(float(impulse["end_price"]) - float(impulse["start_price"]))
    if impulse["direction"] == "UP":
        extreme = min(float(row["low"]) for row in after)
        depth = max(0.0, float(impulse["end_price"]) - extreme)
    else:
        extreme = max(float(row["high"]) for row in after)
        depth = max(0.0, extreme - float(impulse["end_price"]))
    fraction = depth / displacement if displacement else None
    if fraction is None or fraction < .05:
        return None
    classification = "SHALLOW" if fraction <= .382 else "NORMAL" if fraction <= .618 else "DEEP"
    return {
        "state": f"{classification}_PULLBACK", "classification": classification,
        "recent_impulse_high": impulse["end_price"] if impulse["direction"] == "UP" else None,
        "recent_impulse_low": impulse["end_price"] if impulse["direction"] == "DOWN" else None,
        "pullback_extreme": extreme, "pullback_depth_pct": fraction * 100,
        "pullback_depth_atr": depth / atr,
    }


def _compression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 16:
        return {"state": "NOT_AVAILABLE", "range_compression_ratio": None}
    recent, previous, broad = rows[-5:], rows[-15:-5], rows[-20:]
    recent_range = max(float(row["high"]) for row in recent) - min(float(row["low"]) for row in recent)
    previous_range = max(float(row["high"]) for row in previous) - min(float(row["low"]) for row in previous)
    ratio = recent_range / previous_range if previous_range else None
    broad_low, broad_high = min(float(row["low"]) for row in broad), max(float(row["high"]) for row in broad)
    position = (float(rows[-1]["close"]) - broad_low) / (broad_high - broad_low) if broad_high > broad_low else .5
    higher_lows = sum(float(b["low"]) >= float(a["low"]) for a, b in zip(recent, recent[1:]))
    lower_highs = sum(float(b["high"]) <= float(a["high"]) for a, b in zip(recent, recent[1:]))
    compressed = ratio is not None and ratio <= .68 and (higher_lows >= 2 or lower_highs >= 2)
    state = "NO_COMPRESSION"
    if compressed:
        state = "HIGH_LEVEL_COMPRESSION" if position >= .65 else "LOW_LEVEL_COMPRESSION" if position <= .35 else "RANGE_COMPRESSION"
    return {"state": state, "recent_range": recent_range, "previous_range": previous_range,
            "range_compression_ratio": ratio, "higher_lows": higher_lows,
            "lower_highs": lower_highs, "price_position": position}


def _momentum(facts: dict[str, Any], drawdown_atr: float | None, pullback: dict[str, Any] | None) -> dict[str, Any]:
    history = [float(value) for value in facts.get("recent_rsi_values", []) if value is not None]
    current = _finite((facts.get("rsi14") or {}).get("value"))
    prior = history[-6] if len(history) >= 6 else (max(history[:-1]) if len(history) > 1 else None)
    change = current - prior if current is not None and prior is not None else None
    state = "MOMENTUM_UNAVAILABLE"
    if current is not None:
        if prior is not None and prior >= 65 and 40 <= current <= 60 and change <= -8:
            state = "MOMENTUM_RESET"
        elif prior is not None and change <= -8:
            state = "MOMENTUM_COOLING"
        elif prior is not None and change >= 6 and current >= 55:
            state = "MOMENTUM_REACCELERATING"
        elif current >= 65 or current <= 35:
            state = "MOMENTUM_EXPANDING"
        else:
            state = "MOMENTUM_STABLE"
    resilient = state == "MOMENTUM_RESET" and (
        (drawdown_atr is not None and drawdown_atr <= 1.0)
        or bool(pullback and pullback.get("classification") == "SHALLOW")
    )
    return {"state": "PRICE_RESILIENT_MOMENTUM_RESET" if resilient else state,
            "base_state": state, "rsi": current, "rsi_change": change,
            "price_drawdown_atr": drawdown_atr, "price_resilient": resilient}


def _volume(rows: list[dict[str, Any]], facts: dict[str, Any], impulse: dict[str, Any] | None) -> dict[str, Any]:
    current_ratio = _finite((facts.get("volume_ratio") or {}).get("value"))
    impulse_ratio = _finite((impulse or {}).get("impulse_volume_ratio"))
    post = [row for row in rows if impulse and int(row["open_time"]) >= int(impulse["end"])]
    post_ratio = _volume_ratio(rows, post[-5:]) if post else None
    rejection = False
    for row in rows[-5:]:
        span = float(row["high"]) - float(row["low"])
        if not span:
            continue
        upper_wick = (float(row["high"]) - max(float(row["open"]), float(row["close"]))) / span
        close_position = (float(row["close"]) - float(row["low"])) / span
        one_ratio = _volume_ratio(rows, [row])
        rejection = rejection or bool(one_ratio is not None and one_ratio >= 1.5 and upper_wick >= .4 and close_position <= .45)
    states = []
    if impulse_ratio is not None and impulse_ratio >= 1.25:
        states.append("IMPULSE_VOLUME_EXPANSION")
    if impulse_ratio is not None and impulse_ratio >= 1.15 and post_ratio is not None and post_ratio <= .8:
        states.append("POST_IMPULSE_VOLUME_CONTRACTION")
    if rejection:
        states.append("HIGH_VOLUME_REJECTION")
    if not states:
        states.append("VOLUME_NORMAL" if current_ratio is not None else "VOLUME_UNAVAILABLE")
    return {"state": states[0], "states": states, "current_ratio": current_ratio,
            "impulse_ratio": impulse_ratio, "post_impulse_ratio": post_ratio,
            "high_volume_rejection": rejection}


def build_timeframe_intelligence(facts: dict[str, Any], timeline: dict[str, Any],
                                 swings: list[dict[str, Any]]) -> dict[str, Any]:
    rows = facts.get("confirmed_bars", [])
    close = _finite(facts.get("confirmed_close"))
    atr = _finite((facts.get("atr14") or {}).get("value"))
    recent = rows[-20:]
    local = rows[-12:]
    local_high = max((float(row["high"]) for row in local), default=None)
    local_low = min((float(row["low"]) for row in local), default=None)
    swing_high = next((float(item["price"]) for item in reversed(swings) if item["kind"] == "HIGH"), None)
    swing_low = next((float(item["price"]) for item in reversed(swings) if item["kind"] == "LOW"), None)
    recent_return = (close / float(recent[0]["open"]) - 1) * 100 if close is not None and recent else None
    peak = max((float(row["high"]) for row in recent), default=None)
    drawdown = (peak - close) if peak is not None and close is not None else None
    drawdown_pct = drawdown / peak * 100 if drawdown is not None and peak else None
    drawdown_atr = drawdown / atr if drawdown is not None and atr else None
    ma_distances = {name: _finite(item.get("value")) for name, item in facts.get("price_to_ma_distance", {}).items()
                    if name in {"ema20", "ma20", "ma60", "ma200"}}
    ema_distance = abs((close - _finite((facts.get("moving_averages", {}).get("ema20") or {}).get("value")))) if (
        close is not None and _finite((facts.get("moving_averages", {}).get("ema20") or {}).get("value")) is not None) else None
    normalized_extension = ema_distance / atr if ema_distance is not None and atr else None
    extension = "HIGHLY_EXTENDED" if normalized_extension is not None and normalized_extension >= 3 else (
        "EXTENDED" if normalized_extension is not None and normalized_extension >= 1.5 else
        "NORMAL" if normalized_extension is not None else "NOT_AVAILABLE")
    impulse = _impulse(facts, timeline)
    pullback = _pullback(rows, impulse, atr)
    compression = _compression(rows)
    momentum = _momentum(facts, drawdown_atr, pullback)
    volume = _volume(rows, facts, impulse)
    phase_map = {
        "BREAKOUT_ATTEMPT": "BREAKOUT_TEST", "BREAKOUT_CONFIRMED": "BREAKOUT_ACCEPTANCE",
        "RETEST": "RETEST", "CONTINUATION": "TREND_CONTINUATION",
        "FAILED_BREAKOUT": "FAILED_BREAKOUT", "POST_BREAKOUT_PULLBACK": "PULLBACK",
    }
    tactical = phase_map.get(str(timeline.get("current_phase")))
    if pullback:
        tactical = pullback["state"]
    elif compression["state"] in {"HIGH_LEVEL_COMPRESSION", "LOW_LEVEL_COMPRESSION"}:
        tactical = compression["state"]
    elif impulse:
        tactical = impulse["state"]
    tactical = tactical or "NO_CLEAR_TACTICAL_STATE"
    supply = bool(volume["high_volume_rejection"])
    top_confirmed = supply and any(item.get("classification") == "LH" for item in swings[-4:]) and any(
        item.get("classification") == "LL" for item in swings[-4:])
    return {
        "version": AI_DETERMINISTIC_INTELLIGENCE_VERSION,
        "role": TIMEFRAME_ROLES[facts["timeframe"]], "state": tactical,
        "current_price": close, "local_high": local_high, "local_low": local_low,
        "recent_swing_high": swing_high, "recent_swing_low": swing_low,
        "recent_return_pct": recent_return, "recent_drawdown_pct": drawdown_pct,
        "ma_distances_pct": ma_distances, "atr_normalized_extension": normalized_extension,
        "extension_state": extension, "momentum": momentum,
        "tactical": {"state": tactical, "impulse": impulse, "pullback": pullback,
                     "compression": compression, "supply_evidence": supply,
                     "top_confirmed": top_confirmed},
        "volume": volume,
    }


def build_multi_timeframe_intelligence(structures: list[dict[str, Any]]) -> dict[str, Any]:
    by_tf = {item["timeframe"]: item.get("deterministic_intelligence", {}) for item in structures}
    states = {tf: value.get("state", "NO_CLEAR_TACTICAL_STATE") for tf, value in by_tf.items()}
    extensions = {tf: value.get("extension_state", "NOT_AVAILABLE") for tf, value in by_tf.items()}
    tactical = by_tf.get("15m", {}).get("tactical", {}).get("state")
    setup_momentum = by_tf.get("1H", {}).get("momentum", {}).get("state")
    higher_extended = [tf for tf in ("4H", "1D", "1W") if extensions.get(tf) in {"EXTENDED", "HIGHLY_EXTENDED"}]
    conflicts = []
    if tactical in {"IMPULSE_DOWN", "DEEP_PULLBACK", "STRUCTURE_WEAKENING"} and higher_extended:
        conflicts.append("TACTICAL_WEAKNESS_INSIDE_HIGHER_TIMEFRAME_EXTENSION")
    if setup_momentum in {"MOMENTUM_COOLING", "MOMENTUM_RESET", "PRICE_RESILIENT_MOMENTUM_RESET"} and higher_extended:
        conflicts.append("SETUP_COOLING_WHILE_HIGHER_TIMEFRAMES_EXTENDED")
    alignment = "CONFLICTED" if conflicts else "ALIGNED" if len(set(states.values())) == 1 else "MIXED"
    return {"intelligence_version": AI_DETERMINISTIC_INTELLIGENCE_VERSION,
            "timeframe_states": states, "extension_states": extensions,
            "alignment": alignment, "conflicts": conflicts,
            "dominant_context": "HIGHER_TIMEFRAME_EXTENSION" if higher_extended else "TACTICAL_STRUCTURE",
            "higher_timeframes_extended": higher_extended}
