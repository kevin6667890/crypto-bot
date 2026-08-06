from __future__ import annotations

from typing import Any

from .canonical import identity
from .versions import AI_SWING_STRUCTURE_VERSION


def confirmed_swings(candles: list[dict[str, Any]], timeframe: str, *, left: int = 2,
                     right: int = 2, atr: float | None = None, limit: int = 12,
                     tolerance_pct: float = .0005) -> list[dict[str, Any]]:
    """Return pivots only after all right-hand bars have closed.

    The strict-left/non-strict-right rule exactly matches Market Context V2's
    confirmed-fractal-2x2-v1 semantics while exposing the complete bounded sequence.
    """
    swings: list[dict[str, Any]] = []
    last_by_kind: dict[str, float] = {}
    for pivot in range(left, len(candles) - right):
        row = candles[pivot]
        candidates: list[tuple[str, str]] = []
        if row["high"] > max(item["high"] for item in candles[pivot-left:pivot]) and row["high"] >= max(item["high"] for item in candles[pivot+1:pivot+right+1]):
            candidates.append(("HIGH", "high"))
        if row["low"] < min(item["low"] for item in candles[pivot-left:pivot]) and row["low"] <= min(item["low"] for item in candles[pivot+1:pivot+right+1]):
            candidates.append(("LOW", "low"))
        for kind, field in candidates:
            price = float(row[field])
            previous = last_by_kind.get(kind)
            tolerance = max(abs(price) * tolerance_pct, (atr or 0) * .05)
            label = "UNCLASSIFIED"
            if previous is not None:
                if abs(price - previous) <= tolerance:
                    label = "EQUAL_HIGH" if kind == "HIGH" else "EQUAL_LOW"
                elif kind == "HIGH":
                    label = "HH" if price > previous else "LH"
                else:
                    label = "HL" if price > previous else "LL"
            confirmed_at = int(candles[pivot + right]["close_time"])
            payload = {"timeframe": timeframe, "kind": kind, "pivot_time": int(row["open_time"]),
                       "confirmed_at": confirmed_at, "price": price, "left_strength": left,
                       "right_strength": right}
            swings.append({
                "swing_id": identity("swing", {**payload, "version": AI_SWING_STRUCTURE_VERSION}),
                **payload, "classification": label,
                "source_bar_timestamps": [int(item["close_time"]) for item in candles[pivot-left:pivot+right+1]],
                "quality": "VALID", "version": AI_SWING_STRUCTURE_VERSION,
            })
            last_by_kind[kind] = price
    return swings[-limit:]


def swing_summary(swings: list[dict[str, Any]]) -> str:
    highs = [item["classification"] for item in swings if item["kind"] == "HIGH" and item["classification"] != "UNCLASSIFIED"]
    lows = [item["classification"] for item in swings if item["kind"] == "LOW" and item["classification"] != "UNCLASSIFIED"]
    if highs and lows and highs[-1] == "HH" and lows[-1] == "HL":
        return "HH_HL"
    if highs and lows and highs[-1] == "LH" and lows[-1] == "LL":
        return "LH_LL"
    if any(item.startswith("EQUAL") for item in highs[-1:] + lows[-1:]):
        return "RANGE"
    return "MIXED" if highs or lows else "UNKNOWN"
