from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path

import pytest

from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_collector import Collector
from dashboard.realtime_aggregation import (
    DAY_MS,
    MINUTE_MS,
    RealtimeAggregationEngine,
)
from dashboard.canonical_microstructure_history import (
    BuildIdentity, CanonicalHistoryStore,
)
from dashboard.canonical_realtime import CanonicalRealtimeWriter


INSTRUMENT = "BTC-USDT-SWAP"
BASE_DAY = 1_700_000_000_000 // DAY_MS * DAY_MS


def store(tmp_path: Path) -> MicrostructureStore:
    value = MicrostructureStore(tmp_path / "micro.db")
    value.initialize()
    return value


def trade(value: MicrostructureStore, timestamp: int, trade_id: str,
          side: str = "buy", size: float = 1) -> None:
    value.insert_trade(INSTRUMENT, {
        "ts": timestamp, "px": 100, "sz": size,
        "side": side, "tradeId": trade_id,
    }, contract_value=1)


def oi(value: MicrostructureStore, timestamp: int, identity: str,
       amount: float) -> None:
    value.insert_oi(
        INSTRUMENT, timestamp, oi_contracts=amount,
        oi_currency=amount, oi_usd=amount,
        source="official OI", source_identity=identity)


def test_realtime_task_is_independent_of_disabled_maintenance(tmp_path: Path) -> None:
    value = store(tmp_path)
    collector = Collector(
        value, maintenance_enabled=False, realtime_aggregation_enabled=True)
    collector.contract_values = {INSTRUMENT: 1}

    async def scenario() -> set[str]:
        tasks = collector._start_tasks(None)  # type: ignore[arg-type]
        names = {task.get_name() for task in tasks}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return names

    names = asyncio.run(scenario())
    assert "realtime-aggregation-supervisor" in names
    assert "retention-aggregation" not in names
    assert "serialized-sqlite-writer" in names
    assert "public-trades-BTC-USDT-SWAP" in names


def test_realtime_disabled_does_not_schedule_task(tmp_path: Path) -> None:
    value = store(tmp_path)
    collector = Collector(
        value, maintenance_enabled=False, realtime_aggregation_enabled=False)
    collector.contract_values = {INSTRUMENT: 1}

    async def scenario() -> set[str]:
        tasks = collector._start_tasks(None)  # type: ignore[arg-type]
        names = {task.get_name() for task in tasks}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return names

    assert "realtime-aggregation" not in asyncio.run(scenario())


def test_one_instrument_failure_does_not_stop_other_instruments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = store(tmp_path)
    collector = Collector(
        value, maintenance_enabled=False, realtime_aggregation_enabled=True)
    called: list[str] = []

    async def aggregate(_engine, instrument, *_args, **_kwargs):
        called.append(instrument)
        if instrument == "ETH-USDT-SWAP":
            raise RuntimeError("instrument boom")

    monkeypatch.setattr(collector, "_aggregate_range", aggregate)

    async def scenario() -> tuple[bool, bool]:
        engine = RealtimeAggregationEngine(value)
        failed = await collector._aggregate_instrument_safely(
            engine, "ETH-USDT-SWAP", 0, MINUTE_MS, catchup=False)
        succeeded = await collector._aggregate_instrument_safely(
            engine, "BTC-USDT-SWAP", 0, MINUTE_MS, catchup=False)
        return failed, succeeded

    assert asyncio.run(scenario()) == (False, True)
    assert called == ["ETH-USDT-SWAP", "BTC-USDT-SWAP"]
    metrics = value.operational_metrics()
    assert metrics["realtime_aggregation_status"] == "DEGRADED"
    assert "RuntimeError" in metrics["realtime_aggregation_task_exception"]


def test_supervisor_restarts_once_and_reports_liveness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = store(tmp_path)
    collector = Collector(
        value, maintenance_enabled=False, realtime_aggregation_enabled=True)
    attempts = 0
    restarted = asyncio.Event()
    monkeypatch.setattr(
        "dashboard.microstructure_collector.REALTIME_RESTART_BASE_SECONDS", 0)

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("task boom")
        restarted.set()
        await collector.stop_event.wait()

    monkeypatch.setattr(collector, "_realtime_aggregation_loop", worker)
    async def scenario() -> dict[str, object]:
        task = asyncio.create_task(collector._supervise_realtime_aggregation())
        await restarted.wait()
        metrics = value.operational_metrics()
        collector.stop_event.set()
        await task
        return metrics

    metrics = asyncio.run(scenario())
    assert attempts == 2
    assert metrics["realtime_aggregation_task_restart_count"] == 1
    assert metrics["realtime_aggregation_task_alive"] is True


