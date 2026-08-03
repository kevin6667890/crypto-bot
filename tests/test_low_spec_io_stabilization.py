from __future__ import annotations

import asyncio
import sqlite3
import time

from dashboard.microstructure import MicrostructureStore, now_ms
from dashboard.microstructure_collector import BoundedPriorityQueue, Collector


def trade(identifier: str, timestamp: int) -> tuple:
    return (
        "BTC-USDT-SWAP",
        {"tradeId": identifier, "px": "100", "sz": "1", "side": "buy",
         "ts": str(timestamp)},
        0.01, "OKX WS trades-all", None,
    )


def test_live_writer_reuses_one_connection_and_one_transaction(tmp_path, monkeypatch):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    opened = 0
    original = store._open_connection

    def counted(**values):
        nonlocal opened
        opened += 1
        return original(**values)

    monkeypatch.setattr(store, "_open_connection", counted)
    writer = store.live_writer()
    writer.transaction(lambda: (
        store.insert_trade_batch([trade("one", now_ms())]),
        store.record_health("writer", "LIVE", last_success_ms=now_ms()),
        store.checkpoint("writer", "queue", cursor=None, last_source_ts_ms=None,
                         status="running"),
    ))
    writer.close()
    assert opened == 1


def test_priority_queue_keeps_live_ahead_of_telemetry():
    async def scenario():
        queue = BoundedPriorityQueue(3)
        await queue.put(("health_flush", {}))
        await queue.put(("trade", {"payload": {}}))
        return (await queue.get())[0]

    assert asyncio.run(scenario()) == "trade"


