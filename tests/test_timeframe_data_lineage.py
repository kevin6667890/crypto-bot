from __future__ import annotations

import sqlite3

from dashboard.market_context_v2 import (
    BoundedMarketDataReaderV2,
    MarketContextServiceV2,
    MarketIndicatorRegistryV2,
    TIMEFRAME_SECONDS,
)
from dashboard.market_state_v2 import MarketStateEngineV2
from dashboard.paper_api import MARKET_DATA_INSTRUMENTS, PaperService


AS_OF = 1_786_924_800  # 2026-08-17 00:00:00 Monday UTC


def _rows(timeframe: str, count: int = 220, *, end: int = AS_OF) -> list[dict]:
    width = TIMEFRAME_SECONDS[timeframe]
    start = end - count * width
    return [
        {
            "ts": start + index * width,
            "candle_close_ts": start + (index + 1) * width,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 10 + index,
            "confirmed": True,
        }
        for index in range(count)
    ]


def test_materialization_uses_utc_daily_and_persists_every_native_frame() -> None:
    service = object.__new__(PaperService)
    calls: list[tuple[str, str, int]] = []
    stored: list[tuple[str, str, int]] = []

    def candles(instrument: str, raw_interval: str, limit: int) -> list[dict]:
        calls.append((instrument, raw_interval, limit))
        logical = "1D" if raw_interval == "1Dutc" else raw_interval
        return _rows(logical, 2)

    service._candles = candles  # type: ignore[method-assign]
    service._store_candles = lambda instrument, timeframe, rows: stored.append(  # type: ignore[method-assign]
        (instrument, timeframe, len(rows))
    )
    result = service._materialize_market_timeframes("ETH-USDT")
    assert [item[1] for item in calls] == ["15m", "1H", "4H", "1Dutc"]
    assert [item[1] for item in stored] == ["15m", "1H", "4H", "1D"]
    assert set(result) == {"15m", "1H", "4H", "1D"}


def test_sol_is_data_only_and_does_not_expand_paper_execution_allowlist() -> None:
    from dashboard.paper_api import INSTRUMENTS

    assert "SOL-USDT" in MARKET_DATA_INSTRUMENTS
    assert "SOL-USDT" not in INSTRUMENTS


def test_reader_merges_research_history_with_fresher_live_materialization(tmp_path) -> None:
    path = tmp_path / "paper.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE historical_candles(
        instrument TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,
        close REAL,volume REAL,confirmed INTEGER,source TEXT,
        PRIMARY KEY(instrument,timeframe,ts));
      CREATE INDEX idx_historical_range ON historical_candles(instrument,timeframe,ts);
      CREATE TABLE market_candles(
        instrument TEXT,bar TEXT,ts INTEGER,open REAL,high REAL,low REAL,
        close REAL,volume REAL,PRIMARY KEY(instrument,bar,ts));
    """)
    old = _rows("4H", 210, end=AS_OF - 10 * TIMEFRAME_SECONDS["4H"])
    live = _rows("4H", 20)
    connection.executemany(
        "INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?)",
        [("ETH-USDT", "4H", r["ts"], r["open"], r["high"], r["low"],
          r["close"], r["volume"], 1, "research") for r in old],
    )
    connection.executemany(
        "INSERT INTO market_candles VALUES(?,?,?,?,?,?,?,?)",
        [("ETH-USDT", "4H", r["ts"], r["open"], r["high"], r["low"],
          r["close"], r["volume"]) for r in live],
    )
    connection.commit(); connection.close()

    rows = BoundedMarketDataReaderV2(path).candles("ETH-USDT-SWAP", "4H", AS_OF, 512)
    assert rows[-1]["candle_close_ts"] == AS_OF
    assert rows[-1]["_source_store"] == "market_candles"
    assert len({row["ts"] for row in rows}) == len(rows)


def test_observation_contract_distinguishes_missing_stale_and_warmup() -> None:
    registry = MarketIndicatorRegistryV2()
    missing = registry.calculate([], "1H", AS_OF)
    stale_rows = _rows("4H", 220, end=AS_OF - 3 * TIMEFRAME_SECONDS["4H"])
    stale = registry.calculate(stale_rows, "4H", AS_OF)
    partial = registry.calculate(_rows("1W", 42), "1W", AS_OF)
    assert missing.quality.status == "MISSING"
    assert stale.quality.status == "STALE"
    assert partial.quality.status == "PARTIAL"
    assert "indicator warmup requires 200 bars" in partial.quality.notes[0]


class _Reader:
    def __init__(self) -> None:
        self.data = {frame: _rows(frame) for frame in ("15m", "1H", "4H", "1D")}

    def candles(self, _instrument: str, timeframe: str, _as_of: int, _limit: int) -> list[dict]:
        return self.data.get(timeframe, [])

    def flow(self, _instrument: str, _as_of: int, _execution_timeframe: str) -> dict:
        return {key: {} for key in ("cvd", "oi", "funding_settled", "funding_predicted", "basis")}


def test_market_state_carries_canonical_observation_and_structure_semantics() -> None:
    context = MarketContextServiceV2(_Reader()).context("ETH-USDT-SWAP", as_of=AS_OF)
    state = MarketStateEngineV2().evaluate(context)
    for timeframe in ("15m", "1H", "4H", "1D"):
        observation = state["timeframes"][timeframe]["observation"]
        assert observation["availability"] == "AVAILABLE"
        assert observation["structure_state"] == state["timeframes"][timeframe]["primary_state"]
        assert observation["symbol"] == "ETH-USDT-SWAP"
        assert observation["bar_count"] >= observation["required_bar_count"]
        assert observation["latest_aggregated_candle_timestamp"] == observation["source_at"]
    weekly = state["timeframes"]["1W"]["observation"]
    assert weekly["availability"] == "PARTIAL"
    assert weekly["reason_codes"] == ("INDICATOR_WARMUP_INCOMPLETE",)
