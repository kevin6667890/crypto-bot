from __future__ import annotations

import inspect
import json
import sqlite3
import time

from dashboard.alert_service import AlertService
from dashboard.job_queue import JobQueue
from dashboard.microstructure import (
    WAL_JOURNAL_SIZE_LIMIT_BYTES,
    MicrostructureStore,
    now_ms,
)
from dashboard import paper_api


def _trade(identifier: str, timestamp: int) -> tuple:
    return (
        "BTC-USDT-SWAP",
        {
            "tradeId": identifier,
            "px": "100",
            "sz": "1",
            "side": "buy",
            "ts": str(timestamp),
        },
        0.01,
        "test",
        None,
    )


def _traced(store: MicrostructureStore) -> list[str]:
    statements: list[str] = []
    original = store._open_connection

    def open_traced(**values):
        connection = original(**values)
        connection.set_trace_callback(statements.append)
        return connection

    store._open_connection = open_traced  # type: ignore[method-assign]
    return statements


def test_health_and_coverage_only_read_small_summary_tables(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    store.insert_trade_batch([_trade("one", now_ms())])
    statements = _traced(store)

    coverage = store.coverage()
    health = store.health_summary()

    assert coverage["trades"][0]["rows"] == 1
    assert health["data_plane_status"]["status"] in {"RUNNING", "STALE"}
    sql = "\n".join(statements).upper()
    assert "FROM SOURCE_RUNTIME_SUMMARY" in sql
    assert "COUNT(*) FROM TRADE_FLOW_OBSERVATIONS" not in sql
    assert "GROUP BY INSTRUMENT" not in sql


def test_coverage_marks_partial_bootstrap_as_refreshing(tmp_path):
    path = tmp_path / "existing.db"
    store = MicrostructureStore(path)
    store.initialize()
    store.insert_trade_batch([
        _trade(str(index), now_ms() - 100 + index) for index in range(5)
    ])
    # Simulate a deployment over pre-existing rows.
    with store.connect() as connection:
        connection.execute(
            """UPDATE source_runtime_summary SET row_count=0,refreshing=1,
               bootstrap_cursor=0,bootstrap_high_water=5
               WHERE lane='trades' AND instrument='BTC-USDT-SWAP'""")
    partial = store.coverage()
    assert partial["_snapshot"]["refreshing"] is True
    for _ in range(50):
        store.bootstrap_summary_slice(maximum_rows=10)
        target = next(
            row for row in store.coverage()["trades"]
            if row["instrument"] == "BTC-USDT-SWAP")
        if not target["refreshing"]:
            break
    complete = store.coverage()
    assert complete["trades"][0]["rows"] == 5
    assert next(
        row for row in complete["trades"]
        if row["instrument"] == "BTC-USDT-SWAP")["refreshing"] is False


def test_summary_bootstrap_bounds_scanned_rowid_range_when_lane_is_sparse(tmp_path):
    store = MicrostructureStore(tmp_path / "sparse.db")
    store.initialize()
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO trade_flow_observations
               (rowid,source,source_version,instrument,source_ts_ms,ingested_at_ms,
                resolution,state,source_identity,uniqueness_key,trade_id,side,
                price,size,contract_value,notional)
               VALUES(10000000,'test','v1','ETH-USDT-SWAP',1,1,'trade',
                      'confirmed','sparse','sparse','sparse','buy',1,1,1,1)""")
        connection.execute(
            "UPDATE source_runtime_summary SET refreshing=0")
        connection.execute(
            """UPDATE source_runtime_summary SET refreshing=1,
               bootstrap_cursor=0,bootstrap_high_water=10000000,row_count=0
               WHERE lane='trades' AND instrument='BTC-USDT-SWAP'""")
    result = store.bootstrap_summary_slice(maximum_rows=100)
    assert result["rows"] == 0
    assert result["cursor"] == 100
    assert result["lane_complete"] is False


def test_eligibility_default_reads_persisted_snapshot(tmp_path, monkeypatch):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    expected = {"feature_groups": {"cvd": {"instruments": {}}}}
    store.put_runtime_snapshot(
        "feature_eligibility", expected, data_as_of_ms=now_ms())
    monkeypatch.setattr(
        store, "_calculate_feature_eligibility",
        lambda **_: (_ for _ in ()).throw(AssertionError("heavy calculation")))
    assert store.eligibility_summary()["feature_groups"] == expected["feature_groups"]


def test_maintenance_is_one_bounded_resumable_cursor_unit(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    store.insert_trade_batch([_trade("one", now_ms())])
    started = time.monotonic()
    first = store.maintenance_slice(wall_clock_seconds=1.5)
    second = store.maintenance_slice(wall_clock_seconds=1.5)
    assert time.monotonic() - started < 2
    assert second["cursor"] == first["cursor"] + 1
    with store.connect(readonly=True) as connection:
        persisted = connection.execute(
            """SELECT cursor FROM collection_checkpoints
               WHERE lane='maintenance_cursor' AND instrument='aggregate'"""
        ).fetchone()[0]
    assert int(persisted) == second["cursor"]


def test_maintenance_progress_handler_honors_live_pause(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    # The queue callback is part of the SQLite progress interruption contract.
    source = inspect.getsource(store.maintenance_slice)
    assert "pause_requested" in source
    assert "set_progress_handler" in source


def test_wal_limit_and_checkpoint_stay_out_of_live_queue(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    writer = store.live_writer()
    try:
        assert writer.connection.execute(
            "PRAGMA journal_size_limit").fetchone()[0] == \
            WAL_JOURNAL_SIZE_LIMIT_BYTES
        assert writer.passive_checkpoint(queue_depth=1) is False
        source = inspect.getsource(writer.passive_checkpoint).upper()
        assert "PASSIVE" in source
        assert "TRUNCATE" not in source
        assert "BUSY_TIMEOUT=0" in source
    finally:
        writer.close()


def test_low_priority_transaction_never_waits_for_live_writer(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    writer = store.live_writer()
    try:
        assert writer.lock.acquire(blocking=False)
        started = time.monotonic()
        assert writer.try_transaction(lambda: {"unexpected": True}) is None
        assert time.monotonic() - started < 0.05
    finally:
        if writer.lock.locked():
            writer.lock.release()
        writer.close()


def test_reader_release_allows_wal_frames_to_checkpoint(tmp_path):
    path = tmp_path / "reader.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE sample(value)")
    writer.commit()
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM sample").fetchall()
    for value in range(100):
        writer.execute("INSERT INTO sample VALUES(?)", (value,))
        writer.commit()
    before = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    reader.rollback()
    reader.close()
    after = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    writer.close()
    assert before[1] >= before[2]
    assert after[1] == after[2]


def test_short_lived_job_and_alert_connections_are_closed(tmp_path):
    alerts = AlertService(tmp_path / "alerts.db")
    queue = JobQueue(tmp_path / "jobs.db", autostart=False)
    with alerts.connect() as connection:
        connection.execute("SELECT 1")
        alert_connection = connection
    with queue.connect() as connection:
        connection.execute("SELECT 1")
        job_connection = connection
    for connection in (alert_connection, job_connection):
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            pass
        else:
            raise AssertionError("connection leaked past context exit")


def test_public_operations_payload_has_no_sensitive_keys(monkeypatch):
    class CollectorResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({
                "service_status": "RUNNING",
                "writer_queue_depth": 0,
                "last_maintenance_duration_ms": 17,
                "last_checkpoint_duration_ms": 23,
                "last_checkpoint_result": [0, 10, 10],
            }).encode()

    monkeypatch.setattr(paper_api, "urlopen", lambda *_args, **_kwargs: CollectorResponse())
    monkeypatch.setattr(
        paper_api.MICROSTRUCTURE,
        "operations_summary",
        lambda: {
            "collector": {"status": "RUNNING"},
            "query_plane": {"status": "AVAILABLE"},
            "database_size_bytes": 1,
            "wal_size_bytes": 2,
            "maintenance": {"status": "RUNNING"},
            "coverage_snapshot": {},
        })
    payload = paper_api.public_operations_summary()
    assert payload["maintenance"]["last_duration_ms"] == 17
    assert payload["collector"]["status"] == "RUNNING"
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "credential", "password", "admin_token", "ssh", "environment",
        "private_key",
    ):
        assert forbidden not in serialized


def test_admin_actions_are_closed_when_token_is_not_configured(
    monkeypatch,
):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    handler = object.__new__(paper_api.Handler)
    captured: list[tuple[dict, int]] = []
    handler._send = lambda body, status=200: captured.append((body, int(status)))
    assert handler._admin() is False
    assert captured[-1][1] == 503


def test_query_stability_code_has_no_strategy_or_order_calls():
    sources = "\n".join((
        inspect.getsource(MicrostructureStore.health_summary),
        inspect.getsource(MicrostructureStore.operations_summary),
        inspect.getsource(MicrostructureStore.maintenance_slice),
    )).lower()
    assert "create_order" not in sources
    assert "strategy" not in sources
