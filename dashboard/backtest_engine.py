"""Causal single-asset backtest engine using next-bar-open execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

try:
    from metrics import calculate_metrics, monthly_returns
    from okx_history import TIMEFRAME_SECONDS
    from strategy_rules import StrategyParameters, calculate_indicators, evaluate_signal
    from decision_engine import TimeframeContext, HISTORICAL_STRATEGY_VERSION
except ImportError:
    from .metrics import calculate_metrics, monthly_returns
    from .okx_history import TIMEFRAME_SECONDS
    from .strategy_rules import StrategyParameters, calculate_indicators, evaluate_signal
    from .decision_engine import TimeframeContext, HISTORICAL_STRATEGY_VERSION


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def run_backtest(
    candles: list[dict[str, Any]], instrument: str, timeframe: str, parameters: StrategyParameters,
    start_ts: int, end_ts: int, progress: Callable[[int, str], None] | None = None,
    timeframe_datasets: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError("Unsupported timeframe.")
    candles = sorted({int(row["ts"]): row for row in candles}.values(), key=lambda row: int(row["ts"]))
    indicators = calculate_indicators(candles, parameters)
    higher_rows: dict[str,list[dict[str,Any]]]={}; higher_pointer:dict[str,int]={}
    for frame,rows in (timeframe_datasets or {}).items():
        ordered=sorted(rows,key=lambda r:int(r["ts"])); values=calculate_indicators(ordered,parameters); seconds=TIMEFRAME_SECONDS.get(frame,86400 if frame=="1D" else 0)
        higher_rows[frame]=[{**row,"candle_close_ts":int(row["ts"])+seconds,"fast_ma":value["fast_ma"],"slow_ma":value["slow_ma"]} for row,value in zip(ordered,values)]
        higher_pointer[frame]=-1
    equity_value = parameters.initial_capital
    equity: list[dict[str, float]] = []
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    cooldown_until_index = -1
    signal_count = 0
    decisions: list[dict[str, Any]] = []
    first_trade_index = next((index for index, row in enumerate(candles) if int(row["ts"]) >= start_ts), len(candles))

    for index, candle in enumerate(candles):
        ts = int(candle["ts"])
        if ts < start_ts or ts > end_ts:
            continue
        if progress and index % max(1, len(candles) // 20) == 0:
            progress(min(90, int(index / max(len(candles), 1) * 90)), "Simulating causal bar sequence")

        if pending and position is None and index >= first_trade_index and index > cooldown_until_index:
            side = pending["side"]
            raw_entry = float(candle["open"])
            entry = raw_entry * (1 + parameters.slippage if side == "LONG" else 1 - parameters.slippage)
            atr = float(pending["atr"])
            stop_distance = atr * parameters.stop_loss_atr_multiplier
            stop = entry - stop_distance if side == "LONG" else entry + stop_distance
            target = entry + stop_distance * parameters.risk_reward_ratio if side == "LONG" else entry - stop_distance * parameters.risk_reward_ratio
            risk_budget = max(equity_value, 0) * parameters.risk_per_trade
            size = min(risk_budget / stop_distance if stop_distance else 0.0, max(equity_value, 0) / entry if entry else 0.0)
            if size > 0:
                entry_fee = entry * size * parameters.trading_fee
                equity_value -= entry_fee
                position = {"side": side, "entry": entry, "raw_entry": raw_entry, "entry_ts": ts, "stop": stop, "target": target, "size": size, "entry_fee": entry_fee, "risk_amount": stop_distance * size, "score": pending["score"], "signal_ts": pending["signal_ts"], "signal_id": pending["signal_id"], "strategy_version": pending["strategy_version"], "config_hash": pending["config_hash"], "expected_entry_ts": ts, "expected_entry_price": raw_entry}
            pending = None

        if position:
            side = position["side"]
            hit_stop = float(candle["low"]) <= position["stop"] if side == "LONG" else float(candle["high"]) >= position["stop"]
            hit_target = float(candle["high"]) >= position["target"] if side == "LONG" else float(candle["low"]) <= position["target"]
            if hit_stop or hit_target:
                # Conservative ordering when an OHLC bar touches both levels.
                reason = "STOP_LOSS" if hit_stop else "TAKE_PROFIT"
                raw_exit = position["stop"] if hit_stop else position["target"]
                exit_price = raw_exit * (1 - parameters.slippage if side == "LONG" else 1 + parameters.slippage)
                gross = (exit_price - position["entry"]) * position["size"] * (1 if side == "LONG" else -1)
                exit_fee = exit_price * position["size"] * parameters.trading_fee
                pnl = gross - exit_fee
                equity_value += gross - exit_fee
                total_fees = position["entry_fee"] + exit_fee
                risk_amount = position["risk_amount"] or 1.0
                trade = {
                    "trade_id": len(trades) + 1, "instrument": instrument, "entry_ts": position["entry_ts"], "exit_ts": ts,
                    "entry_time": _iso(position["entry_ts"]), "exit_time": _iso(ts), "signal_ts": position["signal_ts"],
                    "side": side, "entry_price": position["entry"], "exit_price": exit_price,
                    "stop_loss": position["stop"], "take_profit": position["target"], "position_size": position["size"],
                    "pnl": pnl - position["entry_fee"], "pnl_pct": (pnl - position["entry_fee"]) / max(position["entry"] * position["size"], 1e-12) * 100,
                    "result_r": (pnl - position["entry_fee"]) / risk_amount, "fees": total_fees,
                    "exit_reason": reason, "holding_seconds": ts - position["entry_ts"], "signal_score": position["score"],
                    "signal_id": position["signal_id"], "strategy_version": position["strategy_version"], "config_hash": position["config_hash"],
                    "expected_entry_ts": position["expected_entry_ts"], "expected_entry_price": position["expected_entry_price"],
                }
                trades.append(trade); position = None; cooldown_until_index = index + parameters.cooldown_bars

        unrealized = 0.0
        if position:
            unrealized = (float(candle["close"]) - position["entry"]) * position["size"] * (1 if position["side"] == "LONG" else -1)
        equity.append({"ts": ts, "equity": equity_value + unrealized})

        signal_candle={**candle,"candle_close_ts":ts+TIMEFRAME_SECONDS[timeframe]}
        tf_context=None
        if timeframe=="15m" and higher_rows:
            frames={}
            for frame,rows in higher_rows.items():
                pointer=higher_pointer[frame]
                while pointer+1<len(rows) and int(rows[pointer+1]["candle_close_ts"])<=int(signal_candle["candle_close_ts"]): pointer+=1
                higher_pointer[frame]=pointer
                if pointer>=0:
                    row=rows[pointer]; frames[frame]={"candle_close_ts":row["candle_close_ts"],"close":row["close"],"fast_ma":row["fast_ma"],"slow_ma":row["slow_ma"]}
            required=("1H","4H")+(("1D",) if parameters.enable_daily_context else ())
            tf_context=TimeframeContext(frames,required,parameters.enable_daily_context,"multi-timeframe")
        signal = evaluate_signal(signal_candle, indicators[index], parameters, instrument=instrument, timeframe=timeframe,timeframe_context=tf_context,strategy_version=HISTORICAL_STRATEGY_VERSION if tf_context else None)
        if signal["warmed"]:
            decisions.append(signal)
        if signal["action"] != "WAIT" and index + 1 < len(candles) and int(candles[index + 1]["ts"]) <= end_ts:
            signal_count += 1
            if position is None and pending is None and index >= cooldown_until_index:
                pending = {"side": signal["action"], "atr": signal["atr"], "score": signal["score"], "signal_ts": ts,
                           "signal_id": signal["signal_id"], "strategy_version": signal["strategy_version"], "config_hash": signal["config_hash"]}

    if position and equity:
        candle = next(row for row in reversed(candles) if start_ts <= int(row["ts"]) <= end_ts)
        ts, side = int(candle["ts"]), position["side"]
        raw_exit = float(candle["close"])
        exit_price = raw_exit * (1 - parameters.slippage if side == "LONG" else 1 + parameters.slippage)
        gross = (exit_price - position["entry"]) * position["size"] * (1 if side == "LONG" else -1)
        exit_fee = exit_price * position["size"] * parameters.trading_fee
        pnl = gross - exit_fee - position["entry_fee"]
        equity_value += gross - exit_fee
        trades.append({"trade_id": len(trades) + 1, "instrument": instrument, "entry_ts": position["entry_ts"], "exit_ts": ts, "entry_time": _iso(position["entry_ts"]), "exit_time": _iso(ts), "signal_ts": position["signal_ts"], "side": side, "entry_price": position["entry"], "exit_price": exit_price, "stop_loss": position["stop"], "take_profit": position["target"], "position_size": position["size"], "pnl": pnl, "pnl_pct": pnl / max(position["entry"] * position["size"], 1e-12) * 100, "result_r": pnl / (position["risk_amount"] or 1), "fees": position["entry_fee"] + exit_fee, "exit_reason": "END_OF_DATA", "holding_seconds": ts - position["entry_ts"], "signal_score": position["score"], "signal_id": position["signal_id"], "strategy_version": position["strategy_version"], "config_hash": position["config_hash"], "expected_entry_ts": position["expected_entry_ts"], "expected_entry_price": position["expected_entry_price"]})
        equity[-1]["equity"] = equity_value

    metrics = calculate_metrics(parameters.initial_capital, equity, trades, TIMEFRAME_SECONDS[timeframe])
    drawdown = metrics.pop("drawdown_curve")
    visible_candles = [{"ts": int(row["ts"]), "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]} for row in candles if start_ts <= int(row["ts"]) <= end_ts]
    max_chart = 1500
    stride = max(1, len(visible_candles) // max_chart)
    return {"metrics": metrics, "trades": trades, "equity": equity, "decisions": decisions, "drawdown": drawdown, "monthly_returns": monthly_returns(equity), "candles": visible_candles[::stride], "signal_count": signal_count,
            "execution_model": "Signal confirmed at candle close; entry at next candle open with adverse slippage. Stop wins ties when stop and target are both touched in one OHLC bar.",
            "indicator_model": "Causal rolling indicators with full Slow MA warm-up. Historical CVD/OI is unavailable and is not fabricated."}
