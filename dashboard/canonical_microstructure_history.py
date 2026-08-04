"""Deterministic, offline canonical CVD/OI history construction.

The source database is always opened read-only.  This module deliberately has
no network client and no production cutover support: official backfills must be
materialised in a separately verified source overlay before they are consumed.
Missing observations remain explicit missing facts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


CANONICAL_MICROSTRUCTURE_HISTORY_VERSION = (
    "canonical-microstructure-history-v1"
)
CANONICAL_SCHEMA_VERSION = "canonical-microstructure-schema-v1"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
QUALITY_STATUSES = (
    "VALID",
    "PARTIAL",
    "PARTIAL_AFTER_GAP",
    "MISSING",
    "UNRECOVERABLE_RAW_GAP",
    "BACKFILLED_OFFICIAL",
    "ARCHIVED_CONFIRMED",
    "CONFLICT",
    "SOURCE_UNAVAILABLE",
)
RESOLUTION_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1D": 86_400_000,
}
SOURCE_TABLES = {
    "trades": "trade_flow_observations",
    "oi": "oi_observations",
    "mark": "mark_price_observations",
    "index": "index_price_observations",
    "funding_predicted": "funding_predicted",
    "funding_settled": "funding_settled",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def utc_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date().isoformat()


def minute_floor(timestamp_ms: int) -> int:
    return timestamp_ms - timestamp_ms % RESOLUTION_MS["1m"]


def completed_bucket_end(as_of_ms: int, width_ms: int) -> int:
    return as_of_ms - as_of_ms % width_ms


def _readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS canonical_metadata(
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_assets(
  instrument TEXT NOT NULL,
  source TEXT NOT NULL,
  source_table TEXT NOT NULL,
  start_ms INTEGER,
  end_ms INTEGER,
  earliest_ms INTEGER,
  latest_ms INTEGER,
  row_count INTEGER NOT NULL,
  unique_identity_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  out_of_order_count INTEGER NOT NULL,
  timestamp_error_count INTEGER NOT NULL,
  trade_id_conflict_count INTEGER NOT NULL,
  source_fingerprint TEXT NOT NULL,
  ingestion_source TEXT NOT NULL,
  ingestion_time_ms INTEGER,
  official_backfill_manifest_id TEXT,
  gap_status TEXT NOT NULL CHECK(gap_status IN
    ('VALID','PARTIAL','PARTIAL_AFTER_GAP','MISSING','UNRECOVERABLE_RAW_GAP',
     'BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED','CONFLICT','SOURCE_UNAVAILABLE')),
  PRIMARY KEY(instrument,source,start_ms,end_ms)
);
CREATE TABLE IF NOT EXISTS coverage_ledger(
  instrument TEXT NOT NULL,
  source TEXT NOT NULL,
  bucket_ms INTEGER NOT NULL,
  expected INTEGER NOT NULL CHECK(expected IN (0,1)),
  observed_count INTEGER NOT NULL,
  unique_identity_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  out_of_order_count INTEGER NOT NULL,
  first_source_ts_ms INTEGER,
  last_source_ts_ms INTEGER,
  source_fingerprint TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('VALID','PARTIAL','PARTIAL_AFTER_GAP','MISSING','UNRECOVERABLE_RAW_GAP',
     'BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED','CONFLICT','SOURCE_UNAVAILABLE')),
  gap_reason TEXT,
  classification TEXT NOT NULL CHECK(classification IN
    ('OBSERVED','TRUE_RAW_GAP','AGGREGATE_ONLY_GAP','STALE_LEDGER_ENTRY',
     'MAINTENANCE_GAP','ARCHIVED_CONFIRMED','SOURCE_HISTORY_BOUNDARY')),
  PRIMARY KEY(instrument,source,bucket_ms)
);
CREATE INDEX IF NOT EXISTS idx_coverage_source_time
  ON coverage_ledger(source,instrument,bucket_ms);
CREATE TABLE IF NOT EXISTS official_backfill_manifests(
  manifest_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  instrument TEXT NOT NULL,
  requested_start_ms INTEGER NOT NULL,
  requested_end_ms INTEGER NOT NULL,
  endpoint_or_file TEXT NOT NULL,
  request_count INTEGER NOT NULL,
  response_sha256 TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  unique_row_count INTEGER NOT NULL,
  overlap_start_ms INTEGER,
  overlap_end_ms INTEGER,
  overlap_status TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cvd_1m(
  instrument TEXT NOT NULL,
  bucket_ms INTEGER NOT NULL,
  resolution TEXT NOT NULL CHECK(resolution='1m'),
  buy_volume REAL,
  sell_volume REAL,
  signed_delta REAL,
  trade_count INTEGER NOT NULL,
  source_min_ts_ms INTEGER,
  source_max_ts_ms INTEGER,
  source_row_count INTEGER NOT NULL,
  source_fingerprint TEXT,
  daily_cumulative REAL,
  utc_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN
    ('VALID','PARTIAL','PARTIAL_AFTER_GAP','MISSING','UNRECOVERABLE_RAW_GAP',
     'BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED','CONFLICT','SOURCE_UNAVAILABLE')),
  gap_reason TEXT,
  generated_version TEXT NOT NULL,
  generated_commit TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  PRIMARY KEY(instrument,bucket_ms)
);
CREATE TABLE IF NOT EXISTS oi_1m(
  instrument TEXT NOT NULL,
  bucket_ms INTEGER NOT NULL,
  resolution TEXT NOT NULL CHECK(resolution='1m'),
  confirmed_oi REAL,
  observation_ts_ms INTEGER,
  observation_count INTEGER NOT NULL,
  source_fingerprint TEXT,
  status TEXT NOT NULL CHECK(status IN
    ('VALID','PARTIAL','PARTIAL_AFTER_GAP','MISSING','UNRECOVERABLE_RAW_GAP',
     'BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED','CONFLICT','SOURCE_UNAVAILABLE')),
  gap_reason TEXT,
  generated_version TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  PRIMARY KEY(instrument,bucket_ms)
);
CREATE TABLE IF NOT EXISTS cvd_higher_timeframes(
  instrument TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK(resolution IN ('5m','15m','1h','4h','1D')),
  bucket_ms INTEGER NOT NULL,
  buy_volume REAL,
  sell_volume REAL,
  signed_delta REAL,
  trade_count INTEGER NOT NULL,
  source_min_ts_ms INTEGER,
  source_max_ts_ms INTEGER,
  source_row_count INTEGER NOT NULL,
  source_fingerprint TEXT,
  cumulative_close REAL,
  status TEXT NOT NULL,
  gap_reason TEXT,
  generated_version TEXT NOT NULL,
  generated_commit TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  PRIMARY KEY(instrument,resolution,bucket_ms)
);
CREATE INDEX IF NOT EXISTS idx_cvd_higher_query
  ON cvd_higher_timeframes(instrument,resolution,bucket_ms);
CREATE TABLE IF NOT EXISTS oi_higher_timeframes(
  instrument TEXT NOT NULL,
  resolution TEXT NOT NULL CHECK(resolution IN ('5m','15m','1h','4h','1D')),
  bucket_ms INTEGER NOT NULL,
  confirmed_oi REAL,
  observation_ts_ms INTEGER,
  observation_count INTEGER NOT NULL,
  source_fingerprint TEXT,
  status TEXT NOT NULL,
  gap_reason TEXT,
  generated_version TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  PRIMARY KEY(instrument,resolution,bucket_ms)
);
CREATE INDEX IF NOT EXISTS idx_oi_higher_query
  ON oi_higher_timeframes(instrument,resolution,bucket_ms);
CREATE TABLE IF NOT EXISTS daily_reconciliation(
  instrument TEXT NOT NULL,
  series TEXT NOT NULL CHECK(series IN ('cvd','oi')),
  utc_date TEXT NOT NULL,
  source_row_count INTEGER NOT NULL,
  buy_volume REAL,
  sell_volume REAL,
  delta_sum REAL,
  final_cumulative REAL,
  first_value REAL,
  last_value REAL,
  source_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY(instrument,series,utc_date)
);
CREATE TABLE IF NOT EXISTS rebuild_checkpoints(
  stage TEXT NOT NULL,
  instrument TEXT NOT NULL,
  cursor_ms INTEGER,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY(stage,instrument)
);
CREATE TABLE IF NOT EXISTS migration_manifest(
  manifest_id TEXT PRIMARY KEY,
  source_db_sha256 TEXT NOT NULL,
  shadow_db_sha256 TEXT,
  code_commit TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  history_version TEXT NOT NULL,
  source_watermark_ms INTEGER NOT NULL,
  expected_counts_json TEXT NOT NULL,
  expected_coverage_json TEXT NOT NULL,
  rollback_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class BuildIdentity:
    source_sha256: str
    generated_commit: str
    source_watermark_ms: int
    generated_at_ms: int


class CanonicalHistoryStore:
    """Single-writer owner for a canonical shadow database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialise(self, identity: BuildIdentity) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            metadata = {
                "schema_version": CANONICAL_SCHEMA_VERSION,
                "history_version": CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                "quality_statuses": QUALITY_STATUSES,
                "source_sha256": identity.source_sha256,
                "source_watermark_ms": identity.source_watermark_ms,
                "generated_commit": identity.generated_commit,
            }
            for key, value in metadata.items():
                connection.execute(
                    "INSERT OR REPLACE INTO canonical_metadata VALUES(?,?)",
                    (key, canonical_json(value)),
                )

    def checkpoint(
        self, connection: sqlite3.Connection, stage: str, instrument: str,
        cursor_ms: int | None, status: str, detail: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO rebuild_checkpoints VALUES(?,?,?,?,?,?)
               ON CONFLICT(stage,instrument) DO UPDATE SET
               cursor_ms=excluded.cursor_ms,status=excluded.status,
               detail_json=excluded.detail_json,updated_at_ms=excluded.updated_at_ms""",
            (stage, instrument, cursor_ms, status, canonical_json(detail), now_ms()),
        )


def iter_minutes(start_ms: int, end_ms: int) -> Iterator[int]:
    cursor = minute_floor(start_ms)
    final = minute_floor(end_ms)
    while cursor <= final:
        yield cursor
        cursor += RESOLUTION_MS["1m"]


def status_for_gap(reason: str | None) -> str:
    if reason == "OFFICIAL_SOURCE_UNAVAILABLE":
        return "UNRECOVERABLE_RAW_GAP"
    if reason == "SOURCE_HISTORY_BOUNDARY":
        return "SOURCE_UNAVAILABLE"
    return "MISSING"


def aggregate_quality(statuses: Sequence[str]) -> tuple[str, str | None]:
    unique = set(statuses)
    if not unique:
        return "MISSING", "NO_1M_INPUT"
    if unique <= {"VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"}:
        if "BACKFILLED_OFFICIAL" in unique:
            return "BACKFILLED_OFFICIAL", None
        if "ARCHIVED_CONFIRMED" in unique:
            return "ARCHIVED_CONFIRMED", None
        return "VALID", None
    if "CONFLICT" in unique:
        return "CONFLICT", "CONFLICTING_1M_INPUT"
    if "UNRECOVERABLE_RAW_GAP" in unique:
        return "UNRECOVERABLE_RAW_GAP", "UNRECOVERABLE_1M_INPUT"
    if "SOURCE_UNAVAILABLE" in unique:
        return "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE_1M_INPUT"
    return "PARTIAL", "INCOMPLETE_1M_INPUT"


class CanonicalHistoryBuilder:
    """Rebuild canonical facts from a frozen source DB into a shadow DB."""

    def __init__(
        self, source_path: Path | str, destination_path: Path | str,
        identity: BuildIdentity,
    ) -> None:
        self.source_path = Path(source_path)
        self.destination = CanonicalHistoryStore(destination_path)
        self.identity = identity
        self.destination.initialise(identity)

    def _source_bounds(
        self, source: sqlite3.Connection, table: str, instrument: str,
    ) -> tuple[int, int, int] | None:
        row = source.execute(
            f"""SELECT MIN(source_ts_ms),MAX(source_ts_ms),COUNT(*)
                FROM {table} WHERE instrument=?""", (instrument,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0]), int(row[1]), int(row[2])

    def build_coverage(self, source_name: str, instrument: str) -> dict[str, Any]:
        """Create a factual minute ledger; old collection_gaps are not inputs."""
        table = SOURCE_TABLES[source_name]
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            bounds = self._source_bounds(source, table, instrument)
            out.execute(
                "DELETE FROM coverage_ledger WHERE instrument=? AND source=?",
                (instrument, source_name),
            )
            out.execute(
                "DELETE FROM source_assets WHERE instrument=? AND source=?",
                (instrument, source_name),
            )
            if bounds is None:
                out.execute(
                    """INSERT INTO source_assets VALUES(
                       ?,?,?,NULL,NULL,NULL,NULL,0,0,0,0,0,0,?,?,?,?,?)""",
                    (instrument, source_name, table, fingerprint([]), "local-frozen-db",
                     None, None, "SOURCE_UNAVAILABLE"),
                )
                return {"instrument": instrument, "source": source_name,
                        "row_count": 0, "status": "SOURCE_UNAVAILABLE"}
            earliest, latest, total_rows = bounds
            rows = source.execute(
                f"""SELECT source_ts_ms,uniqueness_key
                    FROM {table} WHERE instrument=?
                    ORDER BY source_ts_ms,uniqueness_key""", (instrument,),
            )
            observed: dict[int, dict[str, Any]] = {}
            asset_hash = hashlib.sha256()
            for row in rows:
                timestamp = int(row[0])
                identity = str(row[1])
                bucket = minute_floor(timestamp)
                cell = observed.setdefault(bucket, {
                    "count": 0, "first": timestamp, "last": timestamp,
                    "identities": set(), "hash": hashlib.sha256(),
                })
                cell["count"] += 1
                cell["first"] = min(cell["first"], timestamp)
                cell["last"] = max(cell["last"], timestamp)
                cell["identities"].add(identity)
                encoded = canonical_json([timestamp, identity]).encode()
                cell["hash"].update(encoded)
                asset_hash.update(encoded)
            inserts = []
            missing = 0
            for bucket in iter_minutes(earliest, latest):
                cell = observed.get(bucket)
                if cell is None:
                    missing += 1
                    inserts.append((
                        instrument, source_name, bucket, 1, 0, 0, 0, 0,
                        None, None, None, "MISSING", "NO_RAW_OBSERVATION",
                        "TRUE_RAW_GAP",
                    ))
                else:
                    duplicate_count = cell["count"] - len(cell["identities"])
                    status = "CONFLICT" if duplicate_count else "VALID"
                    inserts.append((
                        instrument, source_name, bucket, 1, cell["count"],
                        len(cell["identities"]), duplicate_count, 0,
                        cell["first"], cell["last"], cell["hash"].hexdigest(),
                        status, "DUPLICATE_IDENTITY" if duplicate_count else None,
                        "OBSERVED",
                    ))
            out.executemany(
                "INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                inserts,
            )
            status = "PARTIAL" if missing else "VALID"
            out.execute(
                """INSERT INTO source_assets VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (instrument, source_name, table, minute_floor(earliest),
                 minute_floor(latest), earliest, latest, total_rows, total_rows,
                 0, 0, 0, 0, asset_hash.hexdigest(), "local-frozen-db", None,
                 None, status),
            )
            self.destination.checkpoint(
                out, f"coverage:{source_name}", instrument, latest, "COMPLETE",
                {"row_count": total_rows, "missing_minutes": missing},
            )
            return {"instrument": instrument, "source": source_name,
                    "earliest_ms": earliest, "latest_ms": latest,
                    "row_count": total_rows, "missing_minutes": missing,
                    "status": status}

    def build_cvd_1m(self, instrument: str) -> dict[str, Any]:
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            ledger = {
                int(row["bucket_ms"]): row for row in out.execute(
                    """SELECT * FROM coverage_ledger
                       WHERE instrument=? AND source='trades' ORDER BY bucket_ms""",
                    (instrument,),
                )
            }
            if not ledger:
                raise ValueError(f"trade coverage is not built for {instrument}")
            out.execute("DELETE FROM cvd_1m WHERE instrument=?", (instrument,))
            grouped: dict[int, dict[str, Any]] = {}
            rows = source.execute(
                """SELECT source_ts_ms,trade_id,side,size,contract_value,
                          uniqueness_key,state
                   FROM trade_flow_observations WHERE instrument=?
                   ORDER BY source_ts_ms,trade_id,uniqueness_key""", (instrument,),
            )
            for row in rows:
                bucket = minute_floor(int(row[0]))
                cell = grouped.setdefault(bucket, {
                    "buy": 0.0, "sell": 0.0, "count": 0,
                    "first": int(row[0]), "last": int(row[0]),
                    "hash": hashlib.sha256(),
                })
                volume = float(row[3]) * float(row[4])
                side = str(row[2]).lower()
                if side == "buy":
                    cell["buy"] += volume
                elif side == "sell":
                    cell["sell"] += volume
                else:
                    raise ValueError(f"unsupported canonical trade side {side!r}")
                cell["count"] += 1
                cell["first"] = min(cell["first"], int(row[0]))
                cell["last"] = max(cell["last"], int(row[0]))
                cell["hash"].update(canonical_json(list(row)).encode())
            current_date = None
            cumulative: float | None = 0.0
            day_complete = True
            inserts = []
            for bucket, coverage in ledger.items():
                date = utc_date(bucket)
                if date != current_date:
                    current_date, cumulative, day_complete = date, 0.0, True
                cell = grouped.get(bucket)
                status = str(coverage["status"])
                reason = coverage["gap_reason"]
                if cell is None or status not in {
                    "VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"
                }:
                    day_complete = False
                    cumulative = None
                    inserts.append((instrument, bucket, "1m", None, None, None,
                                    0, None, None, 0, None, None, date, status,
                                    reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                                    self.identity.generated_commit,
                                    self.identity.generated_at_ms))
                    continue
                delta = cell["buy"] - cell["sell"]
                output_status = status if day_complete else "PARTIAL_AFTER_GAP"
                if cumulative is None:
                    daily = None
                else:
                    cumulative += delta
                    daily = cumulative
                inserts.append((
                    instrument, bucket, "1m", cell["buy"], cell["sell"], delta,
                    cell["count"], cell["first"], cell["last"], cell["count"],
                    cell["hash"].hexdigest(), daily, date, output_status,
                    None if day_complete else "EARLIER_RAW_GAP_SAME_UTC_DAY",
                    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                    self.identity.generated_commit, self.identity.generated_at_ms,
                ))
            out.executemany(
                "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                inserts,
            )
            self._reconcile_cvd_days(out, instrument)
            self.destination.checkpoint(
                out, "cvd:1m", instrument, max(ledger), "COMPLETE",
                {"rows": len(inserts)},
            )
            return {"instrument": instrument, "rows": len(inserts)}

    def _reconcile_cvd_days(
        self, out: sqlite3.Connection, instrument: str,
    ) -> None:
        out.execute(
            "DELETE FROM daily_reconciliation WHERE instrument=? AND series='cvd'",
            (instrument,),
        )
        dates = [row[0] for row in out.execute(
            "SELECT DISTINCT utc_date FROM cvd_1m WHERE instrument=? ORDER BY utc_date",
            (instrument,),
        )]
        for date in dates:
            rows = list(out.execute(
                """SELECT bucket_ms,buy_volume,sell_volume,signed_delta,trade_count,
                          source_fingerprint,daily_cumulative,status
                   FROM cvd_1m WHERE instrument=? AND utc_date=? ORDER BY bucket_ms""",
                (instrument, date),
            ))
            statuses = [str(row[7]) for row in rows]
            quality, _ = aggregate_quality(statuses)
            valid = [row for row in rows if row[3] is not None]
            final = next((row[6] for row in reversed(rows)
                          if row[6] is not None), None)
            out.execute(
                "INSERT INTO daily_reconciliation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (instrument, "cvd", date, sum(int(row[4]) for row in valid),
                 sum(float(row[1]) for row in valid),
                 sum(float(row[2]) for row in valid),
                 sum(float(row[3]) for row in valid), final, None, None,
                 fingerprint([list(row) for row in rows]), quality),
            )

    def build_oi_1m(self, instrument: str) -> dict[str, Any]:
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            ledger = {
                int(row["bucket_ms"]): row for row in out.execute(
                    """SELECT * FROM coverage_ledger
                       WHERE instrument=? AND source='oi' ORDER BY bucket_ms""",
                    (instrument,),
                )
            }
            if not ledger:
                raise ValueError(f"OI coverage is not built for {instrument}")
            rows = source.execute(
                """SELECT source_ts_ms,oi_contracts,oi_currency,oi_usd,
                          uniqueness_key,state
                   FROM oi_observations WHERE instrument=? AND state='confirmed'
                   ORDER BY source_ts_ms,uniqueness_key""", (instrument,),
            )
            grouped: dict[int, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(minute_floor(int(row[0])), []).append(row)
            out.execute("DELETE FROM oi_1m WHERE instrument=?", (instrument,))
            inserts = []
            for bucket, coverage in ledger.items():
                observations = grouped.get(bucket, [])
                status = str(coverage["status"])
                if not observations or status not in {
                    "VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"
                }:
                    inserts.append((
                        instrument, bucket, "1m", None, None, 0, None, status,
                        coverage["gap_reason"],
                        CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                        self.identity.generated_at_ms,
                    ))
                    continue
                last = observations[-1]
                # Project convention: oi_usd, then oi_currency, then contracts.
                value = next((float(value) for value in last[3:0:-1]
                              if value is not None), None)
                inserts.append((
                    instrument, bucket, "1m", value, int(last[0]),
                    len(observations), fingerprint([list(row) for row in observations]),
                    status, None, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                    self.identity.generated_at_ms,
                ))
            out.executemany(
                "INSERT INTO oi_1m VALUES(?,?,?,?,?,?,?,?,?,?,?)", inserts,
            )
            self._reconcile_oi_days(out, instrument)
            self.destination.checkpoint(
                out, "oi:1m", instrument, max(ledger), "COMPLETE",
                {"rows": len(inserts)},
            )
            return {"instrument": instrument, "rows": len(inserts)}

    def _reconcile_oi_days(
        self, out: sqlite3.Connection, instrument: str,
    ) -> None:
        out.execute(
            "DELETE FROM daily_reconciliation WHERE instrument=? AND series='oi'",
            (instrument,),
        )
        dates = [row[0] for row in out.execute(
            "SELECT DISTINCT substr(datetime(bucket_ms/1000,'unixepoch'),1,10) "
            "FROM oi_1m WHERE instrument=? ORDER BY bucket_ms", (instrument,),
        )]
        for date in dates:
            start = int(datetime.fromisoformat(date).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            rows = list(out.execute(
                """SELECT bucket_ms,confirmed_oi,observation_ts_ms,
                          observation_count,source_fingerprint,status
                   FROM oi_1m WHERE instrument=? AND bucket_ms>=? AND bucket_ms<?
                   ORDER BY bucket_ms""", (instrument, start, start + 86_400_000),
            ))
            valid = [row for row in rows if row[1] is not None]
            quality, _ = aggregate_quality([str(row[5]) for row in rows])
            out.execute(
                "INSERT INTO daily_reconciliation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (instrument, "oi", date,
                 sum(int(row[3]) for row in valid), None, None, None, None,
                 valid[0][1] if valid else None, valid[-1][1] if valid else None,
                 fingerprint([list(row) for row in rows]), quality),
            )

    def derive_higher_timeframes(self, instrument: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.destination.connect() as out:
            out.execute("DELETE FROM cvd_higher_timeframes WHERE instrument=?",
                        (instrument,))
            out.execute("DELETE FROM oi_higher_timeframes WHERE instrument=?",
                        (instrument,))
            for resolution, width in RESOLUTION_MS.items():
                if resolution == "1m":
                    continue
                last_completed = completed_bucket_end(
                    self.identity.source_watermark_ms, width) - width
                cvd_rows = list(out.execute(
                    """SELECT * FROM cvd_1m WHERE instrument=? AND bucket_ms<=?
                       ORDER BY bucket_ms""", (instrument, last_completed + width - 60_000),
                ))
                oi_rows = list(out.execute(
                    """SELECT * FROM oi_1m WHERE instrument=? AND bucket_ms<=?
                       ORDER BY bucket_ms""", (instrument, last_completed + width - 60_000),
                ))
                cvd_groups: dict[int, list[sqlite3.Row]] = {}
                oi_groups: dict[int, list[sqlite3.Row]] = {}
                for row in cvd_rows:
                    cvd_groups.setdefault(int(row["bucket_ms"]) // width * width, []).append(row)
                for row in oi_rows:
                    oi_groups.setdefault(int(row["bucket_ms"]) // width * width, []).append(row)
                expected = width // 60_000
                for bucket, rows in sorted(cvd_groups.items()):
                    statuses = [str(row["status"]) for row in rows]
                    quality, reason = aggregate_quality(statuses)
                    complete = len(rows) == expected and all(
                        row["signed_delta"] is not None for row in rows)
                    if not complete and quality == "VALID":
                        quality, reason = "PARTIAL", "MISSING_REQUIRED_1M_BUCKET"
                    usable = complete and quality in {
                        "VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"
                    }
                    out.execute(
                        "INSERT INTO cvd_higher_timeframes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (instrument, resolution, bucket,
                         sum(float(row["buy_volume"]) for row in rows) if usable else None,
                         sum(float(row["sell_volume"]) for row in rows) if usable else None,
                         sum(float(row["signed_delta"]) for row in rows) if usable else None,
                         sum(int(row["trade_count"]) for row in rows) if usable else 0,
                         min(int(row["source_min_ts_ms"]) for row in rows) if usable else None,
                         max(int(row["source_max_ts_ms"]) for row in rows) if usable else None,
                         sum(int(row["source_row_count"]) for row in rows) if usable else 0,
                         fingerprint([row["source_fingerprint"] for row in rows]) if usable else None,
                         rows[-1]["daily_cumulative"] if usable else None,
                         quality, reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                         self.identity.generated_commit, self.identity.generated_at_ms),
                    )
                for bucket, rows in sorted(oi_groups.items()):
                    statuses = [str(row["status"]) for row in rows]
                    quality, reason = aggregate_quality(statuses)
                    complete = len(rows) == expected and all(
                        row["confirmed_oi"] is not None for row in rows)
                    if not complete and quality == "VALID":
                        quality, reason = "PARTIAL", "MISSING_REQUIRED_1M_BUCKET"
                    usable = complete and quality in {
                        "VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"
                    }
                    last = rows[-1] if usable else None
                    out.execute(
                        "INSERT INTO oi_higher_timeframes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (instrument, resolution, bucket,
                         last["confirmed_oi"] if last else None,
                         last["observation_ts_ms"] if last else None,
                         sum(int(row["observation_count"]) for row in rows) if usable else 0,
                         fingerprint([row["source_fingerprint"] for row in rows]) if usable else None,
                         quality, reason, CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                         self.identity.generated_at_ms),
                    )
                counts[f"cvd:{resolution}"] = len(cvd_groups)
                counts[f"oi:{resolution}"] = len(oi_groups)
            self.destination.checkpoint(
                out, "higher", instrument, self.identity.source_watermark_ms,
                "COMPLETE", counts,
            )
        return counts

    def rebuild(self, sources: Iterable[str] = SOURCE_TABLES) -> dict[str, Any]:
        report: dict[str, Any] = {"coverage": [], "series": []}
        for instrument in INSTRUMENTS:
            for source in sources:
                report["coverage"].append(self.build_coverage(source, instrument))
            report["series"].append(self.build_cvd_1m(instrument))
            report["series"].append(self.build_oi_1m(instrument))
            report["series"].append({"instrument": instrument,
                                     **self.derive_higher_timeframes(instrument)})
        return report
