"""Shared deterministic strategy rules for paper trading and historical research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class StrategyParameters:
    fast_ma: int = 60
    slow_ma: int = 200
    ema_pullback_period: int = 20
    ema_pullback_distance: float = 0.0045
    rsi_period: int = 14
    rsi_min: float = 35.0
    rsi_max: float = 68.0
    minimum_volume_ratio: float = 1.0
    minimum_score: float = 75.0
    atr_period: int = 14
    stop_loss_atr_multiplier: float = 1.0
    risk_reward_ratio: float = 2.0
    trading_fee: float = 0.0005
    slippage: float = 0.0003
    cooldown_bars: int = 16
    enable_long: bool = True
    enable_short: bool = True
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    max_open_positions: int = 1


DEFAULT_PARAMETERS = asdict(StrategyParameters())
STRATEGY_PRESETS: dict[str, dict[str, Any]] = {
    "Conservative": {**DEFAULT_PARAMETERS, "minimum_score": 85.0, "minimum_volume_ratio": 1.2, "stop_loss_atr_multiplier": 1.3, "risk_reward_ratio": 2.5, "risk_per_trade": 0.005},
    "Balanced": DEFAULT_PARAMETERS.copy(),
    "Aggressive": {**DEFAULT_PARAMETERS, "minimum_score": 70.0, "minimum_volume_ratio": 0.85, "ema_pullback_distance": 0.007, "stop_loss_atr_multiplier": 0.9, "risk_reward_ratio": 1.7, "risk_per_trade": 0.02, "cooldown_bars": 8},
}


def validate_parameters(raw: dict[str, Any] | None) -> StrategyParameters:
    values = {**DEFAULT_PARAMETERS, **(raw or {})}
    integer_fields = ("fast_ma", "slow_ma", "ema_pullback_period", "rsi_period", "atr_period", "cooldown_bars", "max_open_positions")
    float_fields = ("ema_pullback_distance", "rsi_min", "rsi_max", "minimum_volume_ratio", "minimum_score", "stop_loss_atr_multiplier", "risk_reward_ratio", "trading_fee", "slippage", "initial_capital", "risk_per_trade")
    try:
        for key in integer_fields:
            values[key] = int(values[key])
        for key in float_fields:
            values[key] = float(values[key])
        values["enable_long"] = bool(values["enable_long"])
        values["enable_short"] = bool(values["enable_short"])
    except (TypeError, ValueError) as error:
        raise ValueError("Strategy parameters must be numeric where required.") from error

    ranges = {
        "fast_ma": (2, 300), "slow_ma": (10, 500), "ema_pullback_period": (2, 200),
        "ema_pullback_distance": (0.0001, 0.05), "rsi_period": (2, 100),
        "rsi_min": (0, 99), "rsi_max": (1, 100), "minimum_volume_ratio": (0.1, 10),
        "minimum_score": (0, 100), "atr_period": (2, 100),
        "stop_loss_atr_multiplier": (0.1, 10), "risk_reward_ratio": (0.2, 10),
        "trading_fee": (0, 0.02), "slippage": (0, 0.02), "cooldown_bars": (0, 1000),
        "initial_capital": (100, 100_000_000), "risk_per_trade": (0.0001, 0.1),
        "max_open_positions": (1, 10),
    }
    for key, (minimum, maximum) in ranges.items():
        if not minimum <= values[key] <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    if values["fast_ma"] >= values["slow_ma"]:
        raise ValueError("Fast MA must be smaller than Slow MA.")
    if values["rsi_min"] >= values["rsi_max"]:
        raise ValueError("RSI Min must be smaller than RSI Max.")
    if not values["enable_long"] and not values["enable_short"]:
        raise ValueError("At least one of Enable Long or Enable Short must be enabled.")
    return StrategyParameters(**values)


def _sma(values: list[float], period: int, index: int) -> float | None:
    if index + 1 < period:
        return None
    return sum(values[index - period + 1:index + 1]) / period


def calculate_indicators(candles: Iterable[dict[str, Any]], parameters: StrategyParameters) -> list[dict[str, float | None]]:
    """Calculate causal indicators; every row uses only that row and earlier rows."""
    rows = list(candles)
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    ema_value: float | None = None
    alpha = 2.0 / (parameters.ema_pullback_period + 1)
    output: list[dict[str, float | None]] = []
    for index, row in enumerate(rows):
        close = closes[index]
        ema_value = close if ema_value is None else close * alpha + ema_value * (1 - alpha)
        fast = _sma(closes, parameters.fast_ma, index)
        slow = _sma(closes, parameters.slow_ma, index)
        rsi = None
        if index >= parameters.rsi_period:
            changes = [closes[pos] - closes[pos - 1] for pos in range(index - parameters.rsi_period + 1, index + 1)]
            avg_gain = sum(max(change, 0.0) for change in changes) / parameters.rsi_period
            avg_loss = sum(max(-change, 0.0) for change in changes) / parameters.rsi_period
            rsi = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
        atr = None
        if index >= parameters.atr_period:
            true_ranges = []
            for pos in range(index - parameters.atr_period + 1, index + 1):
                previous_close = closes[pos - 1]
                true_ranges.append(max(float(rows[pos]["high"]) - float(rows[pos]["low"]), abs(float(rows[pos]["high"]) - previous_close), abs(float(rows[pos]["low"]) - previous_close)))
            atr = sum(true_ranges) / parameters.atr_period
        volume_ratio = None
        if index >= 20:
            baseline = sum(volumes[index - 20:index]) / 20
            volume_ratio = volumes[index] / baseline if baseline else 0.0
        output.append({"fast_ma": fast, "slow_ma": slow, "ema": ema_value, "rsi": rsi, "atr": atr, "volume_ratio": volume_ratio})
    return output


def score_rule_components(has_trend: bool, pullback: bool, momentum: bool, flow_available: bool, flow_aligned: bool) -> list[dict[str, Any]]:
    """Shared explainable point allocation used by live paper and historical engines."""
    return [
        {"key": "trend", "label": "Trend alignment", "points": 30 if has_trend else 8, "max": 30},
        {"key": "structure", "label": "MA structure", "points": 20 if has_trend else 6, "max": 20},
        {"key": "pullback", "label": "EMA pullback", "points": 20 if pullback else 5, "max": 20},
        {"key": "momentum", "label": "Volume + RSI", "points": 15 if momentum else 6, "max": 15},
        {"key": "flow", "label": "CVD alignment", "points": 15 if flow_available and flow_aligned else 5, "max": 15},
    ]


def evaluate_signal(candle: dict[str, Any], indicators: dict[str, float | None], parameters: StrategyParameters, flow_delta: float | None = None) -> dict[str, Any]:
    close = float(candle["close"])
    fast, slow, ema_value = indicators["fast_ma"], indicators["slow_ma"], indicators["ema"]
    rsi, atr, volume_ratio = indicators["rsi"], indicators["atr"], indicators["volume_ratio"]
    warmed = all(value is not None for value in (fast, slow, ema_value, rsi, atr, volume_ratio))
    if not warmed:
        return {"action": "WAIT", "bias": "WAIT", "score": 0, "warmed": False, "reason": "indicator warm-up incomplete"}
    assert fast is not None and slow is not None and ema_value is not None and rsi is not None and atr is not None and volume_ratio is not None
    long_trend = close > fast > slow and parameters.enable_long
    short_trend = close < fast < slow and parameters.enable_short
    bias = "LONG" if long_trend else "SHORT" if short_trend else "WAIT"
    distance = abs(close - ema_value) / ema_value if ema_value else 1.0
    pullback = distance <= parameters.ema_pullback_distance
    momentum = parameters.rsi_min <= rsi <= parameters.rsi_max and volume_ratio >= parameters.minimum_volume_ratio
    flow_aligned = flow_delta is None or (bias == "LONG" and flow_delta > 0) or (bias == "SHORT" and flow_delta < 0)
    contributions = score_rule_components(bias != "WAIT", pullback, momentum, flow_delta is not None, flow_aligned)
    score = sum(item["points"] for item in contributions)
    action = bias if bias != "WAIT" and pullback and momentum and flow_aligned and score >= parameters.minimum_score else "WAIT"
    return {
        "action": action, "bias": bias, "score": score, "warmed": True, "atr": atr,
        "distance_ema_pct": distance * 100, "rsi": rsi, "volume_ratio": volume_ratio,
        "contributions": contributions,
        "reason": "entry gates passed" if action != "WAIT" else "one or more entry gates failed",
    }
