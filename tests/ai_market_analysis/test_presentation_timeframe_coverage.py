from dashboard.ai_market_analysis.presentation_feed import _timeframe_quality


class Repository:
    def __init__(self, context):
        self.context = context

    def load_context(self, _context_id):
        return self.context


def test_future_context_uses_frozen_compact_timeframe_coverage():
    coverage = {
        timeframe: {"quality": "COMPLETE", "actual_bars": bars, "required_bars": 200, "latest": 1_787_110_200}
        for timeframe, bars in (("15m", 512), ("1H", 299), ("4H", 512), ("1D", 299))
    }
    coverage["1W"] = {"quality": "WARMUP_INCOMPLETE", "actual_bars": 42, "required_bars": 200, "latest": 1_787_097_600}
    value = _timeframe_quality(Repository({"base_context": {"timeframe_coverage": coverage}}), {"context_id": "new"})
    assert [(item["timeframe"], item["availability"], item["bar_count"]) for item in value] == [
        ("15m", "AVAILABLE", 512), ("1H", "AVAILABLE", 299), ("4H", "AVAILABLE", 512),
        ("1D", "AVAILABLE", 299), ("1W", "PARTIAL", 42),
    ]


def test_historical_context_without_coverage_is_not_rewritten():
    value = _timeframe_quality(Repository({"base_context": {"timeframe_structures": []}}), {"context_id": "old"})
    assert all(item["availability"] == "MISSING" for item in value)
