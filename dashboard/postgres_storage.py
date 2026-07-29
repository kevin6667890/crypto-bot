"""PostgreSQL storage prototype for immutable microstructure observations.

This module deliberately has no import-time database connection and does not
read a DSN from the environment.  Callers must pass an already-open connection.
The only optional driver-specific feature is psycopg3's COPY context manager.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SQL_ROOT = Path(__file__).resolve().parent.parent / "sql" / "postgres"
SCHEMA_SQL_PATH = SQL_ROOT / "storage_schema.sql"
PARTITION_SQL_PATH = SQL_ROOT / "monthly_partitions.sql"

MIN_EPOCH_MS = -62_135_596_800_000  # 0001-01-01T00:00:00Z
MAX_EPOCH_MS = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z

TRADE_COLUMNS = (
    "observed_at",
    "source_ts_ms",
    "source",
    "source_version",
    "instrument",
    "ingested_at",
    "ingested_at_ms",
    "resolution",
    "state",
    "source_identity",
    "uniqueness_key",
    "trade_id",
    "side",
    "price",
    "size",
    "contract_value",
    "notional",
    "provenance_table",
)

TRADE_UPSERT_SQL = """
INSERT INTO crypto_bot.trade_flow_observations (
    observed_at, source_ts_ms, source, source_version, instrument,
    ingested_at, ingested_at_ms, resolution, state, source_identity,
    uniqueness_key, trade_id, side, price, size, contract_value,
    notional, provenance_table
) VALUES (
    %(observed_at)s, %(source_ts_ms)s, %(source)s, %(source_version)s,
    %(instrument)s, %(ingested_at)s, %(ingested_at_ms)s, %(resolution)s,
    %(state)s, %(source_identity)s, %(uniqueness_key)s, %(trade_id)s,
    %(side)s, %(price)s, %(size)s, %(contract_value)s, %(notional)s,
    %(provenance_table)s
)
ON CONFLICT (observed_at, uniqueness_key) DO NOTHING
""".strip()

TRADE_COVERAGE_SUMMARY_SQL = """
SELECT
    instrument,
    COUNT(*) AS row_count,
    MIN(source_ts_ms) AS earliest_ms,
    MAX(source_ts_ms) AS latest_ms,
    MIN(observed_at) AS earliest_at,
    MAX(observed_at) AS latest_at
FROM crypto_bot.trade_flow_observations
WHERE observed_at >= %(start_at)s
  AND observed_at < %(end_at)s
GROUP BY instrument
ORDER BY instrument
""".strip()

TRADE_RANGE_PAGE_SQL = """
SELECT
    observed_at, source_ts_ms, source, source_version, instrument,
    ingested_at, ingested_at_ms, resolution, state, source_identity,
    uniqueness_key, trade_id, side, price, size, contract_value,
    notional, provenance_table
FROM crypto_bot.trade_flow_observations
WHERE instrument = %(instrument)s
  AND observed_at >= %(start_at)s
  AND observed_at < %(end_at)s
  AND (
      %(cursor_at)s IS NULL
      OR (observed_at, uniqueness_key) >
         (%(cursor_at)s, %(cursor_key)s)
  )
ORDER BY observed_at ASC, uniqueness_key ASC
LIMIT %(limit)s
""".strip()


SQLITE_TO_POSTGRES_FIELDS = {
    "source_ts_ms": "source_ts_ms",
    "ingested_at_ms": "ingested_at_ms",
    "source": "source",
    "source_version": "source_version",
    "instrument": "instrument",
    "resolution": "resolution",
    "state": "state",
    "source_identity": "source_identity",
    "uniqueness_key": "uniqueness_key",
    "trade_id": "trade_id",
    "side": "side",
    "price": "price",
    "size": "size",
    "contract_value": "contract_value",
    "notional": "notional",
    "provenance_table": "provenance_table",
}


def epoch_ms_to_timestamptz(value: int) -> datetime:
    """Convert an exact integer epoch millisecond to an aware UTC datetime."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("epoch milliseconds must be an integer")
    if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise ValueError("epoch milliseconds are outside PostgreSQL/Python bounds")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + timedelta(milliseconds=value)


def timestamptz_to_epoch_ms(value: datetime) -> int:
    """Convert an aware datetime to epoch ms without float rounding."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamptz values must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc_value - epoch
    return (
        delta.days * 86_400_000
        + delta.seconds * 1000
        + delta.microseconds // 1000
    )


def partition_name(table: str, source_ts_ms: int) -> str:
    """Return the deterministic UTC monthly partition name."""
    if not table or not table.replace("_", "").isalnum():
        raise ValueError("table must be an unqualified SQL identifier")
    month = epoch_ms_to_timestamptz(source_ts_ms)
    return f"{table}_{month:%Y_%m}"


def sqlite_trade_to_postgres(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a SQLite trade row without changing nullable or financial fields."""
    missing = [
        field
        for field in (
            "source_ts_ms",
            "ingested_at_ms",
            "source",
            "source_version",
            "instrument",
            "resolution",
            "state",
            "source_identity",
            "uniqueness_key",
            "side",
            "price",
            "size",
            "contract_value",
            "notional",
        )
        if field not in row
    ]
    if missing:
        raise KeyError(f"missing SQLite fields: {', '.join(missing)}")
    result = {
        postgres: row.get(sqlite)
        for sqlite, postgres in SQLITE_TO_POSTGRES_FIELDS.items()
    }
    result["source_ts_ms"] = int(result["source_ts_ms"])
    result["ingested_at_ms"] = int(result["ingested_at_ms"])
    result["observed_at"] = epoch_ms_to_timestamptz(result["source_ts_ms"])
    result["ingested_at"] = epoch_ms_to_timestamptz(result["ingested_at_ms"])
    return result


