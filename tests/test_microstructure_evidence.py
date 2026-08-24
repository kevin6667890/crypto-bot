from __future__ import annotations

import sqlite3

from dashboard.microstructure_evidence import (
    CanonicalMicrostructureEvidenceAdapter,
    EvidenceWindow,
    MarketIdentity,
    canonical_market_evidence_set,
)


def _live(path):
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE cvd_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,
              cumulative_anchored REAL,first_source_ts_ms INTEGER,last_source_ts_ms INTEGER,gap_flag INTEGER);
            CREATE TABLE oi_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,
              last_value REAL,first_source_ts_ms INTEGER,last_source_ts_ms INTEGER,gap_flag INTEGER);
            CREATE TABLE funding_settled(instrument TEXT,funding_time_ms INTEGER,funding_rate REAL,source_ts_ms INTEGER);
            CREATE TABLE basis_aggregates(instrument TEXT,resolution TEXT,bucket_ms INTEGER,last_basis REAL,
              last_basis_pct REAL,first_source_ts_ms INTEGER,last_source_ts_ms INTEGER,gap_flag INTEGER);
            CREATE TABLE trade_flow_observations(instrument TEXT,state TEXT,source_ts_ms INTEGER,
              side TEXT,price REAL,notional REAL);
        """)
        c.executemany("INSERT INTO cvd_aggregates VALUES(?,?,?,?,?,?,?)", [
            ("BTC-USDT-SWAP", "1m", 60_000, 10.0, 60_001, 60_010, 0),
            ("BTC-USDT-SWAP", "1m", 180_000, 30.0, 180_001, 180_010, 1),
        ])
        c.execute("INSERT INTO oi_aggregates VALUES(?,?,?,?,?,?,?)", ("BTC-USDT-SWAP", "1m", 60_000, 100.0, 60_001, 60_010, 0))
        c.execute("INSERT INTO funding_settled VALUES(?,?,?,?)", ("BTC-USDT-SWAP", 60_000, 0.0001, 60_010))
        c.execute("INSERT INTO basis_aggregates VALUES(?,?,?,?,?,?,?,?)", ("BTC-USDT-SWAP", "1m", 60_000, 12.0, 0.0012, 60_001, 60_010, 0))
        c.executemany("INSERT INTO trade_flow_observations VALUES(?,?,?,?,?,?)", [
            ("BTC-USDT-SWAP", "confirmed", 60_001, "buy", 100.0, 50.0),
            ("BTC-USDT-SWAP", "confirmed", 60_010, "sell", 101.0, 25.0),
        ])


def test_spot_and_swap_are_never_silently_substituted(tmp_path):
    live = tmp_path / "live.db"
    _live(live)
    adapter = CanonicalMicrostructureEvidenceAdapter(live)
    window = EvidenceWindow(0, 180_000, anchor="UTC_DAY_START", reset="UTC_DAILY_RESET")
    spot = adapter.query("cvd", MarketIdentity("OKX", "SPOT", "BTC-USDT"), window, now_ms=240_000)
    swap = adapter.query("cvd", MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"), window, now_ms=240_000)
    assert spot["value"] is None
    assert spot["missing_reason"] == "SOURCE_PRODUCT_UNAVAILABLE"
    assert swap["value"] == 30.0
    assert swap["identity"]["product_type"] == "SWAP"


def test_missing_values_are_null_and_gap_is_propagated(tmp_path):
    live = tmp_path / "live.db"
    _live(live)
    adapter = CanonicalMicrostructureEvidenceAdapter(live)
    missing = adapter.query("oi", MarketIdentity("OKX", "SWAP", "ETH-USDT-SWAP"), EvidenceWindow(0, 60_000), now_ms=120_000)
    assert missing["value"] is None
    assert missing["missing_reason"] == "NO_CONFIRMED_OBSERVATION"
    cvd = adapter.query("cvd", MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"), EvidenceWindow(0, 180_000), now_ms=240_000)
    assert cvd["quality"] == "PARTIAL"
    assert cvd["coverage"]["has_gaps"] is True
    assert cvd["window"]["anchor"] == "UTC_DAY_START"
    assert cvd["window"]["reset"] == "UTC_DAILY_RESET"


def test_old_numeric_observation_is_explicitly_stale(tmp_path):
    live = tmp_path / "live.db"
    _live(live)
    adapter = CanonicalMicrostructureEvidenceAdapter(live)
    result = adapter.query(
        "oi", MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"),
        EvidenceWindow(60_000, 60_000), now_ms=600_000,
    )
    assert result["value"] == 100.0
    assert result["quality"] == "STALE"
    assert result["freshness_ms"] > result["freshness_limit_ms"]


def test_live_and_canonical_history_are_compatible_without_collector_replacement(tmp_path):
    live, history = tmp_path / "live.db", tmp_path / "history.db"
    _live(live)
    with sqlite3.connect(history) as c:
        c.executescript("""
            CREATE TABLE cvd_1m(instrument TEXT,bucket_ms INTEGER,daily_cumulative REAL,
              source_min_ts_ms INTEGER,source_max_ts_ms INTEGER,status TEXT,gap_reason TEXT);
            CREATE TABLE oi_1m(instrument TEXT,bucket_ms INTEGER,confirmed_oi REAL,
              observation_ts_ms INTEGER,status TEXT,gap_reason TEXT);
        """)
        c.execute("INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?)", ("BTC-USDT-SWAP", 60_000, 7.0, 60_001, 60_010, "VALID", None))
    adapter = CanonicalMicrostructureEvidenceAdapter(live, history)
    result = adapter.query("cvd", MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"), EvidenceWindow(0, 60_000), now_ms=120_000)
    assert result["value"] == 7.0
    assert result["provenance"]["source"] == "canonical_history"
    assert adapter.query("funding", MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"), EvidenceWindow(0, 60_000), now_ms=120_000)["provenance"]["source"] == "live_microstructure"


def test_vpvr_methods_are_explicitly_exact_or_approximate(tmp_path):
    live = tmp_path / "live.db"
    _live(live)
    adapter = CanonicalMicrostructureEvidenceAdapter(live)
    identity, window = MarketIdentity("OKX", "SWAP", "BTC-USDT-SWAP"), EvidenceWindow(0, 120_000)
    exact = adapter.query_trade_vpvr(identity, window, bins=4, now_ms=120_000)
    approximate = adapter.query_ohlcv_vpvr(identity, window, [
        {"ts": n * 60_000, "candle_close_ts": (n + 1) * 60_000, "low": 99, "high": 102, "volume": 10}
        for n in range(20)
    ], now_ms=1_300_000)
    assert exact["price_attribution"] == "TRADE_PRICE_EXACT"
    assert approximate["price_attribution"] == "OHLCV_APPROXIMATE"


def test_snapshot_evidence_set_has_bounded_product_qualified_lanes(tmp_path):
    live = tmp_path / "live.db"
    _live(live)
    values = canonical_market_evidence_set(
        CanonicalMicrostructureEvidenceAdapter(live), "BTC-USDT", 241,
    )
    lanes = {(item["series"], item["identity"]["product_type"]) for item in values}
    assert {("cvd", "SWAP"), ("cvd", "SPOT"), ("oi", "SWAP"),
            ("oi", "SPOT"), ("funding", "SWAP"), ("basis", "SWAP"),
            ("vpvr", "SWAP")} <= lanes
    live_cvd = next(item for item in values
                    if item["series"] == "cvd" and item["identity"]["product_type"] == "SWAP")
    assert live_cvd["window"]["end_ms"] < 241_000
    assert live_cvd["window"]["resolution"] == "1m"
    spot_cvd = next(item for item in values
                    if item["series"] == "cvd" and item["identity"]["product_type"] == "SPOT")
    assert spot_cvd["value"] is None and spot_cvd["missing_reason"]
