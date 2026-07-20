from dashboard.volume_profile import calculate_trade_volume_profile, calculate_volume_profile


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


def test_trade_volume_profile_uses_executed_trade_prices_not_candle_ranges():
    rows = [
        {"price": 100, "buy_notional": 100, "sell_notional": 0, "trade_count": 1},
        {"price": 101, "buy_notional": 400, "sell_notional": 100, "trade_count": 4},
        {"price": 102, "buy_notional": 0, "sell_notional": 50, "trade_count": 1},
    ]
    result = calculate_trade_volume_profile(rows, bins=12)
    assert result["available"] is True
    assert 100 < result["poc"] < 102
    assert result["val"] <= result["poc"] <= result["vah"]
