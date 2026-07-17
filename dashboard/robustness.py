"""Reproducible Monte Carlo, bootstrap, and execution stress tests."""

from __future__ import annotations

import math
import random
from typing import Any

MAX_SIMULATIONS = 5000
DISCLAIMER = "Monte Carlo results describe perturbations of the observed sample only; they do not establish future probabilities."


def _percentile(values: list[float], pct: float) -> float | None:
    if not values: return None
    ordered = sorted(values); index = (len(ordered) - 1) * pct; lower = math.floor(index); upper = math.ceil(index)
    return ordered[lower] if lower == upper else ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _path(initial: float, pnls: list[float]) -> tuple[float, float, int, list[float]]:
    equity = peak = initial; maximum_dd = 0.0; losses = streak = 0; points = [initial]
    for pnl in pnls:
        equity += pnl; peak = max(peak, equity); maximum_dd = max(maximum_dd, (peak - equity) / peak * 100 if peak else 100); streak = streak + 1 if pnl < 0 else 0; losses = max(losses, streak); points.append(equity)
    return equity, maximum_dd, losses, points


def run_robustness(trades: list[dict[str, Any]], initial_capital: float, simulations: int = 1000, seed: int = 42, mode: str = "TRADE_ORDER", fee_multiplier: float = 1.0, slippage_multiplier: float = 1.0, missed_trade_rate: float = 0.0, ruin_threshold_pct: float = 50.0, loss_threshold_pct: float = 20.0, drawdown_threshold_pct: float = 25.0) -> dict[str, Any]:
    if not 1 <= simulations <= MAX_SIMULATIONS: raise ValueError(f"Simulation count must be between 1 and {MAX_SIMULATIONS}.")
    if not trades: return {"status": "INSUFFICIENT_DATA", "simulation_count": simulations, "seed": seed, "disclaimer": DISCLAIMER, "returns": [], "drawdowns": []}
    if not 0 <= missed_trade_rate < 1: raise ValueError("Missed trade rate must be between 0 and 1.")
    rng = random.Random(seed); base = [float(t.get("pnl", 0)) - float(t.get("fees", 0)) * (fee_multiplier - 1) - abs(float(t.get("slippage_cost", 0))) * (slippage_multiplier - 1) for t in trades]
    finals, returns, drawdowns, streaks, paths = [], [], [], [], []
    for _ in range(simulations):
        sample = rng.choices(base, k=len(base)) if mode == "BOOTSTRAP" else rng.sample(base, len(base))
        sample = [value for value in sample if rng.random() >= missed_trade_rate]
        final, dd, streak, path = _path(initial_capital, sample); finals.append(final); returns.append((final / initial_capital - 1) * 100); drawdowns.append(dd); streaks.append(streak); paths.append(path)
    confidence_steps = max(len(path) for path in paths); fan = []
    stride = max(1, confidence_steps // 100)
    for index in range(0, confidence_steps, stride):
        values = [path[min(index, len(path)-1)] for path in paths]; fan.append({"step": index, "p5": _percentile(values, .05), "p25": _percentile(values, .25), "median": _percentile(values, .5), "p75": _percentile(values, .75), "p95": _percentile(values, .95)})
    return {"status": "COMPLETED", "mode": mode, "simulation_count": simulations, "seed": seed, "median_return": _percentile(returns, .5), "return_percentiles": {"p5": _percentile(returns, .05), "p25": _percentile(returns, .25), "p75": _percentile(returns, .75), "p95": _percentile(returns, .95)}, "median_drawdown": _percentile(drawdowns, .5), "p95_drawdown": _percentile(drawdowns, .95), "probability_positive_return": sum(x > 0 for x in returns) / len(returns), "probability_losing_more_than_x": sum(x < -loss_threshold_pct for x in returns) / len(returns), "probability_drawdown_above_x": sum(x > drawdown_threshold_pct for x in drawdowns) / len(drawdowns), "median_final_equity": _percentile(finals, .5), "worst_simulated_equity": min(finals), "best_simulated_equity": max(finals), "risk_of_ruin": sum(x <= initial_capital * (1 - ruin_threshold_pct / 100) for x in finals) / len(finals), "consecutive_loss_distribution": dict(sorted(__import__('collections').Counter(streaks).items())), "returns": returns[:1000], "drawdowns": drawdowns[:1000], "percentile_fan": fan, "parameters": {"fee_multiplier": fee_multiplier, "slippage_multiplier": slippage_multiplier, "missed_trade_rate": missed_trade_rate, "ruin_threshold_pct": ruin_threshold_pct}, "disclaimer": DISCLAIMER}


def stress_curve(trades: list[dict[str, Any]], initial: float, multipliers: list[float], kind: str) -> list[dict[str, float]]:
    output = []
    for multiplier in multipliers[:20]:
        adjusted = [float(t.get("pnl", 0)) - float(t.get("fees" if kind == "fee" else "slippage_cost", 0)) * (float(multiplier) - 1) for t in trades]
        final, dd, _, _ = _path(initial, adjusted); output.append({"multiplier": float(multiplier), "return": (final / initial - 1) * 100, "drawdown": dd})
    return output