def test_batched_rows_are_complete_and_duplicates_idempotent(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    timestamp = now_ms()
    writer = store.live_writer()
    rows = [trade(str(index), timestamp + index) for index in range(25)]
    assert writer.transaction(lambda: store.insert_trade_batch(rows)) == 25
    assert writer.transaction(lambda: store.insert_trade_batch(rows)) == 0
    writer.close()
    with store.connect(readonly=True) as connection:
        values = connection.execute(
            "SELECT trade_id,source_ts_ms FROM trade_flow_observations "
            "ORDER BY source_ts_ms").fetchall()
    assert len(values) == 25
    assert [row[1] for row in values] == list(range(timestamp, timestamp + 25))


def test_busy_retry_is_deterministic(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    writer = store.live_writer()
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return store.insert_trade_batch([trade("retry", now_ms())])

    assert writer.transaction(operation) == 1
    writer.close()
    assert attempts == 2
    assert store.operational_metrics()["busy_retry_count"] == 1


def test_queue_is_bounded():
    async def scenario():
        queue = BoundedPriorityQueue(1)
        await queue.put(("trade", {}))
        blocked = asyncio.create_task(queue.put(("trade", {})))
        await asyncio.sleep(0.01)
        result = not blocked.done()
        blocked.cancel()
        await asyncio.gather(blocked, return_exceptions=True)
        return result

    assert asyncio.run(scenario())


def test_low_volume_batch_flushes_on_timeout(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    collector = Collector(store)
    collector.contract_values["BTC-USDT-SWAP"] = 0.01

    async def scenario():
        writer = asyncio.create_task(collector._writer())
        await collector.queue.put(("trade", {
            "instrument": "BTC-USDT-SWAP",
            "payload": trade("timeout", now_ms())[1],
            "received_at_ms": now_ms(),
        }))
        await asyncio.wait_for(collector.queue.join(), timeout=1)
        size = store.operational_metrics()["writer_batch_size"]
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        return size

    assert asyncio.run(scenario()) == 1


def test_second_startup_does_not_count_historical_tables(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    statements: list[str] = []
    original = store._open_connection

    def traced(**values):
        connection = original(**values)
        connection.set_trace_callback(statements.append)
        return connection

    store._open_connection = traced  # type: ignore[method-assign]
    store.initialize()
    assert not any(
        "COUNT(*) FROM TRADE_FLOW_OBSERVATIONS" in statement.upper()
        for statement in statements)


def test_bounded_pruning_never_exceeds_limit(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    old = now_ms() - 100 * 86_400_000
    store.insert_trade_batch([trade(str(index), old + index) for index in range(20)])
    result = store.prune_raw_bounded(maximum_rows=7)
    assert sum(result.values()) == 7
    with store.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trade_flow_observations").fetchone()[0] == 13


def test_passive_checkpoint_defers_while_live_queue_busy(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    writer = store.live_writer()
    assert writer.passive_checkpoint(queue_depth=1) is False
    writer.close()


def test_liveness_uses_in_memory_operational_metrics(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    store.update_operational_metrics(
        writer_queue_depth=4, writer_batch_size=123, write_latency_ms=7,
        last_maintenance_duration_ms=11,
        maintenance_paused_reason="live_queue_not_empty")
    started = time.monotonic()
    health = store.liveness()
    assert time.monotonic() - started < 0.05
    assert health["writer_queue_depth"] == 4
    assert health["writer_batch_size"] == 123
    assert health["maintenance_paused_reason"] == "live_queue_not_empty"


def test_disabled_maintenance_is_not_scheduled_but_live_workers_are(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    collector = Collector(store, maintenance_enabled=False)
    collector.contract_values = {"BTC-USDT-SWAP": 0.01}

    async def scenario():
        tasks = collector._start_tasks(None)  # type: ignore[arg-type]
        names = {task.get_name() for task in tasks}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return names

    names = asyncio.run(scenario())
    assert "serialized-sqlite-writer" in names
    assert "public-trades-BTC-USDT-SWAP" in names
    assert "rest-BTC-USDT-SWAP" in names
    assert "passive-checkpoint" in names
    assert "retention-aggregation" not in names
    assert store.liveness()["maintenance_enabled"] is False
    assert store.liveness()["maintenance_status"] == "DISABLED"


def test_disabled_maintenance_does_not_scan_or_advance_cursor(tmp_path, monkeypatch):
    store = MicrostructureStore(tmp_path / "micro.db")
    collector = Collector(store, maintenance_enabled=False)
    collector.contract_values = {"BTC-USDT-SWAP": 0.01}
    calls: list[str] = []

    monkeypatch.setattr(
        store, "maintenance_slice",
        lambda **_values: calls.append("maintenance_slice"))
    monkeypatch.setattr(
        store, "aggregate_all", lambda: calls.append("aggregate_all"))
    async def scenario():
        tasks = collector._start_tasks(None)  # type: ignore[arg-type]
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert calls == []
    with store.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT cursor FROM collection_checkpoints "
            "WHERE lane='maintenance_cursor' AND instrument='aggregate'"
        ).fetchone() is None


def test_disabled_maintenance_skips_okx_history_but_keeps_live_rest(
    tmp_path, monkeypatch
):
    store = MicrostructureStore(tmp_path / "micro.db")
    collector = Collector(store, maintenance_enabled=False)
    collector.contract_values = {"BTC-USDT-SWAP": 0.01}
    paths: list[str] = []

    async def fake_get(_session, path, _params):
        paths.append(path)
        timestamp = str(now_ms())
        if path.endswith("open-interest"):
            return [{"ts": timestamp, "oi": "1", "oiCcy": "1", "oiUsd": "1"}]
        if path.endswith("funding-rate"):
            return [{"ts": timestamp, "fundingRate": "0", "fundingTime": timestamp}]
        if path.endswith("mark-price"):
            return [{"ts": timestamp, "markPx": "100"}]
        if path.endswith("index-tickers"):
            collector.stop_event.set()
            return [{"ts": timestamp, "idxPx": "100"}]
        raise AssertionError(f"unexpected historical endpoint: {path}")

    monkeypatch.setattr(collector, "_get", fake_get)
    asyncio.run(collector._rest_instrument(None, "BTC-USDT-SWAP"))
    assert "/api/v5/public/open-interest" in paths
    assert "/api/v5/public/funding-rate" in paths
    assert "/api/v5/public/mark-price" in paths
    assert "/api/v5/market/index-tickers" in paths
    assert "/api/v5/public/funding-rate-history" not in paths


def test_operations_summary_reports_persisted_maintenance_disabled(tmp_path):
    store = MicrostructureStore(tmp_path / "micro.db")
    store.initialize()
    store.record_health("maintenance", "DISABLED", last_success_ms=now_ms())
    assert store.operations_summary()["maintenance"]["status"] == "DISABLED"
