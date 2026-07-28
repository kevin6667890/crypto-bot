"""Dedicated public/read-only cloud microstructure collector."""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import threading
import time
from itertools import count
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import aiohttp
import websockets

from .microstructure import INSTRUMENTS, MicrostructureStore, now_ms


REST_BASE = "https://www.okx.com"
BUSINESS_WS = "wss://ws.okx.com:8443/ws/v5/business"
PUBLIC_WS = "wss://ws.okx.com:8443/ws/v5/public"
QUEUE_MAX = 20_000
LIVE_BATCH_MAX = 300
LIVE_BATCH_DELAY_SECONDS = 0.15
TRADE_STALL_SECONDS = 30
FUNDING_HISTORY_POLL_SECONDS = 300


class BoundedPriorityQueue:
    """Compatibility wrapper that prioritizes genuine live observations."""

    def __init__(self, maximum: int) -> None:
        self._queue: asyncio.PriorityQueue[
            tuple[int, int, tuple[str, dict[str, Any]]]
        ] = asyncio.PriorityQueue(maximum)
        self._sequence = count()

    @staticmethod
    def _priority(item: tuple[str, dict[str, Any]]) -> int:
        return 1 if item[0] == "health_flush" else 0

    async def put(self, item: tuple[str, dict[str, Any]]) -> None:
        await self._queue.put((self._priority(item), next(self._sequence), item))

    async def get(self) -> tuple[str, dict[str, Any]]:
        return (await self._queue.get())[2]

    def qsize(self) -> int:
        return self._queue.qsize()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()


