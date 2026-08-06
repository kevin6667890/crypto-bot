from dashboard.ai_market_analysis.range_compression import detect_compression, detect_range
from dashboard.ai_market_analysis.timeframe_facts import build_timeframe_facts

from .helpers import BASE, breakout_path, candles


def test_bounded_atr_normalized_range_detects_golden_zone():
    rows = breakout_path()[:40]
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+40*900)
    result = detect_range(facts["confirmed_bars"], "15m", facts["atr14"]["value"])
    assert result is not None
    assert result["low"] == 1845
    assert result["high"] == 1890
    assert result["upper_touches"] >= 2 and result["lower_touches"] >= 2
    assert result["bars_inside_ratio"] >= .8


def test_high_volatility_directional_market_is_not_stable_range():
    rows = candles(80, slope=5)
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+80*900)
    assert detect_range(facts["confirmed_bars"], "15m", facts["atr14"]["value"]) is None


def test_compression_requires_multiple_evidence_sources():
    rows = breakout_path()[:40]
    # Falling volumes plus stable repeated range.
    for i, row in enumerate(rows):
        row["volume"] = 200-i*3
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+40*900)
    range_fact = detect_range(facts["confirmed_bars"], "15m", facts["atr14"]["value"])
    result = detect_compression(facts["confirmed_bars"], "15m", "CONTRACTING", range_fact, facts["quality"])
    assert result["state"] in {"BUILDING", "MATURE", "INSUFFICIENT_EVIDENCE"}
    if result["state"] != "INSUFFICIENT_EVIDENCE":
        assert len(result["evidence"]) >= 3


def test_gap_forces_insufficient_compression_evidence():
    facts = build_timeframe_facts(candles(80, gap_at=30), "ETH-USDT-SWAP", "15m", BASE+81*900)
    result = detect_compression(facts["confirmed_bars"], "15m", "CONTRACTING", None, facts["quality"])
    assert result["state"] == "INSUFFICIENT_EVIDENCE"


def test_flat_market_never_invents_breakout():
    rows = candles(80, slope=0)
    facts = build_timeframe_facts(rows, "ETH-USDT-SWAP", "15m", BASE+80*900)
    from dashboard.ai_market_analysis.structure_timeline import build_timeline
    result = build_timeline(facts, [])
    assert "BREAKOUT_ATTEMPT" not in [item["event_type"] for item in result["events"]]
