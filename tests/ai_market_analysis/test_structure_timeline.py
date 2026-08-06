from __future__ import annotations

import pytest

from dashboard.ai_market_analysis.structure_timeline import build_timeline
from dashboard.ai_market_analysis.swing_structure import confirmed_swings
from dashboard.ai_market_analysis.timeframe_facts import build_timeframe_facts

from .helpers import BASE, breakout_path


def timeline(rows):
    decision = max(row["ts"] for row in rows)+900
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", decision)
    swings = confirmed_swings(facts["confirmed_bars"], "15m", atr=facts["atr14"]["value"])
    return build_timeline(facts, swings)


@pytest.mark.parametrize("direction", ["UP", "DOWN"])
def test_mature_range_breaks_in_both_directions(direction):
    result = timeline(breakout_path(direction=direction))
    types = [item["event_type"] for item in result["events"]]
    assert "BREAKOUT_ATTEMPT" in types
    assert "BREAKOUT_CONFIRMED" in types
    assert result["direction"] == direction


def test_attempt_immediately_reclaimed_is_not_confirmed():
    result = timeline(breakout_path(tail=[1893,1880]))
    assert result["current_phase"] == "BREAKOUT_ATTEMPT"
    assert "BREAKOUT_CONFIRMED" not in [item["event_type"] for item in result["events"]]


def test_confirmed_breakout_impulse_and_pullback():
    result = timeline(breakout_path())
    assert result["current_phase"] == "POST_BREAKOUT_PULLBACK"
    assert result["impulse"]["extreme"] == 1928
    assert result["pullback"]["structure_held"] is True


def test_zone_retest_is_recorded_without_exact_touch():
    result = timeline(breakout_path(tail=[1895,1904,1920,1925,1910,1892,1894]))
    assert result["retest"] is not None
    assert result["retest"]["zone_low"] < 1892 < result["retest"]["zone_high"]
    assert result["current_phase"] == "RETEST"


def test_retest_then_continuation_requires_confirmed_close():
    result = timeline(breakout_path(tail=[1895,1904,1920,1925,1910,1892,1900,1932]))
    assert result["current_phase"] == "CONTINUATION"
    assert result["events"][-1]["confirmed_at"] == max(row["ts"] for row in breakout_path(tail=[1895,1904,1920,1925,1910,1892,1900,1932]))+900


def test_confirmed_breakout_then_two_inside_closes_fails():
    result = timeline(breakout_path(tail=[1895,1904,1920,1910,1885,1880]))
    assert result["current_phase"] == "FAILED_BREAKOUT"
    assert result["events"][-1]["event_type"] == "FAILED_BREAKOUT"


def test_failed_breakout_then_opposite_boundary_break_is_reversal():
    result = timeline(breakout_path(tail=[1895,1904,1920,1910,1885,1880,1840]))
    assert result["current_phase"] == "REVERSAL"
    assert result["direction"] == "DOWN"


def test_long_wick_without_close_outside_is_not_attempt():
    rows = breakout_path()[:40]
    rows.append({"ts": rows[-1]["ts"]+900, "open": 1880, "high": 1920, "low": 1870,
                 "close": 1885, "volume": 300, "confirmed": True})
    result = timeline(rows)
    assert "BREAKOUT_ATTEMPT" not in [item["event_type"] for item in result["events"]]


def test_unconfirmed_breakout_is_live_observation_only():
    rows = breakout_path()[:40]
    rows.append({"ts": rows[-1]["ts"]+900, "open": 1888, "high": 1920, "low": 1885,
                 "close": 1910, "volume": 300, "confirmed": False})
    # Decision is inside that candle; helper's +900 cutoff still cannot admit confirmed=False.
    result = timeline(rows)
    assert "BREAKOUT_ATTEMPT" not in [item["event_type"] for item in result["events"]]


def test_gap_downgrades_breakout_confidence():
    result = timeline(breakout_path(gap_at=20))
    breakout = next(item for item in result["events"] if item["event_type"] == "BREAKOUT_CONFIRMED")
    assert breakout["confidence"] == "LOW"


def test_event_identity_does_not_depend_on_recalculation():
    rows = breakout_path()
    assert [item["event_id"] for item in timeline(rows)["events"]] == [item["event_id"] for item in timeline(rows)["events"]]


def test_future_data_cannot_rewrite_old_timeline():
    rows = breakout_path()
    cutoff_rows = rows[:-1]
    before = timeline(cutoff_rows)
    decision = max(row["ts"] for row in cutoff_rows)+900
    facts = build_timeframe_facts(rows+[dict(rows[-1], ts=rows[-1]["ts"]+900)], "ETH-USDT-SWAP", "15m", decision)
    after = build_timeline(facts, confirmed_swings(facts["confirmed_bars"], "15m", atr=facts["atr14"]["value"]))
    assert before == after
