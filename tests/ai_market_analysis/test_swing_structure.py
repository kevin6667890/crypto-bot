from dashboard.ai_market_analysis.swing_structure import confirmed_swings
from dashboard.ai_market_analysis.timeframe_facts import build_timeframe_facts

from .helpers import BASE


def _rows(highs, lows=None):
    lows = lows or [value-2 for value in highs]
    return [{"ts": BASE+i*900, "open": (high+lows[i])/2, "high": high, "low": lows[i],
             "close": (high+lows[i])/2, "volume": 100, "confirmed": True}
            for i, high in enumerate(highs)]


def test_pivot_is_invisible_until_two_right_bars_close():
    rows = _rows([1,2,5,3,2,4])
    early = build_timeframe_facts(rows[:4], "ETH-USDT-SWAP", "15m", BASE+4*900)
    late = build_timeframe_facts(rows[:5], "ETH-USDT-SWAP", "15m", BASE+5*900)
    assert confirmed_swings(early["confirmed_bars"], "15m") == []
    swing = confirmed_swings(late["confirmed_bars"], "15m")[0]
    assert swing["pivot_time"] == BASE+2*900
    assert swing["confirmed_at"] == BASE+5*900


def test_hh_hl_lh_ll_sequence_is_classified():
    highs = [1,2,5,3,2,4,7,5,4,5,6,4,3,3,5,3,2]
    lows = [-1,1,3,2,0,2,5,3,1,3,4,2,-1,1,3,1,0]
    facts = build_timeframe_facts(_rows(highs,lows), "ETH-USDT-SWAP", "15m", BASE+len(highs)*900)
    labels = {item["classification"] for item in confirmed_swings(facts["confirmed_bars"], "15m", atr=.5)}
    assert {"HH", "HL", "LH", "LL"}.issubset(labels)


def test_equal_high_and_low_use_tolerance():
    highs = [1,2,5,3,2,4,5.001,3,2,3,2]
    lows = [-1,1,3,2,0,2,3,2,.001,2,1]
    facts = build_timeframe_facts(_rows(highs,lows), "ETH-USDT-SWAP", "15m", BASE+len(highs)*900)
    labels = [item["classification"] for item in confirmed_swings(facts["confirmed_bars"], "15m", atr=.1, tolerance_pct=.001)]
    assert "EQUAL_HIGH" in labels and "EQUAL_LOW" in labels


def test_swing_sequence_is_bounded_to_twelve():
    highs = [10+(i%4) for i in range(100)]
    facts = build_timeframe_facts(_rows(highs), "ETH-USDT-SWAP", "15m", BASE+100*900)
    assert len(confirmed_swings(facts["confirmed_bars"], "15m")) <= 12


def test_appending_future_bars_does_not_change_prior_confirmed_swings():
    rows = _rows([1,2,5,3,2,4,7,4,3])
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+len(rows)*900)
    before = confirmed_swings(facts["confirmed_bars"], "15m")
    later = build_timeframe_facts(rows+_rows([20,1])[0:2], "ETH-USDT-SWAP", "15m", BASE+len(rows)*900)
    assert confirmed_swings(later["confirmed_bars"], "15m") == before
