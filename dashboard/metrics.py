"""Backtest performance metrics with explicit insufficient-sample handling."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def maximum_drawdown(equity: list[dict[str, float]]) -> tuple[float, list[dict[str, float]]]:
    peak = 0.0
    maximum = 0.0
    curve: list[dict[str, float]] = []
    for point in equity:
        value = float(point["equity"])
        peak = max(peak, value)
        drawdown = (value - peak) / peak if peak else 0.0
        maximum = min(maximum, drawdown)
        curve.append({"ts": int(point["ts"]), "drawdown": drawdown * 100})
    return abs(maximum) * 100, curve


def calculate_metrics(initial_capital: float, equity: list[dict[str, float]], trades: list[dict[str, Any]], timeframe_seconds: int) -> dict[str, Any]:
    final_equity = float(equity[-1]["equity"]) if equity else initial_capital
    net_profit = final_equity - initial_capital
    total_return = net_profit / initial_capital * 100 if initial_capital else 0.0
    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) < 0]
    gross_profit = sum(float(trade["pnl"]) for trade in wins)
    gross_loss = abs(sum(float(trade["pnl"]) for trade in losses))
    profit_factor = _safe_ratio(gross_profit, gross_loss)
    average_win = sum(float(trade["pnl"]) for trade in wins) / len(wins) if wins else None
    average_loss = sum(float(trade["pnl"]) for trade in losses) / len(losses) if losses else None
    realized_rr = _safe_ratio(average_win or 0.0, abs(average_loss or 0.0)) if wins and losses else None
    expectancy = sum(float(trade["pnl"]) for trade in trades) / len(trades) if trades else None
    max_dd, drawdown_curve = maximum_drawdown(equity)

    returns: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        prior = float(previous["equity"])
        if prior:
            returns.append(float(current["equity"]) / prior - 1)
    periods_per_year = 365.25 * 86400 / timeframe_seconds
    sharpe = sortino = None
    if len(returns) >= 30:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        deviation = math.sqrt(variance)
        sharpe = mean / deviation * math.sqrt(periods_per_year) if deviation else None
        downside = [min(value, 0.0) for value in returns]
        downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside))
        sortino = mean / downside_deviation * math.sqrt(periods_per_year) if downside_deviation else None

    annualized_return = None
    if len(equity) > 1 and final_equity > 0 and initial_capital > 0:
        elapsed_years = (int(equity[-1]["ts"]) - int(equity[0]["ts"])) / (365.25 * 86400)
        if elapsed_years >= 1 / 365.25:
            annualized_return = ((final_equity / initial_capital) ** (1 / elapsed_years) - 1) * 100

    best_wins = best_losses = current_wins = current_losses = 0
    for trade in trades:
        if float(trade["pnl"]) > 0:
            current_wins += 1; current_losses = 0; best_wins = max(best_wins, current_wins)
        elif float(trade["pnl"]) < 0:
            current_losses += 1; current_wins = 0; best_losses = max(best_losses, current_losses)
    holding_seconds = [int(trade["exit_ts"]) - int(trade["entry_ts"]) for trade in trades]
    sample_note = None if len(trades) >= 30 else f"Only {len(trades)} trades; statistical ratios are descriptive, not significant."
    return {
        "initial_capital": initial_capital, "final_equity": final_equity, "net_profit": net_profit,
        "total_return": total_return, "annualized_return": annualized_return, "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else None, "profit_factor": profit_factor,
        "expectancy": expectancy, "average_win": average_win, "average_loss": average_loss,
        "realized_risk_reward": realized_rr, "maximum_drawdown": max_dd, "sharpe_ratio": sharpe,
        "sortino_ratio": sortino, "consecutive_wins": best_wins, "consecutive_losses": best_losses,
        "fees_paid": sum(float(trade["fees"]) for trade in trades),
        "long_trades": sum(trade["side"] == "LONG" for trade in trades),
        "short_trades": sum(trade["side"] == "SHORT" for trade in trades),
        "average_holding_seconds": sum(holding_seconds) / len(holding_seconds) if holding_seconds else None,
        "sample_note": sample_note, "drawdown_curve": drawdown_curve,
    }


def monthly_returns(equity: list[dict[str, float]]) -> list[dict[str, Any]]:
    months: dict[str, tuple[float, float]] = {}
    for point in equity:
        month = datetime.fromtimestamp(int(point["ts"]), tz=timezone.utc).strftime("%Y-%m")
        value = float(point["equity"])
        if month not in months:
            months[month] = (value, value)
        else:
            months[month] = (months[month][0], value)
    return [{"month": month, "return": (end / start - 1) * 100 if start else 0.0} for month, (start, end) in sorted(months.items())]
