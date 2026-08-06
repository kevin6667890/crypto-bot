"""Phase 6E live-readiness, gap, and chronological-validation regressions."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from dashboard.microstructure import (
    MICROSTRUCTURE_SOURCE_VERSION,
    MicrostructureStore,
    now_ms,
)
from dashboard.microstructure_backfill import (
    BACKFILL_WRITE_BATCH_SIZE,
    OfficialBackfill,
)
from dashboard.microstructure_collector import Collector
from dashboard.microstructure_research import (
    RESEARCH_SEGMENT,
    VALIDATION_SEGMENT,
    SourceSpecificEventStudy,
)


DAY = 86_400_000
NOW = 1_780_000_000_000


@pytest.fixture
def store(tmp_path: Path) -> MicrostructureStore:
    value = MicrostructureStore(tmp_path / "phase6e.db")
    value.initialize()
    return value


def test_live_trades_have_priority_over_historical_backfill(
    store: MicrostructureStore,
) -> None:
    store.checkpoint(
        "writer", "live_queue", cursor=None, last_source_ts_ms=None,
        status="running", metadata={"queue_depth": 4, "write_latency_ms": 10})
    backfill = OfficialBackfill(store, client=object())  # type: ignore[arg-type]
    assert backfill._live_collection_has_priority() is True
    assert backfill._wait_for_live_capacity(max_wait_seconds=0) is False


class TradePageClient:
    retries = 0
    failed_requests = 0

    def get_public(self, path: str, params: dict[str, object]):
        if path.endswith("/instruments"):
            return [{"ctType": "linear", "ctVal": "0.01"}]
        if params.get("after"):
            return []
        return [
            {
                "tradeId": str(index), "px": "100", "sz": "1", "side": "buy",
                "ts": str(NOW - index),
            }
            for index in range(100)
        ]


def test_backfill_uses_bounded_batches_and_cannot_monopolize_writer(
    store: MicrostructureStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sizes = []
    original = store.insert_trade_batch

    def observed(items):
        batch = list(items)
        sizes.append(len(batch))
        return original(batch)

    monkeypatch.setattr(store, "insert_trade_batch", observed)
    monkeypatch.setattr("dashboard.microstructure_backfill.time.sleep", lambda _: None)
    result = OfficialBackfill(store, TradePageClient()).backfill_trades(
        "BTC-USDT-SWAP", max_pages=1)
    assert result["inserted"] == 100
    assert max(sizes) <= BACKFILL_WRITE_BATCH_SIZE
    assert len(sizes) == 2


def test_sqlite_wait_does_not_block_websocket_event_loop(
    store: MicrostructureStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = Collector(store)
    collector.contract_values["BTC-USDT-SWAP"] = 0.01
    original = store.insert_trade_batch
    started = threading.Event()
    finished = threading.Event()

    release = threading.Event()

    async def scenario() -> tuple[bool, bool]:
        loop = asyncio.get_running_loop()
        writer_entered = asyncio.Event()

        def blocked_insert(items):
            started.set()
            loop.call_soon_threadsafe(writer_entered.set)
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release blocked SQLite write")
            result = original(items)
            finished.set()
            return result

        monkeypatch.setattr(store, "insert_trade_batch", blocked_insert)
        writer = asyncio.create_task(collector._writer())
        await collector.queue.put(("trade", {
            "instrument": "BTC-USDT-SWAP",
            "payload": {
                "tradeId": "slow", "px": "100", "sz": "1",
                "side": "buy", "ts": str(now_ms())},
            "received_at_ms": now_ms(),
        }))
        try:
            await asyncio.wait_for(writer_entered.wait(), timeout=1)
            event_loop_advanced_while_sqlite_waited = started.is_set() and not finished.is_set()
            release.set()
            await collector.queue.join()
            return event_loop_advanced_while_sqlite_waited, finished.is_set()
        finally:
            release.set()
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)

    assert asyncio.run(scenario()) == (True, True)


def test_stalled_trade_lane_supervision_is_instrument_local(
    store: MicrostructureStore,
) -> None:
    collector = Collector(store)
    calls = {"btc": 0, "eth": 0}

    async def scenario() -> None:
        async def btc_operation() -> None:
            calls["btc"] += 1
            collector.stop_event.set()
            raise TimeoutError("BTC stalled")

        async def eth_operation() -> None:
            calls["eth"] += 1
            await collector.stop_event.wait()

        await asyncio.gather(
            collector._supervise("trades:BTC-USDT-SWAP", btc_operation),
            collector._supervise("trades:ETH-USDT-SWAP", eth_operation),
        )

    asyncio.run(scenario())
    assert collector._counter("trades:BTC-USDT-SWAP")["reconnect_count"] == 1
    assert collector._counter("trades:ETH-USDT-SWAP")["reconnect_count"] == 0


def test_health_distinguishes_received_persisted_and_aggregated(
    store: MicrostructureStore,
) -> None:
    timestamp = now_ms() - 1_000
    store.insert_trade_batch([(
        "BTC-USDT-SWAP",
        {"tradeId": "one", "px": "100", "sz": "1", "side": "buy",
         "ts": str(timestamp)},
        0.01, "OKX WS trades-all", None,
    )])
    store.checkpoint(
        "trades_forward", "BTC-USDT-SWAP", cursor=None,
        last_source_ts_ms=timestamp, status="running",
        metadata={"received_at_ms": timestamp + 10, "persisted_at_ms": timestamp + 20})
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO cvd_aggregates VALUES(
               ?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("BTC-USDT-SWAP", "1m", timestamp // 60_000 * 60_000,
             1.0, 0.0, 1.0, 1.0, 1, timestamp, timestamp, 0,
             MICROSTRUCTURE_SOURCE_VERSION))
    pipeline = store.health(include_eligibility=False)["trade_pipeline"]["BTC-USDT-SWAP"]
    assert pipeline["last_received_at_ms"] == timestamp + 10
    assert pipeline["last_persisted_at_ms"] == timestamp + 20
    assert pipeline["last_aggregated_source_ts_ms"] == timestamp


def test_settled_funding_uses_schedule_not_generic_event_age(
    store: MicrostructureStore,
) -> None:
    current = now_ms()
    latest = current - 7 * 3_600_000
    store.insert_funding(
        "BTC-USDT-SWAP",
        {"fundingTime": latest, "fundingRate": "0.0001"}, settled=True)
    store.checkpoint(
        "funding_schedule", "BTC-USDT-SWAP", cursor=None,
        last_source_ts_ms=latest, status="settled",
        metadata={
            "previous_funding_time_ms": latest,
            "next_expected_settlement_ms": current + 3_600_000,
        })
    schedule = store.health(include_eligibility=False)[
        "funding_schedule"]["BTC-USDT-SWAP"]
    assert schedule["latest_settlement_ms"] == latest
    assert schedule["overdue"] is False


def test_sparse_liquidation_events_do_not_imply_disconnect(
    store: MicrostructureStore,
) -> None:
    store.record_health("liquidations", "CONNECTED", last_success_ms=now_ms())
    health = store.health(include_eligibility=False)["liquidation_health"]
    assert health["stream_connected"] is True
    assert health["event_count"] == 0


def test_gap_classification_is_deterministic() -> None:
    arguments = dict(
        lane="trades", duration_ms=5 * 60_000,
        before_source="OKX WS trades-all", after_source="OKX WS trades-all",
        start_ms=NOW - DAY, reference_ms=NOW)
    assert MicrostructureStore.classify_gap(**arguments) == \
        MicrostructureStore.classify_gap(**arguments)
    assert MicrostructureStore.classify_gap(**arguments) == \
        "RECOVERABLE_BACKFILL_GAP"


def test_later_genuine_backfill_resolves_recorded_gap(
    store: MicrostructureStore,
) -> None:
    for timestamp, trade_id in (
        (NOW, "start"), (NOW + 60_000, "interior"), (NOW + 120_000, "end")):
        store.insert_trade_batch([(
            "BTC-USDT-SWAP",
            {"tradeId": trade_id, "px": "100", "sz": "1", "side": "buy",
             "ts": str(timestamp)},
            0.01, "OKX GET /api/v5/market/history-trades", None,
        )])
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO collection_gaps VALUES(
               'trades','BTC-USDT-SWAP',?,?,?, ?,NULL)""",
            (NOW, NOW + 120_000, "source observation gap", NOW))
    report = store.gap_report(reference_ms=NOW + 120_000, include_items=True)
    assert report["items"][0]["classification"] == "RESOLVED"


