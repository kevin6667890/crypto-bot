import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from dashboard.thesis_derivatives import (
    CurrentDerivativeReaderV1, DerivativeSnapshotReaderV1, asof_fact, causal_percentile,
    composite_dataset_identity, verify_snapshot_file,
)
from scripts.backfill_thesis_derivatives import backfill, connect, snapshot


def test_asof_never_future_joins_and_enforces_staleness() -> None:
    rows = [{"source_ts_ms": 95, "value": 1.0},
            {"source_ts_ms": 105, "value": 999.0}]
    assert asof_fact(rows, 100, value_key="value", max_age_ms=10).value == 1.0
    stale = asof_fact(rows, 100, value_key="value", max_age_ms=4)
    assert stale.value is None and stale.status == "UNKNOWN_STALE"
    missing = asof_fact(rows, 90, value_key="value", max_age_ms=10)
    assert missing.value is None and missing.status == "UNKNOWN_NO_PRIOR_OBSERVATION"


def test_causal_percentile_future_change_cannot_rewrite_past() -> None:
    first = causal_percentile([1, 2, 3, 4, 5], min_history=2)
    changed = causal_percentile([1, 2, 3, 4, -999], min_history=2)
    assert first[:4] == changed[:4]
    assert first[:2] == [None, None]
    assert first[2] == 100.0


def test_composite_identity_is_order_independent_and_keeps_components() -> None:
    ohlcv = {"kind": "OHLCV", "dataset_id": "price", "sha256": "a" * 64,
             "raw_start_ms": 1, "raw_end_ms": 10}
    oi = {"kind": "OI", "dataset_id": "oi", "sha256": "b" * 64,
          "raw_start_ms": 5, "raw_end_ms": 9}
    left = composite_dataset_identity([ohlcv, oi], effective_start_ms=5, effective_end_ms=9)
    right = composite_dataset_identity([oi, ohlcv], effective_start_ms=5, effective_end_ms=9)
    assert left == right
    assert [row["kind"] for row in left["components"]] == ["OHLCV", "OI"]


def test_wrong_snapshot_sha_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "derivatives.sqlite"
    path.write_bytes(b"immutable")
    assert verify_snapshot_file(path, hashlib.sha256(b"immutable").hexdigest())["status"] == "READY"
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        verify_snapshot_file(path, "0" * 64)


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _path, _params):
        self.calls += 1
        rows = [["200", "10", "20", "30"], ["100", "9", "19", "29"]]
        body = json.dumps({"code": "0", "data": rows}, sort_keys=True).encode()
        return rows, body, "https://www.okx.com/official", 1


def test_oi_backfill_is_bounded_deduplicated_and_resumable(tmp_path: Path) -> None:
    database = tmp_path / "derivatives.sqlite"
    with connect(database) as connection:
        first = backfill(connection, FakeClient(), lane="oi", instrument="BTC-USDT-SWAP",
                         start_ms=100, end_ms=200, period="1H", max_pages=2)
        second = backfill(connection, FakeClient(), lane="oi", instrument="BTC-USDT-SWAP",
                          start_ms=100, end_ms=200, period="1H", max_pages=2)
        rows = connection.execute("SELECT value,unit,aux_json FROM derivative_observations ORDER BY source_ts_ms").fetchall()
    assert first["status"] == "COMPLETE" and first["rows_inserted"] == 2
    assert second == {"status": "COMPLETE", "resumed": True, "pages": 0, "rows_inserted": 0}
    assert [(row[0], row[1]) for row in rows] == [(29.0, "USD"), (30.0, "USD")]
    assert json.loads(rows[0][2])["official_period"] == "1H"


def test_source_window_shortfall_is_not_reported_complete(tmp_path: Path) -> None:
    database = tmp_path / "limited.sqlite"
    with connect(database) as connection:
        result = backfill(connection, FakeClient(), lane="oi", instrument="BTC-USDT-SWAP",
                          start_ms=1, end_ms=300, period="1H", max_pages=2)
    assert result["status"] == "SOURCE_LIMIT_REACHED"


def test_snapshot_reader_batch_aligns_without_future_join_and_keeps_components(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.sqlite"
    with connect(database) as connection:
        now = 1_700_100_000_000
        for timestamp, value in ((1_700_000_000_000, 100.0),
                                 (1_700_014_400_000, 110.0),
                                 (1_700_028_800_000, 120.0)):
            connection.execute(
                "INSERT INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("OPEN_INTEREST_USD", "BTC-USDT-SWAP", timestamp, value, "USD", "{}",
                 "OKX_OFFICIAL", "v1", now, "a" * 64),
            )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    reader = DerivativeSnapshotReaderV1(database, expected_sha256=digest, dataset_id="derivatives-test")
    candles = [
        {"ts": 1_700_000_000 + index * 14_400,
         "candle_close_ts": 1_700_000_000 + (index + 1) * 14_400,
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 10.0, "confirmed": 1}
        for index in range(2)
    ]
    result = reader.align(candles, canonical_instrument="BTC-USDT", timeframe="4H",
                          required_groups=("OI",), as_of=candles[-1]["candle_close_ts"])
    assert [item["open_interest_usd"] for item in result["rows"]] == [110.0, 120.0]
    assert [item["kind"] for item in result["composite_dataset_identity"]["components"]] == ["OHLCV", "OI"]


