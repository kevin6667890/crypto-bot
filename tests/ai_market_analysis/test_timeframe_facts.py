from __future__ import annotations

import math

import pytest

from dashboard.discovery_features import build_features
from dashboard.ai_market_analysis.timeframe_facts import build_timeframe_facts
from dashboard.ai_market_analysis.quality import derive_weekly, normalize_candles

from .helpers import BASE, breakout_path, candles


def test_all_required_indicators_match_reused_feature_core():
    rows = candles(240)
    decision = rows[-1]["ts"]+900
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", decision)
    reused = build_features(facts["confirmed_bars"], {"ma_periods": [20,30,60,200],
        "atr_period": 14, "bb_period": 20, "rsi_period": 14, "volume_period": 20})[-1]
    for period in (20,30,60,200):
        assert facts["moving_averages"][f"ma{period}"]["value"] == pytest.approx(reused[f"sma_{period}"])
    assert facts["moving_averages"]["ema20"]["value"] == pytest.approx(reused["ema_20"])
    assert facts["rsi14"]["value"] == pytest.approx(reused["rsi"])
    assert facts["atr14"]["value"] == pytest.approx(reused["atr"])
    assert facts["bollinger"]["upper"]["value"] == pytest.approx(reused["bb_upper"])
    assert facts["bollinger"]["bandwidth"]["value"] == pytest.approx(reused["bb_width"]*100)
    assert facts["rolling_volatility"]["value"] is not None
    assert all(math.isfinite(item["value"]) for item in facts["moving_averages"].values())


@pytest.mark.parametrize("count,available", [(19,False),(20,True),(29,False),(30,True),(59,False),(60,True),(199,False),(200,True)])
def test_ma_warmup_is_explicit(count, available):
    facts = build_timeframe_facts(candles(count), "ETH-USDT-SWAP", "15m", BASE+count*900)
    period = 20 if count in (19,20) else 30 if count in (29,30) else 60 if count in (59,60) else 200
    metric = facts["moving_averages"][f"ma{period}"]
    assert (metric["status"] == "AVAILABLE") is available
    assert metric["value"] is not None if available else metric["value"] is None


def test_stoch_rsi_and_candle_shape_metrics_exist_after_warmup():
    rows = candles(240)
    for i, row in enumerate(rows):
        close = 100 + (i % 17) - (i % 7)
        row.update(open=close-.4, high=close+1, low=close-1, close=close)
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+240*900)
    assert facts["stoch_rsi"]["stoch_rsi_k"]["value"] is not None
    assert facts["stoch_rsi"]["stoch_rsi_d"]["value"] is not None
    assert facts["body_percentage"]["value"] > 0
    assert facts["upper_wick_percentage"]["value"] > 0
    assert facts["lower_wick_percentage"]["value"] > 0


def test_single_high_volume_bar_is_not_automatically_expanding():
    rows = candles(120)
    rows[-1]["volume"] = 250
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+120*900)
    assert facts["volume_ratio"]["value"] > 1
    assert facts["volume_regime"]["classification"] != "EXPANDING"


def test_volume_regime_uses_ratio_percentile_and_trend():
    rows = candles(120)
    for i, row in enumerate(rows[-5:]):
        row["volume"] = 180+i*50
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+120*900)
    assert facts["volume_regime"]["classification"] in {"EXPANDING", "CLIMACTIC"}
    assert facts["volume_regime"]["percentile"] >= 70
    assert facts["volume_regime"]["trend"] > 0


def test_unconfirmed_candle_is_live_only():
    rows = candles(40)
    live = candles(1, start=BASE+40*900, confirmed=False)[0]
    live["close"] = 9999; live["high"] = 10000
    decision = live["ts"]+300
    facts = build_timeframe_facts(rows+[live], "ETH-USDT-SWAP", "15m", decision)
    assert facts["confirmed_close"] == rows[-1]["close"]
    assert facts["live_observation"]["close"] == 9999
    assert len(facts["confirmed_bars"]) == 40


def test_gap_is_visible_and_never_forward_filled():
    normalized, _, quality = normalize_candles(candles(40, gap_at=20), "ETH-USDT-SWAP", "15m", BASE+41*900)
    assert len(normalized) == 40
    assert quality["missing_bars"] == 1
    assert quality["status"] == "GAP_AFFECTED"


def test_daily_gap_prevents_complete_week_derivation():
    daily = candles(14, "1D")
    daily.pop(4)
    weekly, quality = derive_weekly(daily, "ETH-USDT-SWAP", BASE+14*86400)
    assert len(weekly) == 1
    assert quality["status"] == "GAP_AFFECTED"


def test_week_is_monday_utc_and_only_seven_contiguous_days():
    weekly, quality = derive_weekly(candles(14, "1D"), "ETH-USDT-SWAP", BASE+14*86400)
    assert len(weekly) == 2
    assert all(row["ts"] % 604800 == 345600 for row in weekly)  # Unix epoch Thursday offset
    assert "Monday 00:00 UTC" in quality["derivation"]


def test_future_bar_does_not_change_old_decision_facts():
    rows = breakout_path()
    cutoff = rows[-2]["ts"]+900
    before = build_timeframe_facts(rows[:-1], "ETH-USDT-SWAP", "15m", cutoff)
    after = build_timeframe_facts(rows+[candles(1, start=rows[-1]["ts"]+900)[0]], "ETH-USDT-SWAP", "15m", cutoff)
    assert before["input_fingerprint"] == after["input_fingerprint"]
