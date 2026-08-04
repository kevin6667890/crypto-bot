from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import time

import pytest

from dashboard.decision_engine import LIVE_STRATEGY_VERSION, MarketContext, evaluate_decision
from dashboard.market_context_v2 import (
    BoundedMarketDataReaderV2, CONFLUENCE_THRESHOLD_PCT, CONTEXT_VERSION,
    IndicatorValueV2, MarketContextServiceV2, MarketIndicatorRegistryV2,
    MarketLevelV2, STOCH_RSI_VERSION, TIMEFRAME_SECONDS,
    aggregate_confirmed_daily_to_weekly, confirmed_candles_as_of,
    merge_confluence_levels, stoch_rsi_series, _combination,
)
from dashboard.strategy_rules import StrategyParameters


BASE = 1_704_067_200  # 2024-01-01 00:00:00 Monday UTC


def candles(count: int, timeframe: str = "15m", *, start: int = BASE,
            gap_at: int | None = None, confirmed: bool = True) -> list[dict]:
    width = TIMEFRAME_SECONDS[timeframe]
    rows = []
    for index in range(count):
        offset = index + (1 if gap_at is not None and index >= gap_at else 0)
        close = 100 + index * 0.25 + ((index % 5) - 2) * 0.03
        rows.append({"ts": start + offset*width, "candle_close_ts": start+(offset+1)*width,
                     "open": close-0.4, "high": close+1.0, "low": close-1.0,
                     "close": close, "volume": 100+index, "confirmed": confirmed})
    return rows


def test_stoch_rsi_matches_hand_calculated_sample() -> None:
    values = [float(value) for value in range(1, 15)] + [8.0, 7.0, 6.0, 5.0, 4.0]
    result = stoch_rsi_series(values)
    assert result[13]["stoch_rsi"] == 100.0
    assert result[14]["stoch_rsi"] == 50.0
    assert result[15]["stoch_rsi"] == pytest.approx(4/11*100)
    assert result[15]["stoch_rsi_k"] == pytest.approx((100+50+4/11*100)/3)
    assert result[-1]["stoch_rsi_d"] is not None


def test_stoch_rsi_warmup_is_null() -> None:
    result = stoch_rsi_series([50.0]*13)
    assert all(row == {"stoch_rsi": None, "stoch_rsi_k": None, "stoch_rsi_d": None} for row in result)


def test_stoch_rsi_zero_denominator_is_null_not_nan() -> None:
    result = stoch_rsi_series([50.0]*30)
    assert result[-1] == {"stoch_rsi": None, "stoch_rsi_k": None, "stoch_rsi_d": None}


def test_stoch_rsi_is_causal_when_future_values_are_appended() -> None:
    original = [float((index*7) % 31) for index in range(60)]
    before = stoch_rsi_series(original)
    after = stoch_rsi_series(original + [99.0, 1.0, 88.0])
    assert after[:len(before)] == before
    assert STOCH_RSI_VERSION == "stoch-rsi-v2-rsi14-stoch14-k3-d3"


@pytest.mark.parametrize("lower,higher", [("15m", "1H"), ("1H", "4H"), ("4H", "1D")])
def test_lower_timeframe_only_reads_closed_higher_timeframe(lower: str, higher: str) -> None:
    decision_time = BASE + 10*TIMEFRAME_SECONDS[higher]
    rows = candles(12, higher)
    assert confirmed_candles_as_of(rows, higher, decision_time)[-1]["candle_close_ts"] == decision_time
    assert rows[10]["candle_close_ts"] > decision_time


def test_daily_only_reads_completed_week() -> None:
    daily = candles(14, "1D")
    weeks = aggregate_confirmed_daily_to_weekly(daily, BASE+14*86_400)
    assert [row["candle_close_ts"] for row in weeks] == [BASE+7*86_400, BASE+14*86_400]


def test_unclosed_higher_timeframe_is_unavailable() -> None:
    row = candles(1, "4H")[0]
    assert confirmed_candles_as_of([row], "4H", row["candle_close_ts"]-1) == []
    row["confirmed"] = False
    assert confirmed_candles_as_of([row], "4H", row["candle_close_ts"]+1) == []


