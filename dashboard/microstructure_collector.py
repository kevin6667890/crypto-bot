"""Dedicated public/read-only cloud microstructure collector."""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import threading
import time
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
QUEUE_MAX = 100_000
TRADE_STALL_SECONDS = 30
FUNDING_HISTORY_POLL_SECONDS = 300


class Collector:
    """Independent workers feed exactly one SQLite-writing coroutine."""

    def __init__(self, store: MicrostructureStore) -> None:
        self.store = store
        self.store.initialize()
        self.stop_event = asyncio.Event()
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(QUEUE_MAX)
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
        """Health telemetry must never terminate or block a source coroutine."""
        try:
            await asyncio.to_thread(
                self.store.record_health, component, status, **values)
        except Exception:
            # The writer/watchdog will refresh the record after a transient lock.
            return

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
            self.store.record_health(
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
        """Only this task mutates SQLite; every store method owns its connection."""
        while True:
            first = await self.queue.get()
            batch = [first]
            deadline = asyncio.get_running_loop().time() + 0.2
            while len(batch) < 500:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self.queue.get(), timeout=remaining))
                except TimeoutError:
                    break
            try:
                write_started = time.monotonic()
                trades = [
                    (item["instrument"], item["payload"],
                     self.contract_values[item["instrument"]], "OKX WS trades-all", None)
                    for kind, item in batch if kind == "trade"
                ]
                if trades:
                    await asyncio.to_thread(self.store.insert_trade_batch, trades)
                    for instrument in {item[0] for item in trades}:
                        timestamps = [int(item[1]["ts"]) for item in trades if item[0] == instrument]
                        received = [
                            int(item["received_at_ms"])
                            for kind, item in batch
                            if kind == "trade" and item["instrument"] == instrument
                        ]
                        persisted_at = now_ms()
                        await self._record_health(
                            f"trades:{instrument}:received", "LIVE",
                            last_success_ms=max(received),
                            source_lag_ms=max(0, persisted_at - max(timestamps)),
                            **self._counter(f"trades:{instrument}"))
                        await self._record_health(
                            f"trades:{instrument}:persisted", "LIVE",
                            last_success_ms=persisted_at,
                            source_lag_ms=max(0, persisted_at - max(timestamps)),
                            **self._counter(f"trades:{instrument}"))
                        await asyncio.to_thread(
                            self.store.checkpoint,
                            "trades_forward", instrument, cursor=None,
                            last_source_ts_ms=max(timestamps), status="running",
                            metadata={
                                "batch_size": len(timestamps),
                                "received_at_ms": max(received),
                                "persisted_at_ms": persisted_at,
                                "queue_depth": self.queue.qsize(),
                            })
                for kind, item in batch:
                    if kind == "trade":
                        continue
                    if kind == "oi":
                        row = item["payload"]
                        await asyncio.to_thread(
                            self.store.insert_oi,
                            item["instrument"], int(row["ts"]),
                            oi_contracts=float(row["oi"]) if row.get("oi") else None,
                            oi_currency=float(row["oiCcy"]) if row.get("oiCcy") else None,
                            oi_usd=float(row["oiUsd"]) if row.get("oiUsd") else None,
                            source="OKX GET /api/v5/public/open-interest",
                            source_identity=f"{item['instrument']}:{row['ts']}")
                    elif kind == "funding":
                        await asyncio.to_thread(
                            self.store.insert_funding, item["instrument"],
                            item["payload"], settled=False)
                    elif kind == "funding_settled":
                        await asyncio.to_thread(
                            self.store.insert_funding, item["instrument"],
                            item["payload"], settled=True)
                    elif kind in {"mark", "index"}:
                        row = item["payload"]
                        api_instrument = (item["instrument"].removesuffix("-SWAP")
                                          if kind == "index" else item["instrument"])
                        value_key = "idxPx" if kind == "index" else "markPx"
                        await asyncio.to_thread(
                            self.store.insert_price,
                            kind, api_instrument, int(row["ts"]), float(row[value_key]),
                            source_identity=f"{api_instrument}:snapshot:{row['ts']}")
                    elif kind == "liquidation":
                        await asyncio.to_thread(
                            self.store.insert_liquidation,
                            item["instrument"], item["payload"])
                    timestamp = int((item.get("payload") or {}).get("ts") or now_ms())
                    if kind == "funding":
                        payload = item["payload"]
                        await asyncio.to_thread(
                            self.store.checkpoint,
                            "funding_schedule", item["instrument"], cursor=None,
                            last_source_ts_ms=int(payload.get("prevFundingTime") or 0) or None,
                            status=str(payload.get("settState") or "observed"),
                            metadata={
                                "previous_funding_time_ms": int(
                                    payload.get("prevFundingTime") or 0) or None,
                                "next_expected_settlement_ms": int(
                                    payload.get("fundingTime") or 0) or None,
                                "following_expected_settlement_ms": int(
                                    payload.get("nextFundingTime") or 0) or None,
                            })
                    if kind == "funding_settled":
                        timestamp = int(item["payload"]["fundingTime"])
                    await asyncio.to_thread(
                        self.store.checkpoint,
                        f"{kind}_forward", item["instrument"], cursor=None,
                        last_source_ts_ms=timestamp, status="running")
                write_latency_ms = int((time.monotonic() - write_started) * 1000)
            except Exception as error:
                await self._record_health(
                    "writer", "ERROR",
                    last_error=f"{type(error).__name__}: {str(error)[:160]}")
                # The deterministic identities make a replay safe.  Do not
                # discard observations merely because another maintenance
                # transaction briefly held SQLite's single writer lock.
                for item in batch:
                    await self.queue.put(item)
                await asyncio.sleep(1)
            else:
                try:
                    await self._record_health(
                        "writer", "LIVE", last_success_ms=now_ms(),
                        last_error=None, source_lag_ms=self.queue.qsize())
                    await asyncio.to_thread(
                        self.store.checkpoint,
                        "writer", "live_queue", cursor=None,
                        last_source_ts_ms=None, status="running",
                        metadata={
                            "queue_depth": self.queue.qsize(),
                            "write_latency_ms": write_latency_ms,
                            "batch_size": len(batch),
                        })
                except Exception:
                    pass
            finally:
                for _ in batch:
                    self.queue.task_done()

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
        # Let public streams and health become live before a potentially large
        # restart-time aggregation pass.
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=60)
        except TimeoutError:
            pass
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.store.aggregate_recent)
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
                # Daily pruning is restart-safe and aggregates first.
                if now_ms() - self.last_prune_ms > 86_400_000:
                    await asyncio.to_thread(
                        self.store.prune_raw, aggregate_before=False)
                    self.last_prune_ms = now_ms()
            except Exception as error:
                self.store.record_health("maintenance", "ERROR",
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
