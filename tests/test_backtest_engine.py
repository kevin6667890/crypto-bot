from __future__ import annotations

from dataclasses import replace

import pytest

from dashboard.backtest_engine import run_backtest
from dashboard.metrics import calculate_metrics, maximum_drawdown
from dashboard.strategy_rules import StrategyParameters, calculate_indicators, evaluate_signal


def candles(count: int = 80, direction: int = 1) -> list[dict[str, float | int]]:
    rows = []
    for index in range(count):
        close = 100 + direction * index * 0.2
        rows.append({"ts": 1_700_000_000 + index * 900, "open": close - direction * 0.05, "high": close + 0.35, "low": close - 0.35, "close": close, "volume": 100 + index})
    return rows


def parameters(**changes: object) -> StrategyParameters:
    base = StrategyParameters(fast_ma=3, slow_ma=8, ema_pullback_period=3, ema_pullback_distance=0.1, rsi_period=3, rsi_min=0, rsi_max=100, minimum_volume_ratio=0.1, minimum_score=70, atr_period=3, stop_loss_atr_multiplier=1, risk_reward_ratio=2, trading_fee=0, slippage=0, cooldown_bars=0, initial_capital=10_000, risk_per_trade=0.01)
    return replace(base, **changes)


def run(rows: list[dict[str, float | int]], params: StrategyParameters | None = None):
    return run_backtest(rows, "BTC-USDT", "15m", params or parameters(), int(rows[0]["ts"]), int(rows[-1]["ts"]))


def test_indicators_do_not_use_future_candles():
    rows = candles()
    before = calculate_indicators(rows[:40], parameters())[-1]
    mutated_future = candles()
    for row in mutated_future[40:]:
        row["close"] = 1_000_000
    after = calculate_indicators(mutated_future, parameters())[39]
    assert before == after


def test_slow_ma_warmup_blocks_signals():
    rows = candles(25)
    indicators = calculate_indicators(rows, parameters(slow_ma=15))
    assert all(not evaluate_signal(rows[index], indicators[index], parameters(slow_ma=15))["warmed"] for index in range(14))
    assert evaluate_signal(rows[20], indicators[20], parameters(slow_ma=15))["warmed"]


def test_signal_enters_at_next_bar_open():
    rows = candles()
    result = run(rows)
    assert result["trades"]
    first = result["trades"][0]
    entry_row = next(row for row in rows if row["ts"] == first["entry_ts"])
    assert first["signal_ts"] < first["entry_ts"]
    assert first["entry_price"] == pytest.approx(entry_row["open"])


def test_fees_reduce_equity_and_are_recorded():
    rows = candles()
    without_fee = run(rows, parameters(trading_fee=0))
    with_fee = run(rows, parameters(trading_fee=0.002))
    assert with_fee["metrics"]["fees_paid"] > 0
    assert with_fee["metrics"]["final_equity"] < without_fee["metrics"]["final_equity"]


def test_slippage_is_adverse_for_long_entries():
    rows = candles()
    normal = run(rows, parameters(slippage=0))
    slipped = run(rows, parameters(slippage=0.01))
    assert normal["trades"] and slipped["trades"]
    assert slipped["trades"][0]["entry_price"] > normal["trades"][0]["entry_price"]


def test_long_and_short_are_supported():
    long_result = run(candles(direction=1), parameters(enable_long=True, enable_short=False))
    short_result = run(candles(direction=-1), parameters(enable_long=False, enable_short=True))
    assert long_result["trades"] and all(trade["side"] == "LONG" for trade in long_result["trades"])
    assert short_result["trades"] and all(trade["side"] == "SHORT" for trade in short_result["trades"])


def test_stop_loss_and_take_profit_paths():
    stop_rows = candles(35)
    stop_rows[23]["low"] = 50
    stop_result = run(stop_rows, parameters(risk_reward_ratio=20))
    assert any(trade["exit_reason"] == "STOP_LOSS" for trade in stop_result["trades"])
    target_rows = candles(35)
    target_rows[23]["high"] = 200
    target_result = run(target_rows, parameters(risk_reward_ratio=2))
    assert any(trade["exit_reason"] == "TAKE_PROFIT" for trade in target_result["trades"])


def test_maximum_drawdown():
    drawdown, curve = maximum_drawdown([{"ts": 1, "equity": 100}, {"ts": 2, "equity": 120}, {"ts": 3, "equity": 90}, {"ts": 4, "equity": 110}])
    assert drawdown == pytest.approx(25)
    assert curve[2]["drawdown"] == pytest.approx(-25)


def test_profit_factor():
    trades = [{"pnl": 100, "side": "LONG", "entry_ts": 1, "exit_ts": 2, "fees": 0}, {"pnl": 50, "side": "LONG", "entry_ts": 2, "exit_ts": 3, "fees": 0}, {"pnl": -50, "side": "SHORT", "entry_ts": 3, "exit_ts": 4, "fees": 0}]
    metrics = calculate_metrics(1000, [{"ts": 1, "equity": 1000}, {"ts": 4, "equity": 1100}], trades, 900)
    assert metrics["profit_factor"] == pytest.approx(3)


def test_empty_trade_result_is_explicit():
    rows = candles(10)
    result = run(rows, parameters(slow_ma=20))
    assert result["trades"] == []
    assert result["metrics"]["total_trades"] == 0
    assert result["metrics"]["profit_factor"] is None
    assert result["metrics"]["sample_note"]