class Collector:
    """Independent workers feed exactly one SQLite-writing coroutine."""

    def __init__(self, store: MicrostructureStore) -> None:
        self.store = store
        self.store.initialize()
        self.stop_event = asyncio.Event()
        self.queue = BoundedPriorityQueue(QUEUE_MAX)
        self.pending_health: dict[str, tuple[str, dict[str, Any]]] = {}
        self.health_flush_queued = False
        self.db_writer = None
        self.writer_transactions = 0
        self.writer_rows_total = 0
        self.contract_values: dict[str, float] = {}
        self.counters: dict[str, dict[str, int]] = {
            name: {"reconnect_count": 0, "failed_request_count": 0, "retry_count": 0}
            for name in ("liquidations", "rest")
        }
        self.last_received_ms: dict[str, int] = {}
        # Restart-time full-history pruning previously starved live persistence.
        # Existing aggregates make it safe to wait for the normal daily cycle.
        self.last_prune_ms = now_ms()

    def _counter(self, component: str) -> dict[str, int]:
        return self.counters.setdefault(
            component,
            {"reconnect_count": 0, "failed_request_count": 0, "retry_count": 0},
        )

    async def _record_health(self, component: str, status: str, **values: Any) -> None:
        """Coalesce telemetry into the same logical database writer."""
        self.pending_health[component] = (status, values)
        if not self.health_flush_queued:
            self.health_flush_queued = True
            await self.queue.put(("health_flush", {}))

    async def _supervise(self, component: str, operation: Any) -> None:
        """Restart one lane without affecting any other instrument or source."""
        delay = 1.0
        while not self.stop_event.is_set():
            try:
                await operation()
                if not self.stop_event.is_set():
                    raise RuntimeError("collector worker returned unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception as error:
                state = self._counter(component)
                state["reconnect_count"] += 1
                state["retry_count"] += 1
                await self._record_health(
                    component, "RECONNECTING",
                    last_error=f"{type(error).__name__}: {str(error)[:160]}",
                    **state,
                )
                await asyncio.sleep(min(30, delay) + random.uniform(0, 0.25))
                delay = min(30, delay * 2)

    async def run(self) -> None:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": "crypto-bot-research/1"},
        ) as session:
            await self._load_contract_values(session)
            await self._record_health(
                "service", "RUNNING", last_success_ms=now_ms(),
                last_error=None)
            tasks = [
                asyncio.create_task(self._writer(), name="serialized-sqlite-writer"),
                *(asyncio.create_task(
                    self._supervise(
                        f"trades:{instrument}",
                        lambda i=instrument: self._trade_instrument(i)),
                    name=f"public-trades-{instrument}")
                  for instrument in self.contract_values),
                asyncio.create_task(
                    self._supervise("liquidations", self._liquidations),
                    name="public-liquidations"),
                *(asyncio.create_task(
                    self._supervise(
                        f"rest:{instrument}",
                        lambda i=instrument: self._rest_instrument(session, i)),
                    name=f"rest-{instrument}") for instrument in INSTRUMENTS),
                asyncio.create_task(
                    self._supervise("maintenance", self._maintenance),
                    name="retention-aggregation"),
            ]
            try:
                await self.stop_event.wait()
            finally:
                for task in tasks[1:]:
                    task.cancel()
                await asyncio.gather(*tasks[1:], return_exceptions=True)
                await self.queue.join()
                tasks[0].cancel()
                await asyncio.gather(tasks[0], return_exceptions=True)
                self.store.record_health("service", "STOPPED", last_success_ms=now_ms())

    async def _load_contract_values(self, session: aiohttp.ClientSession) -> None:
        for instrument in INSTRUMENTS:
            try:
                rows = await self._get(session, "/api/v5/public/instruments",
                                       {"instType": "SWAP", "instId": instrument})
                row = rows[0]
                if row.get("state") != "live" or row.get("ctType") != "linear":
                    raise ValueError("instrument not stable linear swap")
                self.contract_values[instrument] = float(row["ctVal"])
            except Exception as error:
                self.store.record_health(f"instrument:{instrument}", "ERROR",
                                         last_error=f"{type(error).__name__}: {str(error)[:160]}")
        if not {"BTC-USDT-SWAP", "ETH-USDT-SWAP"} <= self.contract_values.keys():
            raise RuntimeError("BTC/ETH public swap metadata is required")

    async def _writer(self) -> None:
        """Persist each bounded batch with one durable transaction."""
        self.db_writer = self.store.live_writer()
        try:
            while True:
                first = await self.queue.get()
                batch = [first]
                deadline = (
                    asyncio.get_running_loop().time() + LIVE_BATCH_DELAY_SECONDS)
                while len(batch) < LIVE_BATCH_MAX:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(
                            self.queue.get(), timeout=remaining))
                    except TimeoutError:
                        break
                health = self.pending_health
                self.pending_health = {}
                self.health_flush_queued = False
                try:
                    write_started = time.monotonic()
                    rows = await asyncio.to_thread(
                        self.db_writer.transaction,
                        lambda: self._persist_batch(batch, health))
                    write_latency_ms = int((time.monotonic() - write_started) * 1000)
                    self.writer_transactions += 1
                    self.writer_rows_total += rows
                    self.store.update_operational_metrics(
                        writer_queue_depth=self.queue.qsize(),
                        writer_batch_size=len(batch),
                        writer_rows=rows,
                        writer_transactions_total=self.writer_transactions,
                        writer_rows_total=self.writer_rows_total,
                        last_transaction_at_ms=now_ms(),
                        write_latency_ms=write_latency_ms,
                        maintenance_paused_reason=None)
                    await asyncio.to_thread(
                        self.db_writer.passive_checkpoint,
                        queue_depth=self.queue.qsize())
                except Exception as error:
                    self.pending_health["writer"] = (
                        "ERROR", {"last_error":
                                  f"{type(error).__name__}: {str(error)[:160]}"})
                    for item in batch:
                        await self.queue.put(item)
                    await asyncio.sleep(1)
                finally:
                    for _ in batch:
                        self.queue.task_done()
        finally:
            if self.db_writer is not None:
                await asyncio.to_thread(self.db_writer.close)

    def _persist_batch(
        self, batch: list[tuple[str, dict[str, Any]]],
        health: dict[str, tuple[str, dict[str, Any]]],
    ) -> int:
        rows_written = 0
        received_metrics: dict[str, int] = {}
        persisted_metrics: dict[str, int] = {}
        lag_metrics: dict[str, int] = {}
        trades = [
            (item["instrument"], item["payload"],
             self.contract_values[item["instrument"]], "OKX WS trades-all", None)
            for kind, item in batch if kind == "trade"
        ]
        if trades:
            rows_written += self.store.insert_trade_batch(trades)
            for instrument in {item[0] for item in trades}:
                timestamps = [
                    int(item[1]["ts"]) for item in trades if item[0] == instrument]
                received = [
                    int(item["received_at_ms"]) for kind, item in batch
                    if kind == "trade" and item["instrument"] == instrument]
                persisted_at = now_ms()
                received_metrics[instrument] = max(received)
                persisted_metrics[instrument] = persisted_at
                lag_metrics[instrument] = max(0, persisted_at - max(timestamps))
                for suffix, success in (
                    ("received", max(received)), ("persisted", persisted_at)):
                    self.store.record_health(
                        f"trades:{instrument}:{suffix}", "LIVE",
                        last_success_ms=success,
                        source_lag_ms=max(0, persisted_at - max(timestamps)),
                        **self._counter(f"trades:{instrument}"))
                self.store.checkpoint(
                    "trades_forward", instrument, cursor=None,
                    last_source_ts_ms=max(timestamps), status="running",
                    metadata={
                        "batch_size": len(timestamps),
                        "received_at_ms": max(received),
                        "persisted_at_ms": persisted_at,
                        "queue_depth": self.queue.qsize(),
                    })
        for kind, item in batch:
            if kind in {"trade", "health_flush"}:
                continue
            payload = item["payload"]
            if kind == "oi":
                rows_written += int(self.store.insert_oi(
                    item["instrument"], int(payload["ts"]),
                    oi_contracts=float(payload["oi"]) if payload.get("oi") else None,
                    oi_currency=float(payload["oiCcy"]) if payload.get("oiCcy") else None,
                    oi_usd=float(payload["oiUsd"]) if payload.get("oiUsd") else None,
                    source="OKX GET /api/v5/public/open-interest",
                    source_identity=f"{item['instrument']}:{payload['ts']}"))
            elif kind == "funding":
                rows_written += int(self.store.insert_funding(
                    item["instrument"], payload, settled=False))
            elif kind == "funding_settled":
                rows_written += int(self.store.insert_funding(
                    item["instrument"], payload, settled=True))
            elif kind in {"mark", "index"}:
                api_instrument = (item["instrument"].removesuffix("-SWAP")
                                  if kind == "index" else item["instrument"])
                value_key = "idxPx" if kind == "index" else "markPx"
                rows_written += int(self.store.insert_price(
                    kind, api_instrument, int(payload["ts"]), float(payload[value_key]),
                    source_identity=f"{api_instrument}:snapshot:{payload['ts']}"))
            elif kind == "liquidation":
                rows_written += int(self.store.insert_liquidation(
                    item["instrument"], payload))
            timestamp = int(payload.get("ts") or now_ms())
            if kind == "funding":
                self.store.checkpoint(
                    "funding_schedule", item["instrument"], cursor=None,
                    last_source_ts_ms=int(payload.get("prevFundingTime") or 0) or None,
                    status=str(payload.get("settState") or "observed"),
                    metadata={
                        "previous_funding_time_ms":
                            int(payload.get("prevFundingTime") or 0) or None,
                        "next_expected_settlement_ms":
                            int(payload.get("fundingTime") or 0) or None,
                        "following_expected_settlement_ms":
                            int(payload.get("nextFundingTime") or 0) or None,
                    })
            if kind == "funding_settled":
                timestamp = int(payload["fundingTime"])
            self.store.checkpoint(
                f"{kind}_forward", item["instrument"], cursor=None,
                last_source_ts_ms=timestamp, status="running")
        for component, (status, values) in health.items():
            self.store.record_health(component, status, **values)
        self.store.record_health(
            "writer", "LIVE", last_success_ms=now_ms(), last_error=None,
            source_lag_ms=self.queue.qsize())
        self.store.checkpoint(
            "writer", "live_queue", cursor=None, last_source_ts_ms=None,
            status="running", metadata={
                "queue_depth": self.queue.qsize(),
                "batch_size": len(batch),
                "rows_written": rows_written,
            })
        if received_metrics:
            self.store.update_operational_metrics(
                received_timestamp_by_instrument=received_metrics,
                persisted_timestamp_by_instrument=persisted_metrics,
                live_lag_ms_by_instrument=lag_metrics)
        return rows_written

    async def _trade_instrument(self, instrument: str) -> None:
        component = f"trades:{instrument}"
        await self._websocket_loop(
            component, BUSINESS_WS,
            [{"channel": "trades-all", "instId": instrument}],
            self._handle_trades)

    async def _handle_trades(self, message: dict[str, Any]) -> None:
        argument = message.get("arg") or {}
        instrument = argument.get("instId")
        if argument.get("channel") != "trades-all" or instrument not in self.contract_values:
            return
        latest = None
        received_at = now_ms()
        for trade in message.get("data") or []:
            await self.queue.put(("trade", {
                "instrument": instrument, "payload": trade,
                "received_at_ms": received_at,
            }))
            latest = max(latest or 0, int(trade["ts"]))
        if latest:
            self.last_received_ms[f"trades:{instrument}"] = received_at

    async def _liquidations(self) -> None:
        await self._websocket_loop(
            "liquidations", PUBLIC_WS,
            [{"channel": "liquidation-orders", "instType": "SWAP"}],
            self._handle_liquidations)

    async def _handle_liquidations(self, message: dict[str, Any]) -> None:
        if (message.get("arg") or {}).get("channel") != "liquidation-orders":
            return
        for event in message.get("data") or []:
            instrument = event.get("instId")
            if instrument not in self.contract_values:
                continue
            details = event.get("details") or [event]
            for detail in details:
                payload = {**event, **detail}
                await self.queue.put(("liquidation", {
                    "instrument": instrument, "payload": payload}))
        self.last_received_ms["liquidations:message"] = now_ms()
        await self._record_health(
            "liquidations:message", "LIVE", last_success_ms=now_ms(),
            **self._counter("liquidations"))

    async def _websocket_loop(
        self, component: str, url: str, args: list[dict[str, str]], handler: Any
    ) -> None:
        delay = 1.0
        while not self.stop_event.is_set():
            delivered = False
            last_health_update = 0
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                              close_timeout=5) as socket:
                    await socket.send(json.dumps({"op": "subscribe", "args": args}))
                    await self._record_health(
                        component, "CONNECTED", last_success_ms=now_ms(),
                        last_error=None, **self._counter(component))
                    while not self.stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=15)
                        except TimeoutError:
                            pong = await socket.ping()
                            await asyncio.wait_for(pong, timeout=10)
                            if (component.startswith("trades:")
                                    and now_ms() - self.last_received_ms.get(component, 0)
                                    > TRADE_STALL_SECONDS * 1000):
                                raise TimeoutError(
                                    f"{component} received no trades for "
                                    f"{TRADE_STALL_SECONDS}s")
                            await self._record_health(
                                component, "CONNECTED", last_success_ms=now_ms(),
                                **self._counter(component))
                            continue
                        message = json.loads(raw)
                        if message.get("event") == "error":
                            raise RuntimeError(str(message.get("msg") or "subscription rejected"))
                        if message.get("event") == "subscribe":
                            self.last_received_ms.setdefault(component, now_ms())
                            await self._record_health(
                                component, "CONNECTED", last_success_ms=now_ms(),
                                **self._counter(component))
                            continue
                        await handler(message)
                        delivered = True
                        delay = 1.0
                        if now_ms() - last_health_update >= 5_000:
                            await self._record_health(
                                component, "CONNECTED", last_success_ms=now_ms(),
                                last_error=None, **self._counter(component))
                            last_health_update = now_ms()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                state = self._counter(component)
                state["reconnect_count"] += 1
                state["retry_count"] += 1
                await self._record_health(
                    component, "RECONNECTING",
                    last_error=f"{type(error).__name__}: {str(error)[:160]}",
                    **state)
                await asyncio.sleep(min(30, delay) + random.uniform(0, 0.25))
                delay = 1.0 if delivered else min(30, delay * 2)

    async def _rest_instrument(
        self, session: aiohttp.ClientSession, instrument: str
    ) -> None:
        if instrument not in self.contract_values:
            return
        last_funding_history_poll = 0
        while not self.stop_event.is_set():
            try:
                oi = (await self._get(session, "/api/v5/public/open-interest",
                                      {"instType": "SWAP", "instId": instrument}))[0]
                funding = (await self._get(session, "/api/v5/public/funding-rate",
                                           {"instId": instrument}))[0]
                mark = (await self._get(session, "/api/v5/public/mark-price",
                                        {"instType": "SWAP", "instId": instrument}))[0]
                index = (await self._get(session, "/api/v5/market/index-tickers",
                                         {"instId": instrument.removesuffix("-SWAP")}))[0]
                settled_rows: list[dict[str, Any]] = []
                if now_ms() - last_funding_history_poll >= FUNDING_HISTORY_POLL_SECONDS * 1000:
                    settled_rows = await self._get(
                        session, "/api/v5/public/funding-rate-history",
                        {"instId": instrument, "limit": "20"})
                    last_funding_history_poll = now_ms()
                for kind, payload in (("oi", oi), ("funding", funding),
                                      ("mark", mark), ("index", index)):
                    await self.queue.put((kind, {"instrument": instrument, "payload": payload}))
                for payload in reversed(settled_rows):
                    await self.queue.put(("funding_settled", {
                        "instrument": instrument, "payload": payload}))
                latest = max(int(oi["ts"]), int(mark["ts"]), int(index["ts"]))
                await self._record_health(
                    f"rest:{instrument}", "LIVE", last_success_ms=now_ms(),
                    source_lag_ms=max(0, now_ms() - latest), **self._counter("rest"))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A SOL failure terminates neither this loop nor BTC/ETH tasks.
                state = self._counter("rest")
                state["failed_request_count"] += 1
                await self._record_health(
                    f"rest:{instrument}", "ERROR",
                    last_error=f"{type(error).__name__}: {str(error)[:160]}", **state)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=15)
            except TimeoutError:
                pass

    async def _get(self, session: aiohttp.ClientSession, path: str,
                   params: dict[str, str]) -> list[Any]:
        del session
        if not path.startswith(("/api/v5/public/", "/api/v5/market/")):
            raise ValueError("public endpoint allowlist")
        delay = 0.5
        for attempt in range(5):
            try:
                payload = await asyncio.to_thread(self._sync_public_get, path, params)
                if payload.get("code") != "0":
                    raise RuntimeError(str(payload.get("msg") or payload.get("code")))
                return list(payload.get("data") or [])
            except asyncio.CancelledError:
                raise
            except Exception:
                self._counter("rest")["failed_request_count"] += 1
                if attempt == 4:
                    raise
                self._counter("rest")["retry_count"] += 1
                await asyncio.sleep(delay + random.uniform(0, 0.2))
                delay = min(8, delay * 2)
        return []

    @staticmethod
    def _sync_public_get(path: str, params: dict[str, str]) -> dict[str, Any]:
        request = Request(
            f"{REST_BASE}{path}?{urlencode(params)}",
            headers={"User-Agent": "crypto-bot-research/1"})
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    async def _maintenance(self) -> None:
        # Startup only verifies schema.  Low-priority incremental work waits
        # until live streams have established and the queue is empty.
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=60)
        except TimeoutError:
            pass
        while not self.stop_event.is_set():
            try:
                if self.queue.qsize() or self.db_writer is None:
                    self.store.update_operational_metrics(
                        maintenance_paused_reason="live_queue_not_empty")
                    raise RuntimeError("maintenance deferred for live collection")
                started = time.monotonic()
                await asyncio.to_thread(
                    self.db_writer.transaction, self.store.aggregate_recent)
                duration = int((time.monotonic() - started) * 1000)
                self.store.update_operational_metrics(
                    last_maintenance_duration_ms=duration,
                    maintenance_paused_reason=None)
                with self.store.connect(readonly=True) as connection:
                    aggregate_latest = {
                        instrument: connection.execute(
                            """SELECT MAX(last_source_ts_ms) FROM cvd_aggregates
                               WHERE instrument=? AND resolution='1m'""",
                            (instrument,)).fetchone()[0]
                        for instrument in INSTRUMENTS
                    }
                for instrument, source_timestamp in aggregate_latest.items():
                    if source_timestamp is not None:
                        await self._record_health(
                            f"trades:{instrument}:aggregated", "LIVE",
                            last_success_ms=now_ms(),
                            source_lag_ms=max(0, now_ms() - int(source_timestamp)),
                            **self._counter(f"trades:{instrument}"))
                self.store.update_operational_metrics(
                    aggregated_timestamp_by_instrument={
                        instrument: int(timestamp)
                        for instrument, timestamp in aggregate_latest.items()
                        if timestamp is not None
                    })
                # Retention work is one small, restart-safe transaction.
                if now_ms() - self.last_prune_ms > 86_400_000:
                    await asyncio.to_thread(
                        self.db_writer.transaction,
                        lambda: self.store.prune_raw_bounded(maximum_rows=500))
                    if not self.queue.qsize():
                        self.last_prune_ms = now_ms()
            except Exception as error:
                if not str(error).startswith("maintenance deferred"):
                    await self._record_health(
                        "maintenance", "ERROR",
                        last_error=f"{type(error).__name__}: {str(error)[:160]}")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=300)
            except TimeoutError:
                pass


def start_health_server(store: MicrostructureStore) -> ThreadingHTTPServer:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/health", "/api/research/microstructure/health"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = json.dumps(store.liveness()).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 8770), HealthHandler)
    threading.Thread(target=server.serve_forever, name="microstructure-health",
                     daemon=True).start()
    return server


async def main_async() -> None:
    store = MicrostructureStore(Path(os.getenv(
        "MICROSTRUCTURE_DB_PATH", "/app/data_cache/market_microstructure.db")))
    server = start_health_server(store)
    collector = Collector(store)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, collector.stop_event.set)
        except NotImplementedError:
            pass
    try:
        await collector.run()
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
