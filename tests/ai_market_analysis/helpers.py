from __future__ import annotations

from dashboard.ai_market_analysis.versions import TIMEFRAME_SECONDS

BASE = 1_704_067_200


def candles(count: int, timeframe: str = "15m", *, start: int = BASE,
            slope: float = .2, gap_at: int | None = None, confirmed: bool = True,
            instrument: str = "ETH-USDT-SWAP") -> list[dict]:
    width = TIMEFRAME_SECONDS[timeframe]
    rows = []
    for i in range(count):
        offset = i + (1 if gap_at is not None and i >= gap_at else 0)
        close = 100 + slope*i + ((i % 5)-2)*.03
        rows.append({"instrument": instrument, "timeframe": timeframe,
                     "ts": start+offset*width, "open": close-.4, "high": close+1,
                     "low": close-1, "close": close, "volume": 100+i,
                     "confirmed": confirmed, "source": "synthetic",
                     "source_timestamp": start+(offset+1)*width})
    return rows


def breakout_path(*, direction: str = "UP", tail: list[float] | None = None,
                  gap_at: int | None = None) -> list[dict]:
    closes = [1848,1854,1862,1872,1882,1888,1880,1870,1858,1848]*4
    tail = tail if tail is not None else [1895,1904,1920,1925,1915,1906,1900]
    closes += tail
    rows = []
    for i, close in enumerate(closes):
        high = 1890 if close == 1888 else close+3
        low = 1845 if close == 1848 else close-3
        rows.append({"instrument": "ETH-USDT-SWAP", "timeframe": "15m",
                     "ts": BASE+(i+(1 if gap_at is not None and i >= gap_at else 0))*900,
                     "open": close-1, "high": high, "low": low, "close": close,
                     "volume": 80 if i < 40 else 160, "confirmed": True,
                     "source": "golden-price-structure"})
    if direction == "DOWN":
        for row in rows:
            old_open, old_high, old_low, old_close = row["open"], row["high"], row["low"], row["close"]
            row.update(open=3735-old_open, high=3735-old_low, low=3735-old_high, close=3735-old_close)
    return rows


def datasets() -> dict[str, list[dict]]:
    return {
        "15m": candles(240, "15m"),
        "1H": candles(240, "1H", slope=.4),
        "4H": candles(240, "4H", slope=.6),
        "1D": candles(1400, "1D", slope=.8),
    }
