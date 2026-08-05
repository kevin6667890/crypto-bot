from pathlib import Path
import hashlib
import json
import sqlite3
import zipfile

import pytest

from dashboard.canonical_microstructure_history import (
    BuildIdentity,
    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
    CanonicalHistoryBuilder,
    CanonicalHistoryStore,
    aggregate_quality,
    fingerprint,
)
from dashboard.flow_history import CanonicalFlowHistoryStore


def test_schema_has_explicit_quality_and_version(tmp_path: Path) -> None:
    store = CanonicalHistoryStore(tmp_path / "canonical.db")
    store.initialise(BuildIdentity("a" * 64, "commit", 120_000, 123))
    with store.connect() as connection:
        version = connection.execute(
            "SELECT value_json FROM canonical_metadata WHERE key='history_version'"
        ).fetchone()[0]
        assert CANONICAL_MICROSTRUCTURE_HISTORY_VERSION in version
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='cvd_1m'"
        ).fetchone()[0]
        assert "UNRECOVERABLE_RAW_GAP" in sql
        assert "source_fingerprint" in sql


def test_fingerprint_excludes_generation_time_when_caller_excludes_it() -> None:
    fact = {"bucket_ms": 60_000, "delta": "1.25", "status": "VALID"}
    assert fingerprint(fact) == fingerprint(dict(reversed(list(fact.items()))))


def test_higher_quality_inheritance_is_conservative() -> None:
    assert aggregate_quality(["VALID"] * 5) == ("VALID", None)
    assert aggregate_quality(["VALID", "BACKFILLED_OFFICIAL"])[0] == (
        "BACKFILLED_OFFICIAL"
    )
    assert aggregate_quality(["VALID", "MISSING"])[0] == "PARTIAL"
    assert aggregate_quality(["VALID", "UNRECOVERABLE_RAW_GAP"])[0] == (
        "UNRECOVERABLE_RAW_GAP"
    )
    assert aggregate_quality(["VALID", "CONFLICT"])[0] == "CONFLICT"
    assert aggregate_quality(["VALID", "PARTIAL_AFTER_GAP"])[0] == (
        "PARTIAL_AFTER_GAP"
    )
    assert aggregate_quality(["PARTIAL_AFTER_GAP"] * 5) == (
        "PARTIAL_AFTER_GAP", "EARLIER_RAW_GAP_SAME_UTC_DAY"
    )


def test_partial_after_gap_progress_is_current_but_not_confirmed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical.db"
    store = CanonicalHistoryStore(path)
    store.initialise(BuildIdentity("a" * 64, "commit", 600_000, 123))
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", 0, "1m", 2.0, 1.0, 1.0, 1, 1, 2, 1,
             "f0", 1.0, "1970-01-01", "VALID", None,
             CANONICAL_MICROSTRUCTURE_HISTORY_VERSION, "commit", 123),
        )
        for bucket in range(60_000, 660_000, 60_000):
            connection.execute(
                "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("BTC-USDT-SWAP", bucket, "1m", 2.0, 1.0, 1.0, 1,
                 bucket + 1, bucket + 2, 1, f"f{bucket}", None,
                 "1970-01-01", "PARTIAL_AFTER_GAP",
                 "EARLIER_RAW_GAP_SAME_UTC_DAY",
                 CANONICAL_MICROSTRUCTURE_HISTORY_VERSION, "commit", 123),
            )
    result = CanonicalFlowHistoryStore(path).query(
        "BTC-USDT", "cvd", start=60, end=600, max_points=10,
        now=780, timeframe="1m",
    )
    assert result["status"] == "PARTIAL_AFTER_GAP"
    assert result["data_as_of"] == 600
    assert result["latest_timestamp"] == 600
    assert result["source_coverage"]["confirmed_end"] == 0
    assert result["stale"] is True
    assert all(point["status"] == "WHITESPACE" for point in result["points"])
    assert all(point["partial_after_gap"] for point in result["points"])


def test_index_uses_non_swap_source_identity() -> None:
    assert CanonicalHistoryBuilder._source_instrument(
        "index", "BTC-USDT-SWAP"
    ) == "BTC-USDT"
    assert CanonicalHistoryBuilder._source_instrument(
        "trades", "BTC-USDT-SWAP"
    ) == "BTC-USDT-SWAP"