def test_week_boundary_is_monday_utc_and_partial_week_is_not_confirmed() -> None:
    daily = candles(8, "1D")
    assert aggregate_confirmed_daily_to_weekly(daily, BASE+6*86_400+1) == []
    result = aggregate_confirmed_daily_to_weekly(daily, BASE+7*86_400)
    assert len(result) == 1 and result[0]["ts"] == BASE
    assert result[0]["confirmed"] is True


def test_ma_slopes_atr_bollinger_volume_and_wicks() -> None:
    rows = candles(240)
    context = MarketIndicatorRegistryV2().calculate(rows, "15m", rows[-1]["candle_close_ts"])
    assert context.trend["ema20_slope"].value > 0
    assert context.trend["ma60_slope"].value > 0
    assert context.trend["ma200_slope"].value > 0
    assert context.volatility["atr_percentage"].value == pytest.approx(2/rows[-1]["close"]*100)
    assert context.volatility["bollinger_bandwidth"].value > 0
    expected_baseline = sum(row["volume"] for row in rows[-21:-1])/20
    assert context.volume["volume_ratio"].value == pytest.approx(rows[-1]["volume"]/expected_baseline)
    assert context.volume["candle_body_percentage"].value == pytest.approx(20.0)
    assert context.volume["upper_wick_percentage"].value == pytest.approx(50.0)
    assert context.volume["lower_wick_percentage"].value == pytest.approx(30.0)


def test_swing_levels_use_only_past_confirmed_rows() -> None:
    rows = candles(20)
    rows[10].update(high=999.0)
    registry = MarketIndicatorRegistryV2()
    cutoff = rows[12]["candle_close_ts"]
    before = registry.calculate(rows, "15m", cutoff).structure["recent_confirmed_swing_high"]
    with_future = registry.calculate(rows + [{**rows[-1], "ts": rows[-1]["ts"]+900,
                                              "candle_close_ts": rows[-1]["candle_close_ts"]+900,
                                              "high": 5000.0}], "15m", cutoff).structure["recent_confirmed_swing_high"]
    assert before.value == 999.0 and asdict(before) == asdict(with_future)


def test_confluence_zone_is_deterministic_and_versioned() -> None:
    candidates = [MarketLevelV2("EMA20", "15m", 100.0, BASE, 0, 2, True, ("15m:EMA20",)),
                  MarketLevelV2("MA60", "1H", 100.2, BASE+1, 0.2, 3, True, ("1H:MA60",)),
                  MarketLevelV2("MA200", "4H", 101.0, BASE+2, 1, 1, True, ("4H:MA200",))]
    first = merge_confluence_levels(candidates, 100.0)
    second = merge_confluence_levels(list(reversed(candidates)), 100.0)
    assert first == second and first[0].type == "CONFLUENCE_ZONE"
    assert first[0].touches == 5 and CONFLUENCE_THRESHOLD_PCT == 0.25


class FakeReader:
    def __init__(self, datasets: dict[str, list[dict]], flow: dict | None = None) -> None:
        self.datasets, self.flow_data = datasets, flow or {key: {} for key in ("cvd", "oi", "funding_settled", "funding_predicted", "basis")}
        self.calls: list[tuple] = []

    def candles(self, instrument: str, timeframe: str, as_of: int, limit: int) -> list[dict]:
        self.calls.append(("candles", instrument, timeframe, as_of, limit))
        return list(self.datasets.get(timeframe, []))

    def flow(self, instrument: str, as_of: int, execution_timeframe: str) -> dict:
        self.calls.append(("flow", instrument, as_of, execution_timeframe))
        return self.flow_data


