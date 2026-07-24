"""Forward-only OKX public market-microstructure research foundation.

This module is deliberately isolated from paper execution.  It has no order
client, credential handling, or strategy-discovery entry point.  Missing
observations remain missing.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


MICROSTRUCTURE_SCHEMA_VERSION = "market-microstructure-schema-v1"
MICROSTRUCTURE_SOURCE_VERSION = "okx-public-microstructure-v1"
MICROSTRUCTURE_FEATURE_VERSION = "market-microstructure-features-v1"
MICROSTRUCTURE_REPORT_VERSION = "microstructure-exploratory-report-v1"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
RESOLUTIONS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1H": 3_600_000,
               "4H": 14_400_000, "1D": 86_400_000}
RAW_RETENTION_MS = 90 * 86_400_000
LIQUIDATION_RETENTION_MS = 180 * 86_400_000
FORMAL_SAMPLE_DAYS = 90
MINIMUM_SAMPLE_DAYS = 14


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_iso(timestamp_ms: int | None = None) -> str:
    value = datetime.fromtimestamp((timestamp_ms or now_ms()) / 1000, timezone.utc)
    return value.replace(microsecond=0).isoformat()


def default_database_path() -> Path:
    configured = os.getenv("MICROSTRUCTURE_DB_PATH")
    if configured:
        return Path(configured)
    return Path.home() / "crypto-bot-research" / "data" / "market_microstructure.db"


def identity(*parts: object) -> str:
    """Stable semantic identity; paths and ingestion time are never inputs."""
    raw = "\x1f".join(str(item) for item in parts).encode()
    return hashlib.sha256(raw).hexdigest()


OBSERVATION_COLUMNS = """
    source TEXT NOT NULL,
    source_version TEXT NOT NULL,
    instrument TEXT NOT NULL,
    source_ts_ms INTEGER NOT NULL,
    ingested_at_ms INTEGER NOT NULL,
    resolution TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('confirmed','provisional')),
    source_identity TEXT NOT NULL,
    uniqueness_key TEXT PRIMARY KEY