def test_expected_sparse_events_are_not_critical_gaps() -> None:
    assert MicrostructureStore.classify_gap(
        "liquidations", 10 * DAY) == "EXPECTED_EVENT_SPARSE"
    assert MicrostructureStore.classify_gap(
        "oi", 55_000) == "FALSE_POSITIVE"


def _basis(store: MicrostructureStore, instrument: str, timestamp: int) -> None:
    with store.connect() as connection:
        connection.execute(
            """INSERT INTO basis_aggregates VALUES(
               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (instrument, "1H", timestamp, 1.0, 1.0, 1.0, 1.0,
             0.001, 0.001, 0.0, 1, timestamp, timestamp, 0,
             MICROSTRUCTURE_SOURCE_VERSION))


def test_btc_basis_eligibility_is_not_reduced_by_eth_sol(store: MicrostructureStore) -> None:
    for instrument, days in (
        ("BTC-USDT-SWAP", 70), ("ETH-USDT-SWAP", 3), ("SOL-USDT-SWAP", 2)):
        _basis(store, instrument, NOW - days * DAY)
        _basis(store, instrument, NOW)
    group = store.per_feature_eligibility()["feature_groups"]["basis"]
    assert group["instruments"]["BTC-USDT-SWAP"]["source_days"] == 70
    assert group["instruments"]["ETH-USDT-SWAP"]["source_days"] == 3
    assert group["source_usable_days"] == 2


def test_chronological_validation_is_causal_nonoverlapping_and_not_oot(
    store: MicrostructureStore,
) -> None:
    study = SourceSpecificEventStudy(store)
    observations = []
    marks = {}
    for index in range(50):
        timestamp = NOW + index * DAY
        observations.append((timestamp, float(index)))
        for horizon in (0, 900_000, 1_800_000, 3_600_000, 7_200_000,
                        14_400_000, 28_800_000, 86_400_000):
            marks[timestamp + horizon] = 100 + index + horizon / DAY
    marks = dict(sorted(marks.items()))
    study._mark_timestamp_cache[id(marks)] = list(marks)
    result = study._study_features(
        "causal", observations, marks, "BTC-USDT-SWAP")
    one_hour = result["1H"]
    boundary = one_hour["partition_boundary_ms"]
    research = one_hour["segments"][RESEARCH_SEGMENT]
    validation = one_hour["segments"][VALIDATION_SEGMENT]
    assert research["label_latest_ms"] <= boundary
    assert validation["event_earliest_ms"] > boundary
    assert one_hour["segments_overlap"] is False
    assert one_hour["completed_oot_claim"] is False
    assert study._forward_return(
        marks, observations[0][0], DAY, max_label_ms=observations[0][0]) is None


def test_multiple_testing_diagnostics_cannot_promote_features(
    store: MicrostructureStore,
) -> None:
    study = SourceSpecificEventStudy(store)
    payload = {
        "feature": {
            "1H": {
                "segments": {
                    RESEARCH_SEGMENT: {
                        "spearman_p_value_diagnostic": 0.0001, "event_count": 100},
                    VALIDATION_SEGMENT: {
                        "spearman_p_value_diagnostic": 0.0001, "event_count": 100,
                        "spearman_ic": 0.5},
                }
            }
        }
    }
    study._apply_multiple_testing(payload)
    diagnostic = payload["feature"]["1H"]["segments"][
        VALIDATION_SEGMENT]["multiple_testing"]
    assert diagnostic["can_promote_feature"] is False


def test_validation_code_has_no_order_endpoint_or_order_client() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "dashboard/microstructure.py",
            "dashboard/microstructure_collector.py",
            "dashboard/microstructure_research.py",
            "scripts/run_microstructure_validation.py",
        ))
    assert "/api/v5/trade/order" not in source
    assert "place_order(" not in source