def test_supervisor_stops_after_bounded_consecutive_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = store(tmp_path)
    collector = Collector(
        value, maintenance_enabled=False, realtime_aggregation_enabled=True)
    monkeypatch.setattr(
        "dashboard.microstructure_collector.REALTIME_RESTART_BASE_SECONDS", 0)
    monkeypatch.setattr(
        "dashboard.microstructure_collector.REALTIME_RESTART_MAX_CONSECUTIVE", 2)

    async def worker() -> None:
        raise RuntimeError("always broken")

    monkeypatch.setattr(collector, "_realtime_aggregation_loop", worker)

    async def scenario() -> None:
        task = asyncio.create_task(collector._supervise_realtime_aggregation())
        while value.operational_metrics()["realtime_aggregation_status"] != "FAILED":
            await asyncio.sleep(0)
        collector.stop_event.set()
        await task

    asyncio.run(scenario())
    metrics = value.operational_metrics()
    assert metrics["realtime_aggregation_task_alive"] is False
    assert metrics["realtime_aggregation_task_restart_count"] == 1
    assert metrics["realtime_aggregation_consecutive_failures"] == 2
    assert value.liveness()["realtime_aggregation_health"] == "FAILED"


def test_liveness_degrades_when_raw_advances_past_aggregate(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.update_operational_metrics(
        realtime_aggregation_enabled=True,
        realtime_aggregation_task_alive=True,
        realtime_aggregation_status="LIVE",
        raw_timestamp_by_instrument_series={
            INSTRUMENT: {"cvd": 600_000, "oi": 600_000}},
        aggregated_timestamp_by_instrument_series={
            INSTRUMENT: {"cvd": 300_000, "oi": 600_000}})
    health = value.liveness()
    assert health["realtime_aggregation_health"] == "DEGRADED"
    assert health["aggregate_lag_seconds"] == 300


def test_liveness_does_not_let_oi_mask_missing_cvd(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.update_operational_metrics(
        realtime_aggregation_enabled=True,
        realtime_aggregation_task_alive=True,
        realtime_aggregation_status="LIVE",
        raw_timestamp_by_instrument_series={
            INSTRUMENT: {"cvd": 600_000, "oi": 600_000}},
        aggregated_timestamp_by_instrument_series={
            INSTRUMENT: {"oi": 590_000}})
    health = value.liveness()
    assert health["realtime_aggregation_health"] == "DEGRADED"
    assert health["aggregate_missing_series"] == [f"{INSTRUMENT}:cvd"]


def test_liveness_detects_dead_task_as_degraded(tmp_path: Path) -> None:
    value = store(tmp_path)
    value.update_operational_metrics(
        realtime_aggregation_enabled=True,
        realtime_aggregation_task_alive=False,
        realtime_aggregation_status="DEGRADED")
    health = value.liveness()
    assert health["service_status"] == "DEGRADED"
    assert health["realtime_aggregation_health"] == "DEGRADED"


def test_live_loop_is_limited_to_fifteen_minute_lookback() -> None:
    source = inspect.getsource(Collector._realtime_aggregation_loop)
    assert "LIVE_LOOKBACK_MINUTES * MINUTE_MS" in source
    assert "24 *" not in source


def test_live_engine_has_no_maintenance_backfill_or_history_entrypoint() -> None:
    source = inspect.getsource(RealtimeAggregationEngine)
    assert "maintenance_slice" not in source
    assert "backfill" not in source.lower()
    assert "history-trades" not in source
    assert "24 *" not in source


def test_one_minute_cvd_and_oi_are_real_and_idempotent(tmp_path: Path) -> None:
    value = store(tmp_path); start = BASE_DAY + 2 * DAY_MS
    trade(value, start + 1_000, "a", "buy", 2)
    trade(value, start + 2_000, "b", "sell", 1)
    oi(value, start + 5_000, "o1", 10)
    oi(value, start + 45_000, "o2", 12)
    engine = RealtimeAggregationEngine(value)
    first = engine.process(INSTRUMENT, start, start + MINUTE_MS)
    second = engine.process(INSTRUMENT, start, start + MINUTE_MS)
    assert first["inserted"] == {"cvd:1m": 1, "oi:1m": 1}
    assert second["existing"] == {"cvd:1m": 1, "oi:1m": 1}
    with value.connect(readonly=True) as connection:
        cvd = connection.execute(
            "SELECT * FROM cvd_aggregates WHERE resolution='1m'").fetchone()
        stored_oi = connection.execute(
            "SELECT * FROM oi_aggregates WHERE resolution='1m'").fetchone()
    assert (cvd["buy_notional"], cvd["sell_notional"], cvd["delta"],
            cvd["cumulative_anchored"], cvd["observation_count"]) == (
                200, 100, 100, 100, 2)
    assert (stored_oi["first_value"], stored_oi["last_value"],
            stored_oi["absolute_change"], stored_oi["observation_count"]) == (
                10, 12, 2, 2)


def test_realtime_writer_continues_prebuilt_canonical_db(tmp_path: Path) -> None:
    value = store(tmp_path); start = BASE_DAY + 30 * DAY_MS
    trade(value, start + 1_000, "a", "buy", 2)
    trade(value, start + 2_000, "b", "sell", 1)
    oi(value, start + 5_000, "o1", 10)
    RealtimeAggregationEngine(value).process(
        INSTRUMENT, start, start + MINUTE_MS
    )
    canonical = tmp_path / "canonical.db"
    CanonicalHistoryStore(canonical).initialise(
        BuildIdentity("a" * 64, "history", start - 1, 1)
    )
    writer = CanonicalRealtimeWriter(value.path, canonical, "realtime")
    assert writer.sync(INSTRUMENT, start, start + MINUTE_MS)["minutes"] == 1
    assert writer.sync(INSTRUMENT, start, start + MINUTE_MS)["minutes"] == 1
    with sqlite3.connect(canonical) as connection:
        cvd = connection.execute(
            "SELECT signed_delta,daily_cumulative,status,trade_count FROM cvd_1m"
        ).fetchone()
        stored_oi = connection.execute(
            "SELECT confirmed_oi,status,observation_count FROM oi_1m"
        ).fetchone()
    assert cvd == (100.0, 100.0, "VALID", 2)
    assert stored_oi == (10.0, "VALID", 1)


def test_canonical_cvd_gap_keeps_real_delta_partial_and_next_day_recovers(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); midnight = BASE_DAY + 31 * DAY_MS
    trade(value, midnight + 1_000, "m0")
    oi(value, midnight + 1_000, "o0", 10)
    trade(value, midnight + 2 * MINUTE_MS + 1_000, "m2")
    oi(value, midnight + 2 * MINUTE_MS + 1_000, "o2", 12)
    next_day = midnight + DAY_MS
    trade(value, next_day + 1_000, "next")
    oi(value, next_day + 1_000, "on", 15)
    engine = RealtimeAggregationEngine(value)
    engine.process(INSTRUMENT, midnight, midnight + 3 * MINUTE_MS)
    engine.process(INSTRUMENT, next_day, next_day + MINUTE_MS)
    canonical = tmp_path / "canonical-gap.db"
    CanonicalHistoryStore(canonical).initialise(
        BuildIdentity("a" * 64, "history", midnight - 1, 1)
    )
    result = CanonicalRealtimeWriter(value.path, canonical, "realtime").sync(
        INSTRUMENT, midnight, next_day + MINUTE_MS
    )
    assert result["errors"] == []
    with sqlite3.connect(canonical) as connection:
        rows = connection.execute(
            "SELECT bucket_ms,signed_delta,daily_cumulative,status FROM cvd_1m "
            "WHERE instrument=? AND bucket_ms IN (?,?,?,?) ORDER BY bucket_ms",
            (INSTRUMENT, midnight, midnight + MINUTE_MS,
             midnight + 2 * MINUTE_MS, next_day),
        ).fetchall()
        oi_rows = connection.execute(
            "SELECT bucket_ms,confirmed_oi,status FROM oi_1m WHERE instrument=? "
            "AND bucket_ms IN (?,?) ORDER BY bucket_ms",
            (INSTRUMENT, midnight + 2 * MINUTE_MS, next_day),
        ).fetchall()
    assert rows == [
        (midnight, 100.0, 100.0, "VALID"),
        (midnight + MINUTE_MS, None, None, "MISSING"),
        (midnight + 2 * MINUTE_MS, 100.0, 200.0, "PARTIAL_AFTER_GAP"),
        (next_day, 100.0, 100.0, "VALID"),
    ]
    assert oi_rows == [
        (midnight + 2 * MINUTE_MS, 12.0, "VALID"),
        (next_day, 15.0, "VALID"),
    ]


def test_canonical_cvd_failure_does_not_block_oi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 32 * DAY_MS
    trade(value, start + 1_000, "trade")
    oi(value, start + 1_000, "oi", 7)
    RealtimeAggregationEngine(value).process(INSTRUMENT, start, start + MINUTE_MS)
    canonical = tmp_path / "canonical-isolation.db"
    CanonicalHistoryStore(canonical).initialise(
        BuildIdentity("a" * 64, "history", start - 1, 1)
    )
    writer = CanonicalRealtimeWriter(value.path, canonical, "realtime")
    monkeypatch.setattr(writer, "_append_cvd", lambda *_args: (_ for _ in ()).throw(RuntimeError("cvd boom")))
    result = writer.sync(INSTRUMENT, start, start + MINUTE_MS)
    assert any("cvd boom" in error["exception"] for error in result["errors"])
    with sqlite3.connect(canonical) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cvd_1m").fetchone()[0] == 0
        assert connection.execute("SELECT confirmed_oi,status FROM oi_1m").fetchone() == (7.0, "VALID")


def test_missing_raw_stays_missing_and_unclosed_minute_is_not_requested(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 3 * DAY_MS
    trade(value, start + 2 * MINUTE_MS + 1, "later")
    oi(value, start + 2 * MINUTE_MS + 1, "later", 5)
    result = RealtimeAggregationEngine(value).process(
        INSTRUMENT, start, start + 2 * MINUTE_MS)
    assert result["missing"] == {"cvd:1m": 2, "oi:1m": 2}
    with value.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT 1 FROM cvd_aggregates").fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM oi_aggregates").fetchone() is None


def test_cvd_resets_at_utc_midnight(tmp_path: Path) -> None:
    value = store(tmp_path); midnight = BASE_DAY + 10 * DAY_MS
    trade(value, midnight - MINUTE_MS + 1_000, "before")
    oi(value, midnight - MINUTE_MS + 1_000, "ob", 1)
    trade(value, midnight + 1_000, "after")
    oi(value, midnight + 1_000, "oa", 2)
    engine = RealtimeAggregationEngine(value)
    engine.process(INSTRUMENT, midnight - MINUTE_MS, midnight + MINUTE_MS)
    with value.connect(readonly=True) as connection:
        rows = connection.execute(
            """SELECT bucket_ms,cumulative_anchored FROM cvd_aggregates
               WHERE resolution='1m' ORDER BY bucket_ms""").fetchall()
    assert [tuple(row) for row in rows] == [
        (midnight - MINUTE_MS, 100), (midnight, 100)]


def test_cvd_continues_delta_at_an_artificial_lookback_boundary(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 12 * DAY_MS
    trade(value, start + 1_000, "prior")
    trade(value, start + MINUTE_MS + 1_000, "target")
    oi(value, start + MINUTE_MS + 1_000, "oi", 1)
    RealtimeAggregationEngine(value).process(
        INSTRUMENT, start + MINUTE_MS, start + 2 * MINUTE_MS)
    with value.connect(readonly=True) as connection:
        aggregate = connection.execute(
            "SELECT delta,cumulative_anchored,gap_flag FROM cvd_aggregates").fetchone()
        status = connection.execute(
            """SELECT status,detail_json FROM realtime_aggregate_fingerprints
               WHERE series='cvd' AND resolution='1m'""").fetchone()
    assert tuple(aggregate) == (100.0, 100.0, 1)
    assert status[0] == "VALID"


def test_fingerprint_conflict_pauses_without_overwrite_or_cursor_advance(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 4 * DAY_MS
    trade(value, start + 1_000, "first")
    oi(value, start + 1_000, "oi", 1)
    engine = RealtimeAggregationEngine(value)
    engine.process(INSTRUMENT, start, start + MINUTE_MS)
    trade(value, start + 2_000, "late")
    result = engine.process(INSTRUMENT, start, start + MINUTE_MS)
    assert result["conflicts"][0]["series"] == "cvd"
    with value.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT observation_count FROM cvd_aggregates").fetchone()
        checkpoint = connection.execute(
            """SELECT cursor,status FROM collection_checkpoints
               WHERE lane='realtime_aggregation' AND instrument=?""",
            (INSTRUMENT,),).fetchone()
    assert row[0] == 1
    assert tuple(checkpoint) == (str(start + MINUTE_MS), "CONFLICT")


def test_transaction_failure_rolls_back_aggregate_and_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 5 * DAY_MS
    trade(value, start + 1_000, "trade")
    oi(value, start + 1_000, "oi", 1)
    engine = RealtimeAggregationEngine(value)
    monkeypatch.setattr(
        engine, "_minute_oi",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    writer = value.live_writer()
    with pytest.raises(RuntimeError, match="boom"):
        writer.transaction(
            lambda: engine.process(INSTRUMENT, start, start + MINUTE_MS),
            retries=0)
    writer.close()
    with value.connect(readonly=True) as connection:
        assert connection.execute("SELECT 1 FROM cvd_aggregates").fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM collection_checkpoints WHERE lane='realtime_aggregation'"
        ).fetchone() is None


def test_high_resolutions_derive_only_from_complete_confirmed_minutes(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 20 * DAY_MS
    for index in range(60):
        timestamp = start + index * MINUTE_MS + 1_000
        trade(value, timestamp, str(index), "buy" if index % 2 else "sell")
        oi(value, timestamp, str(index), 100 + index)
    engine = RealtimeAggregationEngine(value)
    engine.process(INSTRUMENT, start, start + 30 * MINUTE_MS)
    engine.process(
        INSTRUMENT, start + 30 * MINUTE_MS, start + 60 * MINUTE_MS)
    with value.connect(readonly=True) as connection:
        for table in ("cvd_aggregates", "oi_aggregates"):
            counts = dict(connection.execute(
                f"""SELECT resolution,COUNT(*) FROM {table}
                    WHERE resolution IN ('5m','15m','1H') GROUP BY resolution"""))
            assert counts == {"15m": 4, "1H": 1, "5m": 12}


def test_incomplete_minutes_block_higher_resolution(tmp_path: Path) -> None:
    value = store(tmp_path); start = BASE_DAY + 21 * DAY_MS
    for index in range(15):
        if index == 7:
            continue
        timestamp = start + index * MINUTE_MS + 1_000
        trade(value, timestamp, str(index)); oi(value, timestamp, str(index), index + 1)
    RealtimeAggregationEngine(value).process(
        INSTRUMENT, start, start + 15 * MINUTE_MS)
    with value.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT 1 FROM cvd_aggregates WHERE resolution='15m'").fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM oi_aggregates WHERE resolution='15m'").fetchone() is None


def test_range_is_hard_bounded_to_120_minutes(tmp_path: Path) -> None:
    engine = RealtimeAggregationEngine(store(tmp_path))
    with pytest.raises(ValueError, match="120 minutes"):
        engine.dry_run(INSTRUMENT, 0, 121 * MINUTE_MS)


def test_paper_api_does_not_import_or_trigger_realtime_aggregation() -> None:
    source = Path("dashboard/paper_api.py").read_text(encoding="utf-8")
    assert "RealtimeAggregationEngine" not in source
    assert "request_realtime_catchup" not in source


def test_compose_enables_realtime_but_keeps_maintenance_and_ultimate_safe() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert 'MICROSTRUCTURE_REALTIME_AGGREGATION_ENABLED: "true"' in compose
    assert 'MICROSTRUCTURE_MAINTENANCE_ENABLED: "false"' in compose
    assert "profiles:\n      - ultimate-bot" in compose
