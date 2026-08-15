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


def test_real_provider_scenario_labels_are_not_market_numbers():
    """Regression for request_8812: labels 2/3 are structure, while prices remain auditable."""
    text = (
        "\u60c5\u666f1\uff1a\u5ef6\u7eed\u8def\u5f84 -> \u963b\u529b\u4f4d1946.4\u3002"
        "\u60c5\u666f2\uff1a\u6b63\u5e38\u56de\u6d4b\u3002"
        "\u60c5\u666f3\uff1a\u5931\u8d25\u7a81\u7834 -> \u652f\u6491\u4f4d1772.83-1787.17\u3002"
    )
    assert [item["value"] for item in normalize_numbers(text)] == [1946.4, 1772.83, 1787.17]


def test_real_provider_line_enumerators_and_epoch_metadata_are_not_market_numbers():
    """Regression for request_87d09 without hiding adjacent canonical prices."""
    text = (
        "level flipped_at=1784131200, price 1878.82.\n"
        "1. first path at 1900.0\n2. second path at 1780.0\n3. third path at 1512.04"
    )
    assert [item["value"] for item in normalize_numbers(text)] == [1878.82, 1900.0, 1780.0, 1512.04]
