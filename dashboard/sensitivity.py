"""Bounded parameter-sensitivity utilities; no optimization or AI."""

from __future__ import annotations

import itertools
import math
from typing import Any

MAX_COMBINATIONS = 100
SUPPORTED_PARAMETERS = {"fast_ma", "slow_ma", "ema_pullback_distance", "rsi_min", "rsi_max", "minimum_volume_ratio", "minimum_score", "stop_loss_atr_multiplier", "risk_reward_ratio", "cooldown_bars", "trading_fee", "slippage"}


def range_values(spec: dict[str, Any]) -> list[float | int]:
    start, stop, step = float(spec["start"]), float(spec["stop"]), float(spec["step"])
    if step <= 0 or stop < start: raise ValueError("Parameter range requires stop >= start and step > 0.")
    count = int(math.floor((stop - start) / step + 1e-9)) + 1
    if count > MAX_COMBINATIONS: raise ValueError("A single parameter range exceeds 100 values.")
    values = [start + index * step for index in range(count)]
    if spec.get("parameter") in {"fast_ma", "slow_ma", "cooldown_bars"}: return [int(round(value)) for value in values]
    return [round(value, 10) for value in values]


def parameter_combinations(base: dict[str, Any], ranges: list[dict[str, Any]], mode: str = "OAT") -> list[dict[str, Any]]:
    if mode not in {"OAT", "GRID_2D"}: raise ValueError("Mode must be OAT or GRID_2D.")
    expected = 1 if mode == "OAT" else 2
    if len(ranges) != expected: raise ValueError(f"{mode} requires exactly {expected} parameter range(s).")
    names = [str(item.get("parameter")) for item in ranges]
    if len(set(names)) != len(names) or any(name not in SUPPORTED_PARAMETERS for name in names): raise ValueError("Unsupported or duplicate sensitivity parameter.")
    values = [range_values(item) for item in ranges]
    combos = [{**base, **dict(zip(names, selected))} for selected in itertools.product(*values)]
    if len(combos) > MAX_COMBINATIONS: raise ValueError(f"Estimated combination count {len(combos)} exceeds the limit of {MAX_COMBINATIONS}.")
    return combos


def stability_scores(results: list[dict[str, Any]], parameter_names: list[str]) -> list[dict[str, Any]]:
    """Transparent 0-100 score: neighborhood variance 25, OOS 25, positive neighbors 20, DD stability 15, sample 15."""
    if not results: return []
    for index, row in enumerate(results):
        neighbors = []
        for other_index, other in enumerate(results):
            if index == other_index: continue
            differing = sum(row["parameters"].get(name) != other["parameters"].get(name) for name in parameter_names)
            if differing <= 1: neighbors.append(other)
        region = neighbors + [row]
        returns = [float(item.get("total_return") or 0) for item in region]
        mean = sum(returns) / len(returns); variance = sum((value - mean) ** 2 for value in returns) / len(returns)
        variance_component = 25 / (1 + math.sqrt(variance) / 5)
        is_return, oos_return = float(row.get("total_return") or 0), float(row.get("oos_return") or 0)
        degradation = max(0.0, is_return - oos_return); oos_component = max(0.0, 25 - degradation * 1.25)
        positive_component = 20 * sum(value > 0 for value in returns) / len(returns)
        dds = [float(item.get("maximum_drawdown") or 0) for item in region]; dd_span = max(dds) - min(dds)
        dd_component = 15 / (1 + dd_span / 5)
        trades = int(row.get("trades") or 0); sample_component = min(15.0, trades / 30 * 15)
        row["stability_score"] = round(variance_component + oos_component + positive_component + dd_component + sample_component, 2)
        row["stability_components"] = {"neighborhood_variance": round(variance_component, 2), "oos_degradation": round(oos_component, 2), "positive_neighborhood": round(positive_component, 2), "drawdown_stability": round(dd_component, 2), "sample_size": round(sample_component, 2)}
        row["positive_neighborhood_ratio"] = sum(value > 0 for value in returns) / len(returns)
        row["labels"] = (["Low Sample Size"] if trades < 30 else []) + (["High Return / Fragile"] if is_return > 0 and row["stability_score"] < 50 else []) + (["Overfitting Risk"] if oos_return < 0 < is_return else [])
        row["label_codes"] = (["validation.label.low_sample_size"] if trades < 30 else []) + (["validation.label.high_return_fragile"] if is_return > 0 and row["stability_score"] < 50 else []) + (["validation.label.overfitting_risk"] if oos_return < 0 < is_return else [])
    if results:
        max(results, key=lambda item: float(item.get("total_return") or -1e99))["labels"].append("Best Historical Result")
        max(results, key=lambda item: item["stability_score"])["labels"].append("Most Stable Region")
        max(results, key=lambda item: float(item.get("total_return") or -1e99))["label_codes"].append("validation.label.best_historical_result")
        max(results, key=lambda item: item["stability_score"])["label_codes"].append("validation.label.most_stable_region")
    return results
