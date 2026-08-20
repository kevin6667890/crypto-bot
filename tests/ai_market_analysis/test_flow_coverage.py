from dashboard.ai_market_analysis.flow_coverage import classify_flow_coverage


def coverage(missing=(), end=900):
    return classify_flow_coverage(
        snapshot_start=0, snapshot_end=end, bucket_seconds=60,
        timestamps=[value for value in range(0, end, 60) if value not in missing],
    )


def test_complete_coverage_allows_deterministic_flow():
    result = coverage()
    assert result["state"] == "FLOW_COMPLETE"
    assert result["coverage_ratio"] == 1.0


def test_isolated_old_one_minute_gap_is_partial_usable_not_unavailable():
    result = coverage((60,))
    assert result["state"] == "FLOW_PARTIAL_USABLE"
    assert result["total_gap_minutes"] == 1
    assert result["synthetic_data"] is False


def test_consecutive_large_gaps_are_unavailable():
    result = coverage((120, 180, 240, 300, 360, 420), end=1200)
    assert result["state"] == "FLOW_UNAVAILABLE"
    assert result["max_consecutive_gap_minutes"] == 6


def test_recent_gap_is_downgraded_and_natural_recovery_is_complete():
    assert coverage((840,), end=900)["state"] == "FLOW_UNAVAILABLE"
    assert coverage((), end=900)["state"] == "FLOW_COMPLETE"
