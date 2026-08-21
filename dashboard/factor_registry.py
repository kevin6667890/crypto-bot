"""Versioned, causal factor catalogue for deterministic strategy programs.

The registry is deliberately a view over ``discovery_features``; it is not a
second indicator engine.  Entries marked unavailable are documentation only and
can never be selected by the grammar.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .discovery_features import FEATURE_VERSION

FACTOR_REGISTRY_VERSION = "factor-registry-v1"

@dataclass(frozen=True)
class FactorMetadata:
    id: str
    version: str
    lookback: int
    timeframe: str
    output: str
    warmup: int
    availability: str = "AVAILABLE"
    feature_key: str | None = None

def _factor(id: str, key: str | None, lookback: int = 1, output: str = "number",
            availability: str = "AVAILABLE") -> FactorMetadata:
    return FactorMetadata(id, FACTOR_REGISTRY_VERSION + "/" + FEATURE_VERSION,
                          lookback, "execution", output, lookback, availability, key)

# Only entries with feature_key are executable.  CVD/OI deliberately remain
# excluded until complete historical coverage is proven.
FACTORS = {
    "close": _factor("close", "_close"), "open": _factor("open", "_open"),
    "return_1": _factor("return_1", "candle_return", 2),
    "sma_20": _factor("sma_20", "sma_20", 20), "ema_20": _factor("ema_20", "ema_20", 20),
    "ma_slope": _factor("ma_slope", "sma_20_slope", 24),
    "ma_distance": _factor("ma_distance", "distance_ema20_atr", 20),
    "atr_pct": _factor("atr_pct", "atr_pct", 15), "rsi_14": _factor("rsi_14", "rsi", 15),
    "volume_ratio": _factor("volume_ratio", "volume_ratio", 21),
    "body_range": _factor("body_range", "body_range_ratio"),
    "rolling_high": _factor("rolling_high", "recent_high", 20),
    "rolling_low": _factor("rolling_low", "recent_low", 20),
    "breakout_distance": _factor("breakout_distance", "breakout_distance", 20),
    "pullback_distance": _factor("pullback_distance", "distance_ema20_atr", 20),
    "hh_hl_structure": _factor("hh_hl_structure", None, 20, "state", "UNAVAILABLE_NO_CAUSAL_FEATURE"),
    "support_resistance_distance": _factor("support_resistance_distance", None, 20, "number", "UNAVAILABLE_NO_CONFIRMED_LEVEL_FEATURE"),
    "cvd": _factor("cvd", None, 1, "number", "UNAVAILABLE_INCOMPLETE_HISTORY"),
    "open_interest": _factor("open_interest", None, 1, "number", "UNAVAILABLE_INCOMPLETE_HISTORY"),
}

def executable_factors() -> tuple[FactorMetadata, ...]:
    return tuple(item for item in FACTORS.values() if item.availability == "AVAILABLE" and item.feature_key)

def metadata() -> dict[str, Any]:
    return {"registry_version": FACTOR_REGISTRY_VERSION, "feature_version": FEATURE_VERSION,
            "factors": [asdict(item) for item in FACTORS.values()]}

def value(factor_id: str, candle: Mapping[str, Any], feature: Mapping[str, Any]) -> float | None:
    spec = FACTORS.get(factor_id)
    if not spec or spec.availability != "AVAILABLE" or not spec.feature_key:
        return None
    if factor_id == "breakout_distance":
        level = feature.get("recent_high")
        close = candle.get("close")
        return (float(close) / float(level) - 1) if level not in (None, 0) and close is not None else None
    raw = candle.get(spec.feature_key.removeprefix("_")) if spec.feature_key.startswith("_") else feature.get(spec.feature_key)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
