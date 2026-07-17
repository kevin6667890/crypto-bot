"""Comparable deterministic benchmark engines."""

from __future__ import annotations

import math
from typing import Any

try:
    from metrics import calculate_metrics
except ImportError:
    from .metrics import calculate_metrics


def _trade(side: str, entry: float, exit_price: float, entry_ts: int, exit_ts: int, capital: float, fee: float, slippage: float) -> dict[str, Any]:
    adjusted_entry = entry * (1 + slippage if side == "LONG" else 1 - slippage); adjusted_exit = exit_price * (1 - slippage if side == "LONG" else 1 + slippage)
    size = capital / adjusted_entry if adjusted_entry else 0; fees = (adjusted_entry + adjusted_exit) * size * fee
    pnl = (adjusted_exit - adjusted_entry) * size * (1 if side == "LONG" else -1) - fees
    return {"side": side, "entry_ts": entry_ts, "exit_ts": exit_ts, "entry_price": adjusted_entry, "exit_price": adjusted_exit, "pnl": pnl, "fees": fees}


def _extended_metrics(initial: float, equity: list[dict[str, float]], trades: list[dict[str, Any]], seconds: int, bars: int, exposed_bars: int) -> dict[str, Any]:
    metrics = calculate_metrics(initial, equity, trades, seconds); returns = [b["equity"] / a["equity"] - 1 for a, b in zip(equity, equity[1:]) if a["equity"]]
    volatility = math.sqrt(sum((x - sum(returns) / len(returns)) ** 2 for x in returns) / max(1, len(returns) - 1)) * math.sqrt(365.25 * 86400 / seconds) * 100 if len(returns) > 1 else 0
    downside = math.sqrt(sum(min(value, 0) ** 2 for value in returns) / len(returns)) * math.sqrt(365.25 * 86400 / seconds) * 100 if returns else 0
    metrics.update({"exposure": exposed_bars / bars * 100 if bars else 0, "time_in_market": exposed_bars / bars * 100 if bars else 0, "volatility": volatility, "downside_deviation": downside, "calmar_ratio": metrics["annualized_return"] / metrics["maximum_drawdown"] if metrics.get("annualized_return") is not None and metrics["maximum_drawdown"] else None, "turnover": sum(abs(float(t.get("entry_price", 0))) for t in trades) / initial if initial else 0, "recovery_time_bars": _recovery(equity)})
    return metrics


def _recovery(equity: list[dict[str, float]]) -> int | None:
    peak = -math.inf; underwater = longest = 0
    for point in equity:
        if point["equity"] >= peak: peak = point["equity"]; longest = max(longest, underwater); underwater = 0
        else: underwater += 1
    longest = max(longest, underwater)
    return longest


def run_asset_benchmarks(candles: list[dict[str, Any]], initial: float, fee: float, slippage: float, seconds: int, canonical: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if len(candles) < 2: raise ValueError("At least two confirmed candles are required for benchmarks.")
    rows = sorted(candles, key=lambda row: int(row["ts"])); output = []
    cash_equity = [{"ts": int(row["ts"]), "equity": initial} for row in rows]
    output.append({"name": "Cash / No Position", "metrics": _extended_metrics(initial, cash_equity, [], seconds, len(rows), 0), "equity": cash_equity, "execution_model": "No position and no fees."})
    bh = _trade("LONG", float(rows[0]["open"]), float(rows[-1]["close"]), int(rows[0]["ts"]), int(rows[-1]["ts"]), initial, fee, slippage)
    bh_equity = [{"ts": int(row["ts"]), "equity": initial + (float(row["close"]) - bh["entry_price"]) * (initial / bh["entry_price"]) - initial * fee} for row in rows]; bh_equity[-1]["equity"] = initial + bh["pnl"]
    output.append({"name": "Buy & Hold", "metrics": _extended_metrics(initial, bh_equity, [bh], seconds, len(rows), len(rows)), "equity": bh_equity, "execution_model": "Buy first tradable bar open, sell final bar close; adverse slippage and both-side fees included."})
    closes = [float(row["close"]) for row in rows]
    for name, fast_period, slow_period in (("MA60 / MA200 Crossover", 60, 200), ("Simple Trend Following", 20, 60)):
        cash, position, trades, equity, exposed = initial, None, [], [], 0
        for i, row in enumerate(rows):
            fast = sum(closes[i-fast_period+1:i+1]) / fast_period if i + 1 >= fast_period else None; slow = sum(closes[i-slow_period+1:i+1]) / slow_period if i + 1 >= slow_period else None
            desired = "LONG" if fast is not None and slow is not None and fast > slow else None
            if position and not desired:
                trade = _trade("LONG", position["entry"], float(row["open"]), position["ts"], int(row["ts"]), position["capital"], fee, slippage); cash = position["capital"] + trade["pnl"]; trades.append(trade); position = None
            if not position and desired: position = {"entry": float(row["open"]), "ts": int(row["ts"]), "capital": cash}
            if position: exposed += 1
            value = position["capital"] + (float(row["close"]) - position["entry"] * (1 + slippage)) * (position["capital"] / (position["entry"] * (1 + slippage))) - position["capital"] * fee if position else cash
            equity.append({"ts": int(row["ts"]), "equity": value})
        if position:
            trade = _trade("LONG", position["entry"], float(rows[-1]["close"]), position["ts"], int(rows[-1]["ts"]), position["capital"], fee, slippage); trades.append(trade); equity[-1]["equity"] = position["capital"] + trade["pnl"]
        output.append({"name": name, "metrics": _extended_metrics(initial, equity, trades, seconds, len(rows), exposed), "equity": equity, "execution_model": "Confirmed close determines state; next available bar open execution with fees and slippage."})
    if canonical:
        metrics = dict(canonical["metrics"]); metrics.setdefault("exposure", None); metrics.setdefault("time_in_market", None); metrics.setdefault("turnover", None)
        output.append({"name": "Current Canonical Strategy", "metrics": metrics, "equity": canonical.get("equity", []), "execution_model": canonical.get("execution_model")})
    buy_hold = next(item for item in output if item["name"] == "Buy & Hold")["metrics"]
    buy_hold_equity = {point["ts"]: point["equity"] for point in next(item for item in output if item["name"] == "Buy & Hold")["equity"]}
    for item in output:
        m = item["metrics"]; m["excess_return_vs_buy_hold"] = float(m.get("total_return") or 0) - float(buy_hold.get("total_return") or 0); m["drawdown_reduction"] = float(buy_hold.get("maximum_drawdown") or 0) - float(m.get("maximum_drawdown") or 0); m["risk_adjusted_improvement"] = (m.get("sharpe_ratio") or 0) - (buy_hold.get("sharpe_ratio") or 0); m["fee_drag"] = float(m.get("fees_paid") or 0) / initial * 100
        item["advantage_message"] = None if item["name"] != "Current Canonical Strategy" or (m["excess_return_vs_buy_hold"] > 0 and m["risk_adjusted_improvement"] > 0) else "No demonstrated advantage over the selected benchmark in this period."
        series=item.get("equity") or [];window=max(1,int(30*86400/seconds));rolling=[]
        for index in range(window,len(series),max(1,window//30)):
            start,end=series[index-window],series[index];bh_start,bh_end=buy_hold_equity.get(start["ts"]),buy_hold_equity.get(end["ts"])
            if bh_start and bh_end and start["equity"]:rolling.append({"ts":end["ts"],"excess_return":((end["equity"]/start["equity"]-1)-(bh_end/bh_start-1))*100})
        item["rolling_30d_excess_return"]=rolling
    return output
