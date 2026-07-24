"""Official OKX public historical backfill with durable checkpoints."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .microstructure import INSTRUMENTS, MicrostructureStore, now_ms


OKX_BASE = "https://www.okx.com"


class PublicOKXClient:
    """GET-only client.  There is intentionally no generic request method."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.retries = 0
        self.failed_requests = 0

    def get_public(self, path: str, params: dict[str, Any]) -> list[Any]:
        if not path.startswith(("/api/v5/market/", "/api/v5/public/")):
            raise ValueError("only allowlisted public market endpoints are permitted")
        url = f"{OKX_BASE}{path}?{urlencode(params)}"
        delay = 0.5
        for attempt in range(5):
            try:
                request = Request(url, headers={"User-Agent": "crypto-bot-research/1"})
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                if payload.get("code") != "0":
                    raise RuntimeError(f"OKX public error {payload.get('code')}: {payload.get('msg')}")
                return list(payload.get("data") or [])
            except (HTTPError, URLError, TimeoutError, RuntimeError):
                self.failed_requests += 1
                if attempt == 4:
                    raise
                self.retries += 1
                time.sleep(delay)
                delay = min(8, delay * 2)
        return []


class OfficialBackfill:
    def __init__(self, store: MicrostructureStore, client: PublicOKXClient | None = None) -> None:
        self.store = store
        self.client = client or PublicOKXClient()
        self.store.initialize()
        self.contract_values: dict[str, float] = {}

    def contract_value(self, instrument: str) -> float:
        if instrument not in self.contract_values:
            rows = self.client.get_public(
                "/api/v5/public/instruments", {"instType": "SWAP", "instId": instrument})
            if not rows or rows[0].get("ctType") != "linear":
                raise ValueError(f"unsupported or unstable linear swap: {instrument}")
            self.contract_values[instrument] = float(rows[0]["ctVal"])
        return self.contract_values[instrument]

    def backfill_trades(self, instrument: str, *, max_pages: int = 1000) -> dict[str, Any]:
        """Backfill genuine side-labelled trades, oldest cursor persisted each page.

        ``max_pages`` is an operational batch boundary, not fabricated
        completeness. Re-running resumes with the official cursor.
        """
        instrument = instrument if instrument.endswith("-SWAP") else f"{instrument}-SWAP"
        checkpoint = self._checkpoint("trades", instrument)
        cursor = checkpoint.get("cursor")
        cursor_before = cursor
        prior_source_earliest = checkpoint.get("last_source_ts_ms")
        batch_start_source_earliest = prior_source_earliest
        with self.store.connect(readonly=True) as connection:
            before_row = connection.execute(
                """SELECT MIN(source_ts_ms),MAX(source_ts_ms),COUNT(*)
                   FROM trade_flow_observations
                   WHERE instrument=? AND state='confirmed'""", (instrument,)).fetchone()
        earliest_before, latest_before, rows_before = (
            int(before_row[0]) if before_row[0] is not None else None,
            int(before_row[1]) if before_row[1] is not None else None,
            int(before_row[2]),
        )
        pages = inserted = fetched = duplicates = 0
        earliest = latest = None
        terminal_status = "BATCH_LIMIT_REACHED"
        seen_pages: set[tuple[str, str, int]] = set()
        retries_before = self.client.retries
        database_retries = 0
        contract_value = self.contract_value(instrument)
        while pages < max_pages:
            params: dict[str, Any] = {"instId": instrument, "limit": 100}
            if cursor:
                params["after"] = cursor
            rows = self.client.get_public("/api/v5/market/history-trades", params)
            if not rows:
                terminal_status = "SOURCE_RETENTION_BOUNDARY_REACHED"
                break
            page_identity = (
                str(rows[0].get("tradeId")), str(rows[-1].get("tradeId")), len(rows))
            if page_identity in seen_pages:
                terminal_status = "REPEATED_PAGE_SOURCE_LIMITATION"
                break
            seen_pages.add(page_identity)
            pages += 1
            fetched += len(rows)
            batch = [
                (instrument, row, contract_value,
                 "OKX GET /api/v5/market/history-trades", None)
                for row in rows
            ]
            for database_attempt in range(5):
                try:
                    page_inserted = self.store.insert_trade_batch(batch)
                    break
                except sqlite3.OperationalError as error:
                    if "locked" not in str(error).lower() or database_attempt == 4:
                        raise
                    database_retries += 1
                    time.sleep(min(30, 2 ** (database_attempt + 1)))
            inserted += page_inserted
            duplicates += len(rows) - page_inserted
            for row in rows:
                timestamp = int(row["ts"])
                earliest = timestamp if earliest is None else min(earliest, timestamp)
                latest = timestamp if latest is None else max(latest, timestamp)
            new_cursor = str(rows[-1]["tradeId"])
            if new_cursor == cursor:
                terminal_status = "NON_ADVANCING_CURSOR_SOURCE_LIMITATION"
                break
            if prior_source_earliest is not None and earliest > int(prior_source_earliest):
                terminal_status = "NON_MONOTONIC_SOURCE_TIMESTAMP"
                break
            cursor = new_cursor
            prior_source_earliest = earliest
            self.store.checkpoint("trades", instrument, cursor=cursor,
                                  last_source_ts_ms=earliest, status="running",
                                  metadata={"pages": pages, "fetched": fetched,
                                            "inserted": inserted,
                                            "duplicates": duplicates,
                                            "cursor_before": cursor_before})
            time.sleep(0.12)
        if pages == max_pages and terminal_status == "BATCH_LIMIT_REACHED":
            status = "BATCH_LIMIT_REACHED"
        else:
            status = terminal_status
        if (status == "BATCH_LIMIT_REACHED" and pages
                and batch_start_source_earliest is not None
                and earliest is not None
                and earliest >= int(batch_start_source_earliest)):
            status = "NON_MONOTONIC_SOURCE_TIMESTAMP"
        self.store.checkpoint("trades", instrument, cursor=cursor,
                              last_source_ts_ms=earliest, status=status,
                              metadata={"pages": pages, "fetched": fetched,
                                        "inserted": inserted, "duplicates": duplicates,
                                        "cursor_before": cursor_before,
                                        "official_retention": "last 3 months"})
        self._coverage("trades", "OKX GET /api/v5/market/history-trades + WS trades-all",
                       earliest, latest, pages, status)
        with self.store.connect(readonly=True) as connection:
            after_row = connection.execute(
                """SELECT MIN(source_ts_ms),MAX(source_ts_ms),COUNT(*)
                   FROM trade_flow_observations
                   WHERE instrument=? AND state='confirmed'""", (instrument,)).fetchone()
        return {
            "lane": "trades", "instrument": instrument, "pages": pages,
            "fetched_trades": fetched, "inserted": inserted,
            "duplicate_rows_ignored": duplicates,
            "earliest_ms": earliest, "latest_ms": latest,
            "earliest_ms_before": earliest_before,
            "earliest_ms_after": int(after_row[0]) if after_row[0] is not None else None,
            "latest_ms_before": latest_before,
            "latest_ms_after": int(after_row[1]) if after_row[1] is not None else None,
            "rows_before": rows_before, "rows_after": int(after_row[2]),
            "completeness": status, "cursor_before": cursor_before,
            "cursor": cursor,
            "retries": self.client.retries - retries_before + database_retries,
            "database_lock_retries": database_retries,
        }

    def backfill_funding(self, instrument: str, *, max_pages: int = 100) -> dict[str, Any]:
        cursor = self._checkpoint("funding_settled", instrument).get("cursor")
        pages = inserted = 0
        earliest = latest = None
        exhausted = False
        while pages < max_pages:
            params: dict[str, Any] = {"instId": instrument, "limit": 400}
            if cursor:
                params["after"] = cursor
            rows = self.client.get_public("/api/v5/public/funding-rate-history", params)
            if not rows:
                exhausted = True
                break
            pages += 1
            for row in rows:
                inserted += int(self.store.insert_funding(instrument, row, settled=True))
                timestamp = int(row["fundingTime"])
                earliest = timestamp if earliest is None else min(earliest, timestamp)
                latest = timestamp if latest is None else max(latest, timestamp)
            new_cursor = str(min(int(row["fundingTime"]) for row in rows))
            if new_cursor == cursor:
                exhausted = True
                break
            cursor = new_cursor
            self.store.checkpoint("funding_settled", instrument, cursor=cursor,
                                  last_source_ts_ms=earliest, status="running",
                                  metadata={"pages": pages, "inserted": inserted})
            time.sleep(0.11)
        status = "complete" if exhausted else "limited_batch"
        self.store.checkpoint("funding_settled", instrument, cursor=cursor,
                              last_source_ts_ms=earliest, status=status,
                              metadata={"pages": pages, "inserted": inserted})
        self._coverage("funding_settled", "OKX GET /api/v5/public/funding-rate-history",
                       earliest, latest, pages, status)
        return {"lane": "funding_settled", "instrument": instrument, "pages": pages,
                "inserted": inserted, "earliest_ms": earliest, "latest_ms": latest,
                "completeness": status}

    def backfill_prices(
        self, kind: str, instrument: str, *, max_pages: int = 1500
    ) -> dict[str, Any]:
        if kind == "mark":
            path, api_instrument = "/api/v5/market/history-mark-price-candles", instrument
        elif kind == "index":
            path, api_instrument = "/api/v5/market/history-index-candles", instrument.removesuffix("-SWAP")
        else:
            raise ValueError("kind")
        cursor = self._checkpoint(kind, instrument).get("cursor")
        pages = inserted = 0
        earliest = latest = None
        exhausted = False
        while pages < max_pages:
            params: dict[str, Any] = {"instId": api_instrument, "bar": "1m", "limit": 100}
            if cursor:
                params["after"] = cursor
            rows = self.client.get_public(path, params)
            if not rows:
                exhausted = True
                break
            pages += 1
            inserted += self.store.insert_price_batch(kind, [
                (api_instrument if kind == "index" else instrument,
                 int(row[0]), float(row[4]), float(row[1]), float(row[2]),
                 float(row[3]), str(row[5]) == "1",
                 f"{api_instrument}:1m:{int(row[0])}")
                for row in rows
            ])
            for row in rows:
                timestamp = int(row[0])
                earliest = timestamp if earliest is None else min(earliest, timestamp)
                latest = timestamp if latest is None else max(latest, timestamp)
            new_cursor = str(min(int(row[0]) for row in rows))
            if new_cursor == cursor:
                exhausted = True
                break
            cursor = new_cursor
            self.store.checkpoint(kind, instrument, cursor=cursor,
                                  last_source_ts_ms=earliest, status="running",
                                  metadata={"pages": pages, "inserted": inserted})
            time.sleep(0.11)
        status = "complete" if exhausted else "limited_batch"
        self.store.checkpoint(kind, instrument, cursor=cursor, last_source_ts_ms=earliest,
                              status=status, metadata={"pages": pages, "inserted": inserted})
        self._coverage(kind, f"OKX GET {path}", earliest, latest, pages, status)
        return {"lane": kind, "instrument": instrument, "pages": pages,
                "inserted": inserted, "earliest_ms": earliest, "latest_ms": latest,
                "completeness": status}

    def run(self, *, trade_pages: int = 1000, price_pages: int = 1500) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for instrument in INSTRUMENTS:
            # Lane failures remain independent, especially for SOL.
            for operation in (
                lambda i=instrument: self.backfill_trades(i, max_pages=trade_pages),
                lambda i=instrument: self.backfill_funding(i),
                lambda i=instrument: self.backfill_prices("mark", i, max_pages=price_pages),
                lambda i=instrument: self.backfill_prices("index", i, max_pages=price_pages),
            ):
                try:
                    results.append(operation())
                except Exception as error:
                    results.append({"instrument": instrument, "status": "failed",
                                    "error": f"{type(error).__name__}: {str(error)[:200]}"})
        self.store.aggregate_all()
        self.store.record_health("backfill", "LIVE", last_success_ms=now_ms(),
                                 retry_count=self.client.retries,
                                 failed_request_count=self.client.failed_requests)
        return results

    def _checkpoint(self, lane: str, instrument: str) -> dict[str, Any]:
        with self.store.connect(readonly=True) as c:
            row = c.execute(
                "SELECT * FROM collection_checkpoints WHERE lane=? AND instrument=?",
                (lane, instrument)).fetchone()
        return dict(row) if row else {}

    def _coverage(self, lane: str, source: str, earliest: int | None, latest: int | None,
                  pages: int, status: str) -> None:
        with self.store.connect() as c:
            c.execute(
                """UPDATE source_coverage SET actual_start_ms=COALESCE(
                   MIN(actual_start_ms,?),?),actual_end_ms=COALESCE(MAX(actual_end_ms,?),?),
                   page_count=page_count+?,retries=?,completeness_status=?,updated_at_ms=?
                   WHERE lane=? AND source=?""",
                (earliest, earliest, latest, latest, pages, self.client.retries, status,
                 now_ms(), lane, source))
