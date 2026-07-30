from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_lifecycle import (
    MicrostructureLifecycleError,
    archive_raw_trade_day,
    build_offhost_ack,
    prune_archived_raw_trades,
    verify_offhost_ack,
    verify_raw_trade_archive,
)
from dashboard.snapshot_bundle import (
    SnapshotBundleError,
    build_compact_database,
    build_snapshot_bundles,
    restore_snapshot,
    verify_snapshot_bundle,
)
from dashboard.snapshot_storage import (
    ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES,
    ANALYSIS_SNAPSHOT_STORAGE_VERSION,
    SnapshotPayloadError,
    compact_analysis_snapshot,
    snapshot_payload_for_reader,
    stable_sha256,
    validate_compact_payload,
    write_compact_snapshot,
)
from dashboard.storage_guard import evaluate_disk_guard


def _analysis(*, oversized: bool = True) -> dict[str, object]:
    flow = {
        "cvd": 12.5,
        "cvd_delta": 12.5,
        "oi": 1000.0,
        "oi_change_pct": 0.3,
        "source": "canonical",
        "decision_quality": {
            "ready": True,
            "trade_count": 123,
            "oi_samples": 60,
            "cvd_timestamp": 1_700_000_000,
            "oi_timestamp": 1_700_000_001,
        },
    }
    if oversized:
        flow.update(
            {
                "cvd_series": [{"time": i, "value": i * 2} for i in range(500)],
                "oi_history": [{"time": i, "oi": i} for i in range(500)],
                "professional": {
                    "unknown_nested_series": list(range(5000)),
                    "raw_trades": [{"id": i} for i in range(500)],
                },
            }
        )
    return {
        "updated_at": "2026-07-01T00:00:00+00:00",
        "instrument": "BTC-USDT",
        "execution_timeframe": "15m",
        "evaluation_id": "evaluation-1",
        "signal_setup_id": "signal-1",
        "strategy_version": "strategy-v1",
        "decision_engine_version": "decision-v1",
        "config_hash": "config-1",
        "candle_close_ts": 1_700_000_000,
        "action": "WAIT",
        "bias": "NEUTRAL",
        "score": 42,
        "price": 100.0,
        "ema20": 99.0,
        "rsi14": 51.0,
        "atr14": 2.0,
        "flow_context": {
            "cvd_delta": 12.5,
            "oi_change_pct": 0.3,
            "cvd_timestamp": 1_700_000_000,
            "oi_timestamp": 1_700_000_001,
        },
        "flow": flow,
        "vpvr": {"rows": [{"price": i} for i in range(1000)]},
        "gate_results": [{"key": "trend", "passed": False, "reason": "mixed"}],
        "contributions": [{"key": "trend", "status": "fail"}],
    }


def _paper_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """PRAGMA user_version=17;
               CREATE TABLE paper_trades(
                 id INTEGER PRIMARY KEY,status TEXT NOT NULL,reason TEXT);
               CREATE TABLE paper_account(
                 account_id INTEGER PRIMARY KEY,cash REAL NOT NULL);
               CREATE TABLE decision_signal_runs(
                 id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
               CREATE TABLE decision_signals(
                 id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
               CREATE TABLE decision_evaluations(
                 id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
               CREATE TABLE lineage(
                 id INTEGER PRIMARY KEY,identity TEXT NOT NULL);
               CREATE TABLE analysis_snapshots(
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 created_at TEXT NOT NULL,payload TEXT NOT NULL,instrument TEXT);
               CREATE INDEX idx_analysis_instrument
                 ON analysis_snapshots(instrument,id DESC);
               CREATE VIEW closed_orders AS
                 SELECT * FROM paper_trades WHERE status!='OPEN';
               CREATE TRIGGER protect_orders BEFORE DELETE ON paper_trades
                 BEGIN SELECT RAISE(ABORT,'protected'); END;"""
        )
        connection.execute("INSERT INTO paper_trades VALUES(1,'WIN','take profit')")
        connection.execute("INSERT INTO paper_account VALUES(1,10100)")
        connection.execute("INSERT INTO decision_signal_runs VALUES(1,'run')")
        connection.execute("INSERT INTO decision_signals VALUES(1,'signal')")
        connection.execute("INSERT INTO decision_evaluations VALUES(1,'evaluation')")
        connection.execute("INSERT INTO lineage VALUES(1,'canonical')")
        for snapshot_id, timestamp in (
            (1, "2026-06-30T23:59:00+00:00"),
            (2, "2026-07-01T00:01:00+00:00"),
            (3, "2026-07-01T00:02:00+00:00"),
        ):
            payload = json.dumps({**_analysis(), "evaluation_id": f"e-{snapshot_id}"})
            connection.execute(
                "INSERT INTO analysis_snapshots VALUES(?,?,?,?)",
                (snapshot_id, timestamp, payload, "BTC-USDT"),
            )
    return path


