from dashboard.ai_market_analysis.intelligence_quality import classify_evidence_quality


def base(*, missing_core: str | None = None, weekly_bars: int = 42, flow_state: str = "FLOW_COMPLETE"):
    coverage = {
        tf: {"quality": "MISSING" if tf == missing_core else "COMPLETE", "actual_bars": 0 if tf == missing_core else 220}
        for tf in ("15m", "1H", "4H", "1D")
    }
    coverage["1W"] = {"quality": "WARMUP_INCOMPLETE" if weekly_bars < 200 else "COMPLETE", "actual_bars": weekly_bars}
    return {
        "timeframe_coverage": coverage,
        "order_flow_phases": [{"phase": "CURRENT", "metrics": {"quality": {"flow_coverage": {
            "state": flow_state, "coverage_ratio": .9833, "gap_count": 1,
            "max_consecutive_gap_minutes": 1,
        }}}}],
    }


def test_core_complete_weekly_missing_is_available():
    value = classify_evidence_quality(base(weekly_bars=0))
    assert value["analysis_availability"] == "ANALYSIS_AVAILABLE"
    assert value["long_term_quality"] == "UNAVAILABLE"


def test_enhanced_and_optional_never_block_core_analysis():
    for flow in ("FLOW_PARTIAL_USABLE", "FLOW_UNAVAILABLE"):
        value = classify_evidence_quality(base(flow_state=flow), {"items": [], "quality": "UNAVAILABLE"})
        assert value["analysis_availability"] == "ANALYSIS_AVAILABLE"
        assert value["flow_quality"] == flow
        assert value["macro_quality"] == "NOT_INCLUDED"


def test_core_missing_degrades_or_unavailable():
    assert classify_evidence_quality(base(missing_core="1D"))["analysis_availability"] == "ANALYSIS_DEGRADED"
    value = base(missing_core="1D")
    for tf in ("15m", "1H"):
        value["timeframe_coverage"][tf] = {"quality": "MISSING", "actual_bars": 0}
    assert classify_evidence_quality(value)["analysis_availability"] == "ANALYSIS_UNAVAILABLE"


def test_old_flow_gap_outside_current_window_expires():
    value = base()
    value["order_flow_phases"].insert(0, {"phase": "IMPULSE", "metrics": {"quality": {"flow_coverage": {
        "state": "FLOW_UNAVAILABLE", "coverage_ratio": .2, "gap_count": 9,
    }}}})
    assert classify_evidence_quality(value)["flow_quality"] == "FLOW_COMPLETE"
