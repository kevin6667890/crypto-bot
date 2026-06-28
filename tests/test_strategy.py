import sys
import os
import sqlite3
import numpy as np
import pandas as pd
import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules_blueprint import compute_indicators, calculate_signal_score, get_market_session
from ultimate_bot import PaperTrader


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_ohlcv(n=500) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame big enough for all indicators (ema200 needs 200+ rows, rolling center=True trims edges)."""
    rng = np.random.default_rng(42)
    close = 2000.0 + np.cumsum(rng.normal(0, 5, n))
    high = close + rng.uniform(1, 10, n)
    low = close - rng.uniform(1, 10, n)
    open_ = close + rng.normal(0, 3, n)
    volume = rng.uniform(100, 1000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume})


# ──────────────────────────────────────────────
# 1. compute_indicators
# ──────────────────────────────────────────────

class TestComputeIndicators:
    def test_required_columns_present(self):
        df = _make_ohlcv()
        result = compute_indicators(df)
        required = {"ema20", "ema50", "ema200", "zlema20", "rsi", "atr", "adx", "vol_ratio"}
        assert required.issubset(set(result.columns)), (
            f"Missing columns: {required - set(result.columns)}"
        )

    def test_no_nan_in_last_10_rows(self):
        df = _make_ohlcv()
        result = compute_indicators(df)
        check_cols = ["ema20", "ema50", "ema200", "zlema20", "rsi", "atr", "adx", "vol_ratio"]
        tail = result[check_cols].tail(10)
        assert not tail.isnull().any().any(), (
            f"NaN found in last 10 rows:\n{tail.isnull().sum()}"
        )

    def test_returns_dataframe(self):
        df = _make_ohlcv()
        result = compute_indicators(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0


# ──────────────────────────────────────────────
# 2. calculate_signal_score
# ──────────────────────────────────────────────

class TestCalculateSignalScore:
    def _make_df_15m(self, direction="LONG"):
        df = _make_ohlcv(n=500)
        df = compute_indicators(df)
        # Nudge last row so breakout logic can trigger
        if direction == "LONG":
            df.at[df.index[-1], "close"] = df["high"].iloc[:-1].max() + 5
            df.at[df.index[-1], "zlema20"] = df.at[df.index[-1], "close"] - 1
            df.at[df.index[-1], "ema20"] = df.at[df.index[-1], "zlema20"] - 1
        return df

    def test_returns_tuple(self):
        df = self._make_df_15m()
        trend_info = {"direction": "LONG", "structure_score": 20, "swing_level": 1950.0}
        weights = {
            "trend_alignment": 35, "structure_quality": 20,
            "trigger_quality": 25, "volume_analysis": 10, "volatility_atr": 10,
        }
        result = calculate_signal_score(df, trend_info, weights)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_score_in_range(self):
        df = self._make_df_15m()
        trend_info = {"direction": "LONG", "structure_score": 20, "swing_level": 1950.0}
        weights = {
            "trend_alignment": 35, "structure_quality": 20,
            "trigger_quality": 25, "volume_analysis": 10, "volatility_atr": 10,
        }
        score, _ = calculate_signal_score(df, trend_info, weights)
        assert 0 <= score <= 100, f"Score {score} out of range [0, 100]"

    def test_details_is_dict(self):
        df = self._make_df_15m()
        trend_info = {"direction": "LONG", "structure_score": 20, "swing_level": 1950.0}
        weights = {
            "trend_alignment": 35, "structure_quality": 20,
            "trigger_quality": 25, "volume_analysis": 10, "volatility_atr": 10,
        }
        _, details = calculate_signal_score(df, trend_info, weights)
        assert isinstance(details, dict)

    def test_neutral_direction_returns_zero(self):
        df = self._make_df_15m()
        trend_info = {"direction": "NEUTRAL", "structure_score": 0, "swing_level": np.nan}
        weights = {"trend_alignment": 35, "trigger_quality": 25, "volume_analysis": 10, "volatility_atr": 10}
        score, details = calculate_signal_score(df, trend_info, weights)
        assert score == 0


# ──────────────────────────────────────────────
# 3. get_market_session
# ──────────────────────────────────────────────

class TestGetMarketSession:
    _segments = {
        "morning_trend": {
            "start": "08:30", "end": "12:00",
            "risk_modifier": 1.1, "score_threshold": 55,
        },
        "afternoon_trend": {
            "start": "12:00", "end": "17:00",
            "risk_modifier": 1.0, "score_threshold": 55,
        },
    }

    def test_returns_dict_with_required_keys(self):
        result = get_market_session("America/New_York", self._segments)
        assert isinstance(result, dict)
        assert {"status", "min_score", "modifier"}.issubset(result.keys())

    def test_status_is_valid_value(self):
        result = get_market_session("America/New_York", self._segments)
        assert result["status"] in {"OPEN", "WATCH_ONLY", "CLOSED"}

    def test_empty_segments_returns_watch_only(self):
        result = get_market_session("America/New_York", {})
        assert result["status"] == "WATCH_ONLY"

    def test_min_score_is_numeric(self):
        result = get_market_session("America/New_York", self._segments)
        assert isinstance(result["min_score"], (int, float))

    def test_modifier_is_numeric(self):
        result = get_market_session("America/New_York", self._segments)
        assert isinstance(result["modifier"], (int, float))


# ──────────────────────────────────────────────
# 4. PaperTrader
# ──────────────────────────────────────────────

class TestPaperTrader:
    @pytest.fixture()
    def trader(self, tmp_path):
        db = str(tmp_path / "test_trades.db")
        return PaperTrader(db_path=db)

    def test_open_trade_returns_int(self, trader):
        trade_id = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        assert isinstance(trade_id, int)
        assert trade_id > 0

    def test_get_open_trades_contains_opened(self, trader):
        trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        open_trades = trader.get_open_trades()
        assert len(open_trades) == 1
        assert open_trades[0]["symbol"] == "ETHUSDT"
        assert open_trades[0]["status"] == "OPEN"

    def test_update_sl_changes_value(self, trader):
        trade_id = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        trader.update_sl(trade_id, 1975.0)
        open_trades = trader.get_open_trades()
        assert abs(open_trades[0]["sl"] - 1975.0) < 1e-6

    def test_close_trade_win(self, trader):
        trade_id = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        trader.close_trade(trade_id, exit_price=2100.0, reason="TP1", pnl_r=2.0)
        open_trades = trader.get_open_trades()
        assert len(open_trades) == 0

        summary = trader.get_all_summary()
        assert summary["wins"] == 1
        assert summary["losses"] == 0

    def test_close_trade_loss(self, trader):
        trade_id = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        trader.close_trade(trade_id, exit_price=1940.0, reason="SL", pnl_r=-1.0)
        summary = trader.get_all_summary()
        assert summary["losses"] == 1
        assert summary["wins"] == 0

    def test_close_trade_be(self, trader):
        trade_id = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        trader.close_trade(trade_id, exit_price=2000.0, reason="BE", pnl_r=0.0)
        summary = trader.get_all_summary()
        assert summary["be"] == 1

    def test_get_all_summary_correct_counts(self, trader):
        t1 = trader.open_trade("ETHUSDT", "LONG", 2000.0, 1950.0, 2100.0)
        t2 = trader.open_trade("BTCUSDT", "SHORT", 60000.0, 61000.0, 58000.0)
        t3 = trader.open_trade("ETHUSDT", "LONG", 2010.0, 1960.0, 2110.0)

        trader.close_trade(t1, 2100.0, "TP1", 2.0)    # WIN
        trader.close_trade(t2, 61500.0, "SL", -1.0)   # LOSS
        trader.close_trade(t3, 2010.0, "BE", 0.0)     # BE

        summary = trader.get_all_summary()
        assert summary["total"] == 3
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["be"] == 1
        assert summary["open"] == 0