def _micro_database(path: Path, *, rows: int = 6) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """CREATE TABLE trade_flow_observations(
                 source TEXT NOT NULL,source_version TEXT NOT NULL,
                 instrument TEXT NOT NULL,source_ts_ms INTEGER NOT NULL,
                 ingested_at_ms INTEGER NOT NULL,resolution TEXT NOT NULL,
                 state TEXT NOT NULL,source_identity TEXT NOT NULL,
                 uniqueness_key TEXT PRIMARY KEY,trade_id TEXT,side TEXT NOT NULL,
                 price REAL NOT NULL,size REAL NOT NULL,contract_value REAL NOT NULL,
                 notional REAL NOT NULL,provenance_table TEXT);
               CREATE INDEX idx_trade_flow_time
                 ON trade_flow_observations(instrument,source_ts_ms);
               CREATE TABLE cvd_aggregates(
                 instrument TEXT,resolution TEXT,bucket_ms INTEGER,
                 buy_notional REAL,sell_notional REAL,delta REAL,
                 cumulative_anchored REAL,observation_count INTEGER,
                 first_source_ts_ms INTEGER,last_source_ts_ms INTEGER,
                 gap_flag INTEGER,source_version TEXT,
                 PRIMARY KEY(instrument,resolution,bucket_ms));
               CREATE TABLE collection_gaps(
                 lane TEXT,instrument TEXT,start_ms INTEGER,end_ms INTEGER,
                 reason TEXT,detected_at_ms INTEGER,resolved_at_ms INTEGER);"""
        )
        start = 1_577_836_800_000  # 2020-01-01 UTC
        minute_values: dict[int, list[float | int]] = {}
        for index in range(rows):
            timestamp = start + index * 20_000
            side = "buy" if index % 2 == 0 else "sell"
            notional = float(index + 1)
            connection.execute(
                """INSERT INTO trade_flow_observations VALUES(
                   'OKX','v1','BTC-USDT-SWAP',?,?,'tick','confirmed',
                   ?,?,?,?,100,1,1,?,'raw')""",
                (
                    timestamp,
                    timestamp + 1,
                    f"source-{index}",
                    f"key-{index}",
                    f"trade-{index}",
                    side,
                    notional,
                ),
            )
            bucket = timestamp // 60_000 * 60_000
            value = minute_values.setdefault(bucket, [0.0, 0.0, 0])
            value[0 if side == "buy" else 1] += notional
            value[2] += 1
        cumulative = 0.0
        for bucket, (buy, sell, count) in sorted(minute_values.items()):
            cumulative += float(buy) - float(sell)
            connection.execute(
                """INSERT INTO cvd_aggregates(
                   instrument,resolution,bucket_ms,buy_notional,sell_notional,
                   delta,cumulative_anchored,observation_count,
                   first_source_ts_ms,last_source_ts_ms,gap_flag,source_version)
                   VALUES('BTC-USDT-SWAP','1m',?,?,?,?,?,?,?,?,0,'v1')""",
                (
                    bucket, buy, sell, float(buy) - float(sell), cumulative,
                    count, bucket, bucket + 59_999,
                ),
            )
    return path


