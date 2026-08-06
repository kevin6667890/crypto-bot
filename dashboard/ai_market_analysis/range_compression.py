from __future__ import annotations

import statistics
from typing import Any

from dashboard.discovery_features import build_features

from .canonical import identity
from .versions import AI_RANGE_COMPRESSION_VERSION


RANGE_PARAMETERS = {
    "min_lookback": 20, "max_lookback": 80, "max_width_atr": 10.0,
    "min_touches_each_side": 2, "boundary_zone_fraction": .15,
    "min_inside_ratio": .80, "max_normalized_slope": .08,
}
COMPRESSION_PARAMETERS = {
    "lookback": 100, "minimum_duration": 5, "mature_duration": 8,
    "percentile_threshold": 35.0, "max_contraction_ratio": .72,
}


def _slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    n = len(values)
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    numerator = sum((i-mean_x)*(value-mean_y) for i, value in enumerate(values))
    denominator = sum((i-mean_x)**2 for i in range(n))
    return numerator / denominator if denominator else 0.0


def detect_range(candles: list[dict[str, Any]], timeframe: str, atr: float | None,
                 *, end_index: int | None = None) -> dict[str, Any] | None:
    end = len(candles) if end_index is None else end_index
    if atr is None or atr <= 0 or end < RANGE_PARAMETERS["min_lookback"]:
        return None
    best = None
    max_size = min(RANGE_PARAMETERS["max_lookback"], end)
    for size in range(RANGE_PARAMETERS["min_lookback"], max_size + 1):
        window = candles[end-size:end]
        high, low = max(row["high"] for row in window), min(row["low"] for row in window)
        width = high-low
        if width <= 0 or width/atr > RANGE_PARAMETERS["max_width_atr"]:
            continue
        zone = width * RANGE_PARAMETERS["boundary_zone_fraction"]
        upper = sum(row["high"] >= high-zone for row in window)
        lower = sum(row["low"] <= low+zone for row in window)
        inside = sum(low <= row["close"] <= high for row in window) / size
        closes = [row["close"] for row in window]
        normalized_slope = abs(_slope(closes)) / width
        midpoint = (high+low)/2
        crossings = sum((a-midpoint)*(b-midpoint) <= 0 for a, b in zip(closes, closes[1:]))
        if upper < 2 or lower < 2 or inside < .8 or normalized_slope > .08 or crossings < 2:
            continue
        score = inside + min(upper, 4)/10 + min(lower, 4)/10 + min(crossings, 6)/20 - normalized_slope
        payload = {"timeframe": timeframe, "start": window[0]["open_time"],
                   "end": window[-1]["close_time"], "high": high, "low": low}
        candidate = {
            **payload, "midpoint": midpoint, "width": width, "width_atr": width/atr,
            "upper_touches": upper, "lower_touches": lower, "bars_inside_ratio": inside,
            "slope": _slope(closes), "confidence": "HIGH" if score >= 1.55 else "MEDIUM",
            "source_bar_timestamps": [row["close_time"] for row in window],
            "invalidation": f"confirmed close outside [{low}, {high}]",
            "version": AI_RANGE_COMPRESSION_VERSION, "_score": score,
        }
        if best is None or candidate["_score"] > best["_score"]:
            best = candidate
    if best:
        best.pop("_score")
        best["range_id"] = identity("range", {key: best[key] for key in ("timeframe", "start", "end", "high", "low", "version")})
    return best


def detect_compression(candles: list[dict[str, Any]], timeframe: str,
                       volume_regime: str, range_fact: dict[str, Any] | None,
                       quality: dict[str, Any]) -> dict[str, Any]:
    if len(candles) < 40 or quality["status"] in {"INVALID", "MISSING", "GAP_AFFECTED"}:
        return {"state": "INSUFFICIENT_EVIDENCE", "version": AI_RANGE_COMPRESSION_VERSION}
    features = build_features(candles, {"ma_periods": [20], "atr_period": 14, "bb_period": 20,
                                        "rsi_period": 14, "volume_period": 20})
    atrs = [item["atr"] for item in features[-100:] if item.get("atr") is not None]
    widths = [item["bb_width"] for item in features[-100:] if item.get("bb_width") is not None]
    if len(atrs) < 20 or len(widths) < 20:
        return {"state": "INSUFFICIENT_EVIDENCE", "version": AI_RANGE_COMPRESSION_VERSION}
    rank = lambda values: 100 * sum(value <= values[-1] for value in values) / len(values)
    atr_rank, width_rank = rank(atrs), rank(widths)
    recent_range = max(r["high"] for r in candles[-10:])-min(r["low"] for r in candles[-10:])
    prior_range = max(r["high"] for r in candles[-30:-10])-min(r["low"] for r in candles[-30:-10])
    contraction = recent_range/prior_range if prior_range else None
    conditions = [atr_rank <= 35, width_rank <= 35, contraction is not None and contraction <= .72,
                  volume_regime in {"VERY_LOW", "CONTRACTING"}, range_fact is not None]
    evidence_count = sum(conditions)
    if evidence_count < 3:
        return {"state": "INSUFFICIENT_EVIDENCE", "atr_percentile": atr_rank,
                "bandwidth_percentile": width_rank, "contraction_ratio": contraction,
                "version": AI_RANGE_COMPRESSION_VERSION}
    duration = 8 if all(conditions[:3]) else 5
    window = candles[-duration:]
    payload = {"timeframe": timeframe, "start": window[0]["open_time"], "end": window[-1]["close_time"]}
    return {
        "compression_id": identity("compression", {**payload, "version": AI_RANGE_COMPRESSION_VERSION}),
        **payload, "duration_bars": duration, "price_low": min(r["low"] for r in window),
        "price_high": max(r["high"] for r in window), "atr_percentile": atr_rank,
        "bandwidth_percentile": width_rank, "volume_regime": volume_regime,
        "contraction_ratio": contraction, "state": "MATURE" if duration >= 8 else "BUILDING",
        "direction": "NONE", "evidence": [name for name, met in zip(
            ("low ATR percentile", "low bandwidth percentile", "range contraction", "volume contraction", "stable range"), conditions) if met],
        "counterevidence": [name for name, met in zip(
            ("ATR not compressed", "bandwidth not compressed", "range not contracted", "volume not contracted", "range unstable"), conditions) if not met],
        "confidence": "HIGH" if evidence_count >= 4 else "MEDIUM",
        "source_bar_timestamps": [row["close_time"] for row in window],
        "version": AI_RANGE_COMPRESSION_VERSION,
    }
