"""Offline live-writer pressure benchmark; never opens a production database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.microstructure import MicrostructureStore, now_ms
from dashboard.microstructure_collector import Collector


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


async def run_scenario(name: str, rate: int, delay_ms: int, *,
                       reconnect: bool = False) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="collector-pressure-") as temporary:
        path = Path(temporary) / "microstructure.db"
        store = MicrostructureStore(path)
        collector = Collector(
            store, maintenance_enabled=False,
            realtime_aggregation_enabled=False)
        instrument = "BTC-USDT-SWAP"
        collector.contract_values[instrument] = 0.01
        writer = store.live_writer()
        transaction_ms: list[float] = []
        original_transaction = writer.transaction

        def delayed_transaction(operation):
            started = time.monotonic()
            if delay_ms:
                time.sleep(delay_ms / 1000)
            result = original_transaction(operation)
            transaction_ms.append((time.monotonic() - started) * 1000)
            return result

        writer.transaction = delayed_transaction  # type: ignore[method-assign]
        store.live_writer = lambda: writer  # type: ignore[method-assign]
        writer_task = asyncio.create_task(collector._writer())
        aggregate_stop = asyncio.Event()
        aggregate_success: list[float] = []
        aggregate_deferred = 0

        async def aggregate_probe():
            nonlocal aggregate_deferred
            while not aggregate_stop.is_set():
                if collector.db_writer is None or collector.queue.qsize():
                    aggregate_deferred += 1
                else:
                    result = await asyncio.to_thread(
                        collector.db_writer.try_transaction, lambda: True)
                    if result:
                        aggregate_success.append(time.monotonic())
                    else:
                        aggregate_deferred += 1
                await asyncio.sleep(0.01)

        aggregate_task = asyncio.create_task(aggregate_probe())
        total = rate * 2 if not reconnect else rate
        base_ts = now_ms()
        max_queue = 0
        max_age = 0
        tracemalloc.start()
        started = time.monotonic()
        for index in range(total):
            payload = {
                "tradeId": f"{name}-{index}", "px": "100", "sz": "1",
                "side": "buy" if index % 2 == 0 else "sell",
                "ts": str(base_ts + index),
            }
            await collector.queue.put(("trade", {
                "instrument": instrument, "payload": payload,
                "received_at_ms": now_ms(),
            }))
            telemetry = collector.queue.telemetry()
            max_queue = max(max_queue, int(telemetry["writer_queue_depth"]))
            max_age = max(max_age, int(telemetry["writer_queue_oldest_age_ms"]))
            if not reconnect:
                target = started + (index + 1) / rate
                await asyncio.sleep(max(0, target - time.monotonic()))
        produced_at = time.monotonic()
        while collector.queue.qsize():
            telemetry = collector.queue.telemetry()
            max_queue = max(max_queue, int(telemetry["writer_queue_depth"]))
            max_age = max(max_age, int(telemetry["writer_queue_oldest_age_ms"]))
            await asyncio.sleep(0.005)
        await asyncio.wait_for(collector.queue.join(), timeout=30)
        drained_at = time.monotonic()
        aggregate_stop.set()
        await aggregate_task
        writer_task.cancel()
        await asyncio.gather(writer_task, return_exceptions=True)
        _, memory_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with store.connect(readonly=True) as connection:
            persisted = connection.execute(
                "SELECT COUNT(*) FROM trade_flow_observations").fetchone()[0]
        aggregate_gaps = [
            later - earlier for earlier, later in zip(
                aggregate_success, aggregate_success[1:])]
        return {
            "name": name,
            "input_rate_per_s": rate if not reconnect else None,
            "messages": total,
            "sqlite_delay_ms": delay_ms,
            "reconnect_burst": reconnect,
            "max_queue": max_queue,
            "max_oldest_age_ms": max_age,
            "drain_seconds": round(drained_at - produced_at, 3),
            "raw_loss": total - int(persisted),
            "aggregate_deferred_samples": aggregate_deferred,
            "aggregate_max_resume_gap_ms": round(max(aggregate_gaps, default=0) * 1000, 3),
            "transaction_p95_ms": round(percentile(transaction_ms, .95), 3),
            "transaction_max_ms": round(max(transaction_ms, default=0), 3),
            "memory_peak_bytes": memory_peak,
            "wal_bytes": Path(f"{path}-wal").stat().st_size if Path(f"{path}-wal").exists() else 0,
        }


async def main() -> None:
    scenarios = [
        ("normal", 100, 50, False),
        ("burst_2x", 200, 50, False),
        ("burst_5x", 500, 50, False),
        ("burst_10x", 1_000, 50, False),
        ("sqlite_100ms", 500, 100, False),
        ("sqlite_250ms", 500, 250, False),
        ("reconnect_batch", 3_000, 100, True),
    ]
    results = []
    for name, rate, delay, reconnect in scenarios:
        results.append(await run_scenario(
            name, rate, delay, reconnect=reconnect))
    print(json.dumps({"scenarios": results}, separators=(",", ":")))


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Run bounded collector pressure fixtures against temp SQLite").parse_args()
    asyncio.run(main())