def test_compact_snapshot_removes_all_long_market_sequences() -> None:
    compact = compact_analysis_snapshot(_analysis())
    value = json.loads(compact.payload)
    serialized = compact.payload
    assert value["storage_version"] == ANALYSIS_SNAPSHOT_STORAGE_VERSION
    assert "cvd_series" not in serialized
    assert "oi_history" not in serialized
    assert "raw_trades" not in serialized
    assert '"rows"' not in serialized
    assert compact.compact_bytes <= ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES
    assert value["source_manifest"]["source_fingerprints"]["combined"]
    assert value["dataset_identity"]


def test_recursive_validator_rejects_unbounded_unknown_sequence() -> None:
    with pytest.raises(SnapshotPayloadError, match="sequence length"):
        validate_compact_payload({"innocent_name": {"values": list(range(100))}})


def test_recursive_validator_rejects_depth_size_and_type() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(20):
        child: dict[str, object] = {}
        cursor["x"] = child
        cursor = child
    with pytest.raises(SnapshotPayloadError, match="nesting"):
        validate_compact_payload(deep)
    with pytest.raises(SnapshotPayloadError, match="maximum"):
        validate_compact_payload({"text": "x" * 100}, max_bytes=20)
    with pytest.raises(SnapshotPayloadError, match="unsupported"):
        validate_compact_payload({"bad": {1, 2}})