"""


class MicrostructureStore:
    """SQLite owner with short-lived connections and idempotent writes."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or default_database_path())

    @contextmanager
    def connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        target = f"file:{self.path}?mode=ro" if readonly else str(self.path)
        connection = sqlite3.connect(target, uri=readonly, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        if not readonly:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        try:
            yield connection
            if not readonly:
                connection.commit()
        except Exception:
            if not readonly:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as c:
            c.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS schema_metadata(
                    schema_version TEXT PRIMARY KEY, source_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL, report_version TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_coverage(
                    lane TEXT NOT NULL, source TEXT NOT NULL, stream_type TEXT NOT NULL,
                    native_frequency TEXT NOT NULL, instruments TEXT NOT NULL,
                    requested_start_ms INTEGER, requested_end_ms INTEGER,
                    actual_start_ms INTEGER, actual_end_ms INTEGER, page_count INTEGER NOT NULL DEFAULT 0,
                    retries INTEGER NOT NULL DEFAULT 0, gap_count INTEGER NOT NULL DEFAULT 0,
                    source_limitation TEXT NOT NULL, completeness_status TEXT NOT NULL,
                    status TEXT NOT NULL, updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(lane,source));
                CREATE TABLE IF NOT EXISTS collection_gaps(
                    lane TEXT NOT NULL, instrument TEXT NOT NULL, start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL, reason TEXT NOT NULL, detected_at_ms INTEGER NOT NULL,
                    resolved_at_ms INTEGER, PRIMARY KEY(lane,instrument,start_ms,end_ms));
                CREATE TABLE IF NOT EXISTS collector_health(
                    component TEXT PRIMARY KEY, status TEXT NOT NULL, last_success_ms INTEGER,
                    last_error TEXT, reconnect_count INTEGER NOT NULL DEFAULT 0,
                    failed_request_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0, source_lag_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_checkpoints(
                    lane TEXT NOT NULL, instrument TEXT NOT NULL, cursor TEXT,
                    last_source_ts_ms INTEGER, status TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL, PRIMARY KEY(lane,instrument));
                CREATE TABLE IF NOT EXISTS trade_flow_observations(
                    {OBSERVATION_COLUMNS}, trade_id TEXT, side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                    price REAL NOT NULL, size REAL NOT NULL, contract_value REAL NOT NULL,
                    notional REAL NOT NULL, provenance_table TEXT);
                CREATE INDEX IF NOT EXISTS idx_trade_flow_time
                    ON trade_flow_observations(instrument,source_ts_ms);
                CREATE TABLE IF NOT EXISTS oi_observations(
                    {OBSERVATION_COLUMNS}, oi_contracts REAL, oi_currency REAL, oi_usd REAL,
                    provenance_table TEXT);
                CREATE INDEX IF NOT EXISTS idx_oi_time ON oi_observations(instrument,source_ts_ms);
                CREATE TABLE IF NOT EXISTS funding_settled(
                    {OBSERVATION_COLUMNS}, funding_rate REAL NOT NULL, realized_rate REAL,
                    funding_time_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS funding_predicted(
                    {OBSERVATION_COLUMNS}, funding_rate REAL NOT NULL,
                    next_funding_time_ms INTEGER NOT NULL, premium REAL);
                CREATE TABLE IF NOT EXISTS mark_price_observations(
                    {OBSERVATION_COLUMNS}, open REAL, high REAL, low REAL, close REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_mark_time
                    ON mark_price_observations(instrument,source_ts_ms);
                CREATE TABLE IF NOT EXISTS index_price_observations(
                    {OBSERVATION_COLUMNS}, open REAL, high REAL, low REAL, close REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_index_time
                    ON index_price_observations(instrument,source_ts_ms);
                CREATE TABLE IF NOT EXISTS liquidation_observations(
                    {OBSERVATION_COLUMNS}, side TEXT NOT NULL, size REAL NOT NULL,
                    price REAL, bankruptcy_loss REAL, reliability_note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cvd_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL, bucket_ms INTEGER NOT NULL,
                    buy_notional REAL NOT NULL, sell_notional REAL NOT NULL, delta REAL NOT NULL,
                    cumulative_anchored REAL NOT NULL, observation_count INTEGER NOT NULL,
                    first_source_ts_ms INTEGER NOT NULL, last_source_ts_ms INTEGER NOT NULL,
                    gap_flag INTEGER NOT NULL, source_version TEXT NOT NULL,
                    PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS oi_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL, bucket_ms INTEGER NOT NULL,
                    first_value REAL NOT NULL, last_value REAL NOT NULL, min_value REAL NOT NULL,
                    max_value REAL NOT NULL, absolute_change REAL NOT NULL, percentage_change REAL,
                    observation_count INTEGER NOT NULL, first_source_ts_ms INTEGER NOT NULL,
                    last_source_ts_ms INTEGER NOT NULL, gap_flag INTEGER NOT NULL,
                    source_version TEXT NOT NULL, PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS basis_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL, bucket_ms INTEGER NOT NULL,
                    first_basis REAL NOT NULL, last_basis REAL NOT NULL, min_basis REAL NOT NULL,
                    max_basis REAL NOT NULL, first_basis_pct REAL NOT NULL,
                    last_basis_pct REAL NOT NULL, expansion REAL NOT NULL,
                    observation_count INTEGER NOT NULL, first_source_ts_ms INTEGER NOT NULL,
                    last_source_ts_ms INTEGER NOT NULL, gap_flag INTEGER NOT NULL,
                    source_version TEXT NOT NULL, PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS feature_snapshots(
                    instrument TEXT NOT NULL, decision_ts_ms INTEGER NOT NULL,
                    resolution TEXT NOT NULL, feature_version TEXT NOT NULL,
                    feature_name TEXT NOT NULL, feature_value REAL,
                    source_timestamps_json TEXT NOT NULL, state TEXT NOT NULL,
                    uniqueness_key TEXT PRIMARY KEY);
                CREATE INDEX IF NOT EXISTS idx_features_time
                    ON feature_snapshots(instrument,decision_ts_ms);
                CREATE TABLE IF NOT EXISTS event_study_results(
                    report_id TEXT NOT NULL, feature_name TEXT NOT NULL, horizon TEXT NOT NULL,
                    payload_json TEXT NOT NULL, event_count INTEGER NOT NULL,
                    created_at_ms INTEGER NOT NULL, PRIMARY KEY(report_id,feature_name,horizon));
                CREATE TABLE IF NOT EXISTS research_manifests(
                    manifest_id TEXT PRIMARY KEY, manifest_type TEXT NOT NULL,
                    version TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS table_row_counts(
                    table_name TEXT PRIMARY KEY, row_count INTEGER NOT NULL);
                """
            )
            counted_tables = (
                "trade_flow_observations", "oi_observations", "funding_settled",
                "funding_predicted", "mark_price_observations",
                "index_price_observations", "liquidation_observations",
                "cvd_aggregates", "oi_aggregates", "basis_aggregates",
            )
            for table in counted_tables:
                c.execute(
                    f"""INSERT OR IGNORE INTO table_row_counts
                        SELECT ?,COUNT(*) FROM {table}""", (table,))
                c.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS count_{table}_insert
                        AFTER INSERT ON {table} BEGIN
                        UPDATE table_row_counts SET row_count=row_count+1
                        WHERE table_name='{table}'; END""")
                c.execute(
                    f"""CREATE TRIGGER IF NOT EXISTS count_{table}_delete
                        AFTER DELETE ON {table} BEGIN
                        UPDATE table_row_counts SET row_count=row_count-1
                        WHERE table_name='{table}'; END""")
            c.execute(
                """INSERT OR IGNORE INTO schema_metadata VALUES(?,?,?,?,?)""",
                (MICROSTRUCTURE_SCHEMA_VERSION, MICROSTRUCTURE_SOURCE_VERSION,
                 MICROSTRUCTURE_FEATURE_VERSION, MICROSTRUCTURE_REPORT_VERSION, now_ms()),
            )
        self.seed_source_audit()

    @staticmethod
    def _counted_rows(c: sqlite3.Connection, table: str) -> int:
        return int(c.execute(
            "SELECT row_count FROM table_row_counts WHERE table_name=?", (table,)
        ).fetchone()[0])

    def seed_source_audit(self) -> None:
        rows = [
            ("trades", "OKX GET /api/v5/market/history-trades + WS trades-all",
             "REST+WebSocket", "per trade", "SWAP", "Official retention is last 3 months; 100/page; 20 REST requests/2s/IP.",
             "LIMITED_HISTORY"),
            ("oi", "OKX GET /api/v5/public/open-interest + WS open-interest",
             "REST+WebSocket", "event/current snapshot", "SWAP",
             "No official instrument-level historical OI endpoint; existing genuine observations only.",
             "FORWARD_ONLY"),
            ("funding_settled", "OKX GET /api/v5/public/funding-rate-history",
             "REST", "settlement schedule", "SWAP", "400/page; official settled history.", "LIMITED_HISTORY"),
            ("funding_predicted", "OKX GET /api/v5/public/funding-rate",
             "REST", "current period", "SWAP", "Current/provisional only; never merged with settled.", "FORWARD_ONLY"),
            ("mark", "OKX GET /api/v5/market/history-mark-price-candles",
             "REST", "1m", "SWAP", "100/page; historical candle availability is exchange-limited.",
             "LIMITED_HISTORY"),
            ("index", "OKX GET /api/v5/market/history-index-candles",
             "REST", "1m", "BTC-USDT,ETH-USDT,SOL-USDT",
             "100/page; historical candle availability is exchange-limited.", "LIMITED_HISTORY"),
            ("basis", "causal mark/index as-of join", "derived", "1m", "SWAP",
             "Only where both genuine confirmed observations exist.", "SUPPORTED"),
            ("liquidations", "OKX WS liquidation-orders", "WebSocket", "at most 1 update/s",
             "SWAP/FUTURES", "Historical REST retired April 2023; feed is not a complete liquidation ledger.",
             "FORWARD_ONLY"),
        ]
        timestamp = now_ms()
        with self.connect() as c:
            c.executemany(
                """INSERT INTO source_coverage(
                    lane,source,stream_type,native_frequency,instruments,source_limitation,
                    completeness_status,status,updated_at_ms)
                    VALUES(?,?,?,?,?,?,'not_started',?,?)
                    ON CONFLICT(lane,source) DO UPDATE SET
                    stream_type=excluded.stream_type,native_frequency=excluded.native_frequency,
                    instruments=excluded.instruments,source_limitation=excluded.source_limitation,
                    status=excluded.status,updated_at_ms=excluded.updated_at_ms""",
                [(*row, timestamp) for row in rows],
            )

    @staticmethod
    def observation_base(
        source: str, instrument: str, source_ts_ms: int, resolution: str,
        state: str, source_identity: str, key: str,
    ) -> tuple[Any, ...]:
        return (source, MICROSTRUCTURE_SOURCE_VERSION, instrument, int(source_ts_ms),
                now_ms(), resolution, state, source_identity, key)

    def insert_trade(
        self, instrument: str, payload: dict[str, Any], *,
        contract_value: float, source: str = "OKX public trade",
        provenance_table: str | None = None,
    ) -> bool:
        return bool(self.insert_trade_batch(
            [(instrument, payload, contract_value, source, provenance_table)]))

    def insert_trade_batch(
        self, items: Iterable[tuple[str, dict[str, Any], float, str, str | None]]
    ) -> int:
        rows = []
        for instrument, payload, contract_value, source, provenance_table in items:
            side = str(payload["side"]).lower()
            if side not in {"buy", "sell"}:
                raise ValueError("official trade side must be buy or sell")
            timestamp = int(payload["ts"])
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            price, size = float(payload["px"]), float(payload["sz"])
            # Linear USDT swaps quote contracts in base-coin ctVal.
            notional = price * size * float(contract_value)
            trade_id = str(payload.get("tradeId") or "")
            source_identity = trade_id or identity(instrument, timestamp, side, price, size)
            key = identity("trade", source, instrument, source_identity)
            values = self.observation_base(
                source, instrument, timestamp, "trade", "confirmed", source_identity, key)
            rows.append((*values, trade_id or None, side, price, size, float(contract_value),
                         notional, provenance_table))
        with self.connect() as c:
            before = self._counted_rows(c, "trade_flow_observations")
            c.executemany(
                """INSERT OR IGNORE INTO trade_flow_observations VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows)
            return self._counted_rows(c, "trade_flow_observations") - before

    def insert_oi(
        self, instrument: str, timestamp_ms: int, *,
        oi_contracts: float | None, oi_currency: float | None, oi_usd: float | None,
        source: str, source_identity: str, provenance_table: str | None = None,
    ) -> bool:
        key = identity("oi", source, instrument, source_identity)
        values = self.observation_base(source, instrument, timestamp_ms, "snapshot",
                                       "confirmed", source_identity, key)
        with self.connect() as c:
            before = c.total_changes
            c.execute(
                "INSERT OR IGNORE INTO oi_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values, oi_contracts, oi_currency, oi_usd, provenance_table),
            )
            return c.total_changes > before

    def insert_price(
        self, kind: str, instrument: str, timestamp_ms: int, close: float, *,
        open_: float | None = None, high: float | None = None, low: float | None = None,
        confirmed: bool = True, source_identity: str,
    ) -> bool:
        return bool(self.insert_price_batch(kind, [
            (instrument, timestamp_ms, close, open_, high, low, confirmed, source_identity)
        ]))

    def insert_price_batch(
        self, kind: str,
        items: Iterable[
            tuple[str, int, float, float | None, float | None, float | None, bool, str]
        ],
    ) -> int:
        if kind not in {"mark", "index"}:
            raise ValueError("price kind")
        table = f"{kind}_price_observations"
        source = f"OKX public {kind} price"
        rows = []
        for instrument, timestamp_ms, close, open_, high, low, confirmed, source_identity in items:
            key = identity(kind, instrument, source_identity)
            values = self.observation_base(source, instrument, timestamp_ms, "1m",
                                           "confirmed" if confirmed else "provisional",
                                           source_identity, key)
            rows.append((*values, open_, high, low, close))
        with self.connect() as c:
            before = self._counted_rows(c, table)
            c.executemany(
                f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            return self._counted_rows(c, table) - before

    def insert_funding(
        self, instrument: str, payload: dict[str, Any], *, settled: bool
    ) -> bool:
        if settled:
            timestamp = int(payload["fundingTime"])
            rate = float(payload["fundingRate"])
            realized = float(payload.get("realizedRate") or rate)
            source_id = f"{instrument}:{timestamp}"
            key = identity("funding_settled", source_id)
            base = self.observation_base("OKX funding-rate-history", instrument, timestamp,
                                         "settlement", "confirmed", source_id, key)
            sql = "INSERT OR IGNORE INTO funding_settled VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            tail = (rate, realized, timestamp)
        else:
            source_timestamp = int(payload.get("ts") or now_ms())
            funding_time = int(payload["fundingTime"])
            rate = float(payload["fundingRate"])
            source_id = f"{instrument}:{source_timestamp}:{funding_time}"
            key = identity("funding_predicted", source_id)
            base = self.observation_base("OKX current funding-rate", instrument,
                                         source_timestamp, "current_period", "provisional",
                                         source_id, key)
            sql = "INSERT OR IGNORE INTO funding_predicted VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
            tail = (rate, funding_time, float(payload["premium"]) if payload.get("premium") else None)
        with self.connect() as c:
            before = c.total_changes
            c.execute(sql, (*base, *tail))
            return c.total_changes > before

    def insert_liquidation(self, instrument: str, payload: dict[str, Any]) -> bool:
        timestamp = int(payload.get("ts") or payload.get("bkLossTs") or now_ms())
        source_id = str(payload.get("ordId") or identity(instrument, timestamp,
                                                        payload.get("side"), payload.get("sz")))
        key = identity("liquidation", instrument, source_id)
        base = self.observation_base("OKX WS liquidation-orders", instrument, timestamp,
                                     "event", "confirmed", source_id, key)
        with self.connect() as c:
            before = c.total_changes
            c.execute(
                "INSERT OR IGNORE INTO liquidation_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*base, str(payload["side"]), float(payload["sz"]),
                 float(payload["px"]) if payload.get("px") else None,
                 float(payload["bkLoss"]) if payload.get("bkLoss") else None,
                 "OKX caps pushes and does not represent a complete liquidation ledger"),
            )
            return c.total_changes > before

    def checkpoint(
        self, lane: str, instrument: str, *, cursor: str | None,
        last_source_ts_ms: int | None, status: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as c:
            c.execute(
                """INSERT INTO collection_checkpoints VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(lane,instrument) DO UPDATE SET
                   cursor=excluded.cursor,last_source_ts_ms=excluded.last_source_ts_ms,
                   status=excluded.status,metadata_json=excluded.metadata_json,
                   updated_at_ms=excluded.updated_at_ms""",
                (lane, instrument, cursor, last_source_ts_ms, status,
                 json.dumps(metadata or {}, sort_keys=True), now_ms()),
            )

    def record_health(self, component: str, status: str, **values: Any) -> None:
        fields = {
            "last_success_ms": values.get("last_success_ms"),
            "last_error": values.get("last_error"),
            "reconnect_count": int(values.get("reconnect_count", 0)),
            "failed_request_count": int(values.get("failed_request_count", 0)),
            "retry_count": int(values.get("retry_count", 0)),
            "source_lag_ms": values.get("source_lag_ms"),
        }
        with self.connect() as c:
            c.execute(
                """INSERT INTO collector_health VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(component) DO UPDATE SET
                   status=excluded.status,last_success_ms=COALESCE(excluded.last_success_ms,collector_health.last_success_ms),
                   last_error=excluded.last_error,reconnect_count=excluded.reconnect_count,
                   failed_request_count=excluded.failed_request_count,retry_count=excluded.retry_count,
                   source_lag_ms=excluded.source_lag_ms,updated_at_ms=excluded.updated_at_ms""",
                (component, status, fields["last_success_ms"], fields["last_error"],
                 fields["reconnect_count"], fields["failed_request_count"], fields["retry_count"],
                 fields["source_lag_ms"], now_ms()),
            )

    def aggregate_all(self) -> dict[str, int]:
        return self._aggregate_since(None)

    def aggregate_recent(self, timestamp_ms: int | None = None) -> dict[str, int]:
        """Refresh buckets that can still receive live observations.

        The largest durable bucket is one day, so starting at the current UTC
        day boundary includes every bucket that live collection can change.
        Historical backfills continue to call ``aggregate_all`` explicitly.
        """
        current = timestamp_ms or now_ms()
        return self._aggregate_since((current // RESOLUTIONS["1D"]) * RESOLUTIONS["1D"])

    def _aggregate_since(self, since_ms: int | None) -> dict[str, int]:
        counts = {"cvd": 0, "oi": 0, "basis": 0}
        with self.connect() as c:
            instruments = [row[0] for row in c.execute(
                """SELECT instrument FROM trade_flow_observations UNION
                   SELECT instrument FROM oi_observations UNION
                   SELECT instrument FROM mark_price_observations"""
            )]
            for instrument in instruments:
                self._detect_gaps(c, instrument, since_ms)
                for name, width in RESOLUTIONS.items():
                    counts["cvd"] += self._aggregate_cvd(
                        c, instrument, name, width, since_ms)
                    counts["oi"] += self._aggregate_oi(
                        c, instrument, name, width, since_ms)
                    counts["basis"] += self._aggregate_basis(
                        c, instrument, name, width, since_ms)
            c.execute(
                """INSERT INTO collector_health(component,status,last_success_ms,last_error,
                   reconnect_count,failed_request_count,retry_count,source_lag_ms,updated_at_ms)
                   VALUES('aggregation','LIVE',?,NULL,0,0,0,0,?)
                   ON CONFLICT(component) DO UPDATE SET status='LIVE',
                   last_success_ms=excluded.last_success_ms,last_error=NULL,updated_at_ms=excluded.updated_at_ms""",
                (now_ms(), now_ms()),
            )
        return counts

    @staticmethod
    def _detect_gaps(
        c: sqlite3.Connection, instrument: str, since_ms: int | None = None
    ) -> None:
        for lane, table, threshold in (
            ("trades", "trade_flow_observations", 60_000),
            ("oi", "oi_observations", 45_000),
            ("mark", "mark_price_observations", 120_000),
        ):
            where = "instrument=?"
            parameters: tuple[Any, ...] = (instrument,)
            if since_ms is not None:
                where += " AND source_ts_ms>=?"
                parameters += (since_ms,)
            times = [int(row[0]) for row in c.execute(
                f"""SELECT DISTINCT source_ts_ms FROM {table}
                    WHERE {where} ORDER BY source_ts_ms""", parameters)]
            for start, end in zip(times, times[1:]):
                if end - start > threshold:
                    c.execute(
                        """INSERT OR IGNORE INTO collection_gaps
                           VALUES(?,?,?,?,?,?,NULL)""",
                        (lane, instrument, start, end, "source observation gap", now_ms()))

    @staticmethod
    def _gap_flag(times: list[int], width: int, expected_frequency: int) -> int:
        if len(times) < 2:
            return 0
        return int(any(b - a > expected_frequency * 2 for a, b in zip(times, times[1:]))
                   or times[-1] - times[0] + expected_frequency < min(width, expected_frequency * 2))

    def _aggregate_cvd(self, c: sqlite3.Connection, instrument: str,
                       resolution: str, width: int,
                       since_ms: int | None = None) -> int:
        start_ms = ((since_ms // width) * width
                    if since_ms is not None else None)
        time_filter = " AND source_ts_ms>=?" if start_ms is not None else ""
        parameters: tuple[Any, ...] = (width, width, instrument)
        if start_ms is not None:
            parameters += (start_ms,)
        rows = c.execute(
            """SELECT (source_ts_ms / ?) * ? bucket,source_ts_ms,side,notional
               FROM trade_flow_observations WHERE instrument=? AND state='confirmed'
               """ + time_filter + """
               ORDER BY source_ts_ms,source_identity""", parameters).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["bucket"]), []).append(row)
        cumulative = 0.0
        if grouped:
            prior = c.execute(
                """SELECT cumulative_anchored FROM cvd_aggregates
                   WHERE instrument=? AND resolution=? AND bucket_ms<?
                   ORDER BY bucket_ms DESC LIMIT 1""",
                (instrument, resolution, min(grouped))).fetchone()
            cumulative = float(prior[0]) if prior else 0.0
        values = []
        for bucket, items in sorted(grouped.items()):
            buy = sum(float(x["notional"]) for x in items if x["side"] == "buy")
            sell = sum(float(x["notional"]) for x in items if x["side"] == "sell")
            cumulative += buy - sell
            times = [int(x["source_ts_ms"]) for x in items]
            values.append((instrument, resolution, bucket, buy, sell, buy - sell, cumulative,
                           len(items), min(times), max(times),
                           self._gap_flag(times, width, 1_000), MICROSTRUCTURE_SOURCE_VERSION))
        c.executemany(
            """INSERT INTO cvd_aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instrument,resolution,bucket_ms) DO UPDATE SET
               buy_notional=excluded.buy_notional,sell_notional=excluded.sell_notional,
               delta=excluded.delta,cumulative_anchored=excluded.cumulative_anchored,
               observation_count=excluded.observation_count,
               first_source_ts_ms=excluded.first_source_ts_ms,last_source_ts_ms=excluded.last_source_ts_ms,
               gap_flag=excluded.gap_flag,source_version=excluded.source_version""", values)
        return len(values)

    def _aggregate_oi(self, c: sqlite3.Connection, instrument: str,
                      resolution: str, width: int,
                      since_ms: int | None = None) -> int:
        start_ms = ((since_ms // width) * width
                    if since_ms is not None else None)
        time_filter = " AND source_ts_ms>=?" if start_ms is not None else ""
        parameters: tuple[Any, ...] = (width, width, instrument)
        if start_ms is not None:
            parameters += (start_ms,)
        rows = c.execute(
            """SELECT (source_ts_ms / ?) * ? bucket,source_ts_ms,
                      COALESCE(oi_usd,oi_currency,oi_contracts) value
               FROM oi_observations WHERE instrument=? AND state='confirmed'
               AND COALESCE(oi_usd,oi_currency,oi_contracts) IS NOT NULL
               """ + time_filter + """
               ORDER BY source_ts_ms,source_identity""", parameters).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(int(row["bucket"]), []).append(row)
        values = []
        for bucket, items in sorted(grouped.items()):
            series = [float(x["value"]) for x in items]
            times = [int(x["source_ts_ms"]) for x in items]
            change = series[-1] - series[0]
            pct = change / series[0] if series[0] else None
            values.append((instrument, resolution, bucket, series[0], series[-1], min(series),
                           max(series), change, pct, len(series), min(times), max(times),
                           self._gap_flag(times, width, 15_000), MICROSTRUCTURE_SOURCE_VERSION))
        c.executemany(
            """INSERT INTO oi_aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instrument,resolution,bucket_ms) DO UPDATE SET
               first_value=excluded.first_value,last_value=excluded.last_value,
               min_value=excluded.min_value,max_value=excluded.max_value,
               absolute_change=excluded.absolute_change,percentage_change=excluded.percentage_change,
               observation_count=excluded.observation_count,
               first_source_ts_ms=excluded.first_source_ts_ms,last_source_ts_ms=excluded.last_source_ts_ms,
               gap_flag=excluded.gap_flag,source_version=excluded.source_version""", values)
        return len(values)

    def _aggregate_basis(self, c: sqlite3.Connection, instrument: str,
                         resolution: str, width: int,
                         since_ms: int | None = None) -> int:
        index_instrument = instrument.removesuffix("-SWAP")
        start_ms = ((since_ms // width) * width
                    if since_ms is not None else None)
        mark_filter = " AND source_ts_ms>=?" if start_ms is not None else ""
        mark_parameters: tuple[Any, ...] = (instrument,)
        if start_ms is not None:
            mark_parameters += (start_ms,)
        marks = c.execute(
            """SELECT source_ts_ms,close FROM mark_price_observations
               WHERE instrument=? AND state='confirmed'""" + mark_filter + """
               ORDER BY source_ts_ms""", mark_parameters).fetchall()
        index_filter = ""
        index_parameters: tuple[Any, ...] = (index_instrument,)
        if start_ms is not None:
            # Keep one earlier index value for the causal as-of join.
            index_filter = """ AND source_ts_ms>=COALESCE(
                (SELECT MAX(source_ts_ms) FROM index_price_observations
                 WHERE instrument=? AND state='confirmed' AND source_ts_ms<=?), ?)"""
            index_parameters += (index_instrument, start_ms, start_ms)
        indices = c.execute(
            """SELECT source_ts_ms,close FROM index_price_observations
               WHERE instrument=? AND state='confirmed'""" + index_filter + """
               ORDER BY source_ts_ms""", index_parameters).fetchall()
        values_by_bucket: dict[int, list[tuple[int, float, float]]] = {}
        index_position = -1
        for mark in marks:
            # Strict causal as-of merge: latest confirmed index at or before mark.
            while (index_position + 1 < len(indices)
                   and int(indices[index_position + 1]["source_ts_ms"])
                   <= int(mark["source_ts_ms"])):
                index_position += 1
            if index_position < 0:
                continue
            index = indices[index_position]
            mark_value, index_value = float(mark["close"]), float(index["close"])
            basis = mark_value - index_value
            pct = basis / index_value if index_value else math.nan
            bucket = int(mark["source_ts_ms"]) // width * width
            values_by_bucket.setdefault(bucket, []).append(
                (int(mark["source_ts_ms"]), basis, pct))
        values = []
        for bucket, items in sorted(values_by_bucket.items()):
            bases, pcts = [x[1] for x in items], [x[2] for x in items]
            times = [x[0] for x in items]
            values.append((instrument, resolution, bucket, bases[0], bases[-1],
                           min(bases), max(bases), pcts[0], pcts[-1],
                           abs(bases[-1]) - abs(bases[0]), len(items), min(times), max(times),
                           self._gap_flag(times, width, 60_000), MICROSTRUCTURE_SOURCE_VERSION))
        c.executemany(
            """INSERT INTO basis_aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instrument,resolution,bucket_ms) DO UPDATE SET
               first_basis=excluded.first_basis,last_basis=excluded.last_basis,
               min_basis=excluded.min_basis,max_basis=excluded.max_basis,
               first_basis_pct=excluded.first_basis_pct,last_basis_pct=excluded.last_basis_pct,
               expansion=excluded.expansion,observation_count=excluded.observation_count,
               first_source_ts_ms=excluded.first_source_ts_ms,last_source_ts_ms=excluded.last_source_ts_ms,
               gap_flag=excluded.gap_flag,source_version=excluded.source_version""", values)
        return len(values)

    def prune_raw(self, timestamp_ms: int | None = None) -> dict[str, int]:
        cutoff = (timestamp_ms or now_ms()) - RAW_RETENTION_MS
        liquidation_cutoff = (timestamp_ms or now_ms()) - LIQUIDATION_RETENTION_MS
        self.aggregate_all()
        result: dict[str, int] = {}
        with self.connect() as c:
            for table in ("trade_flow_observations", "oi_observations",
                          "mark_price_observations", "index_price_observations"):
                before = self._counted_rows(c, table)
                c.execute(f"DELETE FROM {table} WHERE source_ts_ms<?", (cutoff,))
                result[table] = before - self._counted_rows(c, table)
            before = self._counted_rows(c, "liquidation_observations")
            c.execute("DELETE FROM liquidation_observations WHERE source_ts_ms<?",
                      (liquidation_cutoff,))
            result["liquidation_observations"] = (
                before - self._counted_rows(c, "liquidation_observations"))
        return result

    def coverage(self) -> dict[str, Any]:
        tables = {
            "trades": "trade_flow_observations", "oi": "oi_observations",
            "funding_settled": "funding_settled", "funding_predicted": "funding_predicted",
            "mark": "mark_price_observations", "index": "index_price_observations",
            "liquidations": "liquidation_observations",
        }
        result: dict[str, Any] = {}
        with self.connect(readonly=True) as c:
            for lane, table in tables.items():
                rows = c.execute(
                    f"""SELECT instrument,COUNT(*) rows,MIN(source_ts_ms) earliest_ms,
                               MAX(source_ts_ms) latest_ms FROM {table} GROUP BY instrument"""
                ).fetchall()
                result[lane] = [dict(row) for row in rows]
        return result

    def _health_coverage(self) -> dict[str, Any]:
        tables = {
            "trades": "trade_flow_observations", "oi": "oi_observations",
            "funding_settled": "funding_settled", "funding_predicted": "funding_predicted",
            "mark": "mark_price_observations", "index": "index_price_observations",
            "liquidations": "liquidation_observations",
        }
        result: dict[str, list[dict[str, Any]]] = {}
        with self.connect(readonly=True) as c:
            for lane, table in tables.items():
                instruments = ([item.removesuffix("-SWAP") for item in INSTRUMENTS]
                               if lane == "index" else list(INSTRUMENTS))
                result[lane] = []
                for instrument in instruments:
                    row = c.execute(
                        f"""SELECT MIN(source_ts_ms),MAX(source_ts_ms)
                            FROM {table} WHERE instrument=?""", (instrument,)).fetchone()
                    if row[1] is not None:
                        result[lane].append({
                            "instrument": instrument, "earliest_ms": int(row[0]),
                            "latest_ms": int(row[1])})
        return result

    def sample_status(self, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
        coverage = coverage or self.coverage()
        starts, ends = [], []
        for lane in ("trades", "oi", "mark", "index"):
            starts.extend(row["earliest_ms"] for row in coverage[lane] if row["earliest_ms"])
            ends.extend(row["latest_ms"] for row in coverage[lane] if row["latest_ms"])
        # Eligibility uses the intersection of required source coverage, not the
        # union.  An older funding or mark row cannot make a missing OI/trade day
        # appear covered.
        start = max(starts) if starts else None
        end = min(ends) if ends else None
        with self.connect(readonly=True) as c:
            latest_gap_end = c.execute(
                """SELECT MAX(end_ms) FROM collection_gaps
                   WHERE resolved_at_ms IS NULL
                   AND lane IN ('trades','oi','mark','index')"""
            ).fetchone()[0]
        if start is not None and latest_gap_end is not None:
            start = max(start, int(latest_gap_end))
        sample_days = max(0.0, ((end - start) / 86_400_000) if start and end else 0.0)
        if sample_days < 14:
            status = "EXPLORATORY_ONLY"
        elif sample_days < 30:
            status = "MINIMUM_SAMPLE_REACHED"
        elif sample_days < 60:
            status = "VALIDATION_READY"
        else:
            status = "FORMAL_RESEARCH_READY"
        next_date = (datetime.fromtimestamp(start / 1000, timezone.utc)
                     + timedelta(days=MINIMUM_SAMPLE_DAYS)).date().isoformat() if start else None
        return {"collection_start_ms": start, "latest_ms": end,
                "sample_days": round(sample_days, 6), "sample_status": status,
                "next_minimum_sample_date": next_date,
                "formal_claims_permitted": False,
                "block_reason": "multiple regimes and 60-90 uninterrupted days are still required"}

    def per_feature_eligibility(self) -> dict[str, Any]:
        coverage = self.coverage()
        feature_groups = {
            "settled_funding": {
                "features": ["funding_level", "funding_change", "funding_zscore"],
                "required_sources": ["funding_settled"]
            },
            "predicted_funding": {
                "features": ["funding_predicted"],
                "required_sources": ["funding_predicted"]
            },
            "basis": {
                "features": ["basis_level", "basis_zscore", "basis_expansion"],
                "required_sources": ["mark", "index"]
            },
            "cvd": {
                "features": ["cvd_delta", "cvd_rolling", "cvd_slope", "cvd_zscore", "cvd_imbalance", "cvd_volume_normalized", "price_cvd_divergence"],
                "required_sources": ["trades"]
            },
            "oi": {
                "features": ["oi_absolute_change", "oi_percentage_change", "oi_zscore", "oi_acceleration"],
                "required_sources": ["oi"]
            },
            "cvd_oi_interactions": {
                "features": ["breakout_with_cvd", "breakout_without_cvd", "price_up_oi_expansion", "price_down_oi_expansion"],
                "required_sources": ["trades", "oi"]
            },
            "funding_oi_interactions": {
                "features": ["extreme_funding_oi_expansion"],
                "required_sources": ["funding_settled", "oi"]
            },
            "liquidation": {
                "features": ["liquidation"],
                "required_sources": ["liquidations"]
            }
        }
        
        with self.connect(readonly=True) as c:
            gaps = [dict(row) for row in c.execute(
                "SELECT lane, instrument, start_ms, end_ms FROM collection_gaps WHERE resolved_at_ms IS NULL"
            ).fetchall()]
            
        gap_dict = {}
        for gap in gaps:
            gap_dict.setdefault(gap["lane"], []).append(gap)
            
        results = {"feature_groups": {}}
        for group_name, group_info in feature_groups.items():
            req_sources = group_info["required_sources"]
            instruments_data = {}
            instrument_bounds = {}
            
            for source in req_sources:
                if source not in coverage:
                    continue
                for item in coverage[source]:
                    inst = item["instrument"]
                    normalized_inst = inst if inst.endswith("-SWAP") else inst + "-SWAP"
                    if normalized_inst not in instrument_bounds:
                        instrument_bounds[normalized_inst] = {"starts": [], "ends": []}
                    if item.get("earliest_ms"):
                        instrument_bounds[normalized_inst]["starts"].append(item["earliest_ms"])
                    if item.get("latest_ms"):
                        instrument_bounds[normalized_inst]["ends"].append(item["latest_ms"])
                        
            group_earliest = None
            group_latest = None
            total_gaps_ms = 0
            
            for inst, bounds in instrument_bounds.items():
                if len(bounds["starts"]) == len(req_sources) and len(bounds["ends"]) == len(req_sources):
                    start = max(bounds["starts"])
                    end = min(bounds["ends"])
                    if start < end:
                        instruments_data[inst] = {"earliest_usable_ms": start, "latest_usable_ms": end}
                        if group_earliest is None or start < group_earliest:
                            group_earliest = start
                        if group_latest is None or end > group_latest:
                            group_latest = end
                            
            # Calculate gap deductions (simplified union of gaps for the required sources)
            gap_intervals = []
            for source in req_sources:
                for gap in gap_dict.get(source, []):
                    if group_earliest and group_latest:
                        gap_start = max(gap["start_ms"], group_earliest)
                        gap_end = min(gap["end_ms"], group_latest)
                        if gap_start < gap_end:
                            gap_intervals.append((gap_start, gap_end))
            
            # merge gap intervals
            if gap_intervals:
                gap_intervals.sort(key=lambda x: x[0])
                merged = [gap_intervals[0]]
                for current in gap_intervals[1:]:
                    last = merged[-1]
                    if current[0] <= last[1]:
                        merged[-1] = (last[0], max(last[1], current[1]))
                    else:
                        merged.append(current)
                for m in merged:
                    total_gaps_ms += (m[1] - m[0])
                    
            if group_earliest and group_latest:
                raw_days = (group_latest - group_earliest) / 86400000.0
                gap_days = total_gaps_ms / 86400000.0
                usable_days = max(0.0, raw_days - gap_days)
            else:
                raw_days = 0.0
                gap_days = 0.0
                usable_days = 0.0
                
            if usable_days < 14:
                status = "EXPLORATORY_ONLY"
                blocking_reason = "Insufficient historical sample size for feature stability validation"
            elif usable_days < 30:
                status = "MINIMUM_SAMPLE_REACHED"
                blocking_reason = "Requires 30 days for formal regime validation"
            elif usable_days < 60:
                status = "VALIDATION_READY"
                blocking_reason = "Requires 60 days for formal research readiness"
            else:
                status = "FORMAL_RESEARCH_READY"
                blocking_reason = None
                
            next_date = (datetime.fromtimestamp(group_earliest / 1000, timezone.utc) + timedelta(days=MINIMUM_SAMPLE_DAYS)).date().isoformat() if group_earliest else None
            
            results["feature_groups"][group_name] = {
                "features": group_info["features"],
                "required_sources": req_sources,
                "instruments": instruments_data,
                "earliest_usable_ms": group_earliest,
                "latest_usable_ms": group_latest,
                "usable_days": round(raw_days, 6),
                "gap_adjusted_sample_days": round(usable_days, 6),
                "event_count": 0,
                "status": status,
                "next_eligibility_date": next_date,
                "blocking_reason": blocking_reason
            }
            
        return results

    def health(self) -> dict[str, Any]:
        if not self.path.exists():
            self.initialize()
        coverage = self._health_coverage()
        with self.connect(readonly=True) as c:
            health = [dict(row) for row in c.execute(
                "SELECT * FROM collector_health ORDER BY component")]
            gap_count = int(c.execute(
                "SELECT COUNT(*) FROM collection_gaps WHERE resolved_at_ms IS NULL").fetchone()[0])
            counts = {row["table_name"]: int(row["row_count"]) for row in c.execute(
                "SELECT table_name,row_count FROM table_row_counts")}
            raw_rows = sum(counts.get(table, 0) for table in (
                "trade_flow_observations", "oi_observations",
                "mark_price_observations", "index_price_observations",
                "funding_settled", "funding_predicted", "liquidation_observations"))
            aggregate_rows = sum(counts.get(table, 0) for table in (
                "cvd_aggregates", "oi_aggregates", "basis_aggregates"))
            aggregation = c.execute(
                "SELECT last_success_ms FROM collector_health WHERE component='aggregation'"
            ).fetchone()
        latest = {lane: {row["instrument"]: row["latest_ms"] for row in rows}
                  for lane, rows in coverage.items()}
        sample = self.sample_status(coverage)
        return {
            "service_status": "RUNNING" if any(x["status"] == "LIVE" for x in health) else "INITIALIZED",
            "database_schema_version": MICROSTRUCTURE_SCHEMA_VERSION,
            "source_version": MICROSTRUCTURE_SOURCE_VERSION,
            "feature_version": MICROSTRUCTURE_FEATURE_VERSION,
            "latest_timestamp_per_source_instrument": latest,
            "source_lag_ms": {x["component"]: x["source_lag_ms"] for x in health},
            "gap_count": gap_count,
            "reconnect_count": sum(x["reconnect_count"] for x in health),
            "failed_request_count": sum(x["failed_request_count"] for x in health),
            "retry_count": sum(x["retry_count"] for x in health),
            "raw_rows": raw_rows, "aggregate_rows": aggregate_rows,
            "database_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "last_aggregation_timestamp_ms": aggregation[0] if aggregation else None,
            **sample,
            "per_feature_eligibility": self.per_feature_eligibility(),
        }


class MicrostructureMigration:
    """Copies only recognized genuine observations; source DB is read-only."""

    def __init__(self, destination: MicrostructureStore) -> None:
        self.destination = destination

    def migrate(self, source_path: Path | str, batch_size: int = 10_000) -> dict[str, int]:
        self.destination.initialize()
        source = sqlite3.connect(f"file:{Path(source_path)}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        result = {"trade_flow_observations": 0, "oi_observations": 0,
                  "cvd_aggregates": 0, "oi_aggregates": 0}
        try:
            tables = {row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "flow_trade_buckets" in tables:
                cursor = source.execute(
                    """SELECT instrument,ts,buy_notional,sell_notional,trade_count
                       FROM flow_trade_buckets ORDER BY instrument,ts""")
                while batch := cursor.fetchmany(batch_size):
                    values = []
                    for row in batch:
                        instrument = self._swap(row["instrument"])
                        timestamp = int(row["ts"]) * 1000
                        for side, amount in (("buy", row["buy_notional"]),
                                             ("sell", row["sell_notional"])):
                            if float(amount or 0) <= 0:
                                continue
                            source_id = f"flow_trade_buckets:{row['instrument']}:{row['ts']}:{side}"
                            key = identity("migrated_trade_bucket", source_id)
                            base = self.destination.observation_base(
                                "migrated genuine OKX trades", instrument, timestamp, "1s",
                                "confirmed", source_id, key)
                            values.append((*base, None, side, 1.0, float(amount), 1.0,
                                           float(amount), "flow_trade_buckets"))
                    with self.destination.connect() as c:
                        before = self.destination._counted_rows(
                            c, "trade_flow_observations")
                        c.executemany(
                            """INSERT OR IGNORE INTO trade_flow_observations VALUES(
                               ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                        result["trade_flow_observations"] += (
                            self.destination._counted_rows(
                                c, "trade_flow_observations") - before)
            if "oi_snapshots" in tables:
                cursor = source.execute(
                    "SELECT instrument,ts,oi,source FROM oi_snapshots ORDER BY instrument,ts")
                while batch := cursor.fetchmany(batch_size):
                    values = []
                    for row in batch:
                        instrument = self._swap(row["instrument"])
                        timestamp = int(row["ts"]) * 1000
                        source_id = f"oi_snapshots:{row['instrument']}:{row['ts']}"
                        base = self.destination.observation_base(
                            f"migrated {row['source']}", instrument, timestamp, "snapshot",
                            "confirmed", source_id,
                            identity("oi", f"migrated {row['source']}", instrument, source_id))
                        values.append((*base, None, None, float(row["oi"]), "oi_snapshots"))
                    with self.destination.connect() as c:
                        before = self.destination._counted_rows(c, "oi_observations")
                        c.executemany(
                            "INSERT OR IGNORE INTO oi_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            values)
                        result["oi_observations"] += (
                            self.destination._counted_rows(c, "oi_observations") - before)
            # Genuine legacy OI before native snapshots; no CVD snapshot migration.
            if "flow_snapshots" in tables:
                columns = {row[1] for row in source.execute("PRAGMA table_info(flow_snapshots)")}
                if {"instrument", "created_at", "oi"} <= columns:
                    query = """SELECT f.instrument,f.created_at,f.oi
                               FROM flow_snapshots f WHERE f.instrument IS NOT NULL
                               AND f.oi>0 AND unixepoch(f.created_at) <
                               COALESCE((SELECT MIN(o.ts) FROM oi_snapshots o
                                         WHERE o.instrument=f.instrument),9223372036854775807)
                               ORDER BY f.instrument,f.created_at"""
                    cursor = source.execute(query)
                    while batch := cursor.fetchmany(batch_size):
                        values = []
                        for row in batch:
                            timestamp = int(datetime.fromisoformat(
                                row["created_at"]).timestamp() * 1000)
                            instrument = self._swap(row["instrument"])
                            source_id = f"flow_snapshots:{row['instrument']}:{row['created_at']}"
                            source_name = "migrated genuine legacy OKX OI"
                            base = self.destination.observation_base(
                                source_name, instrument, timestamp, "snapshot", "confirmed",
                                source_id, identity("oi", source_name, instrument, source_id))
                            values.append((*base, None, None, float(row["oi"]), "flow_snapshots"))
                        with self.destination.connect() as c:
                            before = self.destination._counted_rows(c, "oi_observations")
                            c.executemany(
                                "INSERT OR IGNORE INTO oi_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                values)
                            result["oi_observations"] += (
                                self.destination._counted_rows(c, "oi_observations") - before)
            if "flow_history_aggregates" in tables:
                self._migrate_legacy_aggregates(source, result)
            self.destination.aggregate_all()
            self.destination.checkpoint("migration", "paper_trades.db", cursor=None,
                                        last_source_ts_ms=None, status="complete",
                                        metadata={"rows_written": result})
            return result
        finally:
            source.close()

    @staticmethod
    def _swap(instrument: str) -> str:
        return instrument if instrument.endswith("-SWAP") else f"{instrument}-SWAP"

    def _migrate_legacy_aggregates(
        self, source: sqlite3.Connection, result: dict[str, int]
    ) -> None:
        resolution_names = {300: "5m", 3600: "1H", 14400: "4H", 86400: "1D"}
        with self.destination.connect() as destination:
            for row in source.execute(
                "SELECT * FROM flow_history_aggregates ORDER BY instrument,series,bucket_ts"):
                resolution = resolution_names.get(int(row["resolution_seconds"]))
                if not resolution:
                    continue
                instrument = self._swap(row["instrument"])
                if row["series"] == "cvd" and row["delta"] is not None:
                    # Preserve durable legacy aggregate with explicit provenance.
                    before = self.destination._counted_rows(destination, "cvd_aggregates")
                    delta = float(row["delta"])
                    destination.execute(
                        """INSERT OR IGNORE INTO cvd_aggregates VALUES(
                           ?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (instrument, resolution, int(row["bucket_ts"]) * 1000,
                         max(delta, 0), max(-delta, 0), delta, delta,
                         int(row["observation_count"]), int(row["first_ts"]) * 1000,
                         int(row["last_ts"]) * 1000, 0, MICROSTRUCTURE_SOURCE_VERSION))
                    result["cvd_aggregates"] += (
                        self.destination._counted_rows(destination, "cvd_aggregates") - before)
                elif row["series"] == "oi" and row["value_last"] is not None:
                    before = self.destination._counted_rows(destination, "oi_aggregates")
                    first = float(row["value_last"])
                    destination.execute(
                        """INSERT OR IGNORE INTO oi_aggregates VALUES(
                           ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (instrument, resolution, int(row["bucket_ts"]) * 1000,
                         first, first, float(row["value_min"]), float(row["value_max"]),
                         0.0, 0.0, int(row["observation_count"]),
                         int(row["first_ts"]) * 1000, int(row["last_ts"]) * 1000,
                         0, MICROSTRUCTURE_SOURCE_VERSION))
                    result["oi_aggregates"] += (
                        self.destination._counted_rows(destination, "oi_aggregates") - before)


def _zscore(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    deviation = statistics.pstdev(values)
    return (values[-1] - statistics.mean(values)) / deviation if deviation else 0.0


class FeatureEngine:
    """Causal feature snapshots using confirmed as-of data only."""

    def __init__(self, store: MicrostructureStore) -> None:
        self.store = store

    def generate(self, resolution: str = "15m") -> int:
        self.store.aggregate_all()
        written = 0
        with self.store.connect() as c:
            instruments = [row[0] for row in c.execute(
                "SELECT DISTINCT instrument FROM cvd_aggregates WHERE resolution=?",
                (resolution,))]
            for instrument in instruments:
                times = [row[0] for row in c.execute(
                    """SELECT bucket_ms FROM cvd_aggregates WHERE instrument=? AND resolution=?
                       INTERSECT SELECT bucket_ms FROM oi_aggregates
                       WHERE instrument=? AND resolution=? ORDER BY bucket_ms""",
                    (instrument, resolution, instrument, resolution))]
                for timestamp in times:
                    decision_timestamp = int(timestamp) + RESOLUTIONS[resolution]
                    features = self._at(c, instrument, decision_timestamp, resolution)
                    for name, (value, sources) in features.items():
                        key = identity(MICROSTRUCTURE_FEATURE_VERSION, instrument,
                                       decision_timestamp, resolution, name)
                        before = c.total_changes
                        c.execute(
                            """INSERT OR REPLACE INTO feature_snapshots VALUES(
                               ?,?,?,?,?,?,?,?,?)""",
                            (instrument, decision_timestamp, resolution, MICROSTRUCTURE_FEATURE_VERSION,
                             name, value, json.dumps(sources, sort_keys=True), "EXPLORATORY_ONLY", key))
                        written += int(c.total_changes > before)
        return written

    def _at(self, c: sqlite3.Connection, instrument: str, timestamp: int,
            resolution: str) -> dict[str, tuple[float | None, dict[str, int]]]:
        cvd = c.execute(
            """SELECT * FROM cvd_aggregates WHERE instrument=? AND resolution=?
               AND last_source_ts_ms<=? ORDER BY bucket_ms DESC LIMIT 20""",
            (instrument, resolution, timestamp)).fetchall()[::-1]
        oi = c.execute(
            """SELECT * FROM oi_aggregates WHERE instrument=? AND resolution=?
               AND last_source_ts_ms<=? ORDER BY bucket_ms DESC LIMIT 20""",
            (instrument, resolution, timestamp)).fetchall()[::-1]
        funding = c.execute(
            """SELECT funding_rate,source_ts_ms FROM funding_settled
               WHERE instrument=? AND source_ts_ms<=? ORDER BY source_ts_ms DESC LIMIT 20""",
            (instrument, timestamp)).fetchall()[::-1]
        basis = c.execute(
            """SELECT * FROM basis_aggregates WHERE instrument=? AND resolution=?
               AND last_source_ts_ms<=? ORDER BY bucket_ms DESC LIMIT 20""",
            (instrument, resolution, timestamp)).fetchall()[::-1]
        prices = c.execute(
            """SELECT source_ts_ms,close FROM mark_price_observations
               WHERE instrument=? AND state='confirmed' AND source_ts_ms<=?
               ORDER BY source_ts_ms DESC LIMIT 20""",
            (instrument, timestamp)).fetchall()[::-1]
        cvd_values = [float(x["delta"]) for x in cvd]
        oi_changes = [float(x["percentage_change"] or 0) for x in oi]
        funding_values = [float(x["funding_rate"]) for x in funding]
        basis_values = [float(x["last_basis_pct"]) for x in basis]
        sources = {
            "cvd": int(cvd[-1]["last_source_ts_ms"]) if cvd else 0,
            "oi": int(oi[-1]["last_source_ts_ms"]) if oi else 0,
            "funding": int(funding[-1]["source_ts_ms"]) if funding else 0,
            "basis": int(basis[-1]["last_source_ts_ms"]) if basis else 0,
            "price": int(prices[-1]["source_ts_ms"]) if prices else 0,
        }
        if any(value > timestamp for value in sources.values()):
            raise AssertionError("future source observation in causal feature")
        buy = float(cvd[-1]["buy_notional"]) if cvd else 0.0
        sell = float(cvd[-1]["sell_notional"]) if cvd else 0.0
        delta = cvd_values[-1] if cvd_values else None
        oi_change = float(oi[-1]["absolute_change"]) if oi else None
        oi_pct = oi_changes[-1] if oi_changes else None
        price_values = [float(row["close"]) for row in prices]
        price_change = ((price_values[-1] / price_values[-2] - 1)
                        if len(price_values) >= 2 and price_values[-2] else None)
        breakout = (price_values[-1] >= max(price_values[:-1])
                    if len(price_values) >= 5 else None)
        imbalance = ((buy - sell) / (buy + sell) if buy + sell else None)
        volume_values = [float(row["buy_notional"]) + float(row["sell_notional"])
                         for row in cvd]
        volume_z = _zscore(volume_values)
        return {
            "cvd_interval_delta": (delta, sources),
            "cvd_rolling_delta": (sum(cvd_values[-4:]) if cvd_values else None, sources),
            "cvd_slope": ((cvd_values[-1] - cvd_values[-4]) / 3
                           if len(cvd_values) >= 4 else None, sources),
            "cvd_zscore": (_zscore(cvd_values), sources),
            "cvd_buy_sell_imbalance": ((buy - sell) / (buy + sell)
                                       if buy + sell else None, sources),
            "cvd_volume_normalized_delta": (delta / (buy + sell)
                                            if delta is not None and buy + sell else None, sources),
            "price_cvd_divergence": (
                float((price_change or 0) * (delta or 0) < 0)
                if price_change is not None and delta is not None else None, sources),
            "oi_absolute_change": (oi_change, sources),
            "oi_percentage_change": (oi_pct, sources),
            "oi_zscore": (_zscore(oi_changes), sources),
            "oi_acceleration": (oi_changes[-1] - oi_changes[-2]
                                if len(oi_changes) >= 2 else None, sources),
            "funding_level": (funding_values[-1] if funding_values else None, sources),
            "funding_zscore": (_zscore(funding_values), sources),
            "funding_change": (funding_values[-1] - funding_values[-2]
                               if len(funding_values) >= 2 else None, sources),
            "basis_level": (basis_values[-1] if basis_values else None, sources),
            "basis_zscore": (_zscore(basis_values), sources),
            "basis_expansion_contraction": (
                float(basis[-1]["expansion"]) if basis else None, sources),
            # Price-dependent interactions remain unavailable until a confirmed
            # decision-price registry is present.  They are never guessed from flow.
            "breakout_with_cvd_confirmation": (
                float(bool(breakout) and (delta or 0) > 0)
                if breakout is not None and delta is not None else None, sources),
            "breakout_without_cvd_confirmation": (
                float(bool(breakout) and (delta or 0) <= 0)
                if breakout is not None and delta is not None else None, sources),
            "price_up_oi_expansion": (
                float(price_change > 0 and (oi_pct or 0) > 0)
                if price_change is not None and oi_pct is not None else None, sources),
            "price_up_oi_contraction": (
                float(price_change > 0 and (oi_pct or 0) < 0)
                if price_change is not None and oi_pct is not None else None, sources),
            "price_down_oi_expansion": (
                float(price_change < 0 and (oi_pct or 0) > 0)
                if price_change is not None and oi_pct is not None else None, sources),
            "price_down_oi_contraction": (
                float(price_change < 0 and (oi_pct or 0) < 0)
                if price_change is not None and oi_pct is not None else None, sources),
            "extreme_funding_oi_expansion": (
                float(abs(_zscore(funding_values) or 0) >= 2 and (oi_pct or 0) > 0),
                sources) if funding_values and oi_pct is not None else (None, sources),
            "volume_shock_cvd_imbalance": (
                (volume_z or 0) * (imbalance or 0)
                if volume_z is not None and imbalance is not None else None, sources),
            "price_oi_quadrant": (
                float((1 if price_change >= 0 else -1) * (1 if oi_pct >= 0 else 2))
                if price_change is not None and oi_pct is not None else None, sources),
        }


HORIZONS = {"15m": 900_000, "30m": 1_800_000, "1H": 3_600_000,
            "2H": 7_200_000, "4H": 14_400_000, "8H": 28_800_000,
            "24H": 86_400_000}


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean, right_mean = statistics.mean(left), statistics.mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(sum((x - left_mean) ** 2 for x in left)
                            * sum((y - right_mean) ** 2 for y in right))
    return numerator / denominator if denominator else None


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        rank = (index + end - 1) / 2 + 1
        for _, original in ordered[index:end]:
            ranks[original] = rank
        index = end
    return ranks


class EventStudyEngine:
    """Feature-level descriptive event studies; never creates strategies."""

    def __init__(self, store: MicrostructureStore) -> None:
        self.store = store

    def run(self) -> dict[str, Any]:
        report_id = identity("event-study", MICROSTRUCTURE_FEATURE_VERSION, now_ms())
        output: dict[str, Any] = {"report_id": report_id, "results": [],
                                  "feature_correlations": {}, "redundancy": []}
        with self.store.connect() as c:
            features = [row[0] for row in c.execute(
                """SELECT DISTINCT feature_name FROM feature_snapshots
                   WHERE feature_value IS NOT NULL ORDER BY feature_name""")]
            for feature in features:
                for horizon, milliseconds in HORIZONS.items():
                    result = self._study(c, feature, horizon, milliseconds)
                    c.execute(
                        """INSERT OR REPLACE INTO event_study_results VALUES(?,?,?,?,?,?)""",
                        (report_id, feature, horizon, json.dumps(result, sort_keys=True),
                         result["event_count"], now_ms()))
                    output["results"].append(result)
            output["feature_correlations"], output["redundancy"] = self._correlations(c)
        return output

    def _study(self, c: sqlite3.Connection, feature: str, horizon: str,
               milliseconds: int) -> dict[str, Any]:
        rows = c.execute(
            """SELECT instrument,decision_ts_ms,feature_value FROM feature_snapshots
               WHERE feature_name=? AND feature_value IS NOT NULL
               ORDER BY decision_ts_ms,instrument""", (feature,)).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            start = c.execute(
                """SELECT source_ts_ms,close FROM mark_price_observations
                   WHERE instrument=? AND state='confirmed' AND source_ts_ms<=?
                   ORDER BY source_ts_ms DESC LIMIT 1""",
                (row["instrument"], row["decision_ts_ms"])).fetchone()
            end = c.execute(
                """SELECT source_ts_ms,close FROM mark_price_observations
                   WHERE instrument=? AND state='confirmed' AND source_ts_ms>=?
                   ORDER BY source_ts_ms LIMIT 1""",
                (row["instrument"], int(row["decision_ts_ms"]) + milliseconds)).fetchone()
            if not start or not end or int(end["source_ts_ms"]) <= int(row["decision_ts_ms"]):
                continue
            path = [float(x[0]) for x in c.execute(
                """SELECT close FROM mark_price_observations WHERE instrument=?
                   AND state='confirmed' AND source_ts_ms>? AND source_ts_ms<=?
                   ORDER BY source_ts_ms""",
                (row["instrument"], row["decision_ts_ms"], end["source_ts_ms"]))]
            start_price, end_price = float(start["close"]), float(end["close"])
            raw_return = end_price / start_price - 1 if start_price else math.nan
            direction = 1 if float(row["feature_value"]) >= 0 else -1
            path_returns = [price / start_price - 1 for price in path] if start_price else []
            events.append({
                "instrument": row["instrument"], "decision_ts_ms": int(row["decision_ts_ms"]),
                "label_ts_ms": int(end["source_ts_ms"]), "feature": float(row["feature_value"]),
                "raw_return": raw_return, "adjusted_return": direction * raw_return,
                "mfe": max([direction * value for value in path_returns], default=raw_return),
                "mae": min([direction * value for value in path_returns], default=raw_return),
            })
        returns = [event["raw_return"] for event in events]
        adjusted = [event["adjusted_return"] for event in events]
        feature_values = [event["feature"] for event in events]
        instruments: dict[str, int] = {}
        months: dict[str, int] = {}
        for event in events:
            instruments[event["instrument"]] = instruments.get(event["instrument"], 0) + 1
            month = datetime.fromtimestamp(event["decision_ts_ms"] / 1000,
                                           timezone.utc).strftime("%Y-%m")
            months[month] = months.get(month, 0) + 1
        quantiles: list[float | None] = []
        if events:
            ordered = sorted(events, key=lambda event: event["feature"])
            for group in range(5):
                selected = ordered[group * len(ordered) // 5:(group + 1) * len(ordered) // 5]
                quantiles.append(statistics.mean(x["raw_return"] for x in selected)
                                 if selected else None)
        monotonicity = None
        non_null_quantiles = [value for value in quantiles if value is not None]
        if len(non_null_quantiles) >= 3:
            monotonicity = _pearson(list(range(len(non_null_quantiles))),
                                    non_null_quantiles)
        turnover = (statistics.mean(abs(b - a) for a, b in zip(feature_values, feature_values[1:]))
                    if len(feature_values) >= 2 else None)
        return {
            "feature_name": feature, "horizon": horizon, "event_count": len(events),
            "raw_forward_return_mean": statistics.mean(returns) if returns else None,
            "raw_forward_return_median": statistics.median(returns) if returns else None,
            "direction_adjusted_return_mean": statistics.mean(adjusted) if adjusted else None,
            "direction_adjusted_return_median": statistics.median(adjusted) if adjusted else None,
            "profitable_event_ratio": (sum(value > 0 for value in adjusted) / len(adjusted)
                                       if adjusted else None),
            "mfe_mean": statistics.mean(x["mfe"] for x in events) if events else None,
            "mae_mean": statistics.mean(x["mae"] for x in events) if events else None,
            "instrument_distribution": instruments, "time_distribution": months,
            "regime_distribution": {"UNCLASSIFIED": len(events)},
            "concentration": (max(instruments.values()) / len(events)
                              if events and instruments else None),
            "rank_ic": _pearson(_ranks(feature_values), _ranks(returns)) if events else None,
            "pearson_correlation": _pearson(feature_values, returns),
            "spearman_correlation": (
                _pearson(_ranks(feature_values), _ranks(returns)) if events else None),
            "quantile_returns": quantiles, "monotonicity": monotonicity,
            "feature_turnover": turnover,
            "label_policy": "strictly after decision timestamp",
            "exploratory_only": True,
        }

    @staticmethod
    def _correlations(c: sqlite3.Connection) -> tuple[dict[str, float | None], list[list[str]]]:
        rows = c.execute(
            """SELECT instrument,decision_ts_ms,feature_name,feature_value
               FROM feature_snapshots WHERE feature_value IS NOT NULL
               ORDER BY instrument,decision_ts_ms,feature_name""").fetchall()
        matrix: dict[tuple[str, int], dict[str, float]] = {}
        names: set[str] = set()
        for row in rows:
            matrix.setdefault((row["instrument"], int(row["decision_ts_ms"])), {})[
                row["feature_name"]] = float(row["feature_value"])
            names.add(row["feature_name"])
        correlations: dict[str, float | None] = {}
        redundant: list[list[str]] = []
        ordered = sorted(names)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                pairs = [(values[left], values[right]) for values in matrix.values()
                         if left in values and right in values]
                value = _pearson([x[0] for x in pairs], [x[1] for x in pairs])
                correlations[f"{left}|{right}"] = value
                if value is not None and abs(value) >= 0.9:
                    redundant.append([left, right])
        return correlations, redundant


def forward_validation_manifest(store: MicrostructureStore) -> dict[str, Any]:
    sample = store.sample_status()
    start = sample["collection_start_ms"]
    payload = {
        "protocol": "chronological-forward-only",
        "research_epoch": identity(MICROSTRUCTURE_SCHEMA_VERSION,
                                   MICROSTRUCTURE_SOURCE_VERSION,
                                   MICROSTRUCTURE_FEATURE_VERSION),
        "activation": {
            "initial_research": {"minimum_complete_days": 30, "status": "PENDING"},
            "later_validation": {"minimum_complete_days": 60, "status": "PENDING"},
            "untouched_final_forward_oot": {"minimum_complete_days": 90, "status": "PENDING"},
        },
        "rules": [
            "windows activate only after actual uninterrupted coverage exists",
            "missing days delay every later activation",
            "feature selection and tuning may not inspect final forward OOT",
            "a sample belongs to exactly one chronological window",
            "schema, source, or feature version changes start a new research epoch",
            "current short sample cannot enter discovery, ranking, promotion, robustness, or ablation",
        ],
        "collection_start_ms": start,
        "created_at_ms": now_ms(),
    }
    manifest_id = identity("forward_validation", payload["research_epoch"])
    with store.connect() as c:
        c.execute(
            """INSERT OR REPLACE INTO research_manifests VALUES(?,?,?,?,?,?)""",
            (manifest_id, "forward_validation", "forward-validation-v1", "PENDING",
             json.dumps(payload, sort_keys=True), now_ms()))
    return payload


def exploratory_report(store: MicrostructureStore) -> dict[str, Any]:
    """Descriptive report only.  No candidate selection, ranking, or OOT access."""
    store.initialize()
    features = FeatureEngine(store).generate()
    coverage = store.coverage()
    sample = store.sample_status()
    event_studies = EventStudyEngine(store).run()
    with store.connect(readonly=True) as c:
        gap_count = c.execute(
            "SELECT COUNT(*) FROM collection_gaps WHERE resolved_at_ms IS NULL").fetchone()[0]
        feature_rows = c.execute(
            """SELECT feature_name,COUNT(*) count,AVG(feature_value) mean,
                      MIN(feature_value) min,MAX(feature_value) max
               FROM feature_snapshots GROUP BY feature_name ORDER BY feature_name""").fetchall()
        migrated = c.execute(
            """SELECT provenance_table,COUNT(*) rows,MIN(source_ts_ms) earliest_ms,
                      MAX(source_ts_ms) latest_ms FROM (
               SELECT provenance_table,source_ts_ms FROM trade_flow_observations
               WHERE provenance_table IS NOT NULL UNION ALL
               SELECT provenance_table,source_ts_ms FROM oi_observations
               WHERE provenance_table IS NOT NULL) GROUP BY provenance_table""").fetchall()
        health = [dict(row) for row in c.execute(
            "SELECT * FROM collector_health ORDER BY component")]
    report = {
        "title": "EXPLORATORY_ONLY — INSUFFICIENT SAMPLE",
        "report_version": MICROSTRUCTURE_REPORT_VERSION,
        "coverage": coverage,
        "migrated_coverage": [dict(row) for row in migrated],
        "backfilled_coverage": [row for lane in coverage.values() for row in lane
                                if lane in (coverage["funding_settled"],
                                            coverage["mark"], coverage["index"])],
        "gap_count": int(gap_count),
        "feature_rows_written": features,
        "feature_availability": [dict(row) for row in feature_rows],
        "event_counts": {
            f"{row['feature_name']}:{row['horizon']}": row["event_count"]
            for row in event_studies["results"]},
        "preliminary_descriptive_statistics": [dict(row) for row in feature_rows],
        "feature_correlations": event_studies["feature_correlations"],
        "redundancy": event_studies["redundancy"],
        "collector_health": health,
        "data_defects": ["short sample", "official liquidation feed is incomplete"],
        "unavailable_features": [
            "historical instrument-level OI before genuine collection",
            "historical liquidations", "price-dependent interactions without registered decision prices",
        ],
        **sample,
        "automatic_strategy_discovery": "BLOCKED",
        "formal_ranking": "BLOCKED",
        "holdout_oot_accessed": False,
        "claims": "descriptive diagnostics only",
    }
    report_id = identity(MICROSTRUCTURE_REPORT_VERSION, now_ms())
    with store.connect() as c:
        c.execute(
            "INSERT INTO research_manifests VALUES(?,?,?,?,?,?)",
            (report_id, "exploratory_report", MICROSTRUCTURE_REPORT_VERSION,
             "EXPLORATORY_ONLY", json.dumps(report, sort_keys=True), now_ms()))
    return report
