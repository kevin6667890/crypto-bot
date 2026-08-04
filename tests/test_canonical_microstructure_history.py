from pathlib import Path
import hashlib
import json
import sqlite3
import zipfile

from dashboard.canonical_microstructure_history import (
    BuildIdentity,
    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
    CanonicalHistoryBuilder,
    CanonicalHistoryStore,
    aggregate_quality,
    fingerprint,
)


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
    assert report["missing_minutes"] == 1
    with builder.destination.connect() as connection:
        ledger = connection.execute(
            "SELECT bucket_ms,status FROM coverage_ledger ORDER BY bucket_ms"
        ).fetchall()
    assert [tuple(row) for row in ledger] == [
        (0, "VALID"), (60_000, "VALID"), (120_000, "MISSING"),
        (180_000, "VALID"),
    ]


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
