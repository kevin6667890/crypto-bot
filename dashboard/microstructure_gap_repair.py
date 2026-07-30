"""Bounded, deterministic repair for canonical CVD/OI aggregate gaps."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .microstructure import (
    MICROSTRUCTURE_SOURCE_VERSION,
    RESOLUTIONS,
)


def utc_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


def aligned_buckets(start_ms: int, end_ms: int, width: int) -> list[int]:
    first = (start_ms // width) * width
    last = ((end_ms - 1) // width) * width
    return list(range(first, last + 1, width)) if end_ms > start_ms else []


def missing_runs(values: Iterable[int], width: int) -> list[dict[str, Any]]:
    runs: list[list[int]] = []
    for value in sorted(values):
        if not runs or value != runs[-1][-1] + width:
            runs.append([value])
        else:
            runs[-1].append(value)
    return [
        {
            "start_ms": run[0],
            "end_ms": run[-1] + width,
            "start": utc_iso(run[0]),
            "end": utc_iso(run[-1] + width),
            "bucket_count": len(run),
        }
        for run in runs
    ]


class AggregateGapRepair:
    """Diagnose and repair only a caller-bounded interval.

    Raw observations are immutable inputs. Existing aggregate rows are left
    untouched when every canonical field agrees; missing rows are inserted and
    inconsistent rows are deterministically corrected in one transaction.
    """

    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    def _connect(self, *, readonly: bool) -> sqlite3.Connection:
        if readonly:
            connection = sqlite3.connect(
                f"file:{self.database}?mode=ro", uri=True, timeout=30
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def _raw_table(source: str) -> str:
        return {
            "cvd": "trade_flow_observations",
            "oi": "oi_observations",
        }[source]

    @staticmethod
    def _aggregate_table(source: str) -> str:
        return {"cvd": "cvd_aggregates", "oi": "oi_aggregates"}[source]

    def _raw_minute_rows(
        self,
        connection: sqlite3.Connection,
        instrument: str,
        source: str,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[Any, ...]]:
        if source == "cvd":
            rows = connection.execute(
                """SELECT (source_ts_ms/60000)*60000 AS bucket,
                          SUM(CASE WHEN side='buy' THEN notional ELSE 0 END) AS buy,
                          SUM(CASE WHEN side='sell' THEN notional ELSE 0 END) AS sell,
                          COUNT(*) AS observations,MIN(source_ts_ms) AS first_ts,
                          MAX(source_ts_ms) AS last_ts
                   FROM trade_flow_observations
                   WHERE instrument=? AND state='confirmed'
                     AND source_ts_ms>=? AND source_ts_ms<?
                   GROUP BY bucket ORDER BY bucket""",
                (instrument, start_ms, end_ms),
            ).fetchall()
            prior = connection.execute(
                """SELECT cumulative_anchored FROM cvd_aggregates
                   WHERE instrument=? AND resolution='1m' AND bucket_ms<?
                   ORDER BY bucket_ms DESC LIMIT 1""",
                (instrument, start_ms),
            ).fetchone()
            cumulative = float(prior[0]) if prior else 0.0
            values = []
            for row in rows:
                buy, sell = float(row["buy"]), float(row["sell"])
                cumulative += buy - sell
                values.append((
                    instrument, "1m", int(row["bucket"]), buy, sell, buy - sell,
                    cumulative, int(row["observations"]), int(row["first_ts"]),
                    int(row["last_ts"]), 0, MICROSTRUCTURE_SOURCE_VERSION,
                ))
            return values

        rows = connection.execute(
            """SELECT source_ts_ms,
                      COALESCE(oi_usd,oi_currency,oi_contracts) AS value
               FROM oi_observations
               WHERE instrument=? AND state='confirmed'
                 AND COALESCE(oi_usd,oi_currency,oi_contracts) IS NOT NULL
                 AND source_ts_ms>=? AND source_ts_ms<?
               ORDER BY source_ts_ms,source_identity""",
            (instrument, start_ms, end_ms),
        ).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[int(row["source_ts_ms"]) // 60_000 * 60_000].append(row)
        values = []
        for bucket, items in sorted(grouped.items()):
            observed = [float(item["value"]) for item in items]
            first, last = observed[0], observed[-1]
            values.append((
                instrument, "1m", bucket, first, last, min(observed),
                max(observed), last - first,
                (last - first) / first if first else None,
                len(items), int(items[0]["source_ts_ms"]),
                int(items[-1]["source_ts_ms"]), 0,
                MICROSTRUCTURE_SOURCE_VERSION,
            ))
        return values

    @staticmethod
    def _fingerprint(rows: Iterable[tuple[Any, ...]]) -> str:
        digest = hashlib.sha256()
        for row in rows:
            digest.update(json.dumps(
                row, separators=(",", ":"), default=str
            ).encode())
            digest.update(b"\n")
        return digest.hexdigest()

    def diagnose(
        self,
        instrument: str,
        source: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, Any]:
        if source not in {"cvd", "oi"}:
            raise ValueError("source must be cvd or oi")
        if start_ms >= end_ms:
            raise ValueError("start must be before end")
        with self._connect(readonly=True) as connection:
            raw_rows = self._raw_minute_rows(
                connection, instrument, source, start_ms, end_ms
            )
            raw_buckets = {int(row[2]) for row in raw_rows}
            aggregate_buckets = {
                int(row[0]) for row in connection.execute(
                    f"""SELECT bucket_ms FROM {self._aggregate_table(source)}
                        WHERE instrument=? AND resolution='1m'
                          AND bucket_ms>=? AND bucket_ms<?""",
                    (instrument, start_ms, end_ms),
                )
            }
            resolution_plan: dict[str, dict[str, int]] = {}
            values_by_resolution = {"1m": raw_rows}
            for resolution in ("5m", "15m", "1H"):
                values_by_resolution[resolution] = self._higher_rows(
                    connection, instrument, source, resolution,
                    start_ms, end_ms, raw_rows,
                )
            for resolution, values in values_by_resolution.items():
                inserts = updates = unchanged = 0
                for expected_row in values:
                    existing = connection.execute(
                        f"""SELECT * FROM {self._aggregate_table(source)}
                            WHERE instrument=? AND resolution=?
                              AND bucket_ms=?""",
                        (instrument, resolution, expected_row[2]),
                    ).fetchone()
                    if existing is None:
                        inserts += 1
                    elif self._same(existing, expected_row):
                        unchanged += 1
                    else:
                        updates += 1
                resolution_plan[resolution] = {
                    "insert": inserts,
                    "update": updates,
                    "unchanged": unchanged,
                }
        expected = set(aligned_buckets(start_ms, end_ms, 60_000))
        recoverable = raw_buckets - aggregate_buckets
        raw_missing = expected - raw_buckets
        return {
            "instrument": instrument,
            "source": source,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start": utc_iso(start_ms),
            "end": utc_iso(end_ms),
            "expected_bucket_count": len(expected),
            "raw_bucket_count": len(raw_buckets),
            "aggregate_bucket_count": len(aggregate_buckets),
            "recoverable_bucket_count": len(recoverable),
            "unrecoverable_bucket_count": len(raw_missing),
            "recoverable_runs": missing_runs(recoverable, 60_000),
            "unrecoverable_runs": missing_runs(raw_missing, 60_000),
            "raw_source_fingerprint": self._fingerprint(raw_rows),
            "estimated_rows_written": sum(
                row["insert"] + row["update"]
                for row in resolution_plan.values()
            ),
            "resolution_plan": resolution_plan,
            "affected_tables": [self._aggregate_table(source)],
            "synthetic_data": False,
            "interpolation": False,
        }

    @staticmethod
    def _same(existing: sqlite3.Row, expected: tuple[Any, ...]) -> bool:
        actual = tuple(existing)
        if len(actual) != len(expected):
            return False
        for left, right in zip(actual, expected):
            if isinstance(left, float) or isinstance(right, float):
                if left is None or right is None:
                    if left != right:
                        return False
                elif abs(float(left) - float(right)) > 1e-8 * max(
                    1.0, abs(float(left)), abs(float(right))
                ):
                    return False
            elif left != right:
                return False
        return True

    def rebuild(
        self,
        instrument: str,
        source: str,
        start_ms: int,
        end_ms: int,
        *,
        max_rows: int,
    ) -> dict[str, Any]:
        diagnosis = self.diagnose(instrument, source, start_ms, end_ms)
        if (
            diagnosis["expected_bucket_count"] > max_rows
            or diagnosis["estimated_rows_written"] > max_rows
        ):
            raise ValueError("bounded repair exceeds --max-rows")
        inserted = updated = unchanged = 0
        table = self._aggregate_table(source)
        with self._connect(readonly=False) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                minute_values = self._raw_minute_rows(
                    connection, instrument, source, start_ms, end_ms
                )
                values_by_resolution = {"1m": minute_values}
                for resolution in ("5m", "15m", "1H"):
                    values_by_resolution[resolution] = self._higher_rows(
                        connection, instrument, source, resolution,
                        start_ms, end_ms, minute_values,
                    )
                for resolution, values in values_by_resolution.items():
                    for expected in values:
                        existing = connection.execute(
                            f"""SELECT * FROM {table}
                                WHERE instrument=? AND resolution=?
                                  AND bucket_ms=?""",
                            (instrument, resolution, expected[2]),
                        ).fetchone()
                        if existing is not None and self._same(existing, expected):
                            unchanged += 1
                            continue
                        placeholders = ",".join("?" for _ in expected)
                        if existing is None:
                            connection.execute(
                                f"INSERT INTO {table} VALUES({placeholders})",
                                expected,
                            )
                            inserted += 1
                        else:
                            connection.execute(
                                f"""DELETE FROM {table} WHERE instrument=?
                                    AND resolution=? AND bucket_ms=?""",
                                (instrument, resolution, expected[2]),
                            )
                            connection.execute(
                                f"INSERT INTO {table} VALUES({placeholders})",
                                expected,
                            )
                            updated += 1
                self._record_unrecoverable_gaps(
                    connection, diagnosis, instrument, source
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        verified = self.diagnose(instrument, source, start_ms, end_ms)
        return {
            **diagnosis,
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "verified_recoverable_bucket_count":
                verified["recoverable_bucket_count"],
            "status": "APPLIED",
        }

    def _higher_rows(
        self,
        connection: sqlite3.Connection,
        instrument: str,
        source: str,
        resolution: str,
        start_ms: int,
        end_ms: int,
        minute_values: list[tuple[Any, ...]],
    ) -> list[tuple[Any, ...]]:
        width = RESOLUTIONS[resolution]
        expected_children = width // 60_000
        grouped: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
        for row in minute_values:
            grouped[int(row[2]) // width * width].append(row)
        complete = [
            (bucket, sorted(rows, key=lambda item: int(item[2])))
            for bucket, rows in sorted(grouped.items())
            if bucket >= start_ms and bucket + width <= end_ms
            and len(rows) == expected_children
        ]
        if source == "cvd":
            prior = connection.execute(
                """SELECT cumulative_anchored FROM cvd_aggregates
                   WHERE instrument=? AND resolution=? AND bucket_ms<?
                   ORDER BY bucket_ms DESC LIMIT 1""",
                (instrument, resolution, start_ms),
            ).fetchone()
            cumulative = float(prior[0]) if prior else 0.0
            result = []
            for bucket, rows in complete:
                buy = sum(float(row[3]) for row in rows)
                sell = sum(float(row[4]) for row in rows)
                cumulative += buy - sell
                result.append((
                    instrument, resolution, bucket, buy, sell, buy - sell,
                    cumulative, sum(int(row[7]) for row in rows),
                    min(int(row[8]) for row in rows),
                    max(int(row[9]) for row in rows), 0,
                    MICROSTRUCTURE_SOURCE_VERSION,
                ))
            return result
        result = []
        for bucket, rows in complete:
            first, last = float(rows[0][3]), float(rows[-1][4])
            result.append((
                instrument, resolution, bucket, first, last,
                min(float(row[5]) for row in rows),
                max(float(row[6]) for row in rows),
                last - first, (last - first) / first if first else None,
                sum(int(row[9]) for row in rows),
                min(int(row[10]) for row in rows),
                max(int(row[11]) for row in rows), 0,
                MICROSTRUCTURE_SOURCE_VERSION,
            ))
        return result

    @staticmethod
    def _record_unrecoverable_gaps(
        connection: sqlite3.Connection,
        diagnosis: dict[str, Any],
        instrument: str,
        source: str,
    ) -> None:
        detected = int(datetime.now(timezone.utc).timestamp() * 1000)
        for run in diagnosis["unrecoverable_runs"]:
            connection.execute(
                """INSERT OR IGNORE INTO collection_gaps
                   (lane,instrument,start_ms,end_ms,reason,detected_at_ms,resolved_at_ms)
                   VALUES(?,?,?,?,?,?,NULL)""",
                (
                    "trades" if source == "cvd" else "oi",
                    instrument,
                    run["start_ms"],
                    run["end_ms"],
                    "UNRECOVERABLE_RAW_GAP",
                    detected,
                ),
            )