def deduplicate_trade_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Map and de-duplicate one batch by the partition-safe idempotency key."""
    unique: dict[tuple[datetime, str], dict[str, Any]] = {}
    for source in rows:
        row = sqlite_trade_to_postgres(source)
        key = (row["observed_at"], str(row["uniqueness_key"]))
        unique.setdefault(key, row)
    return list(unique.values())


def trade_row_values(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(column) for column in TRADE_COLUMNS)


@dataclass(frozen=True)
class PageCursor:
    observed_at: datetime
    uniqueness_key: str


def paginate_trade_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int,
    cursor: PageCursor | None = None,
) -> list[Mapping[str, Any]]:
    """Reference implementation of the SQL keyset ordering for unit tests."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    ordered = sorted(
        rows, key=lambda row: (row["observed_at"], row["uniqueness_key"])
    )
    if cursor is not None:
        cursor_key = (cursor.observed_at, cursor.uniqueness_key)
        ordered = [
            row
            for row in ordered
            if (row["observed_at"], row["uniqueness_key"]) > cursor_key
        ]
    return ordered[:limit]


def coverage_summary(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reference implementation of ``TRADE_COVERAGE_SUMMARY_SQL``."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["instrument"]), []).append(row)
    result = []
    for instrument in sorted(grouped):
        group = grouped[instrument]
        result.append(
            {
                "instrument": instrument,
                "row_count": len(group),
                "earliest_ms": min(int(row["source_ts_ms"]) for row in group),
                "latest_ms": max(int(row["source_ts_ms"]) for row in group),
                "earliest_at": min(row["observed_at"] for row in group),
                "latest_at": max(row["observed_at"] for row in group),
            }
        )
    return result


class PostgresTradeStorageAdapter:
    """Small DB-API/psycopg3 adapter; the caller owns connection lifecycle."""

    def __init__(self, connection: Any):
        if connection is None:
            raise ValueError("an explicit PostgreSQL connection is required")
        self.connection = connection

    def upsert(self, sqlite_row: Mapping[str, Any]) -> int:
        row = sqlite_trade_to_postgres(sqlite_row)
        with self.connection.cursor() as cursor:
            cursor.execute(TRADE_UPSERT_SQL, row)
            return max(0, int(cursor.rowcount))

    def copy_batch(self, sqlite_rows: Iterable[Mapping[str, Any]]) -> int:
        """COPY into a temporary stage, then merge idempotently.

        psycopg3 uses ``cursor.copy``.  Other DB-API drivers fall back to
        ``executemany`` against the same staging table.
        """
        rows = deduplicate_trade_rows(sqlite_rows)
        if not rows:
            return 0
        column_sql = ", ".join(TRADE_COLUMNS)
        placeholders = ", ".join(["%s"] * len(TRADE_COLUMNS))
        with self.connection.cursor() as cursor:
            cursor.execute(
                """CREATE TEMP TABLE IF NOT EXISTS _trade_flow_stage
                   (LIKE crypto_bot.trade_flow_observations
                    INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
                   ON COMMIT DROP"""
            )
            cursor.execute("TRUNCATE _trade_flow_stage")
            if hasattr(cursor, "copy"):
                with cursor.copy(
                    f"COPY _trade_flow_stage ({column_sql}) FROM STDIN"
                ) as copy:
                    for row in rows:
                        copy.write_row(trade_row_values(row))
            else:
                cursor.executemany(
                    f"INSERT INTO _trade_flow_stage ({column_sql}) "
                    f"VALUES ({placeholders})",
                    [trade_row_values(row) for row in rows],
                )
            cursor.execute(
                f"""INSERT INTO crypto_bot.trade_flow_observations ({column_sql})
                    SELECT {column_sql} FROM _trade_flow_stage
                    ON CONFLICT (observed_at, uniqueness_key) DO NOTHING"""
            )
            return max(0, int(cursor.rowcount))

    def range_page(
        self,
        *,
        instrument: str,
        start_at: datetime,
        end_at: datetime,
        limit: int = 1000,
        cursor: PageCursor | None = None,
    ) -> Sequence[Any]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        parameters = {
            "instrument": instrument,
            "start_at": start_at,
            "end_at": end_at,
            "cursor_at": cursor.observed_at if cursor else None,
            "cursor_key": cursor.uniqueness_key if cursor else None,
            "limit": limit,
        }
        with self.connection.cursor() as database_cursor:
            database_cursor.execute(TRADE_RANGE_PAGE_SQL, parameters)
            return database_cursor.fetchall()

    def coverage(
        self, *, start_at: datetime, end_at: datetime
    ) -> Sequence[Any]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                TRADE_COVERAGE_SUMMARY_SQL,
                {"start_at": start_at, "end_at": end_at},
            )
            return cursor.fetchall()
