from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_collector import Collector
from dashboard.realtime_aggregation import (
    DAY_MS,
    MINUTE_MS,
    RealtimeAggregationEngine,
)


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
    assert "realtime-aggregation" in names
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


def test_cvd_does_not_reset_at_an_artificial_lookback_boundary(
    tmp_path: Path,
) -> None:
    value = store(tmp_path); start = BASE_DAY + 12 * DAY_MS
    trade(value, start + 1_000, "prior")
    trade(value, start + MINUTE_MS + 1_000, "target")
    oi(value, start + MINUTE_MS + 1_000, "oi", 1)
    RealtimeAggregationEngine(value).process(
        INSTRUMENT, start + MINUTE_MS, start + 2 * MINUTE_MS)
    with value.connect(readonly=True) as connection:
        assert connection.execute(
            "SELECT 1 FROM cvd_aggregates").fetchone() is None
        status = connection.execute(
            """SELECT status,detail_json FROM realtime_aggregate_fingerprints
               WHERE series='cvd' AND resolution='1m'""").fetchone()
    assert status[0] == "MISSING"
    assert "prior minute aggregate pending" in status[1]


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