def test_coverage_streams_observed_and_missing_minutes(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE trade_flow_observations(
        source TEXT,source_version TEXT,instrument TEXT,source_ts_ms INTEGER,
        ingested_at_ms INTEGER,resolution TEXT,state TEXT,source_identity TEXT,
        uniqueness_key TEXT PRIMARY KEY,trade_id TEXT,side TEXT,price REAL,
        size REAL,contract_value REAL,notional REAL,provenance_table TEXT)"""
    )
    connection.execute(
        """CREATE TABLE collection_gaps(
        lane TEXT,instrument TEXT,start_ms INTEGER,end_ms INTEGER,reason TEXT,
        detected_at_ms INTEGER,resolved_at_ms INTEGER)"""
    )
    connection.execute(
        "INSERT INTO collection_gaps VALUES(?,?,?,?,?,?,NULL)",
        ("trades", "BTC-USDT-SWAP", 61_500, 62_500, "partial minute", 70_000),
    )
    rows = []
    for timestamp, key in ((1_000, "a"), (61_000, "b"), (181_000, "c")):
        rows.append(("OKX", "v1", "BTC-USDT-SWAP", timestamp, timestamp,
                     "tick", "confirmed", key, key, key, "buy", 1.0, 1.0,
                     1.0, 1.0, None))
    connection.executemany(
        "INSERT INTO trade_flow_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 240_000, 123),
    )
    report = builder.build_coverage("trades", "BTC-USDT-SWAP")
    assert report["missing_minutes"] == 2
    assert report["recorded_gap_buckets"] == 1
    with builder.destination.connect() as connection:
        ledger = connection.execute(
            "SELECT bucket_ms,status FROM coverage_ledger ORDER BY bucket_ms"
        ).fetchall()
    assert [tuple(row) for row in ledger] == [
        (0, "VALID"), (60_000, "MISSING"), (120_000, "MISSING"),
        (180_000, "VALID"),
    ]


def test_coverage_records_absent_optional_source_as_unavailable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    sqlite3.connect(source).close()
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 240_000, 123),
    )

    report = builder.build_coverage("mark", "BTC-USDT-SWAP")

    assert report == {
        "instrument": "BTC-USDT-SWAP", "source": "mark",
        "row_count": 0, "status": "SOURCE_UNAVAILABLE",
    }
    with builder.destination.connect() as connection:
        asset = connection.execute(
            "SELECT row_count,gap_status FROM source_assets "
            "WHERE instrument=? AND source=?",
            ("BTC-USDT-SWAP", "mark"),
        ).fetchone()
    assert tuple(asset) == (0, "SOURCE_UNAVAILABLE")


def test_cvd_deduplicates_same_trade_identity_without_false_conflict(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE trade_flow_observations(
        source TEXT,source_version TEXT,instrument TEXT,source_ts_ms INTEGER,
        ingested_at_ms INTEGER,resolution TEXT,state TEXT,source_identity TEXT,
        uniqueness_key TEXT PRIMARY KEY,trade_id TEXT,side TEXT,price REAL,
        size REAL,contract_value REAL,notional REAL,provenance_table TEXT)"""
    )
    rows = [
        ("OKX", "v1", "BTC-USDT-SWAP", 1_000, 1_000, "tick", "confirmed",
         key, key, "trade-1", "buy", 100.0, 1.0, 1.0, 100.0, None)
        for key in ("capture-a", "capture-b")
    ]
    connection.executemany(
        "INSERT INTO trade_flow_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    connection.commit()
    connection.close()
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 60_000, 123),
    )

    coverage = builder.build_coverage("trades", "BTC-USDT-SWAP")
    result = builder.build_cvd_1m("BTC-USDT-SWAP")

    assert coverage["duplicate_count"] == 1
    assert coverage["trade_id_conflict_count"] == 0
    assert result["rows"] == 1
    with builder.destination.connect() as connection:
        row = connection.execute(
            "SELECT buy_volume,signed_delta,trade_count,status FROM cvd_1m"
        ).fetchone()
    assert tuple(row) == (100.0, 100.0, 1, "VALID")