def test_missing_cvd_is_null_and_not_zero() -> None:
    rows = candles(240)
    result = MarketContextServiceV2(FakeReader({"15m": rows})).context("ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    assert result["flow"]["cvd"]["current"]["value"] is None
    assert result["flow"]["cvd"]["current"]["available"] is False


def test_stale_oi_is_marked() -> None:
    rows = candles(240); as_of = rows[-1]["candle_close_ts"]
    flow = {key: {} for key in ("cvd", "oi", "funding_settled", "funding_predicted", "basis")}
    flow["oi"] = {"current": 1000.0, "absolute_change": 10.0, "percentage_change": 1.0,
                  "timestamp": as_of-181, "start_timestamp": as_of-3600, "partial": False}
    result = MarketContextServiceV2(FakeReader({"15m": rows}, flow)).context("ETH-USDT-SWAP", as_of=as_of)
    assert result["flow"]["oi"]["current"]["stale"] is True
    assert "oi" in result["quality"]["stale_sources"]


def test_gap_state_propagates_to_indicators_and_overall_quality() -> None:
    rows = candles(240, gap_at=100); as_of = rows[-1]["candle_close_ts"]
    result = MarketContextServiceV2(FakeReader({"15m": rows})).context("ETH-USDT-SWAP", as_of=as_of)
    assert result["timeframes"]["15m"]["quality"]["gaps"]
    assert result["timeframes"]["15m"]["trend"]["ema20"]["partial"] is True
    assert result["quality"]["gaps"]


@pytest.mark.parametrize("price,other,name,state", [
    (1.0, 2.0, "OI", "PRICE_UP_OI_UP"), (1.0, -2.0, "OI", "PRICE_UP_OI_DOWN"),
    (-1.0, 2.0, "OI", "PRICE_DOWN_OI_UP"), (-1.0, -2.0, "OI", "PRICE_DOWN_OI_DOWN"),
    (1.0, 2.0, "CVD", "PRICE_UP_CVD_UP"), (1.0, -2.0, "CVD", "PRICE_UP_CVD_DOWN"),
    (-1.0, 2.0, "CVD", "PRICE_DOWN_CVD_UP"), (-1.0, -2.0, "CVD", "PRICE_DOWN_CVD_DOWN"),
])
def test_price_flow_combination_facts(price: float, other: float, name: str, state: str) -> None:
    result = _combination(price, other, name, BASE, BASE+3600, "AVAILABLE")
    assert result["state"] == state and "interpretation" not in result


def test_price_flow_combination_requires_both_inputs() -> None:
    assert _combination(None, 1.0, "OI", None, None, "MISSING")["state"] == "INSUFFICIENT_DATA"


def test_context_has_no_future_data_and_all_source_timestamps_are_bounded() -> None:
    rows = candles(241); as_of = rows[-2]["candle_close_ts"]
    result = MarketContextServiceV2(FakeReader({"15m": rows})).context("ETH-USDT-SWAP", as_of=as_of)
    assert result["price"]["source_timestamp"] == as_of
    for group in result["timeframes"]["15m"].values():
        if isinstance(group, dict) and "source_timestamp" not in group:
            for indicator in group.values():
                if isinstance(indicator, dict) and indicator.get("source_timestamp") is not None:
                    assert indicator["source_timestamp"] <= as_of


def test_context_reader_contract_does_not_offer_write_backfill_or_maintenance() -> None:
    public = {name for name in dir(BoundedMarketDataReaderV2) if not name.startswith("_")}
    assert public == {"candles", "flow", "explain_plans"}


def _seed_database(path: Path, rows: list[dict]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
      CREATE TABLE historical_candles(instrument TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,confirmed INTEGER,source TEXT,PRIMARY KEY(instrument,timeframe,ts));
      CREATE INDEX idx_historical_range ON historical_candles(instrument,timeframe,ts);
    """)
    connection.executemany("INSERT INTO historical_candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                           [("ETH-USDT", "15m", row["ts"], row["open"], row["high"], row["low"], row["close"], row["volume"], 1, "fixture") for row in rows])
    connection.commit(); connection.close()


def test_bounded_query_plan_uses_composite_index_and_latency_under_500ms(tmp_path: Path) -> None:
    path = tmp_path/"context.db"; rows = candles(600); _seed_database(path, rows)
    reader = BoundedMarketDataReaderV2(path)
    plans = reader.explain_plans("ETH-USDT-SWAP", rows[-1]["candle_close_ts"])
    assert plans and all("USING INDEX" in plan or "USING COVERING INDEX" in plan for plan in plans)
    started = time.perf_counter()
    result = MarketContextServiceV2(reader).context("ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    assert (time.perf_counter()-started)*1000 < 500
    assert result["version"] == CONTEXT_VERSION


def test_microstructure_query_plans_are_indexed_and_bounded(tmp_path: Path) -> None:
    paper = tmp_path/"paper.db"; micro = tmp_path/"micro.db"; rows = candles(20); _seed_database(paper, rows)
    connection = sqlite3.connect(micro)
    connection.executescript("""
      CREATE TABLE cvd_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,PRIMARY KEY(instrument,resolution,bucket_ms));
      CREATE TABLE oi_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,PRIMARY KEY(instrument,resolution,bucket_ms));
      CREATE TABLE basis_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,PRIMARY KEY(instrument,resolution,bucket_ms));
      CREATE TABLE funding_settled(instrument TEXT,source_ts_ms INTEGER);
      CREATE INDEX idx_funding_settled_time ON funding_settled(instrument,source_ts_ms);
      CREATE TABLE funding_predicted(instrument TEXT,source_ts_ms INTEGER);
      CREATE INDEX idx_funding_predicted_time ON funding_predicted(instrument,source_ts_ms);
    """)
    connection.close()
    plans = BoundedMarketDataReaderV2(paper, micro).explain_plans("ETH-USDT-SWAP", rows[-1]["candle_close_ts"])
    assert len(plans) == 6
    assert all("SCAN" not in plan.upper() for plan in plans)
    assert all("INDEX" in plan.upper() for plan in plans)


def test_read_only_api_service_does_not_change_database(tmp_path: Path) -> None:
    path = tmp_path/"context.db"; rows = candles(240); _seed_database(path, rows)
    before = path.read_bytes()
    MarketContextServiceV2(BoundedMarketDataReaderV2(path)).context("ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    assert path.read_bytes() == before


def test_openapi_contains_complete_context_contract_and_route() -> None:
    schema = json.loads(Path("frontend/openapi/openapi.json").read_text(encoding="utf-8"))
    assert "/api/market/context" in schema["paths"]
    required = schema["components"]["schemas"]["MarketAnalysisContextV2"]["required"]
    assert set(required) == {"version", "instrument", "as_of", "execution_timeframe", "price", "timeframes", "flow", "levels", "quality"}
    assert 'parsed.path == "/api/market/context"' in Path("dashboard/paper_api.py").read_text(encoding="utf-8")


def test_http_context_route_only_invokes_read_only_context_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from dashboard import paper_api
    calls = []
    class Context:
        def context(self, instrument, *, as_of, execution_timeframe):
            calls.append((instrument, as_of, execution_timeframe))
            return {"version": CONTEXT_VERSION}
    monkeypatch.setattr(paper_api, "MARKET_CONTEXT_V2", Context())
    handler = object.__new__(paper_api.Handler)
    handler.path = "/api/market/context?instrument=ETH-USDT-SWAP&as_of=1704067200&execution_timeframe=1H"
    handler.headers = {}; handler.client_address = ("127.0.0.1", 1)
    captured = []
    handler._send = lambda payload, status=200: captured.append((payload, int(status)))
    handler.do_GET()
    assert calls == [("ETH-USDT-SWAP", BASE, "1H")]
    assert captured == [({"version": CONTEXT_VERSION}, 200)]


def test_http_context_route_requires_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    from dashboard import paper_api
    handler = object.__new__(paper_api.Handler); handler.path = "/api/market/context"
    handler.headers = {}; handler.client_address = ("127.0.0.1", 1)
    captured = []; handler._send = lambda payload, status=200: captured.append((payload, int(status)))
    handler.do_GET()
    assert captured == [({"error": "instrument is required"}, 400)]


def test_legacy_decision_result_and_version_are_unchanged() -> None:
    decision = evaluate_decision(StrategyParameters(), MarketContext(
        "ETH-USDT", "15m", BASE, 100.0,
        {"fast_ma": None, "slow_ma": None, "ema": None, "rsi": None, "atr": None, "volume_ratio": None}))
    assert LIVE_STRATEGY_VERSION == "live-mtf-flow-v1"
    assert decision.action == "WAIT" and decision.entry_allowed is False


def test_context_schema_contains_no_signal_or_order_fields() -> None:
    rows = candles(240); result = MarketContextServiceV2(FakeReader({"15m": rows})).context(
        "ETH-USDT-SWAP", as_of=rows[-1]["candle_close_ts"])
    forbidden = {"action", "signal", "side", "entry_price", "stop_loss", "take_profit", "profit_probability", "strategy_name"}
    assert forbidden.isdisjoint(result)