def test_oversized_input_still_writes_core_ledger(tmp_path: Path) -> None:
    database = tmp_path / "paper.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE analysis_snapshots(
               id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,
               payload TEXT NOT NULL,instrument TEXT)"""
        )
        snapshot_id = write_compact_snapshot(
            connection,
            created_at="2026-07-01T00:00:00+00:00",
            instrument="BTC-USDT",
            analysis=_analysis(),
        )
        row = connection.execute(
            """SELECT payload,original_payload_bytes,compact_payload_bytes
               FROM analysis_snapshots WHERE id=?""",
            (snapshot_id,),
        ).fetchone()
        telemetry = connection.execute(
            "SELECT event_type FROM analysis_snapshot_storage_telemetry"
        ).fetchone()[0]
    payload = json.loads(row[0])
    assert payload["final_decision"] == "WAIT"
    assert payload["snapshot_id"] == snapshot_id
    assert row[1] > row[2]
    assert row[2] <= ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES
    assert telemetry == "OVERSIZED_INPUT_COMPACTED"


def test_snapshot_hash_is_stable_and_legacy_reader_compatible() -> None:
    first = compact_analysis_snapshot(_analysis())
    second = compact_analysis_snapshot(_analysis())
    assert first.payload == second.payload
    assert first.original_sha256 == second.original_sha256
    assert stable_sha256("payload") == stable_sha256(b"payload")
    assert snapshot_payload_for_reader('{"legacy":true}') == {"legacy": True}


def test_monthly_bundle_manifest_restore_and_hash(tmp_path: Path) -> None:
    source = _paper_database(tmp_path / "source.db")
    archive = tmp_path / "archive"
    index = build_snapshot_bundles(source, archive, compression="gzip")
    assert len(index["bundles"]) == 2
    assert sum(item["snapshots"] for item in index["bundles"]) == 3
    for item in index["bundles"]:
        verification = verify_snapshot_bundle(archive / item["path"])
        assert verification["verified"]
        assert verification["snapshot_count"] == item["snapshots"]
    july = next(item for item in index["bundles"] if item["utc_month"] == "2026-07")
    metadata, payload = restore_snapshot(archive / july["path"], 2, verify=True)
    assert metadata["snapshot_id"] == 2
    assert json.loads(payload)["evaluation_id"] == "e-2"


def test_snapshot_bundle_corruption_fails_explicitly(tmp_path: Path) -> None:
    source = _paper_database(tmp_path / "source.db")
    archive = tmp_path / "archive"
    index = build_snapshot_bundles(source, archive, compression="gzip")
    bundle = archive / index["bundles"][0]["path"]
    with sqlite3.connect(bundle) as connection:
        connection.execute(
            """UPDATE payload_blobs SET compressed_payload=X'00'
               WHERE payload_sha256=(
                 SELECT payload_sha256 FROM payload_blobs LIMIT 1)"""
        )
    with pytest.raises((SnapshotBundleError, gzip.BadGzipFile)):
        verify_snapshot_bundle(bundle)


def test_compact_database_preserves_ledgers_schema_and_snapshot_ids(
    tmp_path: Path,
) -> None:
    source = _paper_database(tmp_path / "source.db")
    archive = tmp_path / "archive"
    build_snapshot_bundles(source, archive, compression="gzip")
    output = tmp_path / "compact.db"
    report = build_compact_database(source, archive, output)
    assert report["quick_check"] == "ok"
    assert report["foreign_key_violations"] == 0
    assert report["schema_compatible"]
    assert report["non_snapshot_tables_match"]
    assert report["snapshot_ids_and_metadata_match"]
    assert report["archive_mapping_complete"]
    assert report["user_version_old"] == report["user_version_new"] == 17
    for table in (
        "paper_trades", "paper_account", "decision_signal_runs",
        "decision_signals", "decision_evaluations", "lineage",
    ):
        assert report["non_snapshot_tables"][table]["old_hash"] == (
            report["non_snapshot_tables"][table]["new_hash"]
        )


def test_raw_archive_is_sorted_complete_reconciled_and_restorable(
    tmp_path: Path,
) -> None:
    source = _micro_database(tmp_path / "micro.db")
    report = archive_raw_trade_day(
        source,
        tmp_path / "archive",
        instrument="BTC-USDT-SWAP",
        utc_day="2020-01-01",
    )
    assert report["row_count"] == 6
    assert report["aggregate_reconciliation"]["status"] == "PASS"
    assert report["verification"]["unique_row_count"] == 6
    archive = tmp_path / "archive" / report["archive_file"]
    manifest = tmp_path / "archive" / report["manifest_file"]
    assert verify_raw_trade_archive(archive, manifest)["row_count"] == 6


def test_prune_requires_verified_ack_reconciliation_gap_and_cold_window(
    tmp_path: Path,
) -> None:
    source = _micro_database(tmp_path / "micro.db")
    report = archive_raw_trade_day(
        source, tmp_path / "archive",
        instrument="BTC-USDT-SWAP", utc_day="2020-01-01",
    )
    with pytest.raises(MicrostructureLifecycleError, match="ACK"):
        prune_archived_raw_trades(source, report, None)
    ack = build_offhost_ack(report)
    verify_offhost_ack(ack, report)
    failed = {**report, "aggregate_reconciliation": {"status": "FAIL"}}
    with pytest.raises(MicrostructureLifecycleError, match="reconciliation"):
        prune_archived_raw_trades(source, failed, ack)
    gap = {**report, "gap_summary": {"unresolved_gap_count": 1}}
    gap_ack = build_offhost_ack({**gap, "verification": {"verified": True}})
    with pytest.raises(MicrostructureLifecycleError, match="unresolved"):
        prune_archived_raw_trades(source, gap, gap_ack)
    with pytest.raises(MicrostructureLifecycleError, match="critical"):
        prune_archived_raw_trades(
            source, report, ack, critical_gap_count=1
        )
    recent = {**report, "utc_day": datetime.now(timezone.utc).date().isoformat()}
    recent_ack = build_offhost_ack(
        {**recent, "verification": {"verified": True}}
    )
    with pytest.raises(MicrostructureLifecycleError, match="hot window"):
        prune_archived_raw_trades(source, recent, recent_ack)


def test_prune_is_bounded_resumable_and_never_vacuums(tmp_path: Path) -> None:
    source = _micro_database(tmp_path / "micro.db")
    report = archive_raw_trade_day(
        source, tmp_path / "archive",
        instrument="BTC-USDT-SWAP", utc_day="2020-01-01",
    )
    ack = build_offhost_ack(report)
    dry = prune_archived_raw_trades(source, report, ack, max_rows=2)
    assert dry["deleted_rows"] == 0
    first = prune_archived_raw_trades(
        source, report, ack, apply=True, max_rows=2
    )
    second = prune_archived_raw_trades(
        source, report, ack, apply=True, max_rows=10
    )
    assert first["deleted_rows"] == 2
    assert first["status"] == "PRUNE_IN_PROGRESS"
    assert second["deleted_rows"] == 4
    assert second["status"] == "ARCHIVED_CONFIRMED"
    assert second["vacuum"] is False


def test_archived_interval_is_not_a_critical_gap_and_coverage_survives(
    tmp_path: Path,
) -> None:
    store = MicrostructureStore(tmp_path / "store.db")
    store.initialize()
    start, end = 1_577_836_800_000, 1_577_923_200_000
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO microstructure_archive_manifest VALUES(
               'archive','trades','BTC-USDT-SWAP',?,?,10,
               'archive','manifest','PASS','PASS','ARCHIVED_CONFIRMED','{}',?)""",
            (start, end, end),
        )
        connection.execute(
            """INSERT INTO collection_gaps VALUES(
               'trades','BTC-USDT-SWAP',?,?,?, ?,NULL)""",
            (start, end, "archived cold interval", end),
        )
    gap = store.gap_report(reference_ms=end + 1000, include_items=True)
    item = next(
        row for row in gap["items"]
        if row["instrument"] == "BTC-USDT-SWAP"
        and row["start_ms"] == start
    )
    assert item["classification"] == "ARCHIVED_CONFIRMED"
    assert not gap["critical_live_gaps"]
    coverage = store.coverage()["trades"]
    btc = next(row for row in coverage if row["instrument"] == "BTC-USDT-SWAP")
    assert btc["archive_status"] == "ARCHIVED_CONFIRMED"
    assert btc["archived_rows"] == 10
    assert btc["earliest_ms"] == start


