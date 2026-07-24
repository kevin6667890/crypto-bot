"""Durable, range-aware CVD and open-interest history.

Only persisted observations are used:

* CVD aggregates store the sum of observed buy minus sell notional.  Returned
  values are a cumulative sum anchored at the earliest retained observation.
* OI aggregates store the last confirmed observation in each bucket, plus the
  observed minimum and maximum.

No missing bucket is inserted and no missing value is converted to zero.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


LOGGER = logging.getLogger(__name__)
HISTORY_API_VERSION = "flow-history-v1"
RETENTION_POLICY_VERSION = "flow-retention-v2"
RAW_RETENTION_SECONDS = 90 * 86400
AGGREGATE_RESOLUTIONS = (300, 3600, 14400, 86400)
DEFAULT_MAX_POINTS = 1200
MAX_POINT_BUDGET = 5000
MIGRATION_BATCH_SECONDS = 7 * 86400
STALE_AFTER_SECONDS = 90


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolution_name(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}D"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}H"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _encode_cursor(instrument: str, series: str, before_ts: int) -> str:
    payload = json.dumps(
        {"v": 1, "instrument": instrument, "series": series, "before": before_ts},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, instrument: str, series: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            payload.get("v") != 1
            or payload.get("instrument") != instrument
            or payload.get("series") != series
        ):
            raise ValueError
        return int(payload["before"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("Invalid history cursor") from error


class FlowHistoryStore:
    """Owns aggregate schema, resumable backfill, and history range queries."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self) -> Any:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flow_history_aggregates (
                    instrument TEXT NOT NULL,
                    series TEXT NOT NULL CHECK(series IN ('cvd','oi')),
                    resolution_seconds INTEGER NOT NULL,
                    bucket_ts INTEGER NOT NULL,
                    delta REAL,
                    value_last REAL,
                    value_min REAL,
                    value_max REAL,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    first_ts INTEGER NOT NULL,
                    last_ts INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY(instrument,series,resolution_seconds,bucket_ts)
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_flow_history_range
                   ON flow_history_aggregates(instrument,series,resolution_seconds,bucket_ts)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flow_history_migrations (
                    migration_key TEXT PRIMARY KEY,
                    instrument TEXT NOT NULL,
                    series TEXT NOT NULL,
                    resolution_seconds INTEGER NOT NULL,
                    last_completed_ts INTEGER,
                    rows_written INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flow_history_runtime (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS flow_history_policy (
                    version TEXT PRIMARY KEY,
                    raw_retention_seconds INTEGER NOT NULL,
                    aggregate_retention TEXT NOT NULL,
                    resolutions TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO flow_history_policy
                   (version,raw_retention_seconds,aggregate_retention,resolutions,created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    RETENTION_POLICY_VERSION,
                    RAW_RETENTION_SECONDS,
                    "indefinite",
                    json.dumps(AGGREGATE_RESOLUTIONS),
                    _utc_now(),
                ),
            )

    @staticmethod
    def _legacy_oi_source_sql() -> str:
        # flow_snapshots contains genuine REST OI observations from the older
        # collector.  Use only observations before oi_snapshots begins for an
        # instrument, so a time bucket is never double-counted.
        return """
            SELECT instrument,unixepoch(created_at) AS ts,oi,
                   'legacy flow_snapshots OI' AS source
            FROM flow_snapshots AS legacy
            WHERE instrument IS NOT NULL AND oi IS NOT NULL AND oi>0
              AND unixepoch(created_at) IS NOT NULL
              AND unixepoch(created_at) < COALESCE(
                  (SELECT MIN(raw.ts) FROM oi_snapshots AS raw
                   WHERE raw.instrument=legacy.instrument), 9223372036854775807)
        """

    def _series_bounds(
        self, connection: sqlite3.Connection, series: str, instrument: str
    ) -> tuple[int | None, int | None]:
        if series == "cvd":
            row = connection.execute(
                "SELECT MIN(ts),MAX(ts) FROM flow_trade_buckets WHERE instrument=?",
                (instrument,),
            ).fetchone()
        else:
            row = connection.execute(
                f"""WITH observations AS (
                    SELECT instrument,ts,oi,source FROM oi_snapshots
                    UNION ALL {self._legacy_oi_source_sql()}
                )
                SELECT MIN(ts),MAX(ts) FROM observations WHERE instrument=?""",
                (instrument,),
            ).fetchone()
        return (
            int(row[0]) if row and row[0] is not None else None,
            int(row[1]) if row and row[1] is not None else None,
        )

    def backfill(
        self,
        *,
        force: bool = False,
        batch_seconds: int = MIGRATION_BATCH_SECONDS,
    ) -> dict[str, Any]:
        """Backfill in aligned transactional batches and resume after failure."""
        self.initialize()
        summary: dict[str, Any] = {"rows_written": 0, "migrations": []}
        with self._connect() as connection:
            instruments = sorted(
                {
                    row[0]
                    for row in connection.execute(
                        """SELECT instrument FROM flow_trade_buckets
                           UNION SELECT instrument FROM oi_snapshots
                           UNION SELECT instrument FROM flow_snapshots
                                 WHERE instrument IS NOT NULL AND oi IS NOT NULL"""
                    )
                }
            )

        for instrument in instruments:
            for series in ("cvd", "oi"):
                for resolution in AGGREGATE_RESOLUTIONS:
                    written = self._backfill_lane(
                        instrument,
                        series,
                        resolution,
                        force=force,
                        batch_seconds=batch_seconds,
                    )
                    summary["rows_written"] += written
                    summary["migrations"].append(
                        {
                            "instrument": instrument,
                            "series": series,
                            "resolution_seconds": resolution,
                            "rows_written": written,
                        }
                    )
        return summary

    def _backfill_lane(
        self,
        instrument: str,
        series: str,
        resolution: int,
        *,
        force: bool,
        batch_seconds: int,
    ) -> int:
        key = f"{RETENTION_POLICY_VERSION}:{instrument}:{series}:{resolution}"
        with self._connect() as connection:
            progress = connection.execute(
                "SELECT status,last_completed_ts FROM flow_history_migrations WHERE migration_key=?",
                (key,),
            ).fetchone()
            if progress and progress["status"] == "complete" and not force:
                return 0
            lower, upper = self._series_bounds(connection, series, instrument)
            if lower is None or upper is None:
                return 0
            start = (lower // resolution) * resolution
            if progress and progress["last_completed_ts"] is not None and not force:
                start = max(start, int(progress["last_completed_ts"]))
            if force:
                start = (lower // resolution) * resolution
            now = _utc_now()
            connection.execute(
                """INSERT INTO flow_history_migrations
                   (migration_key,instrument,series,resolution_seconds,last_completed_ts,
                    rows_written,status,started_at,updated_at,completed_at)
                   VALUES(?,?,?,?,?,0,'running',?,?,NULL)
                   ON CONFLICT(migration_key) DO UPDATE SET
                    status='running',updated_at=excluded.updated_at,completed_at=NULL,
                    last_completed_ts=CASE WHEN ? THEN NULL ELSE flow_history_migrations.last_completed_ts END,
                    rows_written=CASE WHEN ? THEN 0 ELSE flow_history_migrations.rows_written END""",
                (key, instrument, series, resolution, start, now, now, force, force),
            )

        final_end = (upper // resolution) * resolution + resolution
        step = max(resolution, (batch_seconds // resolution) * resolution)
        total_written = 0
        while start < final_end:
            end = min(final_end, start + step)
            with self._connect() as connection:
                if series == "cvd":
                    cursor = connection.execute(
                        """SELECT instrument,'cvd',?,(ts / ?) * ?,
                                  SUM(buy_notional-sell_notional),NULL,NULL,NULL,
                                  SUM(trade_count),COUNT(*),MIN(ts),MAX(ts),
                                  'flow_trade_buckets'
                           FROM flow_trade_buckets
                           WHERE instrument=? AND ts>=? AND ts<?
                           GROUP BY (ts / ?) * ?""",
                        (
                            resolution,
                            resolution,
                            resolution,
                            instrument,
                            start,
                            end,
                            resolution,
                            resolution,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        f"""WITH observations AS (
                                SELECT instrument,ts,oi,source FROM oi_snapshots
                                UNION ALL {self._legacy_oi_source_sql()}
                            ),
                            ranked AS (
                                SELECT instrument,ts,oi,source,(ts / ?) * ? AS bucket_ts,
                                       ROW_NUMBER() OVER(
                                           PARTITION BY instrument,(ts / ?) * ?
                                           ORDER BY ts DESC,source DESC) AS newest
                                FROM observations
                                WHERE instrument=? AND ts>=? AND ts<?
                            )
                            SELECT instrument,'oi',?,bucket_ts,NULL,
                                   MAX(CASE WHEN newest=1 THEN oi END),MIN(oi),MAX(oi),
                                   0,COUNT(*),MIN(ts),MAX(ts),
                                   GROUP_CONCAT(DISTINCT source)
                            FROM ranked GROUP BY bucket_ts""",
                        (
                            resolution,
                            resolution,
                            resolution,
                            resolution,
                            instrument,
                            start,
                            end,
                            resolution,
                        ),
                    )
                rows = cursor.fetchall()
                connection.executemany(
                    """INSERT INTO flow_history_aggregates
                       (instrument,series,resolution_seconds,bucket_ts,delta,value_last,
                        value_min,value_max,trade_count,observation_count,first_ts,last_ts,source)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(instrument,series,resolution_seconds,bucket_ts) DO UPDATE SET
                        delta=excluded.delta,value_last=excluded.value_last,
                        value_min=excluded.value_min,value_max=excluded.value_max,
                        trade_count=excluded.trade_count,
                        observation_count=excluded.observation_count,
                        first_ts=excluded.first_ts,last_ts=excluded.last_ts,source=excluded.source""",
                    rows,
                )
                connection.execute(
                    """UPDATE flow_history_migrations
                       SET last_completed_ts=?,rows_written=rows_written+?,
                           updated_at=? WHERE migration_key=?""",
                    (end, len(rows), _utc_now(), key),
                )
            total_written += len(rows)
            LOGGER.info(
                "flow history backfill instrument=%s series=%s resolution=%s range=[%s,%s) rows=%s",
                instrument,
                series,
                resolution,
                start,
                end,
                len(rows),
            )
            start = end

        with self._connect() as connection:
            connection.execute(
                """UPDATE flow_history_migrations
                   SET status='complete',completed_at=?,updated_at=?
                   WHERE migration_key=?""",
                (_utc_now(), _utc_now(), key),
            )
        return total_written

    @staticmethod
    def persist_trade_values(
        connection: sqlite3.Connection,
        values: Iterable[tuple[str, int, float, float, int]],
    ) -> None:
        rows = list(values)
        for resolution in AGGREGATE_RESOLUTIONS:
            aggregate: dict[tuple[str, int], list[float | int]] = {}
            for instrument, timestamp, buy, sell, trades in rows:
                key = (instrument, (timestamp // resolution) * resolution)
                lane = aggregate.setdefault(key, [0.0, 0, 0, timestamp, timestamp])
                lane[0] = float(lane[0]) + buy - sell
                lane[1] = int(lane[1]) + trades
                lane[2] = int(lane[2]) + 1
                lane[3] = min(int(lane[3]), timestamp)
                lane[4] = max(int(lane[4]), timestamp)
            connection.executemany(
                """INSERT INTO flow_history_aggregates
                   (instrument,series,resolution_seconds,bucket_ts,delta,value_last,
                    value_min,value_max,trade_count,observation_count,first_ts,last_ts,source)
                   VALUES(?,'cvd',?,?,?,NULL,NULL,NULL,?,?,?,?,?)
                   ON CONFLICT(instrument,series,resolution_seconds,bucket_ts) DO UPDATE SET
                    delta=flow_history_aggregates.delta+excluded.delta,
                    trade_count=flow_history_aggregates.trade_count+excluded.trade_count,
                    observation_count=flow_history_aggregates.observation_count+excluded.observation_count,
                    first_ts=MIN(flow_history_aggregates.first_ts,excluded.first_ts),
                    last_ts=MAX(flow_history_aggregates.last_ts,excluded.last_ts)""",
                [
                    (
                        instrument,
                        resolution,
                        bucket,
                        float(lane[0]),
                        int(lane[1]),
                        int(lane[2]),
                        int(lane[3]),
                        int(lane[4]),
                        "flow_trade_buckets",
                    )
                    for (instrument, bucket), lane in aggregate.items()
                ],
            )

    def persist_oi_observation(
        self,
        connection: sqlite3.Connection,
        instrument: str,
        timestamp: int,
    ) -> None:
        for resolution in AGGREGATE_RESOLUTIONS:
            bucket = (timestamp // resolution) * resolution
            # Recompute the bucket so INSERT OR REPLACE of an identical raw
            # timestamp remains exact and idempotent.
            rows = connection.execute(
                """SELECT ts,oi,source FROM oi_snapshots
                   WHERE instrument=? AND ts>=? AND ts<?
                   ORDER BY ts""",
                (instrument, bucket, bucket + resolution),
            ).fetchall()
            if not rows:
                continue
            connection.execute(
                """INSERT INTO flow_history_aggregates
                   (instrument,series,resolution_seconds,bucket_ts,delta,value_last,
                    value_min,value_max,trade_count,observation_count,first_ts,last_ts,source)
                   VALUES(?,'oi',?,?,NULL,?,?,?,0,?,?,?,?)
                   ON CONFLICT(instrument,series,resolution_seconds,bucket_ts) DO UPDATE SET
                    value_last=excluded.value_last,value_min=excluded.value_min,
                    value_max=excluded.value_max,
                    observation_count=excluded.observation_count,
                    first_ts=excluded.first_ts,last_ts=excluded.last_ts,source=excluded.source""",
                (
                    instrument,
                    resolution,
                    bucket,
                    float(rows[-1]["oi"]),
                    min(float(row["oi"]) for row in rows),
                    max(float(row["oi"]) for row in rows),
                    len(rows),
                    int(rows[0]["ts"]),
                    int(rows[-1]["ts"]),
                    str(rows[-1]["source"]),
                ),
            )

    def record_prune(self, cutoff: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO flow_history_runtime(key,value,updated_at)
                   VALUES('last_raw_prune',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (json.dumps({"cutoff": cutoff}), _utc_now()),
            )

    def policy(self) -> dict[str, Any]:
        with self._connect() as connection:
            prune = connection.execute(
                "SELECT value,updated_at FROM flow_history_runtime WHERE key='last_raw_prune'"
            ).fetchone()
        return {
            "version": RETENTION_POLICY_VERSION,
            "raw_retention_seconds": RAW_RETENTION_SECONDS,
            "durable_aggregate_retention": "indefinite",
            "aggregate_resolutions": [
                _resolution_name(value) for value in AGGREGATE_RESOLUTIONS
            ],
            "last_raw_prune": (
                {"details": json.loads(prune["value"]), "recorded_at": prune["updated_at"]}
                if prune
                else None
            ),
        }

    def _available_bounds(
        self, connection: sqlite3.Connection, instrument: str, series: str
    ) -> tuple[int | None, int | None]:
        row = connection.execute(
            """SELECT MIN(first_ts),MAX(last_ts) FROM flow_history_aggregates
               WHERE instrument=? AND series=? AND resolution_seconds=?""",
            (instrument, series, AGGREGATE_RESOLUTIONS[0]),
        ).fetchone()
        if row and row[0] is not None:
            return int(row[0]), int(row[1])
        return self._series_bounds(connection, series, instrument)

    @staticmethod
    def _choose_resolution(
        duration: int, series: str, max_points: int, aggregate_required: bool
    ) -> int:
        native = 60 if series == "cvd" else 15
        candidates = AGGREGATE_RESOLUTIONS if aggregate_required else (native,) + AGGREGATE_RESOLUTIONS
        for resolution in candidates:
            if math.ceil(max(1, duration) / resolution) <= max_points:
                return resolution
        multiplier = math.ceil(duration / (AGGREGATE_RESOLUTIONS[-1] * max_points))
        return AGGREGATE_RESOLUTIONS[-1] * max(1, multiplier)

    def query(
        self,
        instrument: str,
        series: str,
        *,
        start: int | None = None,
        end: int | None = None,
        max_points: int = DEFAULT_MAX_POINTS,
        cursor: str | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        if series not in {"cvd", "oi"}:
            raise ValueError("series must be cvd or oi")
        if not instrument or len(instrument) > 40:
            raise ValueError("invalid instrument")
        now = int(time.time()) if now is None else int(now)
        requested_end = now if end is None else int(end)
        requested_start = requested_end - 6 * 3600 if start is None else int(start)
        if requested_start > requested_end:
            raise ValueError("start must be less than or equal to end")
        max_points = max(1, min(MAX_POINT_BUDGET, int(max_points)))
        effective_end = requested_end
        if cursor:
            effective_end = min(effective_end, _decode_cursor(cursor, instrument, series) - 1)

        with self._connect() as connection:
            available_start, available_end = self._available_bounds(
                connection, instrument, series
            )
            if available_start is None or available_end is None:
                return self._empty_response(
                    instrument,
                    series,
                    requested_start,
                    requested_end,
                    max_points,
                )
            raw_table = "flow_trade_buckets" if series == "cvd" else "oi_snapshots"
            raw_bounds = connection.execute(
                f"SELECT MIN(ts),MAX(ts) FROM {raw_table} WHERE instrument=?",
                (instrument,),
            ).fetchone()
            raw_start = int(raw_bounds[0]) if raw_bounds and raw_bounds[0] is not None else None
            aggregate_required = raw_start is None or requested_start < raw_start
            duration = max(1, effective_end - requested_start + 1)
            resolution = self._choose_resolution(
                duration, series, max_points, aggregate_required
            )
            raw_row_count = connection.execute(
                f"""SELECT COUNT(*) FROM {raw_table}
                    WHERE instrument=? AND ts>=? AND ts<=?""",
                (instrument, requested_start, effective_end),
            ).fetchone()[0]
            points = self._query_points(
                connection,
                instrument,
                series,
                requested_start,
                effective_end,
                resolution,
            )
            while len(points) > max_points:
                larger = next(
                    (value for value in AGGREGATE_RESOLUTIONS if value > resolution),
                    None,
                )
                if larger is None:
                    larger = resolution * math.ceil(len(points) / max_points)
                resolution = larger
                points = self._query_points(
                    connection,
                    instrument,
                    series,
                    requested_start,
                    effective_end,
                    resolution,
                )
            fallback = False
            if not points and requested_end >= available_end and requested_start > available_end:
                points = self._query_points(
                    connection,
                    instrument,
                    series,
                    available_end - max(resolution, 1),
                    available_end,
                    resolution,
                    latest_only=True,
                )
                fallback = bool(points)
            if series == "cvd" and points:
                first_bucket = (
                    points[0]["time"] // AGGREGATE_RESOLUTIONS[0]
                ) * AGGREGATE_RESOLUTIONS[0]
                baseline = connection.execute(
                    """SELECT COALESCE(SUM(delta),0) FROM flow_history_aggregates
                       WHERE instrument=? AND series='cvd' AND resolution_seconds=?
                         AND bucket_ts<?""",
                    (instrument, AGGREGATE_RESOLUTIONS[0], first_bucket),
                ).fetchone()[0]
                if resolution < AGGREGATE_RESOLUTIONS[0]:
                    baseline += connection.execute(
                        """SELECT COALESCE(SUM(buy_notional-sell_notional),0)
                           FROM flow_trade_buckets
                           WHERE instrument=? AND ts>=? AND ts<?""",
                        (
                            instrument,
                            first_bucket,
                            max(first_bucket, requested_start),
                        ),
                    ).fetchone()[0]
                cumulative = float(baseline or 0)
                for point in points:
                    cumulative += float(point["delta"])
                    point["value"] = round(cumulative, 2)

        interval = resolution
        gaps = [
            {"start": points[index - 1]["time"], "end": points[index]["time"]}
            for index in range(1, len(points))
            if points[index]["time"] - points[index - 1]["time"] > interval * 1.5
        ]
        first = points[0]["time"] if points else None
        last = points[-1]["time"] if points else None
        has_more_before = bool(first is not None and available_start < first)
        has_more_after = bool(
            last is not None and available_end >= last + resolution
        )
        source = (
            "persisted raw observations"
            if resolution < AGGREGATE_RESOLUTIONS[0] and not aggregate_required
            else "durable persisted aggregates"
        )
        if fallback:
            source += "; retained latest-history fallback"
        return {
            "api_version": HISTORY_API_VERSION,
            "instrument": instrument,
            "series": series,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "available_start": available_start,
            "available_end": available_end,
            "latest_timestamp": available_end,
            "raw_row_count": int(raw_row_count),
            "returned_point_count": len(points),
            "resolution": _resolution_name(resolution),
            "resolution_seconds": resolution,
            "stale": now - available_end > STALE_AFTER_SECONDS,
            "has_history": True,
            "has_more_before": has_more_before,
            "has_more_after": has_more_after,
            "next_before_cursor": (
                _encode_cursor(instrument, series, first)
                if has_more_before and first is not None
                else None
            ),
            "source": source,
            "retention_policy_version": RETENTION_POLICY_VERSION,
            "has_gaps": bool(gaps),
            "gap_count": len(gaps),
            "gaps": gaps[:100],
            "fallback": fallback,
            "points": points,
        }

    def _query_points(
        self,
        connection: sqlite3.Connection,
        instrument: str,
        series: str,
        start: int,
        end: int,
        resolution: int,
        *,
        latest_only: bool = False,
    ) -> list[dict[str, Any]]:
        native = 60 if series == "cvd" else 15
        if resolution == native:
            if series == "cvd":
                rows = connection.execute(
                    """SELECT (ts / 60) * 60 AS time,
                              SUM(buy_notional-sell_notional) AS delta,
                              SUM(trade_count) AS trades,COUNT(*) AS observations
                       FROM flow_trade_buckets
                       WHERE instrument=? AND ts>=? AND ts<=?
                       GROUP BY (ts / 60) * 60 ORDER BY time""",
                    (instrument, start, end),
                ).fetchall()
                return [
                    {
                        "time": int(row["time"]),
                        "delta": round(float(row["delta"]), 2),
                        "trades": int(row["trades"]),
                        "observation_count": int(row["observations"]),
                    }
                    for row in (rows[-1:] if latest_only else rows)
                ]
            rows = connection.execute(
                """SELECT ts AS time,oi AS value,oi AS value_min,oi AS value_max,
                          1 AS observations
                   FROM oi_snapshots
                   WHERE instrument=? AND ts>=? AND ts<=? ORDER BY ts""",
                (instrument, start, end),
            ).fetchall()
            selected = rows[-1:] if latest_only else rows
            return [
                {
                    "time": int(row["time"]),
                    "value": float(row["value"]),
                    "min": float(row["value_min"]),
                    "max": float(row["value_max"]),
                    "observation_count": int(row["observations"]),
                }
                for row in selected
            ]

        persisted = max(value for value in AGGREGATE_RESOLUTIONS if value <= resolution)
        rows = connection.execute(
            """SELECT (bucket_ts / ?) * ? AS time,
                      SUM(delta) AS delta,
                      MAX(value_last) FILTER(
                          WHERE bucket_ts=(SELECT MAX(inner_row.bucket_ts)
                                           FROM flow_history_aggregates AS inner_row
                                           WHERE inner_row.instrument=outer_row.instrument
                                             AND inner_row.series=outer_row.series
                                             AND inner_row.resolution_seconds=outer_row.resolution_seconds
                                             AND (inner_row.bucket_ts / ?) * ? =
                                                 (outer_row.bucket_ts / ?) * ?)
                      ) AS value_last,
                      MIN(value_min) AS value_min,MAX(value_max) AS value_max,
                      SUM(trade_count) AS trades,SUM(observation_count) AS observations
               FROM flow_history_aggregates AS outer_row
               WHERE instrument=? AND series=? AND resolution_seconds=?
                 AND bucket_ts>=? AND bucket_ts<=?
               GROUP BY (bucket_ts / ?) * ? ORDER BY time""",
            (
                resolution,
                resolution,
                resolution,
                resolution,
                resolution,
                resolution,
                instrument,
                series,
                persisted,
                (start // persisted) * persisted,
                end,
                resolution,
                resolution,
            ),
        ).fetchall()
        selected = rows[-1:] if latest_only else rows
        if series == "cvd":
            return [
                {
                    "time": int(row["time"]),
                    "delta": round(float(row["delta"] or 0), 2),
                    "trades": int(row["trades"] or 0),
                    "observation_count": int(row["observations"] or 0),
                }
                for row in selected
            ]
        return [
            {
                "time": int(row["time"]),
                "value": float(row["value_last"]),
                "min": float(row["value_min"]),
                "max": float(row["value_max"]),
                "observation_count": int(row["observations"] or 0),
            }
            for row in selected
            if row["value_last"] is not None
        ]

    @staticmethod
    def _empty_response(
        instrument: str,
        series: str,
        requested_start: int,
        requested_end: int,
        max_points: int,
    ) -> dict[str, Any]:
        del max_points
        return {
            "api_version": HISTORY_API_VERSION,
            "instrument": instrument,
            "series": series,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "available_start": None,
            "available_end": None,
            "latest_timestamp": None,
            "raw_row_count": 0,
            "returned_point_count": 0,
            "resolution": None,
            "resolution_seconds": None,
            "stale": True,
            "has_history": False,
            "has_more_before": False,
            "has_more_after": False,
            "next_before_cursor": None,
            "source": "persisted observations",
            "retention_policy_version": RETENTION_POLICY_VERSION,
            "has_gaps": False,
            "gap_count": 0,
            "gaps": [],
            "fallback": False,
            "points": [],
        }
