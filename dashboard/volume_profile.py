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


def calculate_trade_volume_profile(rows: list[dict[str, Any]], bins: int = 80, value_area: float = 0.70) -> dict[str, Any]:
    """Build VPVR from actual trade-price notional, never candle-range allocation."""
    trades = [row for row in rows if float(row.get("buy_notional") or 0) + float(row.get("sell_notional") or 0) > 0]
    if not trades or bins < 2:
        return {"available": False, "reason": "insufficient_trade_coverage"}
    low, high = min(float(row["price"]) for row in trades), max(float(row["price"]) for row in trades)
    if high <= low:
        return {"available": False, "reason": "flat_trade_range"}
    width = (high - low) / bins
    profile = [{"buy": 0.0, "sell": 0.0, "trades": 0} for _ in range(bins)]
    for row in trades:
        index = max(0, min(bins - 1, int((float(row["price"]) - low) / width)))
        profile[index]["buy"] += float(row.get("buy_notional") or 0)
        profile[index]["sell"] += float(row.get("sell_notional") or 0)
        profile[index]["trades"] += int(row.get("trade_count") or 0)
    volumes = [item["buy"] + item["sell"] for item in profile]
    total = sum(volumes)
    if total <= 0:
        return {"available": False, "reason": "no_trade_notional"}
    poc_index = max(range(bins), key=volumes.__getitem__)
    lower = upper = poc_index
    covered = volumes[poc_index]
    target = total * max(0.5, min(value_area, 0.95))
    while covered < target and (lower > 0 or upper < bins - 1):
        lower_volume = volumes[lower - 1] if lower else -1.0
        upper_volume = volumes[upper + 1] if upper < bins - 1 else -1.0
        if upper_volume >= lower_volume:
            upper += 1
            covered += volumes[upper]
        else:
            lower -= 1
            covered += volumes[lower]
    display = []
    for index, item in enumerate(profile):
        if not (item["buy"] or item["sell"]):
            continue
        display.append({"price_low": round(low + index * width, 6), "price_high": round(low + (index + 1) * width, 6), "volume": round(volumes[index], 2), "delta": round(item["buy"] - item["sell"], 2), "trades": item["trades"]})
    return {"available": True, "method": "trade_price_notional_v1", "bins": bins, "poc": round(low + (poc_index + 0.5) * width, 6), "vah": round(low + (upper + 1) * width, 6), "val": round(low + lower * width, 6), "value_area_pct": round(covered / total * 100, 2), "profile_low": round(low, 6), "profile_high": round(high, 6), "total_notional": round(total, 2), "profile": display}
