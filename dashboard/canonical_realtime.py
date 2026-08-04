"""Bounded realtime continuation writer for a prebuilt canonical history DB."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical_microstructure_history import (
    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
    RESOLUTION_MS,
    aggregate_quality,
    fingerprint,
    now_ms,
)


MINUTE_MS = 60_000
VALID_INPUT = {"VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"}


class CanonicalRealtimeWriter:
    """Append completed facts only; schema creation and history scans are forbidden."""

    def __init__(
        self, source_path: Path | str, canonical_path: Path | str,
        generated_commit: str,
    ) -> None:
        self.source_path = Path(source_path)
        self.canonical_path = Path(canonical_path)
        self.generated_commit = generated_commit
        with self._canonical() as connection:
            row = connection.execute(
                "SELECT value_json FROM canonical_metadata WHERE key='history_version'"
            ).fetchone()
            if row is None or json.loads(row[0]) != CANONICAL_MICROSTRUCTURE_HISTORY_VERSION:
                raise RuntimeError("canonical history DB is not migrated to v1")
            watermark = connection.execute(
                "SELECT value_json FROM canonical_metadata WHERE key='source_watermark_ms'"
            ).fetchone()
            if watermark is None:
                raise RuntimeError("canonical source watermark is missing")
            self.immutable_watermark_ms = int(json.loads(watermark[0]))

    def _canonical(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.canonical_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _source(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.source_path.resolve().as_posix()}?mode=ro", uri=True,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _metadata(
        source: sqlite3.Connection, instrument: str, series: str, bucket: int,
    ) -> sqlite3.Row | None:
        return source.execute(
            """SELECT * FROM realtime_aggregate_fingerprints
               WHERE instrument=? AND series=? AND resolution='1m'
                 AND bucket_ms=?""",
            (instrument, series, bucket),
        ).fetchone()

    @staticmethod
    def _insert_once(
        output: sqlite3.Connection, table: str, key: tuple[Any, ...],
        fingerprint_value: str | None, values: tuple[Any, ...],
    ) -> None:
        where = "instrument=? AND bucket_ms=?"
        existing = output.execute(
            f"SELECT source_fingerprint,status FROM {table} WHERE {where}", key,
        ).fetchone()
        if existing is not None:
            if existing["source_fingerprint"] == fingerprint_value:
                return
            output.execute(
                f"UPDATE {table} SET status='CONFLICT',gap_reason=? WHERE {where}",
                ("REALTIME_FINGERPRINT_CONFLICT", *key),
            )
            raise RuntimeError(f"canonical fingerprint conflict: {table} {key}")
        output.execute(
            f"INSERT INTO {table} VALUES({','.join('?' for _ in values)})", values,
        )

    def _append_cvd(
        self, source: sqlite3.Connection, output: sqlite3.Connection,
        instrument: str, bucket: int, generated_at: int,
    ) -> None:
        metadata = self._metadata(source, instrument, "cvd", bucket)
        aggregate = source.execute(
            """SELECT * FROM cvd_aggregates WHERE instrument=?
               AND resolution='1m' AND bucket_ms=?""", (instrument, bucket),
        ).fetchone()
        status = str(metadata["status"]) if metadata is not None else "MISSING"
        source_hash = (str(metadata["source_fingerprint"])
                       if metadata is not None and metadata["source_fingerprint"] else None)
        reason = None
        if aggregate is None or status != "VALID":
            reason = "NO_CONFIRMED_REALTIME_RAW" if status == "MISSING" else status
            values = (instrument, bucket, "1m", None, None, None, 0, None,
                      None, 0, source_hash, None,
                      datetime.fromtimestamp(bucket / 1000, timezone.utc).date().isoformat(),
                      status if status in {"MISSING", "CONFLICT"} else "MISSING",
                      reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                      self.generated_commit, generated_at)
            self._insert_once(output, "cvd_1m", (instrument, bucket), source_hash, values)
            return
        delta = float(aggregate["delta"])
        if bucket % 86_400_000 == 0:
            daily, quality = delta, "VALID"
        else:
            prior = output.execute(
                """SELECT daily_cumulative,status FROM cvd_1m
                   WHERE instrument=? AND bucket_ms=?""",
                (instrument, bucket - MINUTE_MS),
            ).fetchone()
            if (prior is None or prior["daily_cumulative"] is None
                    or str(prior["status"]) not in VALID_INPUT):
                daily, quality = None, "PARTIAL_AFTER_GAP"
                reason = "EARLIER_RAW_GAP_SAME_UTC_DAY"
            else:
                daily, quality = float(prior["daily_cumulative"]) + delta, "VALID"
        values = (
            instrument, bucket, "1m", float(aggregate["buy_notional"]),
            float(aggregate["sell_notional"]), delta,
            int(aggregate["observation_count"]),
            int(aggregate["first_source_ts_ms"]), int(aggregate["last_source_ts_ms"]),
            int(aggregate["observation_count"]), source_hash, daily,
            datetime.fromtimestamp(bucket / 1000, timezone.utc).date().isoformat(),
            quality, reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
            self.generated_commit, generated_at,
        )
        self._insert_once(output, "cvd_1m", (instrument, bucket), source_hash, values)

    def _append_oi(
        self, source: sqlite3.Connection, output: sqlite3.Connection,
        instrument: str, bucket: int, generated_at: int,
    ) -> None:
        metadata = self._metadata(source, instrument, "oi", bucket)
        aggregate = source.execute(
            """SELECT * FROM oi_aggregates WHERE instrument=?
               AND resolution='1m' AND bucket_ms=?""", (instrument, bucket),
        ).fetchone()
        status = str(metadata["status"]) if metadata is not None else "MISSING"
        source_hash = (str(metadata["source_fingerprint"])
                       if metadata is not None and metadata["source_fingerprint"] else None)
        if aggregate is None or status != "VALID":
            values = (instrument, bucket, "1m", None, None, 0, source_hash,
                      status if status in {"MISSING", "CONFLICT"} else "MISSING",
                      "NO_CONFIRMED_REALTIME_OI", CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                      generated_at)
        else:
            values = (instrument, bucket, "1m", float(aggregate["last_value"]),
                      int(aggregate["last_source_ts_ms"]),
                      int(aggregate["observation_count"]), source_hash, "VALID", None,
                      CANONICAL_MICROSTRUCTURE_HISTORY_VERSION, generated_at)
        self._insert_once(output, "oi_1m", (instrument, bucket), source_hash, values)

    def _derive(
        self, output: sqlite3.Connection, instrument: str, resolution: str,
        bucket: int, generated_at: int,
    ) -> None:
        width = RESOLUTION_MS[resolution]
        expected = width // MINUTE_MS
        cvd = list(output.execute(
            """SELECT * FROM cvd_1m WHERE instrument=? AND bucket_ms>=?
               AND bucket_ms<? ORDER BY bucket_ms""",
            (instrument, bucket, bucket + width),
        ))
        oi = list(output.execute(
            """SELECT * FROM oi_1m WHERE instrument=? AND bucket_ms>=?
               AND bucket_ms<? ORDER BY bucket_ms""",
            (instrument, bucket, bucket + width),
        ))
        cvd_quality, cvd_reason = aggregate_quality([str(row["status"]) for row in cvd])
        cvd_usable = (len(cvd) == expected and cvd_quality in VALID_INPUT
                      and all(row["signed_delta"] is not None for row in cvd))
        if not cvd_usable and cvd_quality == "VALID":
            cvd_quality, cvd_reason = "PARTIAL", "MISSING_REQUIRED_1M_BUCKET"
        output.execute("DELETE FROM cvd_higher_timeframes WHERE instrument=? AND resolution=? AND bucket_ms=?",
                       (instrument, resolution, bucket))
        output.execute(
            "INSERT INTO cvd_higher_timeframes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (instrument, resolution, bucket,
             sum(float(row["buy_volume"]) for row in cvd) if cvd_usable else None,
             sum(float(row["sell_volume"]) for row in cvd) if cvd_usable else None,
             sum(float(row["signed_delta"]) for row in cvd) if cvd_usable else None,
             sum(int(row["trade_count"]) for row in cvd) if cvd_usable else 0,
             min(int(row["source_min_ts_ms"]) for row in cvd) if cvd_usable else None,
             max(int(row["source_max_ts_ms"]) for row in cvd) if cvd_usable else None,
             sum(int(row["source_row_count"]) for row in cvd) if cvd_usable else 0,
             fingerprint([row["source_fingerprint"] for row in cvd]) if cvd_usable else None,
             cvd[-1]["daily_cumulative"] if cvd_usable else None,
             cvd_quality, cvd_reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
             self.generated_commit, generated_at),
        )
        oi_quality, oi_reason = aggregate_quality([str(row["status"]) for row in oi])
        oi_usable = (len(oi) == expected and oi_quality in VALID_INPUT
                     and all(row["confirmed_oi"] is not None for row in oi))
        if not oi_usable and oi_quality == "VALID":
            oi_quality, oi_reason = "PARTIAL", "MISSING_REQUIRED_1M_BUCKET"
        output.execute("DELETE FROM oi_higher_timeframes WHERE instrument=? AND resolution=? AND bucket_ms=?",
                       (instrument, resolution, bucket))
        last = oi[-1] if oi_usable else None
        output.execute(
            "INSERT INTO oi_higher_timeframes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (instrument, resolution, bucket,
             last["confirmed_oi"] if last else None,
             last["observation_ts_ms"] if last else None,
             sum(int(row["observation_count"]) for row in oi) if oi_usable else 0,
             fingerprint([row["source_fingerprint"] for row in oi]) if oi_usable else None,
             oi_quality, oi_reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
             generated_at),
        )

    def sync(self, instrument: str, start_ms: int, end_ms: int) -> dict[str, int]:
        start = start_ms // MINUTE_MS * MINUTE_MS
        end = end_ms // MINUTE_MS * MINUTE_MS
        lower = max(start, (self.immutable_watermark_ms // MINUTE_MS + 1) * MINUTE_MS)
        if end <= lower:
            return {"minutes": 0, "higher_buckets": 0}
        generated_at = now_ms()
        with self._source() as source, self._canonical() as output:
            for bucket in range(lower, end, MINUTE_MS):
                self._append_cvd(source, output, instrument, bucket, generated_at)
                self._append_oi(source, output, instrument, bucket, generated_at)
            higher = 0
            for resolution, width in RESOLUTION_MS.items():
                if resolution == "1m":
                    continue
                first = lower // width * width
                for bucket in range(first, end, width):
                    if bucket + width <= end:
                        self._derive(output, instrument, resolution, bucket, generated_at)
                        higher += 1
            output.execute(
                """INSERT INTO rebuild_checkpoints VALUES(?,?,?,?,?,?)
                   ON CONFLICT(stage,instrument) DO UPDATE SET
                   cursor_ms=excluded.cursor_ms,status=excluded.status,
                   detail_json=excluded.detail_json,updated_at_ms=excluded.updated_at_ms""",
                ("realtime-continuation", instrument, end, "COMPLETE",
                 json.dumps({"start_ms": lower, "end_ms": end}, sort_keys=True),
                 generated_at),
            )
        return {"minutes": (end - lower) // MINUTE_MS, "higher_buckets": higher}
