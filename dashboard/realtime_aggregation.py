"""Bounded, live-only CVD/OI aggregation for the microstructure collector.

This module never calls a network API and never mutates canonical raw rows.  It
only closes completed UTC buckets inside an explicitly supplied time range.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .microstructure import MICROSTRUCTURE_SOURCE_VERSION, RESOLUTIONS, now_ms


MINUTE_MS = 60_000
DAY_MS = 86_400_000
LIVE_LOOKBACK_MINUTES = 15
MAX_CATCHUP_MINUTES = 120
MAX_BUCKETS_PER_TRANSACTION = 30
COLLECTOR_BATCH_MINUTES = 2
DERIVED_RESOLUTIONS = ("5m", "15m", "1H")


def _fingerprint(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(
            row, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass
class AggregationResult:
    instrument: str
    start_ms: int
    end_ms: int
    processed_minutes: int = 0
    inserted: dict[str, int] = field(default_factory=dict)
    existing: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    latest_source_ms: int | None = None
    latest_source_ms_by_series: dict[str, int] = field(default_factory=dict)

    def bump(self, bucket: str, kind: str) -> None:
        target = getattr(self, kind)
        target[bucket] = target.get(bucket, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "processed_minutes": self.processed_minutes,
            "inserted": self.inserted,
            "existing": self.existing,
            "missing": self.missing,
            "conflicts": self.conflicts,
            "latest_source_ms": self.latest_source_ms,
            "latest_source_ms_by_series": self.latest_source_ms_by_series,
        }


class RealtimeAggregationEngine:
    """Close a bounded set of minute buckets on the collector's live writer."""

    def __init__(self, store: Any) -> None:
        self.store = store

    @staticmethod
    def completed_minute(reference_ms: int) -> int:
        return reference_ms // MINUTE_MS * MINUTE_MS

    @staticmethod
    def bounded_range(start_ms: int, end_ms: int) -> tuple[int, int]:
        start = start_ms // MINUTE_MS * MINUTE_MS
        end = end_ms // MINUTE_MS * MINUTE_MS
        if end < start:
            raise ValueError("aggregation end precedes start")
        if end - start > MAX_CATCHUP_MINUTES * MINUTE_MS:
            raise ValueError("realtime aggregation range exceeds 120 minutes")
        return start, end

    @staticmethod
    def _raw_gap(
        connection: sqlite3.Connection, lane: str, instrument: str,
        start_ms: int, end_ms: int,
    ) -> bool:
        return connection.execute(
            """SELECT 1 FROM collection_gaps
               WHERE lane=? AND instrument=? AND resolved_at_ms IS NULL
                 AND start_ms<? AND end_ms>?
               LIMIT 1""",
            (lane, instrument, end_ms, start_ms),
        ).fetchone() is not None

    @staticmethod
    def _metadata(
        connection: sqlite3.Connection, instrument: str, series: str,
        resolution: str, bucket_ms: int,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT * FROM realtime_aggregate_fingerprints
               WHERE instrument=? AND series=? AND resolution=? AND bucket_ms=?""",
            (instrument, series, resolution, bucket_ms),
        ).fetchone()

    @staticmethod
    def _put_metadata(
        connection: sqlite3.Connection, instrument: str, series: str,
        resolution: str, bucket_ms: int, fingerprint: str | None, status: str,
        first_source_ms: int | None, last_source_ms: int | None,
        observation_count: int, detail: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO realtime_aggregate_fingerprints VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(instrument,series,resolution,bucket_ms) DO UPDATE SET
                   source_fingerprint=excluded.source_fingerprint,
                   status=excluded.status,
                   first_source_ts_ms=excluded.first_source_ts_ms,
                   last_source_ts_ms=excluded.last_source_ts_ms,
                   observation_count=excluded.observation_count,
                   detail_json=excluded.detail_json,
                   updated_at_ms=excluded.updated_at_ms""",
            (instrument, series, resolution, bucket_ms, fingerprint, status,
             first_source_ms, last_source_ms, observation_count,
             json.dumps(detail or {}, sort_keys=True), now_ms()),
        )

    @staticmethod
    def _same_values(existing: sqlite3.Row, columns: tuple[str, ...],
                     expected: tuple[Any, ...]) -> bool:
        for column, value in zip(columns, expected):
            actual = existing[column]
            if isinstance(value, float):
                if actual is None or not math.isclose(
                    float(actual), value, rel_tol=1e-12, abs_tol=1e-9
                ):
                    return False
            elif actual != value:
                return False
        return True

    def _persist(
        self, connection: sqlite3.Connection, result: AggregationResult,
        *, table: str, series: str, resolution: str, bucket_ms: int,
        fingerprint: str, columns: tuple[str, ...], values: tuple[Any, ...],
        row_values: tuple[Any, ...], first_source_ms: int,
        last_source_ms: int, observation_count: int,
    ) -> bool:
        key = f"{series}:{resolution}"
        metadata = self._metadata(
            connection, result.instrument, series, resolution, bucket_ms)
        existing = connection.execute(
            f"""SELECT * FROM {table}
                WHERE instrument=? AND resolution=? AND bucket_ms=?""",
            (result.instrument, resolution, bucket_ms),
        ).fetchone()
        if metadata is not None and metadata["status"] == "VALID":
            if metadata["source_fingerprint"] == fingerprint:
                result.bump(key, "existing")
                return True
            self._put_metadata(
                connection, result.instrument, series, resolution, bucket_ms,
                str(metadata["source_fingerprint"]), "CONFLICT",
                first_source_ms, last_source_ms, observation_count,
                {"candidate_fingerprint": fingerprint},)
            result.conflicts.append({
                "series": series, "resolution": resolution,
                "bucket_ms": bucket_ms,
                "stored_fingerprint": metadata["source_fingerprint"],
                "candidate_fingerprint": fingerprint,
            })
            return False
        if existing is not None and not self._same_values(existing, columns, values):
            self._put_metadata(
                connection, result.instrument, series, resolution, bucket_ms,
                None, "CONFLICT", first_source_ms, last_source_ms,
                observation_count, {"reason": "legacy aggregate differs"})
            result.conflicts.append({
                "series": series, "resolution": resolution,
                "bucket_ms": bucket_ms, "reason": "legacy aggregate differs",
            })
            return False
        if existing is None:
            placeholders = ",".join("?" for _ in row_values)
            connection.execute(
                f"INSERT INTO {table} VALUES({placeholders})", row_values)
            result.bump(key, "inserted")
        else:
            result.bump(key, "existing")
        self._put_metadata(
            connection, result.instrument, series, resolution, bucket_ms,
            fingerprint, "VALID", first_source_ms, last_source_ms,
            observation_count)
        return True

    def _minute_cvd(
        self, connection: sqlite3.Connection, result: AggregationResult,
        bucket_ms: int,
    ) -> bool:
        rows = connection.execute(
            """SELECT source_ts_ms,source_identity,COALESCE(trade_id,''),side,notional
               FROM trade_flow_observations
               WHERE instrument=? AND state='confirmed'
                 AND source_ts_ms>=? AND source_ts_ms<?
               ORDER BY source_ts_ms,COALESCE(trade_id,source_identity),source_identity""",
            (result.instrument, bucket_ms, bucket_ms + MINUTE_MS),
        ).fetchall()
        if not rows or self._raw_gap(
            connection, "trades", result.instrument,
            bucket_ms, bucket_ms + MINUTE_MS,
        ):
            self._put_metadata(
                connection, result.instrument, "cvd", "1m", bucket_ms,
                None, "MISSING", None, None, 0,
                {"reason": "no confirmed raw trades or recorded raw gap"})
            result.bump("cvd:1m", "missing")
            return True
        stable = [tuple(row) for row in rows]
        fingerprint = _fingerprint(stable)
        buy = sum(float(row["notional"]) for row in rows if row["side"] == "buy")
        sell = sum(float(row["notional"]) for row in rows if row["side"] == "sell")
        first_ms, last_ms = int(rows[0]["source_ts_ms"]), int(rows[-1]["source_ts_ms"])
        prior = None if bucket_ms % DAY_MS == 0 else connection.execute(
            """SELECT cumulative_anchored FROM cvd_aggregates
               WHERE instrument=? AND resolution='1m' AND bucket_ms=?""",
            (result.instrument, bucket_ms - MINUTE_MS),
        ).fetchone()
        if bucket_ms % DAY_MS != 0 and prior is None:
            prior_raw = connection.execute(
                """SELECT 1 FROM trade_flow_observations
                   WHERE instrument=? AND state='confirmed'
                     AND source_ts_ms>=? AND source_ts_ms<? LIMIT 1""",
                (result.instrument, bucket_ms - MINUTE_MS, bucket_ms),
            ).fetchone()
            prior_gap = self._raw_gap(
                connection, "trades", result.instrument,
                bucket_ms - MINUTE_MS, bucket_ms)
            if prior_raw is not None and not prior_gap:
                # The raw predecessor exists but its canonical aggregate has
                # not committed yet.  Preserve ordering instead of inventing a
                # new cumulative segment at an arbitrary lookback boundary.
                self._put_metadata(
                    connection, result.instrument, "cvd", "1m", bucket_ms,
                    fingerprint, "MISSING", first_ms, last_ms, len(rows),
                    {"reason": "prior minute aggregate pending"})
                result.bump("cvd:1m", "missing")
                return True
        cumulative = (float(prior[0]) if prior is not None else 0.0) + buy - sell
        gap_flag = int(bucket_ms % DAY_MS != 0 and prior is None)
        expected = (
            buy, sell, buy - sell, cumulative, len(rows), first_ms, last_ms,
            gap_flag, MICROSTRUCTURE_SOURCE_VERSION)
        row_values = (
            result.instrument, "1m", bucket_ms, *expected)
        ok = self._persist(
            connection, result, table="cvd_aggregates", series="cvd",
            resolution="1m", bucket_ms=bucket_ms, fingerprint=fingerprint,
            columns=("buy_notional", "sell_notional", "delta",
                     "cumulative_anchored", "observation_count",
                     "first_source_ts_ms", "last_source_ts_ms", "gap_flag",
                     "source_version"),
            values=expected, row_values=row_values, first_source_ms=first_ms,
            last_source_ms=last_ms, observation_count=len(rows))
        if ok:
            result.latest_source_ms = max(result.latest_source_ms or 0, last_ms)
            result.latest_source_ms_by_series["cvd"] = max(
                result.latest_source_ms_by_series.get("cvd", 0), last_ms)
        return ok

    def _minute_oi(
        self, connection: sqlite3.Connection, result: AggregationResult,
        bucket_ms: int,
    ) -> bool:
        rows = connection.execute(
            """SELECT source_ts_ms,source_identity,
                      COALESCE(oi_usd,oi_currency,oi_contracts) value
               FROM oi_observations
               WHERE instrument=? AND state='confirmed'
                 AND COALESCE(oi_usd,oi_currency,oi_contracts) IS NOT NULL
                 AND source_ts_ms>=? AND source_ts_ms<?
               ORDER BY source_ts_ms,source_identity""",
            (result.instrument, bucket_ms, bucket_ms + MINUTE_MS),
        ).fetchall()
        if not rows or self._raw_gap(
            connection, "oi", result.instrument,
            bucket_ms, bucket_ms + MINUTE_MS,
        ):
            self._put_metadata(
                connection, result.instrument, "oi", "1m", bucket_ms,
                None, "MISSING", None, None, 0,
                {"reason": "no confirmed OI observation or recorded raw gap"})
            result.bump("oi:1m", "missing")
            return True
        stable = [(int(row["source_ts_ms"]), str(row["source_identity"]),
                   float(row["value"])) for row in rows]
        fingerprint = _fingerprint(stable)
        values_only = [row[2] for row in stable]
        first, last = values_only[0], values_only[-1]
        first_ms, last_ms = stable[0][0], stable[-1][0]
        change = last - first
        expected = (
            first, last, min(values_only), max(values_only), change,
            change / first if first else None, len(rows), first_ms, last_ms, 0,
            MICROSTRUCTURE_SOURCE_VERSION)
        row_values = (result.instrument, "1m", bucket_ms, *expected)
        ok = self._persist(
            connection, result, table="oi_aggregates", series="oi",
            resolution="1m", bucket_ms=bucket_ms, fingerprint=fingerprint,
            columns=("first_value", "last_value", "min_value", "max_value",
                     "absolute_change", "percentage_change", "observation_count",
                     "first_source_ts_ms", "last_source_ts_ms", "gap_flag",
                     "source_version"),
            values=expected, row_values=row_values, first_source_ms=first_ms,
            last_source_ms=last_ms, observation_count=len(rows))
        if ok:
            result.latest_source_ms = max(result.latest_source_ms or 0, last_ms)
            result.latest_source_ms_by_series["oi"] = max(
                result.latest_source_ms_by_series.get("oi", 0), last_ms)
        return ok

    def _derive_cvd(
        self, connection: sqlite3.Connection, result: AggregationResult,
        resolution: str, bucket_ms: int,
    ) -> bool:
        width = RESOLUTIONS[resolution]
        rows = connection.execute(
            """SELECT a.* FROM cvd_aggregates a
               JOIN realtime_aggregate_fingerprints f
                 ON f.instrument=a.instrument AND f.series='cvd'
                AND f.resolution='1m' AND f.bucket_ms=a.bucket_ms
                AND f.status='VALID'
               WHERE a.instrument=? AND a.resolution='1m'
                 AND a.bucket_ms>=? AND a.bucket_ms<?
               ORDER BY a.bucket_ms""",
            (result.instrument, bucket_ms, bucket_ms + width),
        ).fetchall()
        expected_count = width // MINUTE_MS
        complete = (
            len(rows) == expected_count
            and [int(row["bucket_ms"]) for row in rows]
            == list(range(bucket_ms, bucket_ms + width, MINUTE_MS))
            and not any(int(row["gap_flag"]) for row in rows))
        if not complete:
            self._put_metadata(
                connection, result.instrument, "cvd", resolution, bucket_ms,
                None, "MISSING", None, None, len(rows),
                {"required_1m": expected_count, "available_valid_1m": len(rows)})
            result.bump(f"cvd:{resolution}", "missing")
            return True
        stable = [(
            int(row["bucket_ms"]), float(row["buy_notional"]),
            float(row["sell_notional"]), float(row["delta"]),
            int(row["observation_count"]), int(row["first_source_ts_ms"]),
            int(row["last_source_ts_ms"]),
        ) for row in rows]
        fingerprint = _fingerprint(stable)
        buy = sum(row[1] for row in stable); sell = sum(row[2] for row in stable)
        count = sum(row[4] for row in stable)
        first_ms = min(row[5] for row in stable); last_ms = max(row[6] for row in stable)
        expected = (buy, sell, buy - sell, float(rows[-1]["cumulative_anchored"]),
                    count, first_ms, last_ms, 0, MICROSTRUCTURE_SOURCE_VERSION)
        return self._persist(
            connection, result, table="cvd_aggregates", series="cvd",
            resolution=resolution, bucket_ms=bucket_ms, fingerprint=fingerprint,
            columns=("buy_notional", "sell_notional", "delta",
                     "cumulative_anchored", "observation_count",
                     "first_source_ts_ms", "last_source_ts_ms", "gap_flag",
                     "source_version"), values=expected,
            row_values=(result.instrument, resolution, bucket_ms, *expected),
            first_source_ms=first_ms, last_source_ms=last_ms,
            observation_count=count)

    def _derive_oi(
        self, connection: sqlite3.Connection, result: AggregationResult,
        resolution: str, bucket_ms: int,
    ) -> bool:
        width = RESOLUTIONS[resolution]
        rows = connection.execute(
            """SELECT a.* FROM oi_aggregates a
               JOIN realtime_aggregate_fingerprints f
                 ON f.instrument=a.instrument AND f.series='oi'
                AND f.resolution='1m' AND f.bucket_ms=a.bucket_ms
                AND f.status='VALID'
               WHERE a.instrument=? AND a.resolution='1m'
                 AND a.bucket_ms>=? AND a.bucket_ms<?
               ORDER BY a.bucket_ms""",
            (result.instrument, bucket_ms, bucket_ms + width),
        ).fetchall()
        expected_count = width // MINUTE_MS
        complete = (
            len(rows) == expected_count
            and [int(row["bucket_ms"]) for row in rows]
            == list(range(bucket_ms, bucket_ms + width, MINUTE_MS))
            and not any(int(row["gap_flag"]) for row in rows))
        if not complete:
            self._put_metadata(
                connection, result.instrument, "oi", resolution, bucket_ms,
                None, "MISSING", None, None, len(rows),
                {"required_1m": expected_count, "available_valid_1m": len(rows)})
            result.bump(f"oi:{resolution}", "missing")
            return True
        stable = [(
            int(row["bucket_ms"]), float(row["first_value"]),
            float(row["last_value"]), float(row["min_value"]),
            float(row["max_value"]), int(row["observation_count"]),
            int(row["first_source_ts_ms"]), int(row["last_source_ts_ms"]),
        ) for row in rows]
        fingerprint = _fingerprint(stable)
        first, last = stable[0][1], stable[-1][2]
        change = last - first
        count = sum(row[5] for row in stable)
        first_ms = min(row[6] for row in stable); last_ms = max(row[7] for row in stable)
        expected = (
            first, last, min(row[3] for row in stable),
            max(row[4] for row in stable), change,
            change / first if first else None, count, first_ms, last_ms, 0,
            MICROSTRUCTURE_SOURCE_VERSION)
        return self._persist(
            connection, result, table="oi_aggregates", series="oi",
            resolution=resolution, bucket_ms=bucket_ms, fingerprint=fingerprint,
            columns=("first_value", "last_value", "min_value", "max_value",
                     "absolute_change", "percentage_change", "observation_count",
                     "first_source_ts_ms", "last_source_ts_ms", "gap_flag",
                     "source_version"), values=expected,
            row_values=(result.instrument, resolution, bucket_ms, *expected),
            first_source_ms=first_ms, last_source_ms=last_ms,
            observation_count=count)

    def process(
        self, instrument: str, start_ms: int, end_ms: int, *,
        maximum_minute_buckets: int = MAX_BUCKETS_PER_TRANSACTION,
    ) -> dict[str, Any]:
        start, end = self.bounded_range(start_ms, end_ms)
        if maximum_minute_buckets < 1 or maximum_minute_buckets > 30:
            raise ValueError("maximum_minute_buckets must be between 1 and 30")
        end = min(end, start + maximum_minute_buckets * MINUTE_MS)
        result = AggregationResult(instrument, start, end)
        with self.store.connect() as connection:
            checkpoint = connection.execute(
                """SELECT cursor FROM collection_checkpoints
                   WHERE lane='realtime_aggregation' AND instrument=?""",
                (instrument,),).fetchone()
            prior_cursor = checkpoint[0] if checkpoint else None
            for bucket_ms in range(start, end, MINUTE_MS):
                result.processed_minutes += 1
                if not self._minute_cvd(connection, result, bucket_ms):
                    break
                if not self._minute_oi(connection, result, bucket_ms):
                    break
            if not result.conflicts:
                for resolution in DERIVED_RESOLUTIONS:
                    width = RESOLUTIONS[resolution]
                    first = start // width * width
                    for bucket_ms in range(first, end, width):
                        if bucket_ms + width > end:
                            continue
                        if not self._derive_cvd(
                            connection, result, resolution, bucket_ms
                        ) or not self._derive_oi(
                            connection, result, resolution, bucket_ms
                        ):
                            break
                    if result.conflicts:
                        break
            cursor = prior_cursor if result.conflicts else str(end)
            connection.execute(
                """INSERT INTO collection_checkpoints VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(lane,instrument) DO UPDATE SET
                     cursor=excluded.cursor,
                     last_source_ts_ms=excluded.last_source_ts_ms,
                     status=excluded.status,
                     metadata_json=excluded.metadata_json,
                     updated_at_ms=excluded.updated_at_ms""",
                ("realtime_aggregation", instrument, cursor,
                 result.latest_source_ms,
                 "CONFLICT" if result.conflicts else "RUNNING",
                 json.dumps(result.as_dict(), sort_keys=True), now_ms()),)
        return result.as_dict()

    def dry_run(self, instrument: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        """Read-only bucket inventory; it never computes from outside the range."""
        start, end = self.bounded_range(start_ms, end_ms)
        result: dict[str, Any] = {
            "instrument": instrument, "start_ms": start, "end_ms": end,
            "minutes": (end - start) // MINUTE_MS,
            "cvd_1m_recoverable": 0, "cvd_1m_missing_raw": 0,
            "oi_1m_recoverable": 0, "oi_1m_missing_raw": 0,
        }
        coverage = {"cvd": set(), "oi": set()}
        with self.store.connect(readonly=True) as connection:
            for bucket_ms in range(start, end, MINUTE_MS):
                for series, lane, table in (
                    ("cvd", "trades", "trade_flow_observations"),
                    ("oi", "oi", "oi_observations"),
                ):
                    present = connection.execute(
                        f"""SELECT 1 FROM {table} WHERE instrument=?
                            AND state='confirmed' AND source_ts_ms>=?
                            AND source_ts_ms<? LIMIT 1""",
                        (instrument, bucket_ms, bucket_ms + MINUTE_MS),
                    ).fetchone() is not None
                    complete = present and not self._raw_gap(
                        connection, lane, instrument,
                        bucket_ms, bucket_ms + MINUTE_MS)
                    if complete:
                        coverage[series].add(bucket_ms)
                        result[f"{series}_1m_recoverable"] += 1
                    else:
                        result[f"{series}_1m_missing_raw"] += 1
            for series in ("cvd", "oi"):
                for resolution in DERIVED_RESOLUTIONS:
                    width = RESOLUTIONS[resolution]
                    buckets = range(start // width * width, end, width)
                    recoverable = 0
                    for bucket_ms in buckets:
                        required = set(range(
                            bucket_ms, bucket_ms + width, MINUTE_MS))
                        if bucket_ms + width <= end and required <= coverage[series]:
                            recoverable += 1
                    result[f"{series}_{resolution}_recoverable"] = recoverable
        return result
