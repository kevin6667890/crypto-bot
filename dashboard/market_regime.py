"""Deterministic, causal market-regime classification."""

from __future__ import annotations

import math
from typing import Any

REGIME_VERSION = "regime-v1"
REGIMES = ("Bull Trend", "Bear Trend", "High-Volatility Range", "Low-Volatility Range", "Transition", "Unknown")


def classify_regime(close: float | None, indicators: dict[str, Any], history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Classify using only values known at the decision timestamp.

    ATR percentile and realized volatility are optional. When history is supplied it
    must already be truncated as-of the decision; this function never looks ahead.
    """
    fast, slow, atr = indicators.get("fast_ma"), indicators.get("slow_ma"), indicators.get("atr")
    if close is None or fast is None or slow is None or not float(slow):
        return {"name": "Unknown", "version": REGIME_VERSION, "features": {}, "reason": "MA structure unavailable"}
    close, fast, slow = float(close), float(fast), float(slow)
    distance = (close - slow) / slow
    atr_pct = float(atr) / close if atr is not None and close else None
    closes = [float(row["close"]) for row in (history or []) if row.get("close") is not None]
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    realized = math.sqrt(sum(value * value for value in returns[-20:]) / min(20, len(returns))) if returns else None
    atr_samples = [float(row["atr"]) / float(row["close"]) for row in (history or []) if row.get("atr") is not None and row.get("close")]
    atr_percentile = (sum(value <= atr_pct for value in atr_samples) / len(atr_samples) * 100) if atr_pct is not None and atr_samples else None
    strength = abs(fast - slow) / slow
    if close > fast > slow and distance > 0.005:
        name, reason = "Bull Trend", "price > fast MA > slow MA"
    elif close < fast < slow and distance < -0.005:
        name, reason = "Bear Trend", "price < fast MA < slow MA"
    elif strength < 0.003 and ((atr_percentile is not None and atr_percentile >= 70) or (atr_pct is not None and atr_pct >= 0.012)):
        name, reason = "High-Volatility Range", "compressed MA structure with elevated volatility"
    elif strength < 0.003 and ((atr_percentile is not None and atr_percentile <= 30) or (atr_pct is not None and atr_pct < 0.006)):
        name, reason = "Low-Volatility Range", "compressed MA structure with subdued volatility"
    else:
        name, reason = "Transition", "mixed price and MA structure"
    return {"name": name, "version": REGIME_VERSION, "features": {"price_distance_ma200": distance, "ma_spread": strength, "atr_pct": atr_pct, "atr_percentile": atr_percentile, "realized_volatility": realized}, "reason": reason}
