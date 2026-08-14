from dashboard.ai_market_analysis.report_numeric_normalizer import normalize_numbers


def test_clock_components_are_not_market_numbers():
    assert normalize_numbers("17:30") == []
    assert normalize_numbers("17:30:00") == []


def test_clock_is_excluded_without_hiding_adjacent_market_price():
    assert [item["value"] for item in normalize_numbers(
        "2026-08-13 17:30 price 1873.78"
    )] == [1873.78]


def test_date_is_excluded_without_hiding_adjacent_market_price():
    assert [item["value"] for item in normalize_numbers(
        "timestamp 2026-07-16, price 1875.77"
    )] == [1875.77]


def test_non_market_timeframe_enumerator_and_validity_values_are_excluded():
    text = (
        "15 \u5206\u949f, 1 \u5c0f\u65f6, 4 \u5c0f\u65f6. "
        "\u60c5\u666f\uff1a1\uff09\u56de\u6d4b\uff1b2\uff09\u5931\u8d25\uff1b3\uff09\u5ef6\u7eed\u3002"
        "\u6709\u6548\u81f3 1786698900\uff1b\u4ef7\u683c 1878.87\u3002"
    )
    assert [item["value"] for item in normalize_numbers(text)] == [1878.87]
