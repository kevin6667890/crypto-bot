"""Local prototype for UTC-month SQLite microstructure sharding.

The prototype is intentionally isolated from collectors, strategies, orders, and
production configuration.  It demonstrates routing and query semantics with
small local fixtures; it is not a production schema migration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


DAY_MS = 86_400_000
MANIFEST_VERSION = 1
SHARD_SCHEMA_VERSION = 1
MONTH_RE = re.compile(r"^\d{4}_(0[1-9]|1[0-2])$")

TABLES = {
    "trades": "trade_flow_observations",
    "oi": "oi_observations",
    "mark": "mark_price_observations",
    "index": "index_price_observations",
    "funding_settled": "funding_settled",
    "funding_predicted": "funding_predicted",
    "liquidations": "liquidation_observations",
}

COMMON_TABLE_SQL = """
    source TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timestamp_ms INTEGER NOT NULL,
    uniqueness_key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    ingested_at_ms INTEGER NOT NULL
"""


class ShardingError(RuntimeError):
    """Base error for deterministic, caller-visible sharding failures."""


class ManifestError(ShardingError):
    """The manifest is corrupt, inconsistent, or unsafe."""


class MissingShardError(ShardingError):
    """A requested UTC month has no registered physical shard."""


class CursorError(ShardingError):
    """A pagination cursor is invalid or belongs to another query."""


class ColdShardWriteError(ShardingError):
    """A direct write to a sealed shard was attempted."""


@dataclass(frozen=True)
class QueryPage:
    rows: list[dict[str, Any]]
    next_cursor: str | None
    shard_count: int


def utc_month(timestamp_ms: int) -> str:
    """Return ``YYYY_MM`` using UTC only, independent of the host timezone."""
    value = int(timestamp_ms)
    return datetime.fromtimestamp(value / 1000, timezone.utc).strftime("%Y_%m")


def month_start_ms(month: str) -> int:
    if not MONTH_RE.fullmatch(month):
        raise ValueError(f"invalid UTC month: {month!r}")
    value = datetime.strptime(month, "%Y_%m").replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def next_month(month: str) -> str:
    value = datetime.strptime(month, "%Y_%m")
    year, number = value.year, value.month
    if number == 12:
        year, number = year + 1, 1
    else:
        number += 1
    return f"{year:04d}_{number:02d}"


def months_for_range(start_ms: int, end_ms: int) -> tuple[str, ...]:
    """List all UTC months intersecting the half-open range ``[start, end)``."""
    if int(end_ms) <= int(start_ms):
        raise ValueError("end_ms must be greater than start_ms")
    first, last = utc_month(start_ms), utc_month(int(end_ms) - 1)
    result = [first]
    while result[-1] != last:
        result.append(next_month(result[-1]))
    return tuple(result)


def normalize_instrument(table: str, instrument: str) -> str:
    value = str(instrument).upper()
    if table == "index":
        return value.removesuffix("-SWAP")
    return value if value.endswith("-SWAP") else f"{value}-SWAP"


def _manifest_checksum(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "checksum"}
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _stable_key(
    table: str,
    source: str,
    instrument: str,
    timestamp_ms: int,
    payload: dict[str, Any],
) -> str:
    encoded = json.dumps(
        [table, source, instrument, int(timestamp_ms), payload],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class MonthlyMicrostructureStore:
    """Small local hot/cold prototype with a central metadata database."""

    def __init__(
        self,
        root: Path | str,
        *,
        hot_month: str | None = None,
        create: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        self.manifest_path = self.root / "shard_manifest.json"
        self.central_path = self.root / "central" / "market_microstructure_center.db"
        self._plan_cache: dict[
            tuple[int, str, tuple[str, ...]], tuple[tuple[str, Path, str], ...]
        ] = {}
        self._plan_hits = 0
        self._plan_misses = 0
        self.connection_audit: list[dict[str, str]] = []
        if create:
            if hot_month is None:
                raise ValueError("hot_month is required when creating a store")
            self._create(hot_month)
        self.manifest = self._load_manifest()
        if hot_month is not None and self.manifest["hot_month"] != hot_month:
            raise ManifestError(
                f"manifest hot month is {self.manifest['hot_month']}, not {hot_month}"
            )

    def _create(self, hot_month: str) -> None:
        if self.manifest_path.exists():
            raise FileExistsError(self.manifest_path)
        if not MONTH_RE.fullmatch(hot_month):
            raise ValueError(f"invalid UTC month: {hot_month!r}")
        self.root.mkdir(parents=True, exist_ok=True)
        self.central_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_central()
        relative = self._relative_shard_path(hot_month)
        self._initialize_shard(self.root / relative)
        payload: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "schema_version": SHARD_SCHEMA_VERSION,
            "generation": 1,
            "hot_month": hot_month,
            "central": self.central_path.relative_to(self.root).as_posix(),
            "shards": [
                {
                    "month": hot_month,
                    "path": relative.as_posix(),
                    "state": "hot",
                }
            ],
        }
        self._save_manifest(payload)

    def _initialize_central(self) -> None:
        with sqlite3.connect(self.central_path) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cvd_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL,
                    bucket_ms INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS oi_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL,
                    bucket_ms INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS basis_aggregates(
                    instrument TEXT NOT NULL, resolution TEXT NOT NULL,
                    bucket_ms INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(instrument,resolution,bucket_ms));
                CREATE TABLE IF NOT EXISTS collection_gaps(
                    lane TEXT NOT NULL, instrument TEXT NOT NULL,
                    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(lane,instrument,start_ms,end_ms));
                CREATE TABLE IF NOT EXISTS coverage_summary(
                    lane TEXT NOT NULL, instrument TEXT NOT NULL,
                    earliest_ms INTEGER, latest_ms INTEGER, row_count INTEGER NOT NULL,
                    PRIMARY KEY(lane,instrument));
                CREATE TABLE IF NOT EXISTS collection_checkpoints(
                    lane TEXT NOT NULL, instrument TEXT NOT NULL, cursor TEXT,
                    last_source_ts_ms INTEGER, payload_json TEXT NOT NULL,
                    PRIMARY KEY(lane,instrument));
                CREATE TABLE IF NOT EXISTS late_arrivals(
                    table_name TEXT NOT NULL, target_month TEXT NOT NULL,
                    source TEXT NOT NULL, instrument TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL, uniqueness_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL, ingested_at_ms INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_late_time
                    ON late_arrivals(
                        table_name,target_month,timestamp_ms,uniqueness_key);
                CREATE INDEX IF NOT EXISTS idx_late_instrument_time
                    ON late_arrivals(
                        table_name,target_month,instrument,timestamp_ms,
                        uniqueness_key);
                CREATE INDEX IF NOT EXISTS idx_late_source_time
                    ON late_arrivals(
                        table_name,target_month,source,timestamp_ms,
                        uniqueness_key);
                CREATE TABLE IF NOT EXISTS shard_transitions(
                    transition_id TEXT PRIMARY KEY, from_month TEXT NOT NULL,
                    to_month TEXT NOT NULL, state TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL, completed_at_ms INTEGER);
                """
            )

    @staticmethod
    def _initialize_shard(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(f"PRAGMA user_version={SHARD_SCHEMA_VERSION}")
            for physical in TABLES.values():
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {physical}({COMMON_TABLE_SQL})"
                )
                connection.execute(
                    f"""CREATE INDEX IF NOT EXISTS idx_{physical}_time
                        ON {physical}(timestamp_ms,uniqueness_key)"""
                )
                connection.execute(
                    f"""CREATE INDEX IF NOT EXISTS idx_{physical}_instrument_time
                        ON {physical}(instrument,timestamp_ms,uniqueness_key)"""
                )
                connection.execute(
                    f"""CREATE INDEX IF NOT EXISTS idx_{physical}_source_time
                        ON {physical}(source,timestamp_ms,uniqueness_key)"""
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS shard_metadata(
                    schema_version INTEGER NOT NULL,
                    created_at_ms INTEGER NOT NULL)"""
            )
            if not connection.execute(
                "SELECT 1 FROM shard_metadata LIMIT 1"
            ).fetchone():
                connection.execute(
                    "INSERT INTO shard_metadata VALUES(?,?)",
                    (SHARD_SCHEMA_VERSION, _now_ms()),
                )

    def _relative_shard_path(self, month: str) -> Path:
        year, number = month.split("_")
        return (
            Path("shards")
            / year
            / number
            / f"market_microstructure_{month}.db"
        )

    def _save_manifest(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["checksum"] = _manifest_checksum(payload)
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        temporary = self.manifest_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.manifest_path)

    def _load_manifest(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"cannot read shard manifest: {exc}") from exc
        self.validate_manifest(payload, check_files=True)
        return payload

    def validate_manifest(
        self,
        payload: dict[str, Any] | None = None,
        *,
        check_files: bool = True,
    ) -> None:
        value = payload if payload is not None else self.manifest
        if value.get("manifest_version") != MANIFEST_VERSION:
            raise ManifestError("unsupported manifest version")
        if value.get("schema_version") != SHARD_SCHEMA_VERSION:
            raise ManifestError("unsupported shard schema version")
        if not isinstance(value.get("generation"), int) or value["generation"] < 1:
            raise ManifestError("invalid manifest generation")
        if value.get("checksum") != _manifest_checksum(value):
            raise ManifestError("manifest checksum mismatch")
        expected_central = self.central_path.relative_to(self.root).as_posix()
        if value.get("central") != expected_central:
            raise ManifestError("unsafe or noncanonical central database path")
        if check_files and not self.central_path.is_file():
            raise ManifestError(f"central database does not exist: {self.central_path}")
        hot_month = value.get("hot_month")
        if not isinstance(hot_month, str) or not MONTH_RE.fullmatch(hot_month):
            raise ManifestError("invalid hot_month")
        shards = value.get("shards")
        if not isinstance(shards, list) or not shards:
            raise ManifestError("manifest must contain shards")
        seen: set[str] = set()
        hot_count = 0
        ordered_months: list[str] = []
        for entry in shards:
            month = entry.get("month")
            if not isinstance(month, str) or not MONTH_RE.fullmatch(month):
                raise ManifestError(f"invalid shard month: {month!r}")
            if month in seen:
                raise ManifestError(f"duplicate shard month: {month}")
            seen.add(month)
            ordered_months.append(month)
            expected = self._relative_shard_path(month).as_posix()
            if entry.get("path") != expected:
                raise ManifestError(f"unsafe or noncanonical shard path for {month}")
            state = entry.get("state")
            if state not in {"hot", "cold"}:
                raise ManifestError(f"invalid shard state for {month}")
            if state == "hot":
                hot_count += 1
                if month != hot_month:
                    raise ManifestError("hot entry does not match hot_month")
            path = (self.root / expected).resolve()
            if self.root not in path.parents:
                raise ManifestError(f"shard escapes root: {path}")
            if check_files and not path.is_file():
                raise ManifestError(f"registered shard does not exist: {path}")
            if check_files:
                query = "?mode=ro&immutable=1" if state == "cold" else "?mode=ro"
                try:
                    with closing(sqlite3.connect(path.as_uri() + query, uri=True)) as c:
                        schema_version = c.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0]
                        physical_tables = {
                            row[0]
                            for row in c.execute(
                                """SELECT name FROM sqlite_master
                                   WHERE type='table'"""
                            )
                        }
                except sqlite3.DatabaseError as exc:
                    raise ManifestError(
                        f"cannot validate shard schema for {month}: {exc}"
                    ) from exc
                if schema_version != SHARD_SCHEMA_VERSION:
                    raise ManifestError(
                        f"shard schema version mismatch for {month}"
                    )
                missing_tables = set(TABLES.values()) - physical_tables
                if missing_tables:
                    raise ManifestError(
                        f"shard {month} is missing tables: "
                        f"{sorted(missing_tables)}"
                    )
        if hot_count != 1:
            raise ManifestError("manifest must contain exactly one hot shard")
        ordered_months.sort()
        for previous, current in zip(ordered_months, ordered_months[1:]):
            if current != next_month(previous):
                raise ManifestError(
                    f"manifest month sequence has a gap: {previous} -> {current}"
                )
        if ordered_months[-1] != hot_month:
            raise ManifestError("hot shard must be the newest registered month")

    def _entries(self) -> dict[str, dict[str, Any]]:
        return {entry["month"]: entry for entry in self.manifest["shards"]}

    def shard_path(self, month: str) -> Path:
        entry = self._entries().get(month)
        if entry is None:
            raise MissingShardError(f"missing shard for UTC month {month}")
        return self.root / entry["path"]

    def rotate(self, new_hot_month: str) -> None:
        """Seal the current month and atomically publish the next UTC month."""
        old_month = self.manifest["hot_month"]
        if new_hot_month != next_month(old_month):
            raise ValueError(
                f"rotation must be consecutive: {old_month} -> {next_month(old_month)}"
            )
        if new_hot_month in self._entries():
            raise ManifestError(f"shard already registered: {new_hot_month}")
        transition_id = f"{old_month}:{new_hot_month}"
        with sqlite3.connect(self.central_path) as connection:
            connection.execute(
                """INSERT OR REPLACE INTO shard_transitions
                   VALUES(?,?,?,?,?,NULL)""",
                (transition_id, old_month, new_hot_month, "PREPARING", _now_ms()),
            )
        # An immutable reader deliberately ignores WAL files.  The seal must
        # therefore checkpoint every committed hot row into the main database
        # before the manifest can advertise the shard as cold.
        old_path = self.shard_path(old_month)
        with sqlite3.connect(old_path) as connection:
            checkpoint = tuple(connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone())
            if checkpoint[0] != 0:
                raise ShardingError(
                    f"cannot seal {old_month}; WAL checkpoint busy: {checkpoint}"
                )
        relative = self._relative_shard_path(new_hot_month)
        self._initialize_shard(self.root / relative)
        payload = json.loads(json.dumps(self.manifest))
        for entry in payload["shards"]:
            if entry["month"] == old_month:
                entry["state"] = "cold"
        payload["shards"].append(
            {
                "month": new_hot_month,
                "path": relative.as_posix(),
                "state": "hot",
            }
        )
        payload["shards"].sort(key=lambda item: item["month"])
        payload["hot_month"] = new_hot_month
        payload["generation"] = int(payload["generation"]) + 1
        self._save_manifest(payload)
        self.manifest = self._load_manifest()
        self._plan_cache.clear()
        with sqlite3.connect(self.central_path) as connection:
            connection.execute(
                """UPDATE shard_transitions
                   SET state='COMPLETE',completed_at_ms=?
                   WHERE transition_id=?""",
                (_now_ms(), transition_id),
            )

    def insert(
        self,
        table: str,
        *,
        instrument: str,
        source: str,
        timestamp_ms: int,
        payload: dict[str, Any],
        uniqueness_key: str | None = None,
        ingested_at_ms: int | None = None,
    ) -> bool:
        """Route a row to hot or late overlay and return whether it was new."""
        physical = self._physical_table(table)
        timestamp = int(timestamp_ms)
        month = utc_month(timestamp)
        entry = self._entries().get(month)
        if entry is None:
            raise MissingShardError(f"missing shard for UTC month {month}")
        canonical = normalize_instrument(table, instrument)
        key = uniqueness_key or _stable_key(
            table, source, canonical, timestamp, payload
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        ingested = int(ingested_at_ms if ingested_at_ms is not None else _now_ms())
        values = (source, canonical, timestamp, key, encoded, ingested)
        if entry["state"] == "hot":
            with sqlite3.connect(self.root / entry["path"]) as connection:
                cursor = connection.execute(
                    f"""INSERT OR IGNORE INTO {physical}
                        (source,instrument,timestamp_ms,uniqueness_key,
                         payload_json,ingested_at_ms)
                        VALUES(?,?,?,?,?,?)""",
                    values,
                )
                return cursor.rowcount == 1
        if entry["state"] != "cold":
            raise ColdShardWriteError(f"invalid state for {month}: {entry['state']}")
        if self._key_exists_in_cold(physical, self.root / entry["path"], key):
            return False
        with sqlite3.connect(self.central_path) as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO late_arrivals(
                    table_name,target_month,source,instrument,timestamp_ms,
                    uniqueness_key,payload_json,ingested_at_ms)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (physical, month, *values),
            )
            return cursor.rowcount == 1

    def _key_exists_in_cold(self, physical: str, path: Path, key: str) -> bool:
        with closing(self._connect_shard(path, state="cold")) as connection:
            return (
                connection.execute(
                    f"SELECT 1 FROM {physical} WHERE uniqueness_key=?", (key,)
                ).fetchone()
                is not None
            )

    def _connect_shard(
        self, path: Path, *, state: str
    ) -> sqlite3.Connection:
        if state == "cold":
            target = path.as_uri() + "?mode=ro&immutable=1"
            mode = "ro-immutable"
        else:
            target = path.as_uri() + "?mode=ro"
            mode = "ro"
        connection = sqlite3.connect(target, uri=True)
        connection.row_factory = sqlite3.Row
        self.connection_audit.append({"path": str(path), "mode": mode})
        return connection

    def _query_plan(
        self, table: str, months: tuple[str, ...]
    ) -> tuple[tuple[str, Path, str], ...]:
        key = (int(self.manifest["generation"]), table, months)
        cached = self._plan_cache.get(key)
        if cached is not None:
            self._plan_hits += 1
            return cached
        self._plan_misses += 1
        entries = self._entries()
        plan: list[tuple[str, Path, str]] = []
        for month in months:
            entry = entries.get(month)
            if entry is None:
                raise MissingShardError(f"missing shard for UTC month {month}")
            path = self.root / entry["path"]
            if not path.is_file():
                raise MissingShardError(
                    f"registered shard file missing for UTC month {month}: {path}"
                )
            plan.append((month, path, entry["state"]))
        result = tuple(plan)
        self._plan_cache[key] = result
        return result

    def cache_info(self) -> dict[str, int]:
        return {
            "entries": len(self._plan_cache),
            "hits": self._plan_hits,
            "misses": self._plan_misses,
        }

    def query(
        self,
        table: str,
        *,
        start_ms: int,
        end_ms: int,
        instrument: str | None = None,
        source: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> QueryPage:
        physical = self._physical_table(table)
        months = months_for_range(start_ms, end_ms)
        plan = self._query_plan(table, months)
        canonical = (
            normalize_instrument(table, instrument) if instrument is not None else None
        )
        fingerprint = self._query_fingerprint(
            table, int(start_ms), int(end_ms), canonical, source
        )
        after: tuple[int, str] | None = None
        if cursor is not None:
            after = self._decode_cursor(cursor, fingerprint)
        fetch_limit = int(limit) + 1 if limit is not None else None
        rows: list[dict[str, Any]] = []
        for month, path, state in plan:
            with closing(self._connect_shard(path, state=state)) as connection:
                rows.extend(
                    self._select_rows(
                        connection,
                        physical,
                        int(start_ms),
                        int(end_ms),
                        canonical,
                        source,
                        after,
                        month,
                        fetch_limit,
                        late=False,
                    )
                )
            if state == "cold":
                with sqlite3.connect(self.central_path) as connection:
                    connection.row_factory = sqlite3.Row
                    rows.extend(
                        self._select_late_rows(
                            connection,
                            physical,
                            month,
                            int(start_ms),
                            int(end_ms),
                            canonical,
                            source,
                            after,
                            fetch_limit,
                        )
                    )
        deduplicated = {
            str(row["uniqueness_key"]): row
            for row in sorted(
                rows,
                key=lambda row: (
                    int(row["timestamp_ms"]),
                    str(row["uniqueness_key"]),
                    bool(row["_late"]),
                ),
            )
        }
        ordered = sorted(
            deduplicated.values(),
            key=lambda row: (int(row["timestamp_ms"]), str(row["uniqueness_key"])),
        )
        if limit is not None and int(limit) <= 0:
            raise ValueError("limit must be positive")
        has_more = limit is not None and len(ordered) > int(limit)
        visible = ordered[: int(limit)] if limit is not None else ordered
        next_cursor = None
        if has_more and visible:
            final = visible[-1]
            next_cursor = self._encode_cursor(
                fingerprint,
                int(final["timestamp_ms"]),
                str(final["uniqueness_key"]),
            )
        for row in visible:
            row.pop("_late", None)
        return QueryPage(visible, next_cursor, len(plan))

    @staticmethod
    def _select_rows(
        connection: sqlite3.Connection,
        physical: str,
        start_ms: int,
        end_ms: int,
        instrument: str | None,
        source: str | None,
        after: tuple[int, str] | None,
        month: str,
        fetch_limit: int | None,
        *,
        late: bool,
    ) -> list[dict[str, Any]]:
        clauses = ["timestamp_ms>=?", "timestamp_ms<?"]
        parameters: list[Any] = [start_ms, end_ms]
        if instrument is not None:
            clauses.append("instrument=?")
            parameters.append(instrument)
        if source is not None:
            clauses.append("source=?")
            parameters.append(source)
        if after is not None:
            clauses.append("(timestamp_ms>? OR (timestamp_ms=? AND uniqueness_key>?))")
            parameters.extend((after[0], after[0], after[1]))
        result: list[dict[str, Any]] = []
        sql = f"""SELECT source,instrument,timestamp_ms,uniqueness_key,
                         payload_json,ingested_at_ms
                  FROM {physical} WHERE {' AND '.join(clauses)}
                  ORDER BY timestamp_ms,uniqueness_key"""
        if fetch_limit is not None:
            sql += " LIMIT ?"
            parameters.append(fetch_limit)
        for raw in connection.execute(sql, parameters):
            row = dict(raw)
            row["payload"] = json.loads(row.pop("payload_json"))
            row["month"] = month
            row["_late"] = late
            result.append(row)
        return result

    def _select_late_rows(
        self,
        connection: sqlite3.Connection,
        physical: str,
        month: str,
        start_ms: int,
        end_ms: int,
        instrument: str | None,
        source: str | None,
        after: tuple[int, str] | None,
        fetch_limit: int | None,
    ) -> list[dict[str, Any]]:
        clauses = [
            "table_name=?",
            "target_month=?",
            "timestamp_ms>=?",
            "timestamp_ms<?",
        ]
        parameters: list[Any] = [physical, month, start_ms, end_ms]
        if instrument is not None:
            clauses.append("instrument=?")
            parameters.append(instrument)
        if source is not None:
            clauses.append("source=?")
            parameters.append(source)
        if after is not None:
            clauses.append("(timestamp_ms>? OR (timestamp_ms=? AND uniqueness_key>?))")
            parameters.extend((after[0], after[0], after[1]))
        rows: list[dict[str, Any]] = []
        sql = f"""SELECT source,instrument,timestamp_ms,uniqueness_key,
                         payload_json,ingested_at_ms
                  FROM late_arrivals WHERE {' AND '.join(clauses)}
                  ORDER BY timestamp_ms,uniqueness_key"""
        if fetch_limit is not None:
            sql += " LIMIT ?"
            parameters.append(fetch_limit)
        for raw in connection.execute(sql, parameters):
            row = dict(raw)
            row["payload"] = json.loads(row.pop("payload_json"))
            row["month"] = month
            row["_late"] = True
            rows.append(row)
        return rows

    def cvd_daily(
        self,
        *,
        start_ms: int,
        end_ms: int,
        instrument: str,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return trade deltas with cumulative notional reset at UTC midnight."""
        trades = self.query(
            "trades",
            start_ms=start_ms,
            end_ms=end_ms,
            instrument=instrument,
            source=source,
        ).rows
        day: int | None = None
        cumulative = 0.0
        result: list[dict[str, Any]] = []
        for trade in trades:
            current_day = int(trade["timestamp_ms"]) // DAY_MS * DAY_MS
            if current_day != day:
                day = current_day
                cumulative = 0.0
            payload = trade["payload"]
            notional = float(payload["notional"])
            delta = notional if payload["side"] == "buy" else -notional
            cumulative += delta
            result.append(
                {
                    **trade,
                    "utc_day_ms": current_day,
                    "delta": delta,
                    "cumulative": cumulative,
                }
            )
        return result

    def oi_absolute(
        self,
        *,
        start_ms: int,
        end_ms: int,
        instrument: str,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.query(
            "oi",
            start_ms=start_ms,
            end_ms=end_ms,
            instrument=instrument,
            source=source,
        ).rows

    def funding_events(
        self,
        *,
        settled: bool,
        start_ms: int,
        end_ms: int,
        instrument: str,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.query(
            "funding_settled" if settled else "funding_predicted",
            start_ms=start_ms,
            end_ms=end_ms,
            instrument=instrument,
            source=source,
        ).rows

    @staticmethod
    def _physical_table(table: str) -> str:
        try:
            return TABLES[table]
        except KeyError as exc:
            raise ValueError(f"unsupported microstructure table: {table!r}") from exc

    @staticmethod
    def _query_fingerprint(
        table: str,
        start_ms: int,
        end_ms: int,
        instrument: str | None,
        source: str | None,
    ) -> str:
        raw = json.dumps(
            [table, start_ms, end_ms, instrument, source],
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _encode_cursor(fingerprint: str, timestamp_ms: int, key: str) -> str:
        raw = json.dumps(
            {"v": 1, "q": fingerprint, "t": timestamp_ms, "k": key},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str, fingerprint: str) -> tuple[int, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            if payload["v"] != 1 or payload["q"] != fingerprint:
                raise CursorError("cursor does not belong to this query")
            return int(payload["t"]), str(payload["k"])
        except CursorError:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CursorError("malformed pagination cursor") from exc


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)
