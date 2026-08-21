from __future__ import annotations

import math
import statistics
from typing import Any

from dashboard.discovery_features import FEATURE_VERSION, build_features
from dashboard.market_context_v2 import STOCH_RSI_VERSION, stoch_rsi_series

from .canonical import finite_or_none
from .quality import derive_weekly, input_fingerprint, normalize_candles
from .versions import AI_MARKET_FACTS_VERSION, SUPPORTED_TIMEFRAMES


LOOKBACKS = {
    "moving_averages": [20, 30, 60, 200], "ema": 20, "slope_bars": 4,
    "atr": 14, "rsi": 14, "stoch_rsi": [14, 3, 3], "bollinger": 20,
    "rolling_volatility": 20, "volume": 20, "percentile": 100, "rolling_extreme": 20,
}


def _rank(values: list[float], current: float, minimum: int = 20) -> float | None:
    return (100 * sum(value <= current for value in values) / len(values)) if len(values) >= minimum else None


def _metric(value: Any, timestamp: int | None, lookback: Any, *, version: str = AI_MARKET_FACTS_VERSION) -> dict[str, Any]:
    finite = finite_or_none(value)
    return {"value": finite, "status": "AVAILABLE" if finite is not None else "NOT_AVAILABLE",
            "lookback": lookback, "warmup_complete": finite is not None,
            "last_bar_timestamp": timestamp, "calculation_version": version}


def _volume_regime(rows: list[dict[str, Any]], features: list[dict[str, Any]],
                   timestamp: int | None, quality: dict[str, Any]) -> dict[str, Any]:
    volumes = [row["volume"] for row in rows]
    ratio = features[-1].get("volume_ratio") if features else None
    window = volumes[-100:]
    percentile = _rank(window, volumes[-1]) if volumes else None
    median = statistics.median(volumes[-20:]) if len(volumes) >= 20 else None
    recent = volumes[-5:]
    trend = None
    if len(recent) == 5 and recent[0]:
        trend = (recent[-1] / recent[0] - 1) * 100
    classification = "NOT_AVAILABLE"
    expansion_persistence = (sum(value > median*1.10 for value in recent) >= 3) if median is not None else False
    if ratio is not None and percentile is not None and median is not None and quality["status"] not in {"INVALID", "MISSING"}:
        if ratio < .55 and percentile <= 20:
            classification = "VERY_LOW"
        elif ratio < .85 and (trend or 0) < -10:
            classification = "CONTRACTING"
        elif ratio >= 2 and percentile >= 95:
            classification = "CLIMACTIC"
        elif ratio >= 1.25 and percentile >= 70 and (trend or 0) > 5 and expansion_persistence:
            classification = "EXPANDING"
        else:
            classification = "NORMAL"
    return {
        "classification": classification, "current_volume": volumes[-1] if volumes else None,
        "ratio": finite_or_none(ratio), "percentile": finite_or_none(percentile),
        "rolling_median": finite_or_none(median), "trend": finite_or_none(trend),
        "evidence": ["ratio+percentile+five-bar trend; no single-bar expansion inference"],
        "last_bar_timestamp": timestamp, "calculation_version": AI_MARKET_FACTS_VERSION,
    }


