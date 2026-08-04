"""Deterministic, offline canonical CVD/OI history construction.

The source database is always opened read-only.  This module deliberately has
no network client and no production cutover support: official backfills must be
materialised in a separately verified source overlay before they are consumed.
Missing observations remain explicit missing facts.
"""

from __future__ import annotations

import hashlib
import csv
import json
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from itertools import chain
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence


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
CREATE TABLE IF NOT EXISTS official_trade_file_checkpoints(
  file_sha256 TEXT PRIMARY KEY,
  instrument TEXT NOT NULL,
  path TEXT NOT NULL,
  range_start_ms INTEGER NOT NULL,
  range_end_ms INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  unique_trade_id_count INTEGER NOT NULL,
  duplicate_count INTEGER NOT NULL,
  conflict_count INTEGER NOT NULL,
  contract_value TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('COMPLETE','CONFLICT')),
  completed_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS official_trade_1m_overlay(
  file_sha256 TEXT NOT NULL REFERENCES official_trade_file_checkpoints(file_sha256),
  instrument TEXT NOT NULL,
  bucket_ms INTEGER NOT NULL,
  buy_volume_decimal TEXT NOT NULL,
  sell_volume_decimal TEXT NOT NULL,
  trade_count INTEGER NOT NULL,
  source_min_ts_ms INTEGER NOT NULL,
  source_max_ts_ms INTEGER NOT NULL,
  source_fingerprint TEXT NOT NULL,
  PRIMARY KEY(file_sha256,instrument,bucket_ms)
);
CREATE INDEX IF NOT EXISTS idx_official_trade_overlay_query
  ON official_trade_1m_overlay(instrument,bucket_ms);
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
        official_trade_manifest_path: Path | str | None = None,
        contract_values: dict[str, str] | None = None,
        official_oi_manifest_path: Path | str | None = None,
        official_price_manifest_path: Path | str | None = None,
    ) -> None:
        self.source_path = Path(source_path)
        self.destination = CanonicalHistoryStore(destination_path)
        self.identity = identity
        self.official_trade_manifest_path = (
            Path(official_trade_manifest_path)
            if official_trade_manifest_path is not None else None
        )
        self.contract_values = contract_values or {}
        self.official_oi_manifest_path = (
            Path(official_oi_manifest_path)
            if official_oi_manifest_path is not None else None
        )
        self.official_price_manifest_path = (
            Path(official_price_manifest_path)
            if official_price_manifest_path is not None else None
        )
        self.destination.initialise(identity)

    def _load_official_price_points(
        self, source_name: str, instrument: str,
    ) -> tuple[dict[int, list[str]], dict[str, Any] | None]:
        if self.official_price_manifest_path is None:
            return {}, None
        manifest_body = self.official_price_manifest_path.read_bytes()
        manifest = json.loads(manifest_body)
        if manifest.get("manifest_version") != "okx-official-price-gap-manifest-v1":
            raise ValueError("unsupported official price manifest")
        item = next((entry for entry in manifest.get("instruments", [])
                     if entry.get("instrument") == instrument
                     and entry.get("source") == source_name), None)
        if item is None:
            return {}, None
        rows_path = Path(str(item["rows_path"]))
        rows_body = rows_path.read_bytes()
        if hashlib.sha256(rows_body).hexdigest() != item["rows_sha256"]:
            raise ValueError(f"official {source_name} rows SHA mismatch: {instrument}")
        points: dict[int, list[str]] = {}
        for raw in json.loads(rows_body):
            row = [str(value) for value in raw]
            if len(row) != 6 or row[5] != "1" or int(row[0]) % 60_000:
                raise ValueError(f"invalid official {source_name} row: {instrument}")
            timestamp = int(row[0])
            previous = points.get(timestamp)
            if previous is not None and previous != row:
                raise ValueError(f"conflicting official {source_name} timestamp {timestamp}")
            points[timestamp] = row
        audit = dict(item)
        audit["manifest_sha256"] = hashlib.sha256(manifest_body).hexdigest()
        return points, audit

    def apply_official_price_overlay(
        self, source_name: str, instrument: str,
    ) -> dict[str, Any]:
        """Apply exact confirmed official candles to missing ledger cells only."""
        if source_name not in {"mark", "index"}:
            raise ValueError("official price overlay supports mark/index only")
        points, audit = self._load_official_price_points(source_name, instrument)
        if audit is None:
            return {"source": source_name, "instrument": instrument,
                    "status": "SOURCE_UNAVAILABLE", "points_used": 0}
        table = SOURCE_TABLES[source_name]
        source_instrument = self._source_instrument(source_name, instrument)
        overlap_checked = overlap_conflicts = points_used = 0
        official_fingerprint = hashlib.sha256()
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            local: dict[int, sqlite3.Row] = {}
            timestamps = sorted(points)
            for offset in range(0, len(timestamps), 900):
                chunk = timestamps[offset:offset + 900]
                placeholders = ",".join("?" for _ in chunk)
                for row in source.execute(
                    f"""SELECT source_ts_ms,open,high,low,close,state
                          FROM {table} WHERE instrument=?
                           AND state='confirmed'
                           AND source_ts_ms IN ({placeholders})""",
                    (source_instrument, *chunk),
                ):
                    local[int(row[0])] = row
            for timestamp, row in sorted(points.items()):
                official_fingerprint.update(canonical_json(row).encode("utf-8"))
                existing = local.get(timestamp)
                if existing is not None:
                    overlap_checked += 1
                    local_values = tuple(Decimal(str(existing[index])) for index in range(1, 5))
                    official_values = tuple(Decimal(row[index]) for index in range(1, 5))
                    if existing[5] != "confirmed" or local_values != official_values:
                        overlap_conflicts += 1
                    continue
                ledger = out.execute(
                    """SELECT status FROM coverage_ledger
                        WHERE instrument=? AND source=? AND bucket_ms=?""",
                    (instrument, source_name, timestamp),
                ).fetchone()
                if ledger is None or ledger[0] not in {"MISSING", "SOURCE_UNAVAILABLE"}:
                    continue
                row_fingerprint = fingerprint(row)
                out.execute(
                    """UPDATE coverage_ledger SET observed_count=1,
                              unique_identity_count=1,duplicate_count=0,
                              first_source_ts_ms=?,last_source_ts_ms=?,
                              source_fingerprint=?,status='BACKFILLED_OFFICIAL',
                              gap_reason=NULL,classification='OBSERVED'
                        WHERE instrument=? AND source=? AND bucket_ms=?""",
                    (timestamp, timestamp, row_fingerprint, instrument,
                     source_name, timestamp),
                )
                points_used += 1
            if overlap_conflicts:
                raise ValueError(
                    f"official {source_name} overlap conflicts for {instrument}: "
                    f"{overlap_conflicts}"
                )
            for gap in audit.get("gaps", []):
                for timestamp in range(
                    int(gap["start_ms"]), int(gap["end_ms_exclusive"]), 60_000
                ):
                    out.execute(
                        """UPDATE coverage_ledger
                              SET status='SOURCE_UNAVAILABLE',
                                  gap_reason='OFFICIAL_CANDLE_NOT_RETURNED'
                            WHERE instrument=? AND source=? AND bucket_ms=?
                              AND status='MISSING'""",
                        (instrument, source_name, timestamp),
                    )
            manifest_id = f"{audit['manifest_sha256']}:{source_name}:{instrument}"
            out.execute(
                """INSERT OR REPLACE INTO official_backfill_manifests
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (manifest_id, source_name, instrument,
                 min(int(gap["start_ms"]) for gap in audit["gaps"]),
                 max(int(gap["end_ms_exclusive"]) for gap in audit["gaps"]),
                 audit["endpoint"], int(audit["page_count"]), audit["rows_sha256"],
                 points_used, len(points), min(points) if points else None,
                 max(points) if points else None,
                 "MATCH" if overlap_checked else "NO_OVERLAP",
                 str(audit["dedupe_key"]), now_ms(), canonical_json(audit)),
            )
            out.execute(
                """INSERT OR REPLACE INTO source_assets VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (instrument, source_name, f"official:{table}",
                 min(points) if points else None, max(points) if points else None,
                 min(points) if points else None, max(points) if points else None,
                 len(points), len(points), 0, 0, 0, 0,
                 official_fingerprint.hexdigest(), "OKX_OFFICIAL_HISTORY_CANDLES",
                 now_ms(), manifest_id,
                 "BACKFILLED_OFFICIAL" if audit["status"] == "COMPLETE" else "PARTIAL"),
            )
        return {
            "source": source_name, "instrument": instrument,
            "official_points": len(points), "points_used": points_used,
            "overlap_checked": overlap_checked,
            "overlap_conflicts": overlap_conflicts, "status": audit["status"],
        }

    def _load_official_oi_points(
        self, instrument: str,
    ) -> tuple[dict[int, list[str]], dict[str, Any] | None]:
        if self.official_oi_manifest_path is None:
            return {}, None
        manifest_body = self.official_oi_manifest_path.read_bytes()
        manifest = json.loads(manifest_body)
        if manifest.get("manifest_version") != "okx-official-oi-history-manifest-v1":
            raise ValueError("unsupported official OI manifest")
        item = next((entry for entry in manifest.get("instruments", [])
                     if entry.get("instrument") == instrument), None)
        if item is None:
            return {}, None
        rows_path = Path(str(item["rows_path"]))
        rows_body = rows_path.read_bytes()
        if hashlib.sha256(rows_body).hexdigest() != item["rows_sha256"]:
            raise ValueError(f"official OI rows SHA mismatch: {instrument}")
        rows = json.loads(rows_body)
        points: dict[int, list[str]] = {}
        for raw in rows:
            row = [str(value) for value in raw]
            if len(row) != 4 or int(row[0]) % 300_000 != 0:
                raise ValueError(f"invalid official 5m OI row: {instrument}")
            timestamp = int(row[0])
            previous = points.get(timestamp)
            if previous is not None and previous != row:
                raise ValueError(f"conflicting official OI timestamp: {timestamp}")
            points[timestamp] = row
        audit = dict(item)
        audit["manifest_sha256"] = hashlib.sha256(manifest_body).hexdigest()
        return points, audit

    def _official_trade_files(self, instrument: str) -> list[dict[str, Any]]:
        if self.official_trade_manifest_path is None:
            return []
        manifest = json.loads(
            self.official_trade_manifest_path.read_text(encoding="utf-8")
        )
        if manifest.get("status") != "COMPLETE":
            raise ValueError("official trade manifest is not COMPLETE")
        files = [
            item for item in manifest.get("files", [])
            if item.get("status") == "VERIFIED"
            and Path(str(item["filename"])).name.startswith(f"{instrument}-trades-")
        ]
        return sorted(files, key=lambda item: int(item["date_ts"]))

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_official_trade_minutes(
        self, instrument: str,
    ) -> tuple[
        dict[int, dict[str, Any]], list[tuple[int, int]], list[dict[str, Any]]
    ]:
        """Aggregate verified official files without materialising synthetic raw."""
        files = self._official_trade_files(instrument)
        if not files:
            return {}, [], []
        if instrument not in self.contract_values:
            raise ValueError(f"missing verified contract value for {instrument}")
        contract_value = Decimal(self.contract_values[instrument])
        grouped: dict[int, dict[str, Any]] = {}
        covered_ranges: list[tuple[int, int]] = []
        audit: list[dict[str, Any]] = []
        for item in files:
            path = Path(str(item["path"]))
            actual_sha = self._sha256_file(path)
            if actual_sha != str(item["sha256"]).lower():
                raise ValueError(f"official trade file SHA mismatch: {path}")
            range_start = int(item["date_ts"])
            range_end = range_start + 86_400_000
            with self.destination.connect() as cache:
                checkpoint = cache.execute(
                    """SELECT * FROM official_trade_file_checkpoints
                       WHERE file_sha256=? AND instrument=? AND contract_value=?
                             AND status='COMPLETE'""",
                    (actual_sha, instrument, str(contract_value)),
                ).fetchone()
                if checkpoint is not None:
                    cached_rows = list(cache.execute(
                        """SELECT bucket_ms,buy_volume_decimal,sell_volume_decimal,
                                  trade_count,source_min_ts_ms,source_max_ts_ms,
                                  source_fingerprint
                           FROM official_trade_1m_overlay
                           WHERE file_sha256=? AND instrument=? ORDER BY bucket_ms""",
                        (actual_sha, instrument),
                    ))
                    if not cached_rows:
                        raise ValueError(f"empty official checkpoint: {path}")
                    for row in cached_rows:
                        grouped[int(row[0])] = {
                            "buy": Decimal(row[1]), "sell": Decimal(row[2]),
                            "count": int(row[3]), "first": int(row[4]),
                            "last": int(row[5]), "hash": str(row[6]),
                            "status": "BACKFILLED_OFFICIAL", "gap_reason": None,
                        }
                    covered_ranges.append((range_start, range_end))
                    audit.append({
                        "path": str(path), "sha256": actual_sha,
                        "range_start_ms": range_start, "range_end_ms": range_end,
                        "partition_label": Path(str(item["filename"])).stem[-10:],
                        "row_count": int(checkpoint[5]),
                        "unique_trade_id_count": int(checkpoint[6]),
                        "duplicate_count": int(checkpoint[7]),
                        "conflict_count": int(checkpoint[8]), "resumed": True,
                    })
                    continue
            physical_rows = 0
            duplicate_count = 0
            conflict_count = 0
            previous_trade_id: int | None = None
            previous_fact: tuple[str, str, str, str] | None = None
            unique_count = 0
            file_min_ts: int | None = None
            file_max_ts: int | None = None
            file_grouped: dict[int, dict[str, Any]] = {}
            with zipfile.ZipFile(path) as archive:
                member = str(item["member"])
                with archive.open(member) as raw:
                    # ZipExtFile validates the member CRC when read to EOF.
                    reader = csv.DictReader(
                        (line.decode("utf-8") for line in raw)
                    )
                    if reader.fieldnames != [
                        "instrument_name", "trade_id", "side", "price", "size",
                        "created_time",
                    ]:
                        raise ValueError(f"unexpected official CSV columns: {path}")
                    for row in reader:
                        physical_rows += 1
                        if row["instrument_name"] != instrument:
                            raise ValueError(f"instrument mismatch in {path}")
                        trade_id = row["trade_id"]
                        numeric_trade_id = int(trade_id)
                        fact = (row["created_time"], row["side"], row["price"], row["size"])
                        if (previous_trade_id is not None
                                and numeric_trade_id < previous_trade_id):
                            raise ValueError(f"out-of-order official trade ID in {path}")
                        if numeric_trade_id == previous_trade_id:
                            duplicate_count += 1
                            if previous_fact != fact:
                                conflict_count += 1
                            continue
                        previous_trade_id = numeric_trade_id
                        previous_fact = fact
                        unique_count += 1
                        timestamp = int(row["created_time"])
                        file_min_ts = timestamp if file_min_ts is None else min(
                            file_min_ts, timestamp
                        )
                        file_max_ts = timestamp if file_max_ts is None else max(
                            file_max_ts, timestamp
                        )
                        bucket = minute_floor(timestamp)
                        cell = file_grouped.setdefault(bucket, {
                            "buy": Decimal(0), "sell": Decimal(0), "count": 0,
                            "first": timestamp, "last": timestamp,
                            "hash": hashlib.sha256(), "status": "BACKFILLED_OFFICIAL",
                            "gap_reason": None,
                        })
                        notional = Decimal(row["price"]) * Decimal(row["size"]) * contract_value
                        side = row["side"].lower()
                        if side not in {"buy", "sell"}:
                            raise ValueError(f"unsupported official trade side {side!r}")
                        cell[side] += notional
                        cell["count"] += 1
                        cell["first"] = min(cell["first"], timestamp)
                        cell["last"] = max(cell["last"], timestamp)
                        cell["hash"].update(canonical_json(
                            [trade_id, timestamp, side, row["price"], row["size"]]
                        ).encode("utf-8"))
            if conflict_count:
                raise ValueError(f"official trade-ID conflict in {path}: {conflict_count}")
            # OKX daily files are partitioned at 00:00 UTC+8. date_ts is the
            # exact UTC interval start; the filename is only the UTC+8 label.
            if (file_min_ts is None or file_min_ts < range_start
                    or file_max_ts is None or file_max_ts >= range_end):
                raise ValueError(f"official rows outside declared interval: {path}")
            for cell in file_grouped.values():
                cell["hash"] = cell["hash"].hexdigest()
            with self.destination.connect() as cache:
                cache.execute(
                    "DELETE FROM official_trade_1m_overlay WHERE file_sha256=?",
                    (actual_sha,),
                )
                cache.execute(
                    "DELETE FROM official_trade_file_checkpoints WHERE file_sha256=?",
                    (actual_sha,),
                )
                cache.execute(
                    "INSERT INTO official_trade_file_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (actual_sha, instrument, str(path), range_start, range_end,
                     physical_rows, unique_count, duplicate_count, conflict_count,
                     str(contract_value), "COMPLETE", now_ms()),
                )
                cache.executemany(
                    "INSERT INTO official_trade_1m_overlay VALUES(?,?,?,?,?,?,?,?,?)",
                    ((actual_sha, instrument, bucket, str(cell["buy"]),
                      str(cell["sell"]), cell["count"], cell["first"], cell["last"],
                      cell["hash"]) for bucket, cell in sorted(file_grouped.items())),
                )
            for bucket, cell in file_grouped.items():
                if bucket in grouped:
                    raise ValueError(f"overlapping official trade files at {bucket}")
                grouped[bucket] = cell
            covered_ranges.append((range_start, range_end))
            audit.append({
                "path": str(path), "sha256": actual_sha,
                "range_start_ms": range_start, "range_end_ms": range_end,
                "partition_label": Path(str(item["filename"])).stem[-10:],
                "row_count": physical_rows, "unique_trade_id_count": unique_count,
                "duplicate_count": duplicate_count, "conflict_count": conflict_count,
                "resumed": False,
            })
        return grouped, covered_ranges, audit

    def _source_bounds(
        self, source: sqlite3.Connection, table: str, instrument: str,
    ) -> tuple[int, int, int] | None:
        row = source.execute(
            f"""SELECT MIN(source_ts_ms),MAX(source_ts_ms),COUNT(*)
                FROM {table} WHERE instrument=? AND state='confirmed'""", (instrument,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0]), int(row[1]), int(row[2])

    @staticmethod
    def _source_instrument(source_name: str, instrument: str) -> str:
        return instrument.removesuffix("-SWAP") if source_name == "index" else instrument

    def build_coverage(
        self, source_name: str, instrument: str,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Create a factual minute ledger; old collection_gaps are not inputs."""
        table = SOURCE_TABLES[source_name]
        source_instrument = self._source_instrument(source_name, instrument)
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            bounds = self._source_bounds(source, table, source_instrument)
            state_counts = {
                str(row[0]): int(row[1]) for row in source.execute(
                    f"SELECT state,COUNT(*) FROM {table} WHERE instrument=? GROUP BY state",
                    (source_instrument,),
                )
            }
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
                f"""SELECT *
                    FROM {table} WHERE instrument=?
                    AND state='confirmed'
                    ORDER BY source_ts_ms""", (source_instrument,),
            )
            asset_hash = hashlib.sha256()
            columns = [description[0] for description in rows.description]
            ts_index = columns.index("source_ts_ms")
            identity_index = columns.index("uniqueness_key")
            trade_id_index = columns.index("trade_id") if "trade_id" in columns else None
            trade_fact_indexes = (
                [columns.index(name) for name in ("source_ts_ms", "side", "price", "size")]
                if source_name == "trades" else []
            )
            inserts: list[tuple[Any, ...]] = []
            missing = 0
            expected_bucket = minute_floor(earliest)
            current_bucket: int | None = None
            count = 0
            first = last = 0
            identities: set[str] = set()
            cell_hash = hashlib.sha256()
            processed = 0
            asset_unique = 0
            asset_duplicates = 0
            asset_conflicts = 0
            identity_facts: dict[str, tuple[Any, ...]] = {}
            conflicting_identities: set[str] = set()

            def row_identity(row: sqlite3.Row) -> str:
                if trade_id_index is not None and row[trade_id_index] is not None:
                    return str(row[trade_id_index])
                return str(row[identity_index])

            def emit_observed() -> None:
                nonlocal inserts, count, identities
                nonlocal asset_unique, asset_duplicates, asset_conflicts
                assert current_bucket is not None
                duplicate_count = count - len(identities)
                conflict_count = len(conflicting_identities)
                asset_unique += len(identities)
                asset_duplicates += duplicate_count
                asset_conflicts += conflict_count
                status = "CONFLICT" if conflict_count else "VALID"
                inserts.append((
                    instrument, source_name, current_bucket, 1, count,
                    len(identities), duplicate_count, 0, first, last,
                    cell_hash.hexdigest(), status,
                    "TRADE_ID_CONTENT_CONFLICT" if conflict_count else None,
                    "OBSERVED",
                ))

            def stable_rows() -> Iterator[sqlite3.Row]:
                """Use the time index, sorting only equal-ms rows in memory."""
                pending: list[sqlite3.Row] = []
                pending_ts: int | None = None
                for raw_row in rows:
                    raw_ts = int(raw_row[ts_index])
                    if pending_ts is not None and raw_ts != pending_ts:
                        yield from sorted(
                            pending, key=row_identity)
                        pending.clear()
                    pending_ts = raw_ts
                    pending.append(raw_row)
                if pending:
                    yield from sorted(
                        pending, key=row_identity)

            for row in stable_rows():
                processed += 1
                timestamp = int(row[ts_index])
                bucket = minute_floor(timestamp)
                if current_bucket is None or bucket != current_bucket:
                    if current_bucket is not None:
                        emit_observed()
                        expected_bucket = current_bucket + RESOLUTION_MS["1m"]
                    while expected_bucket < bucket:
                        missing += 1
                        inserts.append((
                            instrument, source_name, expected_bucket, 1, 0, 0, 0,
                            0, None, None, None, "MISSING", "NO_RAW_OBSERVATION",
                            "TRUE_RAW_GAP",
                        ))
                        expected_bucket += RESOLUTION_MS["1m"]
                    current_bucket = bucket
                    count = 0
                    first = last = timestamp
                    identities = set()
                    identity_facts = {}
                    conflicting_identities = set()
                    cell_hash = hashlib.sha256()
                identity = row_identity(row)
                count += 1
                last = timestamp
                if trade_fact_indexes:
                    fact = tuple(row[index] for index in trade_fact_indexes)
                    prior_fact = identity_facts.get(identity)
                    if prior_fact is not None and prior_fact != fact:
                        conflicting_identities.add(identity)
                    elif prior_fact is None:
                        identity_facts[identity] = fact
                identities.add(identity)
                encoded = canonical_json(list(row)).encode()
                cell_hash.update(encoded)
                asset_hash.update(encoded)
                if progress is not None and processed % 1_000_000 == 0:
                    progress({
                        "instrument": instrument,
                        "source": source_name,
                        "processed_rows": processed,
                        "total_rows": total_rows,
                        "bucket_ms": bucket,
                    })
                if len(inserts) >= 10_000:
                    out.executemany(
                        "INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        inserts,
                    )
                    inserts.clear()
            if current_bucket is not None:
                emit_observed()
                expected_bucket = current_bucket + RESOLUTION_MS["1m"]
            final_bucket = minute_floor(latest)
            while expected_bucket <= final_bucket:
                missing += 1
                inserts.append((
                    instrument, source_name, expected_bucket, 1, 0, 0, 0, 0,
                    None, None, None, "MISSING", "NO_RAW_OBSERVATION",
                    "TRUE_RAW_GAP",
                ))
                expected_bucket += RESOLUTION_MS["1m"]
            if inserts:
                out.executemany(
                    "INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    inserts,
                )
            status = ("CONFLICT" if asset_conflicts else
                      "PARTIAL" if missing else "VALID")
            out.execute(
                """INSERT INTO source_assets VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (instrument, source_name, table, minute_floor(earliest),
                 minute_floor(latest), earliest, latest, total_rows, asset_unique,
                 asset_duplicates, 0, 0, asset_conflicts,
                 asset_hash.hexdigest(), "local-frozen-db", None,
                 None, status),
            )
            self.destination.checkpoint(
                out, f"coverage:{source_name}", instrument, latest, "COMPLETE",
                {"row_count": total_rows, "missing_minutes": missing,
                 "state_counts": state_counts,
                 "unique_identity_count": asset_unique,
                 "duplicate_count": asset_duplicates,
                 "trade_id_conflict_count": asset_conflicts},
            )
            return {"instrument": instrument, "source": source_name,
                    "earliest_ms": earliest, "latest_ms": latest,
                    "row_count": total_rows, "missing_minutes": missing,
                    "state_counts": state_counts,
                    "unique_identity_count": asset_unique,
                    "duplicate_count": asset_duplicates,
                    "trade_id_conflict_count": asset_conflicts,
                    "status": status}

    def build_cvd_1m(self, instrument: str) -> dict[str, Any]:
        official, official_ranges, official_audit = (
            self._load_official_trade_minutes(instrument)
        )
        official_minute_count = len(official)
        with _readonly(self.source_path) as source, self.destination.connect() as out:
            for range_start, range_end in official_ranges:
                for bucket in range(range_start, range_end, 60_000):
                    out.execute(
                        """INSERT OR IGNORE INTO coverage_ledger VALUES(
                           ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (instrument, "trades", bucket, 1, 0, 0, 0, 0,
                         None, None, None, "MISSING",
                         "NO_TRADE_IN_COMPLETE_OFFICIAL_FILE", "TRUE_RAW_GAP"),
                    )
            for bucket, cell in official.items():
                out.execute(
                    """INSERT INTO coverage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(instrument,source,bucket_ms) DO UPDATE SET
                       observed_count=excluded.observed_count,
                       unique_identity_count=excluded.unique_identity_count,
                       duplicate_count=excluded.duplicate_count,
                       first_source_ts_ms=excluded.first_source_ts_ms,
                       last_source_ts_ms=excluded.last_source_ts_ms,
                       source_fingerprint=excluded.source_fingerprint,
                       status=excluded.status,gap_reason=NULL,
                       classification='OBSERVED'""",
                    (instrument, "trades", bucket, 1, cell["count"], cell["count"],
                     0, 0, cell["first"], cell["last"], cell["hash"],
                     "BACKFILLED_OFFICIAL", None, "OBSERVED"),
                )
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
            grouped: dict[int, dict[str, Any]] = official
            local_seen: dict[str, tuple[Any, ...]] = {}
            trade_select = """SELECT source_ts_ms,trade_id,side,notional,
                                      uniqueness_key,state
                               FROM trade_flow_observations
                               WHERE instrument=? AND state='confirmed'"""
            trade_order = " ORDER BY source_ts_ms"
            if official_ranges:
                official_ranges.sort()
                if any(
                    left[1] != right[0]
                    for left, right in zip(official_ranges, official_ranges[1:])
                ):
                    raise ValueError("official trade days are not contiguous")
                official_start = official_ranges[0][0]
                official_end = official_ranges[-1][1]
                before = source.execute(
                    trade_select + " AND source_ts_ms<?" + trade_order,
                    (instrument, official_start),
                )
                after = source.execute(
                    trade_select + " AND source_ts_ms>=?" + trade_order,
                    (instrument, official_end),
                )
                rows: Iterable[sqlite3.Row] = chain(before, after)
            else:
                rows = source.execute(trade_select + trade_order, (instrument,))
            def stable_local_rows() -> Iterator[sqlite3.Row]:
                pending: list[sqlite3.Row] = []
                pending_ts: int | None = None
                for raw_row in rows:
                    timestamp = int(raw_row[0])
                    if pending_ts is not None and timestamp != pending_ts:
                        yield from sorted(
                            pending,
                            key=lambda item: (
                                str(item[1]) if item[1] is not None else "",
                                str(item[4]),
                            ),
                        )
                        pending.clear()
                    pending_ts = timestamp
                    pending.append(raw_row)
                if pending:
                    yield from sorted(
                        pending,
                        key=lambda item: (
                            str(item[1]) if item[1] is not None else "", str(item[4]),
                        ),
                    )

            for row in stable_local_rows():
                bucket = minute_floor(int(row[0]))
                identity = str(row[1]) if row[1] is not None else str(row[4])
                fact = tuple(row)
                prior = local_seen.get(identity)
                if prior is not None:
                    if prior != fact:
                        cell = grouped.setdefault(bucket, {
                            "buy": Decimal(0), "sell": Decimal(0), "count": 0,
                            "first": int(row[0]), "last": int(row[0]),
                            "hash": hashlib.sha256(), "status": "CONFLICT",
                            "gap_reason": "TRADE_ID_CONTENT_CONFLICT",
                        })
                        cell["status"] = "CONFLICT"
                        cell["gap_reason"] = "TRADE_ID_CONTENT_CONFLICT"
                    continue
                local_seen[identity] = fact
                cell = grouped.setdefault(bucket, {
                    "buy": Decimal(0), "sell": Decimal(0), "count": 0,
                    "first": int(row[0]), "last": int(row[0]),
                    "hash": hashlib.sha256(), "status": "VALID",
                    "gap_reason": None,
                })
                volume = Decimal(str(row[3]))
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
            for cell in grouped.values():
                if hasattr(cell["hash"], "hexdigest"):
                    cell["hash"] = cell["hash"].hexdigest()
            current_date = None
            cumulative: float | None = 0.0
            day_complete = True
            inserts = []
            for bucket, coverage in ledger.items():
                date = utc_date(bucket)
                if date != current_date:
                    starts_at_utc_midnight = bucket % 86_400_000 == 0
                    current_date = date
                    cumulative = 0.0 if starts_at_utc_midnight else None
                    day_complete = starts_at_utc_midnight
                cell = grouped.get(bucket)
                status = str(coverage["status"])
                reason = coverage["gap_reason"]
                if (cell is None and official_ranges
                        and bucket >= official_ranges[-1][1]
                        and status == "MISSING"):
                    status = "SOURCE_UNAVAILABLE"
                    reason = "OKX_DAILY_TRADE_FILE_T_PLUS_2_PENDING"
                    out.execute(
                        """UPDATE coverage_ledger SET status=?,gap_reason=?
                           WHERE instrument=? AND source='trades' AND bucket_ms=?""",
                        (status, reason, instrument, bucket),
                    )
                if cell is not None and cell.get("status") == "CONFLICT":
                    status, reason = "CONFLICT", cell["gap_reason"]
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
                buy = float(cell["buy"])
                sell = float(cell["sell"])
                delta = buy - sell
                output_status = status if day_complete else "PARTIAL_AFTER_GAP"
                if cumulative is None:
                    daily = None
                else:
                    cumulative += delta
                    daily = cumulative
                inserts.append((
                    instrument, bucket, "1m", buy, sell, delta,
                    cell["count"], cell["first"], cell["last"], cell["count"],
                    cell["hash"], daily, date, output_status,
                    None if day_complete else "EARLIER_RAW_GAP_SAME_UTC_DAY",
                    CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                    self.identity.generated_commit, self.identity.generated_at_ms,
                ))
            out.executemany(
                "INSERT INTO cvd_1m VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                inserts,
            )
            if official_audit:
                file_fingerprint = fingerprint([
                    [item["path"], item["sha256"], item["row_count"]]
                    for item in official_audit
                ])
                manifest_id = fingerprint([
                    "trades", instrument, file_fingerprint,
                    official_ranges[0][0], official_ranges[-1][1],
                ])
                official_rows = sum(int(item["row_count"]) for item in official_audit)
                out.execute(
                    """INSERT OR REPLACE INTO official_backfill_manifests
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (manifest_id, "trades", instrument,
                     official_ranges[0][0], official_ranges[-1][1],
                     "OKX /api/v5/public/market-data-history daily tick files",
                     len(official_audit), file_fingerprint, official_rows,
                     official_rows, official_ranges[0][0], official_ranges[-1][1],
                     "AUTHORITATIVE_FULL_PARTITION_REPLACEMENT", "instrument+trade_id",
                     self.identity.generated_at_ms, canonical_json(official_audit)),
                )
                out.execute(
                    """INSERT OR REPLACE INTO source_assets VALUES(
                       ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (instrument, "trades", "OKX_OFFICIAL_DAILY_TICK_FILES",
                     official_ranges[0][0], official_ranges[-1][1] - 1,
                     min(int(cell["first"]) for bucket, cell in official.items()
                         if official_ranges[0][0] <= bucket < official_ranges[-1][1]),
                     max(int(cell["last"]) for bucket, cell in official.items()
                         if official_ranges[0][0] <= bucket < official_ranges[-1][1]),
                     official_rows, official_rows, 0, 0, 0, 0,
                     file_fingerprint, "OKX_OFFICIAL_MARKET_DATA_HISTORY",
                     self.identity.generated_at_ms, manifest_id,
                     "BACKFILLED_OFFICIAL"),
                )
            self._reconcile_cvd_days(out, instrument)
            self.destination.checkpoint(
                out, "cvd:1m", instrument, max(ledger), "COMPLETE",
                {"rows": len(inserts)},
            )
            return {"instrument": instrument, "rows": len(inserts),
                    "official_files": official_audit,
                    "official_minutes": official_minute_count}

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
        official_points, official_audit = self._load_official_oi_points(instrument)
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
            official_used = 0
            official_overlap = 0
            for bucket, coverage in ledger.items():
                observations = grouped.get(bucket, [])
                status = str(coverage["status"])
                official = official_points.get(bucket)
                if observations and official is not None:
                    official_overlap += 1
                if not observations and official is not None:
                    value = next((float(value) for value in official[3:0:-1]
                                  if value not in {None, ""}), None)
                    source_hash = fingerprint([
                        "OKX_OFFICIAL_OI_HISTORY_5M", instrument, official,
                    ])
                    inserts.append((
                        instrument, bucket, "1m", value, int(official[0]), 1,
                        source_hash, "BACKFILLED_OFFICIAL", None,
                        CANONICAL_MICROSTRUCTURE_HISTORY_VERSION,
                        self.identity.generated_at_ms,
                    ))
                    out.execute(
                        """UPDATE coverage_ledger SET observed_count=1,
                           unique_identity_count=1,first_source_ts_ms=?,
                           last_source_ts_ms=?,source_fingerprint=?,
                           status='BACKFILLED_OFFICIAL',gap_reason=NULL,
                           classification='OBSERVED'
                           WHERE instrument=? AND source='oi' AND bucket_ms=?""",
                        (int(official[0]), int(official[0]), source_hash,
                         instrument, bucket),
                    )
                    official_used += 1
                    continue
                if not observations or status not in {
                    "VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"
                }:
                    if not observations:
                        status = "UNRECOVERABLE_RAW_GAP"
                        reason = (
                            "OKX_OFFICIAL_OI_HISTORY_BOUNDARY"
                            if (not official_points or bucket < min(official_points))
                            else "OKX_OFFICIAL_OI_ONLY_5M_NO_EXACT_OBSERVATION"
                        )
                        out.execute(
                            """UPDATE coverage_ledger SET status=?,gap_reason=?
                               WHERE instrument=? AND source='oi' AND bucket_ms=?""",
                            (status, reason, instrument, bucket),
                        )
                    else:
                        reason = coverage["gap_reason"]
                    inserts.append((
                        instrument, bucket, "1m", None, None, 0, None, status,
                        reason,
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
            if official_audit is not None:
                manifest_id = fingerprint([
                    official_audit["manifest_sha256"], instrument,
                    official_audit["rows_sha256"], official_used,
                ])
                out.execute(
                    """INSERT OR REPLACE INTO official_backfill_manifests
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (manifest_id, "oi", instrument,
                     int(official_audit["earliest_ms"]),
                     int(official_audit["latest_ms"]),
                     official_audit["endpoint"],
                     int(official_audit["page_count"]),
                     official_audit["rows_sha256"],
                     official_used, official_used,
                     int(official_audit["earliest_ms"]),
                     int(official_audit["latest_ms"]),
                     f"OVERLAP_MINUTES:{official_overlap}", "instrument+ts",
                     self.identity.generated_at_ms,
                     canonical_json(official_audit)),
                )
            self._reconcile_oi_days(out, instrument)
            self.destination.checkpoint(
                out, "oi:1m", instrument, max(ledger), "COMPLETE",
                {"rows": len(inserts)},
            )
            return {"instrument": instrument, "rows": len(inserts),
                    "official_points_available": len(official_points),
                    "official_points_used": official_used,
                    "official_overlap_minutes": official_overlap}

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
                        "INSERT INTO cvd_higher_timeframes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
