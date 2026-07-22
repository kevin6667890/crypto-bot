"""Deterministic no-network coverage for the shared execution boundary."""
from __future__ import annotations
import math
import pytest
from dashboard.backtest_engine import run_execution_backtest
from dashboard.strategy_rules import StrategyParameters

T = 1_700_000_000
def candles(*rows):
    return [{"ts": T+i*900, "open": o, "high": h, "low": l, "close": c, "volume": 1} for i,(o,h,l,c) in enumerate(rows)]
def parameters(**overrides):
    values={"initial_capital":1000,"risk_per_trade":.1,"trading_fee":0,"slippage":0,
        "stop_loss_atr_multiplier":2,"risk_reward_ratio":2,"cooldown_bars":0}
    values.update(overrides)
    return StrategyParameters(**values)
def provider(action="LONG", atr=5, **extra):
    def signal(candle, index):
        return {"action": action if index == 0 else "WAIT", "atr": atr, "score": 1,
          "signal_ts": int(candle["ts"]), "signal_id": f"s-{index}", "strategy_version": "test",
          "config_hash": "config", "warmed": True, **extra}
    return signal

def test_next_open_stop_target_and_sizing_are_canonical():
    rows=candles((100,101,99,100),(110,131,89,110),(110,111,109,110))
    result=run_execution_backtest(rows,"BTC-USDT","15m",parameters(),T,T+1800,signal_provider=provider())
    trade=result["trades"][0]
    assert trade["entry_price"] == 110 and trade["stop_loss"] == 100 and trade["take_profit"] == 130
    assert trade["position_size"] == pytest.approx(1000 / 110) # risk size is capped by available equity
    assert trade["exit_reason"] == "STOP_LOSS" # conservative collision

@pytest.mark.parametrize(("side","slippage","expected"), [("LONG",.01,111.1),("SHORT",.01,108.9)])
def test_adverse_entry_slippage(side,slippage,expected):
    result=run_execution_backtest(candles((100,101,99,100),(110,111,109,110),(110,111,109,110)),"BTC-USDT","15m",
      parameters(slippage=slippage),T,T+1800,signal_provider=provider(side))
    assert result["trades"][0]["entry_price"] == pytest.approx(expected)

@pytest.mark.parametrize("atr", [None,0,-1,float("nan"),float("inf")])
def test_invalid_non_wait_atr_is_rejected(atr):
    with pytest.raises(ValueError, match="finite positive ATR"):
        run_execution_backtest(candles((100,101,99,100),(100,101,99,100)),"BTC-USDT","15m",parameters(),T,T+900,signal_provider=provider(atr=atr))

def test_boundaries_and_provider_validation_are_deterministic():
    rows=candles((100,101,99,100),(110,111,109,110),(120,121,119,120))
    result=run_execution_backtest(rows,"BTC-USDT","15m",parameters(),T,T+900,signal_provider=provider())
    assert result["trades"][0]["exit_reason"] == "END_OF_DATA" and result["trades"][0]["exit_ts"] == T+900
    with pytest.raises(ValueError, match="mapping"):
        run_execution_backtest(rows,"BTC-USDT","15m",parameters(),T,T+900,signal_provider=lambda *_: "LONG")
    assert result == run_execution_backtest(rows,"BTC-USDT","15m",parameters(),T,T+900,signal_provider=provider())
