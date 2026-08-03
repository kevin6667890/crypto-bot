from __future__ import annotations

import asyncio
import time

from dashboard.collector_pressure import PressureHysteresis, sustained_emergency
from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_collector import BoundedPriorityQueue, Collector


def test_transient_burst_enters_pressure_without_becoming_high_pressure():
    pressure = PressureHysteresis()
    assert pressure.observe(441, 500) == "PRESSURE"
    assert pressure.observe(0, 0) == "RECOVERING"
    assert pressure.observe(0, 0) == "RECOVERING"
    assert pressure.observe(0, 0) == "NORMAL"


def test_emergency_requires_sustained_capacity_age_and_raw_stall():
    transient = [
        {"depth": 441, "oldest_age_ms": 1_328, "raw_stalled": False},
        {"depth": 0, "oldest_age_ms": 0, "raw_stalled": False},
    ]
    assert sustained_emergency(transient) is False
    emergency = [
        {"depth": 18_500, "oldest_age_ms": 31_000, "raw_stalled": True},
        {"depth": 19_000, "oldest_age_ms": 36_000, "raw_stalled": True},
        {"depth": 19_500, "oldest_age_ms": 41_000, "raw_stalled": True},
    ]
    assert sustained_emergency(emergency) is True


def test_lane_telemetry_tracks_depth_age_and_rates():
    async def scenario():
        queue = BoundedPriorityQueue(20_000)
        await queue.put(("trade", {"payload": {}}))
        await queue.put(("oi", {"payload": {}}))
        await asyncio.sleep(0.01)
        before = queue.telemetry()
        await queue.get()
        queue.task_done()
        after = queue.telemetry()
        return before, after

    before, after = asyncio.run(scenario())
    assert before["writer_queue_depth"] == 2
    assert before["writer_queue_depth_by_lane"]["raw_trade"] == 1
    assert before["writer_queue_depth_by_lane"]["oi"] == 1
    assert before["writer_queue_oldest_age_ms"] >= 5
    assert before["writer_enqueue_rate_10s"] > 0
    assert after["writer_queue_depth"] == 1
    assert after["writer_dequeue_rate_10s"] > 0


def test_bounded_queue_drains_a_reconnect_shaped_burst_without_loss():
    async def scenario():
        queue = BoundedPriorityQueue(20_000)
        received = []

        async def consume():
            while len(received) < 1_633:
                kind, payload = await queue.get()
                received.append((kind, payload["payload"]["tradeId"]))
                queue.task_done()

        consumer = asyncio.create_task(consume())
        started = time.monotonic()
        for index in range(1_633):
            await queue.put(("trade", {"payload": {"tradeId": str(index)}}))
        maximum = queue.qsize()
        await asyncio.wait_for(queue.join(), timeout=2)
        elapsed = time.monotonic() - started
        await consumer
        return maximum, elapsed, received

    maximum, elapsed, received = asyncio.run(scenario())
    assert maximum <= 1_633
    assert elapsed < 2
    assert len(received) == 1_633
    assert len({identifier for _, identifier in received}) == 1_633


def test_live_queue_pauses_aggregate_then_resumes_after_drain(tmp_path):
    class Writer:
        @staticmethod
        def try_transaction(operation):
            return operation()

    class Engine:
        calls = 0

        def process(self, _instrument, start, end, **_values):
            self.calls += 1
            return {
                "end_ms": end, "conflicts": [], "missing": {},
                "latest_source_ms": start,
            }

    async def scenario():
        collector = Collector(
            MicrostructureStore(tmp_path / "micro.db"),
            maintenance_enabled=False, realtime_aggregation_enabled=True)
        collector.db_writer = Writer()
        engine = Engine()
        await collector.queue.put(("trade", {"payload": {}}))
        task = asyncio.create_task(collector._aggregate_range(
            engine, "BTC-USDT-SWAP", 60_000, 120_000, catchup=False))
        await asyncio.sleep(0.05)
        assert engine.calls == 0
        await collector.queue.get()
        collector.queue.task_done()
        await asyncio.wait_for(task, timeout=2)
        return engine.calls

    assert asyncio.run(scenario()) == 1