def test_official_trade_file_dedupes_identical_adjacent_trade_id(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE trade_flow_observations(
        source TEXT,source_version TEXT,instrument TEXT,source_ts_ms INTEGER,
        ingested_at_ms INTEGER,resolution TEXT,state TEXT,source_identity TEXT,
        uniqueness_key TEXT PRIMARY KEY,trade_id TEXT,side TEXT,price REAL,
        size REAL,contract_value REAL,notional REAL,provenance_table TEXT)"""
    )
    connection.commit()
    connection.close()
    archive = tmp_path / "BTC-USDT-SWAP-trades-1970-01-01.zip"
    member = "BTC-USDT-SWAP-trades-1970-01-01.csv"
    csv_text = (
        "instrument_name,trade_id,side,price,size,created_time\n"
        "BTC-USDT-SWAP,1,buy,100,2,1000\n"
        "BTC-USDT-SWAP,1,buy,100,2,1000\n"
        "BTC-USDT-SWAP,2,sell,50,1,2000\n"
    )
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(member, csv_text)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "status": "COMPLETE",
        "files": [{"status": "VERIFIED", "filename": archive.name,
                   "path": str(archive), "member": member, "sha256": sha,
                   "date_ts": "-28800000"}],
    }), encoding="utf-8")
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 86_400_000, 123),
        official_trade_manifest_path=manifest,
        contract_values={"BTC-USDT-SWAP": "0.01"},
    )
    grouped, ranges, audit = builder._load_official_trade_minutes(
        "BTC-USDT-SWAP"
    )
    assert ranges == [(-28_800_000, 57_600_000)]
    assert audit[0]["duplicate_count"] == 1
    assert audit[0]["unique_trade_id_count"] == 2
    assert grouped[0]["count"] == 2
    assert float(grouped[0]["buy"]) == 2.0
    assert float(grouped[0]["sell"]) == 0.5
    resumed, resumed_ranges, resumed_audit = (
        builder._load_official_trade_minutes("BTC-USDT-SWAP")
    )
    assert resumed_ranges == ranges
    assert resumed_audit[0]["resumed"] is True
    assert resumed[0]["hash"] == grouped[0]["hash"]


def test_higher_timeframe_rows_match_schema_width(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    sqlite3.connect(source).close()
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 86_400_000, 123),
    )
    with builder.destination.connect() as connection:
        connection.execute(
            "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", 0, "1m", 2.0, 1.0, 1.0, 1, 1, 2, 1,
             "f", 1.0, "1970-01-01", "VALID", None,
             CANONICAL_MICROSTRUCTURE_HISTORY_VERSION, "commit", 123),
        )
        connection.execute(
            "INSERT INTO oi_1m VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", 0, "1m", 10.0, 2, 1, "f", "VALID", None,
             CANONICAL_MICROSTRUCTURE_HISTORY_VERSION, 123),
        )
    counts = builder.derive_higher_timeframes("BTC-USDT-SWAP")
    assert counts["cvd:5m"] == 1
    with builder.destination.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM cvd_higher_timeframes"
        ).fetchone()[0] == 5
    result = CanonicalFlowHistoryStore(tmp_path / "canonical.db").query(
        "BTC-USDT", "cvd", start=0, end=899, max_points=10,
        now=900, timeframe="15m",
    )
    assert result["requested_resolution"] == "15m"
    assert result["actual_resolution"] == "15m"
    assert result["resolution_seconds"] == 900
    assert result["stale_after_seconds"] == 1080
    with pytest.raises(ValueError, match="only supports UTC_DAILY_RESET"):
        CanonicalFlowHistoryStore(tmp_path / "canonical.db").query(
            "BTC-USDT", "cvd", start=0, end=899, max_points=10,
            now=900, timeframe="15m", cvd_mode="CONTINUOUS",
        )


def test_official_oi_only_fills_exact_missing_observation_minute(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE oi_observations(
        source_ts_ms INTEGER,oi_contracts REAL,oi_currency REAL,oi_usd REAL,
        uniqueness_key TEXT,state TEXT,instrument TEXT)"""
    )
    connection.executemany(
        "INSERT INTO oi_observations VALUES(?,?,?,?,?,?,?)",
        [(0, 1, 2, 10, "a", "confirmed", "BTC-USDT-SWAP"),
         (600_000, 1, 2, 20, "b", "confirmed", "BTC-USDT-SWAP")],
    )
    connection.commit()
    connection.close()
    rows_path = tmp_path / "oi.json"
    rows_body = json.dumps([["300000", "1", "2", "15"]]).encode()
    rows_path.write_bytes(rows_body)
    manifest_path = tmp_path / "oi-manifest.json"
    manifest_path.write_text(json.dumps({
        "manifest_version": "okx-official-oi-history-manifest-v1",
        "instruments": [{
            "instrument": "BTC-USDT-SWAP", "rows_path": str(rows_path),
            "rows_sha256": hashlib.sha256(rows_body).hexdigest(),
            "earliest_ms": 300_000, "latest_ms": 300_000,
            "endpoint": "https://www.okx.com/example", "page_count": 1,
        }],
    }), encoding="utf-8")
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 600_000, 123),
        official_oi_manifest_path=manifest_path,
    )
    with builder.destination.connect() as output:
        for bucket in range(0, 600_001, 60_000):
            observed = bucket in {0, 600_000}
            output.execute(
                "INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("BTC-USDT-SWAP", "oi", bucket, 1, int(observed), int(observed),
                 0, 0, bucket if observed else None, bucket if observed else None,
                 "local" if observed else None, "VALID" if observed else "MISSING",
                 None if observed else "NO_RAW_OBSERVATION",
                 "OBSERVED" if observed else "TRUE_RAW_GAP"),
            )
    result = builder.build_oi_1m("BTC-USDT-SWAP")
    assert result["official_points_used"] == 1
    with builder.destination.connect() as output:
        rows = output.execute(
            "SELECT bucket_ms,confirmed_oi,status FROM oi_1m ORDER BY bucket_ms"
        ).fetchall()
    assert tuple(rows[0]) == (0, 10.0, "VALID")
    assert tuple(rows[1]) == (60_000, None, "UNRECOVERABLE_RAW_GAP")
    assert tuple(rows[5]) == (300_000, 15.0, "BACKFILLED_OFFICIAL")


