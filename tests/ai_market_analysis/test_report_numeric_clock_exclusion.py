from dashboard.ai_market_analysis.report_numeric_normalizer import normalize_numbers


def test_clock_components_are_not_market_numbers():
    assert normalize_numbers("17:30") == []
    assert normalize_numbers("17:30:00") == []


def test_clock_is_excluded_without_hiding_adjacent_market_price():
    assert [item["value"] for item in normalize_numbers(
        "2026-08-13 17:30 price 1873.78"
    )] == [1873.78]
