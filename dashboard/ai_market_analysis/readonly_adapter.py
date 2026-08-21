"""Bounded, canonical-only CVD/OI access for AI market analysis.

The Workspace flow history and the AI report must be derived from the same
canonical one-minute observations.  This adapter deliberately does *not* fall
back to the historical ``*_aggregates`` tables: a missing legacy 4H row is not
evidence that flow is unavailable.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sqlite3
from typing import Any

from dashboard.canonical_flow_contract import VALID_FLOW_STATUSES, flow_status
from .versions import ORDERFLOW_RESOLUTIONS, SUPPORTED_INSTRUMENTS


MAX_ORDERFLOW_QUERY_SECONDS = 366 * 86400
MINUTE_SECONDS = 60
RESOLUTION_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400,
    "1D": 86400,
}
VALID_STATUSES = VALID_FLOW_STATUSES


class ReadOnlyOrderflowAdapter:
    """Read canonical 1m facts and deterministically resample them.

    Each returned row carries lower-resolution evidence boundaries and the
    number of contributors. Rows for genuine gaps contain no fabricated value;
    ``PARTIAL_AFTER_GAP`` retains observed CVD delta but is never complete.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.query_plans: list[tuple[Any, ...]] = []

    def read(
        self, instrument: str, start: int, end: int, resolution: str = "15m",
    ) -> dict[str, Any]:
        if instrument not in SUPPORTED_INSTRUMENTS:
            raise ValueError("unsupported instrument")
        if resolution not in ORDERFLOW_RESOLUTIONS:
            raise ValueError("unsupported resolution")
        if end <= start or end - start > MAX_ORDERFLOW_QUERY_SECONDS:
            raise ValueError("query range must be positive and bounded to 366 days")

        with self._connect() as connection:
            self._require_canonical_schema(connection)
            cvd = self._read_cvd(connection, instrument, start, end, resolution)
            oi = self._read_oi(connection, instrument, start, end, resolution)
            metadata = self._metadata(connection, instrument, start, end, resolution)
        return {
            "cvd": cvd, "oi": oi, "basis": [], "funding": [],
            "liquidation": [], "liquidation_complete": False,
            "canonical_metadata": metadata,
        }

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @classmethod
    def available(cls, path: Path | str) -> bool:
        """Whether *path* is a usable canonical CVD/OI source (read-only)."""
        candidate = Path(path)
        if not candidate.exists():
            return False
        try:
            adapter = cls(candidate)
            connection = adapter._connect()
            try:
                adapter._require_canonical_schema(connection)
            finally:
                connection.close()
            return True
        except (OSError, sqlite3.Error, RuntimeError):
            return False

    @staticmethod
    def _require_canonical_schema(connection: sqlite3.Connection) -> None:
        required = {"cvd_1m", "oi_1m"}
        present = {str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = sorted(required - present)
        if missing:
            raise RuntimeError(
                "canonical microstructure source unavailable; missing "
                + ", ".join(missing)
            )

    def _query(
        self, connection: sqlite3.Connection, sql: str, params: tuple[Any, ...],
        table: str,
    ) -> list[sqlite3.Row]:
        plan = [tuple(row) for row in connection.execute(
            "EXPLAIN QUERY PLAN " + sql, params
        )]
        self._assert_indexed(plan, table)
        self.query_plans.extend(plan)
        return list(connection.execute(sql, params))

    def _read_cvd(
        self, connection: sqlite3.Connection, instrument: str, start: int,
        end: int, resolution: str,
    ) -> list[dict[str, Any]]:
        rows = self._query(
            connection,
            """SELECT bucket_ms,buy_volume,sell_volume,signed_delta,trade_count,
                      source_min_ts_ms,source_max_ts_ms,source_row_count,
                      source_fingerprint,status,gap_reason,daily_cumulative
                 FROM cvd_1m WHERE instrument=? AND bucket_ms>=? AND bucket_ms<?
                 ORDER BY bucket_ms""",
            (instrument, self._lower_bound(start, resolution) * 1000,
             self._upper_bound(end, resolution) * 1000),
            "cvd_1m",
        )
        return self._aggregate_cvd(rows, start, end, resolution)

    def _read_oi(
        self, connection: sqlite3.Connection, instrument: str, start: int,
        end: int, resolution: str,
    ) -> list[dict[str, Any]]:
        rows = self._query(
            connection,
            """SELECT bucket_ms,confirmed_oi,observation_ts_ms,observation_count,
                      source_fingerprint,status,gap_reason
                 FROM oi_1m WHERE instrument=? AND bucket_ms>=? AND bucket_ms<?
                 ORDER BY bucket_ms""",
            (instrument, self._lower_bound(start, resolution) * 1000,
             self._upper_bound(end, resolution) * 1000),
            "oi_1m",
        )
        return self._aggregate_oi(rows, start, end, resolution)

    @staticmethod
    def _lower_bound(start: int, resolution: str) -> int:
        width = RESOLUTION_SECONDS[resolution]
        return start // width * width

    @staticmethod
    def _upper_bound(end: int, resolution: str) -> int:
        width = RESOLUTION_SECONDS[resolution]
        return ((end + width - 1) // width) * width

    @staticmethod
    def _status(rows: list[sqlite3.Row], expected: int, value_key: str) -> tuple[str, str | None]:
        if not rows:
            return "MISSING", "NO_CANONICAL_1M_OBSERVATIONS"
        statuses = {str(row["status"]) for row in rows}
        complete = len(rows) == expected and all(
            row[value_key] is not None and str(row["status"]) in VALID_STATUSES
            for row in rows
        )
        if complete:
            return "VALID", None
        if (len(rows) == expected and "PARTIAL_AFTER_GAP" in statuses
                and statuses <= VALID_STATUSES | {"PARTIAL_AFTER_GAP"}
                and all(row[value_key] is not None for row in rows)):
            return "PARTIAL_AFTER_GAP", "EARLIER_CANONICAL_GAP"
        if any(status in {"MISSING", "UNRECOVERABLE_RAW_GAP", "SOURCE_UNAVAILABLE"}
               for status in statuses) or len(rows) < expected:
            return "GAP", "MISSING_CANONICAL_1M_OBSERVATION"
        return "GAP", "INCOMPLETE_CANONICAL_1M_OBSERVATION"

    @staticmethod
    def _group(
        rows: list[sqlite3.Row], start: int, end: int, resolution: str,
    ) -> list[tuple[int, list[sqlite3.Row]]]:
        width = RESOLUTION_SECONDS[resolution]
        by_bucket: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            by_bucket[int(row["bucket_ms"]) // 1000 // width * width].append(row)
        first = start // width * width
        last = (end - 1) // width * width
        return [(bucket, by_bucket.get(bucket, []))
                for bucket in range(first, last + 1, width)]

    def _aggregate_cvd(
        self, rows: list[sqlite3.Row], start: int, end: int, resolution: str,
    ) -> list[dict[str, Any]]:
        expected = RESOLUTION_SECONDS[resolution] // MINUTE_SECONDS
        output = []
        for bucket, children in self._group(rows, start, end, resolution):
            status, reason = self._status(children, expected, "signed_delta")
            observed = [row for row in children if row["signed_delta"] is not None]
            delta = (sum(float(row["signed_delta"]) for row in observed)
                     if observed else None)
            output.append({
                "timestamp": bucket, "bucket_timestamp": bucket,
                "resolution": resolution, "status": status,
                "gap": status in {"GAP", "MISSING"}, "gap_reason": reason,
                "flow_status": flow_status(status, delta is not None),
                "signed_delta": delta, "delta": delta,
                "buy_notional": sum(float(row["buy_volume"] or 0) for row in observed),
                "sell_notional": sum(float(row["sell_volume"] or 0) for row in observed),
                "trade_count": sum(int(row["trade_count"] or 0) for row in observed),
                "source_bucket_count": len(observed),
                "source_bucket_timestamps": [int(row["bucket_ms"]) // 1000 for row in children],
                "source_start_timestamp": min((int(row["source_min_ts_ms"]) // 1000
                                                 for row in observed if row["source_min_ts_ms"] is not None), default=None),
                "source_end_timestamp": max((int(row["source_max_ts_ms"]) // 1000
                                               for row in observed if row["source_max_ts_ms"] is not None), default=None),
                "source": "canonical_microstructure_1m",
                "synthetic_data": False, "interpolation": False,
            })
        return output

    def _aggregate_oi(
        self, rows: list[sqlite3.Row], start: int, end: int, resolution: str,
    ) -> list[dict[str, Any]]:
        expected = RESOLUTION_SECONDS[resolution] // MINUTE_SECONDS
        output = []
        for bucket, children in self._group(rows, start, end, resolution):
            status, reason = self._status(children, expected, "confirmed_oi")
            observed = [row for row in children if row["confirmed_oi"] is not None]
            last = observed[-1] if observed else None
            value = float(last["confirmed_oi"]) if last else None
            output.append({
                "timestamp": bucket, "bucket_timestamp": bucket,
                "resolution": resolution, "status": status,
                "gap": status in {"GAP", "MISSING"}, "gap_reason": reason,
                "flow_status": flow_status(status, value is not None),
                "confirmed_oi": value, "last_value": value, "value": value,
                "observation_count": sum(int(row["observation_count"] or 0) for row in observed),
                "source_bucket_count": len(observed),
                "source_bucket_timestamps": [int(row["bucket_ms"]) // 1000 for row in children],
                "source_end_timestamp": (int(last["observation_ts_ms"]) // 1000
                                         if last and last["observation_ts_ms"] is not None else None),
                "source": "canonical_microstructure_1m",
                "synthetic_data": False, "interpolation": False,
            })
        return output

    @staticmethod
    def _metadata(
        connection: sqlite3.Connection, instrument: str, start: int, end: int,
        resolution: str,
    ) -> dict[str, Any]:
        metadata = ({str(row["key"]): str(row["value_json"]) for row in connection.execute(
            "SELECT key,value_json FROM canonical_metadata WHERE key IN "
            "('history_version','generated_commit','source_watermark_ms')"
        )} if _exists(connection, "canonical_metadata") else {})
        return {
            "source_contract": "canonical-microstructure-1m-resample-v1",
            "instrument": instrument, "start": start, "end": end,
            "requested_resolution": resolution, "source_resolution": "1m",
            "synthetic_data": False, "interpolation": False,
            "canonical_metadata": metadata,
        }

    @staticmethod
    def _assert_indexed(plan: list[tuple[Any, ...]], table: str) -> None:
        detail = " ".join(str(row[-1]).upper() for row in plan)
        if f"SCAN {table.upper()}" in detail and "USING INDEX" not in detail and "USING COVERING INDEX" not in detail:
            raise RuntimeError(f"unbounded/full table scan rejected: {detail}")


def _exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None
