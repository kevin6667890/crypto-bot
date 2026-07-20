"""Causal OHLCV volume-profile calculations for the decision workspace."""
from __future__ import annotations

from typing import Any


def calculate_volume_profile(candles: list[dict[str, Any]], bins: int = 48, value_area: float = 0.70) -> dict[str, Any]:
    """Approximate VPVR by distributing each completed candle's volume over its range."""
    rows = [row for row in candles if all(row.get(key) is not None for key in ("low", "high", "volume"))]
    if len(rows) < 20 or bins < 2:
        return {"available": False, "reason": "insufficient_confirmed_candles"}
    low, high = min(float(row["low"]) for row in rows), max(float(row["high"]) for row in rows)
    if high <= low:
        return {"available": False, "reason": "flat_price_range"}
    width = (high - low) / bins
    volumes = [0.0] * bins
    for row in rows:
        candle_low, candle_high, volume = float(row["low"]), float(row["high"]), max(0.0, float(row["volume"]))
        start = max(0, min(bins - 1, int((candle_low - low) / width)))
        end = max(0, min(bins - 1, int((candle_high - low) / width)))
        if end < start:
            start, end = end, start
        allocation = volume / (end - start + 1)
        for index in range(start, end + 1):
            volumes[index] += allocation
    total = sum(volumes)
    if total <= 0:
        return {"available": False, "reason": "no_volume"}
    poc_index = max(range(bins), key=volumes.__getitem__)
    lower = upper = poc_index
    covered = volumes[poc_index]
    target = total * max(0.5, min(value_area, 0.95))
    while covered < target and (lower > 0 or upper < bins - 1):
        lower_volume = volumes[lower - 1] if lower > 0 else -1.0
        upper_volume = volumes[upper + 1] if upper < bins - 1 else -1.0
        if upper_volume >= lower_volume:
            upper += 1
            covered += volumes[upper]
        else:
            lower -= 1
            covered += volumes[lower]
    price = lambda index: round(low + (index + 0.5) * width, 6)
    return {
        "available": True, "method": "ohlcv_uniform_range_v1", "lookback_bars": len(rows), "bins": bins,
        "poc": price(poc_index), "vah": round(low + (upper + 1) * width, 6), "val": round(low + lower * width, 6),
        "value_area_pct": round(covered / total * 100, 2), "profile_low": round(low, 6), "profile_high": round(high, 6),
        "total_volume": round(total, 6), "start_ts": int(rows[0].get("ts") or 0),
        "end_ts": int(rows[-1].get("candle_close_ts") or rows[-1].get("ts") or 0),
    }