def test_coverage_survives_before_archive_schema_migration(
    tmp_path: Path,
) -> None:
    store = MicrostructureStore(tmp_path / "store.db")
    store.initialize()
    with store.connect() as connection:
        connection.execute("DROP TABLE microstructure_archive_manifest")

    coverage = store.coverage()

    assert coverage["_snapshot"]["source"] == "collector_runtime_summary"
    assert all(
        row["archive_status"] is None
        for lane, rows in coverage.items()
        if not lane.startswith("_")
        for row in rows
    )


@pytest.mark.parametrize(
    ("free", "days85", "days90", "level"),
    (
        (50, None, None, "NORMAL"),
        (19, None, None, "WARNING"),
        (30, 10, None, "WARNING"),
        (11, None, None, "CRITICAL"),
        (30, None, 5, "CRITICAL"),
        (4, None, None, "EMERGENCY"),
    ),
)
def test_disk_guard_levels_keep_core_ledgers(
    free: int, days85: float | None, days90: float | None, level: str
) -> None:
    decision = evaluate_disk_guard(
        total_bytes=100 * 1024**3,
        free_bytes=free * 1024**3,
        projected_days_to_85=days85,
        projected_days_to_90=days90,
    )
    assert decision.level == level
    assert decision.core_ledger_allowed
    assert decision.core_aggregates_allowed
    assert decision.optional_artifacts_allowed is (level == "NORMAL")


def test_lifecycle_sources_never_vacuum_or_call_order_creation() -> None:
    roots = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            roots / "dashboard" / "microstructure_lifecycle.py",
            roots / "scripts" / "archive_microstructure_raw_trades.py",
            roots / "scripts" / "prune_microstructure_raw_trades.py",
            roots / "scripts" / "sync_microstructure_archives_offhost.py",
        )
    ).upper()
    assert 'EXECUTE("VACUUM' not in sources
    assert "EXECUTE('VACUUM" not in sources
    assert "/API/ORDER" not in sources
    assert "FACTOR_AUTORESEARCH" not in sources