def test_official_price_overlay_only_fills_missing_and_checks_overlap(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """CREATE TABLE mark_price_observations(
        source_ts_ms INTEGER,open REAL,high REAL,low REAL,close REAL,
        state TEXT,instrument TEXT,uniqueness_key TEXT)"""
    )
    connection.executemany(
        "INSERT INTO mark_price_observations VALUES(?,?,?,?,?,?,?,?)",
        [(0, 1, 2, 0.5, 1.5, "confirmed", "BTC-USDT-SWAP", "a"),
         (60_000, 1.5, 2.5, 1.25, 1.75, "provisional", "BTC-USDT-SWAP", "p"),
         (30_000, None, None, None, 1.4, "confirmed", "BTC-USDT-SWAP", "s"),
         (120_000, 2, 3, 1.5, 2.5, "confirmed", "BTC-USDT-SWAP", "b")],
    )
    connection.commit()
    connection.close()
    rows_path = tmp_path / "mark.json"
    rows_body = json.dumps([
        ["0", "1", "2", "0.5", "1.5", "1"],
        ["60000", "1.5", "2.5", "1", "2", "1"],
        ["120000", "2", "3", "1.5", "2.5", "1"],
    ]).encode()
    rows_path.write_bytes(rows_body)
    manifest_path = tmp_path / "price-manifest.json"
    manifest_path.write_text(json.dumps({
        "manifest_version": "okx-official-price-gap-manifest-v1",
        "instruments": [{
            "source": "mark", "instrument": "BTC-USDT-SWAP",
            "rows_path": str(rows_path),
            "rows_sha256": hashlib.sha256(rows_body).hexdigest(),
            "endpoint": "https://www.okx.com/example", "page_count": 1,
            "dedupe_key": "source+instrument+ts+resolution", "status": "COMPLETE",
            "gaps": [{"start_ms": 60_000, "end_ms_exclusive": 120_000}],
        }],
    }), encoding="utf-8")
    builder = CanonicalHistoryBuilder(
        source, tmp_path / "canonical.db",
        BuildIdentity("a" * 64, "commit", 120_000, 123),
        official_price_manifest_path=manifest_path,
    )
    builder.build_coverage("mark", "BTC-USDT-SWAP")
    result = builder.apply_official_price_overlay("mark", "BTC-USDT-SWAP")
    assert result == {
        "source": "mark", "instrument": "BTC-USDT-SWAP",
        "official_points": 3, "points_used": 1, "overlap_checked": 2,
        "overlap_rows_checked": 3,
        "overlap_conflicts": 0, "status": "COMPLETE",
    }
    with builder.destination.connect() as output:
        assert tuple(output.execute(
            "SELECT status,classification FROM coverage_ledger WHERE bucket_ms=60000"
        ).fetchone()) == ("BACKFILLED_OFFICIAL", "OBSERVED")
        assert output.execute(
            "SELECT COUNT(*) FROM official_backfill_manifests"
        ).fetchone()[0] == 1