def build_timeframe_facts(rows: list[dict[str, Any]], instrument: str, timeframe: str,
                          decision_time: int, *, pre_normalized: bool = False,
                          supplied_quality: dict[str, Any] | None = None) -> dict[str, Any]:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    if pre_normalized:
        confirmed, live = list(rows), None
        quality = supplied_quality or {}
    else:
        confirmed, live, quality = normalize_candles(rows, instrument, timeframe, decision_time)
    timestamp = confirmed[-1]["close_time"] if confirmed else None
    features = build_features(confirmed, {"ma_periods": [20, 30, 60, 200], "atr_period": 14,
                                          "bb_period": 20, "rsi_period": 14, "volume_period": 20,
                                          "include_body_range_ratio": False,
                                          "include_recent_extremes": False}) if confirmed else []
    latest = features[-1] if features else {}
    closes = [row["close"] for row in confirmed]
    rsi_series = [item.get("rsi") for item in features]
    stoch = stoch_rsi_series(rsi_series)[-1] if rsi_series else {}
    slopes = {}
    for period in (20, 30, 60, 200):
        key = f"sma_{period}"
        old = features[-5].get(key) if len(features) >= 5 else None
        current = latest.get(key)
        slopes[f"ma{period}"] = _metric((current / old - 1) * 100 if current is not None and old else None,
                                         timestamp, {"ma": period, "slope_bars": 4})
    old_ema = features[-5].get("ema_20") if len(features) >= 5 else None
    slopes["ema20"] = _metric((latest.get("ema_20") / old_ema - 1) * 100
                               if latest.get("ema_20") is not None and old_ema else None,
                              timestamp, {"ema": 20, "slope_bars": 4})
    close = closes[-1] if closes else None
    ma = {f"ma{p}": _metric(latest.get(f"sma_{p}"), timestamp, p) for p in (20, 30, 60, 200)}
    ma["ema20"] = _metric(latest.get("ema_20") if len(confirmed) >= 20 else None, timestamp, 20)
    distances = {name: _metric((close / item["value"] - 1) * 100 if close and item["value"] else None,
                                timestamp, item["lookback"]) for name, item in ma.items()}
    available_ma = [(name, item["value"]) for name, item in ma.items() if name != "ema20" and item["value"] is not None]
    ordering = "NOT_AVAILABLE"
    if len(available_ma) == 4:
        vals = [value for _, value in available_ma]
        ordering = "BULLISH" if vals == sorted(vals, reverse=True) else "BEARISH" if vals == sorted(vals) else "MIXED"
    returns = [math.log(b/a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    realized = statistics.pstdev(returns[-20:]) * math.sqrt(20) * 100 if len(returns) >= 20 else None
    prior = confirmed[-21:-1]
    rolling_high = max((row["high"] for row in prior), default=None)
    rolling_low = min((row["low"] for row in prior), default=None)
    widths = [float(item["bb_width"]) * 100 for item in features[-100:] if item.get("bb_width") is not None]
    atr_pcts = [float(item["atr_pct"]) * 100 for item in features[-100:] if item.get("atr_pct") is not None]
    row = confirmed[-1] if confirmed else None
    candle_range = row["high"] - row["low"] if row else 0
    facts = {
        "version": AI_MARKET_FACTS_VERSION, "source_feature_version": FEATURE_VERSION,
        "stoch_rsi_version": STOCH_RSI_VERSION, "instrument": instrument, "timeframe": timeframe,
        "decision_time": decision_time, "confirmed_bars": confirmed, "confirmed_close": close,
        "latest_confirmed_bar_timestamp": timestamp, "live_observation": live,
        "moving_averages": ma, "slopes": slopes, "price_to_ma_distance": distances,
        "ma_ordering": ordering,
        "atr14": _metric(latest.get("atr"), timestamp, 14),
        "atr_percentage": _metric(float(latest["atr_pct"])*100 if latest.get("atr_pct") else None, timestamp, 14),
        "rsi14": _metric(latest.get("rsi"), timestamp, 14),
        # Internal bounded history used by deterministic momentum-state rules.
        # This is not provider prose and contains confirmed bars only.
        "recent_rsi_values": [finite_or_none(item) for item in rsi_series[-20:]],
        "stoch_rsi": {name: _metric(stoch.get(name), timestamp, [14, 3, 3], version=STOCH_RSI_VERSION)
                      for name in ("stoch_rsi", "stoch_rsi_k", "stoch_rsi_d")},
        "bollinger": {
            "upper": _metric(latest.get("bb_upper"), timestamp, 20),
            "mid": _metric(latest.get("bb_mid"), timestamp, 20),
            "lower": _metric(latest.get("bb_lower"), timestamp, 20),
            "bandwidth": _metric(float(latest["bb_width"])*100 if latest.get("bb_width") is not None else None, timestamp, 20),
            "bandwidth_percentile": _metric(_rank(widths, widths[-1]) if widths else None, timestamp, 100),
        },
        "rolling_volatility": _metric(realized, timestamp, 20),
        "atr_percentile": _metric(_rank(atr_pcts, atr_pcts[-1]) if atr_pcts else None, timestamp, 100),
        "volume_ratio": _metric(latest.get("volume_ratio"), timestamp, 20),
        "volume_percentile": _metric(_rank([r["volume"] for r in confirmed[-100:]], row["volume"]) if row else None, timestamp, 100),
        "volume_regime": _volume_regime(confirmed, features, timestamp, quality),
        "body_percentage": _metric(abs(row["close"]-row["open"])/candle_range*100 if row and candle_range else None, timestamp, 1),
        "upper_wick_percentage": _metric((row["high"]-max(row["open"], row["close"]))/candle_range*100 if row and candle_range else None, timestamp, 1),
        "lower_wick_percentage": _metric((min(row["open"], row["close"])-row["low"])/candle_range*100 if row and candle_range else None, timestamp, 1),
        "rolling_high": _metric(rolling_high, timestamp, 20),
        "rolling_low": _metric(rolling_low, timestamp, 20),
        "distance_to_rolling_high": _metric((close/rolling_high-1)*100 if close and rolling_high else None, timestamp, 20),
        "distance_to_rolling_low": _metric((close/rolling_low-1)*100 if close and rolling_low else None, timestamp, 20),
        "quality": quality, "lookbacks": LOOKBACKS,
    }
    facts["input_fingerprint"] = input_fingerprint(confirmed, quality)
    return facts


def build_multi_timeframe_facts(datasets: dict[str, list[dict[str, Any]]], instrument: str,
                                decision_time: int) -> dict[str, dict[str, Any]]:
    result = {tf: build_timeframe_facts(datasets.get(tf, []), instrument, tf, decision_time)
              for tf in ("15m", "1H", "4H", "1D")}
    native_weekly = datasets.get("1W", [])
    if native_weekly:
        result["1W"] = build_timeframe_facts(native_weekly, instrument, "1W", decision_time)
    else:
        daily = result["1D"]["confirmed_bars"]
        weekly, weekly_quality = derive_weekly(daily, instrument, decision_time)
        result["1W"] = build_timeframe_facts(weekly, instrument, "1W", decision_time,
                                             pre_normalized=True, supplied_quality=weekly_quality)
    return result
