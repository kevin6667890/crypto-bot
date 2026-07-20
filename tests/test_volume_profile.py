from dashboard.volume_profile import calculate_volume_profile


def test_volume_profile_returns_poc_and_value_area_from_confirmed_candles():
    candles = [
        {"ts": index * 900, "candle_close_ts": (index + 1) * 900, "low": 90 + index % 3, "high": 100 + index % 3, "volume": 10}
        for index in range(25)
    ]
    result = calculate_volume_profile(candles, bins=24)
    assert result["available"] is True
    assert result["val"] <= result["poc"] <= result["vah"]
    assert result["lookback_bars"] == 25
    assert 70 <= result["value_area_pct"] <= 100


def test_volume_profile_requires_enough_candles():
    assert calculate_volume_profile([], bins=24)["available"] is False