def test_snapshot_reader_wrong_sha_blocks_only_derivative_adapter(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.sqlite"
    database.write_bytes(b"not a sqlite snapshot")
    reader = DerivativeSnapshotReaderV1(database, expected_sha256="0" * 64, dataset_id="bad")
    assert reader.readiness()["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        reader.align([], canonical_instrument="BTC-USDT", timeframe="4H",
                     required_groups=("OI",), as_of=1)


def test_snapshot_manifest_sha_is_stable_after_connection_close(tmp_path: Path) -> None:
    database = tmp_path / "snapshot.sqlite"
    manifest = tmp_path / "snapshot.json"
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("OPEN_INTEREST_USD", "BTC-USDT-SWAP", 100, 1.0, "USD", "{}",
             "OKX_OFFICIAL", "v1", 101, "a" * 64),
        )
        connection.commit()
    frozen = snapshot(database, manifest, 100)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == frozen["database_sha256"]
    assert json.loads(manifest.read_text(encoding="utf-8"))["database_sha256"] == frozen["database_sha256"]


def test_backfill_rejects_unbounded_page_count(tmp_path: Path) -> None:
    with connect(tmp_path / "bounded.sqlite") as connection:
        with pytest.raises(ValueError, match="max_pages"):
            backfill(connection, FakeClient(), lane="oi", instrument="BTC-USDT-SWAP",
                     start_ms=1, end_ms=2, period="1H", max_pages=0)


def test_current_oi_uses_daily_live_publication_and_frozen_causal_history(tmp_path: Path) -> None:
    database, micro = tmp_path / "snapshot.sqlite", tmp_path / "micro.sqlite"
    day, anchor = 86_400_000, 1_700_064_000_000
    with connect(database) as connection:
        for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            for index in range(37):
                connection.execute(
                    "INSERT INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("OPEN_INTEREST_USD", instrument, anchor + index * day,
                     1_000.0 + index, "USD", "{}", "OKX_OFFICIAL", "v1",
                     anchor + 40 * day, "a" * 64))
        connection.commit()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    historical = DerivativeSnapshotReaderV1(
        database, expected_sha256=digest, dataset_id="derivatives-test")
    with sqlite3.connect(micro) as connection:
        connection.execute("""CREATE TABLE oi_observations(
            instrument TEXT,source_ts_ms INTEGER,oi_usd REAL,
            ingested_at_ms INTEGER,state TEXT)""")
        for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            for index in (35, 36):
                source = anchor + index * day
                connection.execute("INSERT INTO oi_observations VALUES(?,?,?,?,?)", (
                    instrument, source,
                    (10_000.0 if instrument == "BTC-USDT-SWAP" and index == 36 else 1_000.0 + index),
                    source + 60_000, "confirmed"))
    as_of = (anchor + 36 * day + 8 * 3_600_000) // 1000
    current = CurrentDerivativeReaderV1(micro, historical, clock=lambda: as_of)
    assert current.readiness()["status"] == "READY"
    fact = current.latest("BTC-USDT", "OI", as_of, timeframe="1D")
    assert fact and fact["timestamp"] == (anchor + 36 * day) // 1000
    assert fact["values"]["OI_CHANGE_PCT"] > 0
    assert fact["values"]["OI_CHANGE_PCT"] < 1
    assert fact["values"]["OI_CHANGE_PERCENTILE"] is not None
    assert current.latest("BTC-USDT", "OI", as_of, timeframe="4H") is None


def test_current_oi_rejects_future_publication_wrong_cadence_and_insufficient_samples(
        tmp_path: Path) -> None:
    database, micro = tmp_path / "snapshot.sqlite", tmp_path / "micro.sqlite"
    day, anchor = 86_400_000, 1_700_064_000_000
    with connect(database) as connection:
        for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            for index in range(31):
                connection.execute(
                    "INSERT INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("OPEN_INTEREST_USD", instrument, anchor + index * day,
                     1_000.0 + index, "USD", "{}", "OKX_OFFICIAL", "v1",
                     anchor + 40 * day, "a" * 64))
        connection.commit()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    historical = DerivativeSnapshotReaderV1(
        database, expected_sha256=digest, dataset_id="derivatives-test")
    with sqlite3.connect(micro) as connection:
        connection.execute("""CREATE TABLE oi_observations(
            instrument TEXT,source_ts_ms INTEGER,oi_usd REAL,
            ingested_at_ms INTEGER,state TEXT)""")
        for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            # A valid but long-stale reviewed sample must not make a fresh
            # frozen endpoint look like current evidence.
            connection.execute("INSERT INTO oi_observations VALUES(?,?,?,?,?)", (
                instrument, anchor - 100 * day, 900.0,
                anchor - 100 * day + 60_000, "confirmed"))
            # 08:00 is not the reviewed daily cadence and must be ignored.
            connection.execute("INSERT INTO oi_observations VALUES(?,?,?,?,?)", (
                instrument, anchor + 31 * day - 8 * 3_600_000, 2_000.0,
                anchor + 31 * day, "confirmed"))
            # Correct source cadence, but it was ingested after the evaluation.
            connection.execute("INSERT INTO oi_observations VALUES(?,?,?,?,?)", (
                instrument, anchor + 31 * day, 2_001.0,
                anchor + 32 * day, "confirmed"))
    as_of = (anchor + 31 * day + 3_600_000) // 1000
    current = CurrentDerivativeReaderV1(micro, historical, clock=lambda: as_of)
    assert current.latest("BTC-USDT", "OI", as_of, timeframe="1D") is None
    assert current.readiness()["status"] == "BLOCKED"
