"""Resumable, bounded logical snapshot of append-only CVD/OI raw tables.

The producer never holds a long-lived transaction: a manifest first freezes a
ROWID and source timestamp watermark for each table, then every chunk reads
only rows at or below both bounds.  The consumer verifies a deterministic
row fingerprint before committing each chunk.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


VERSION = "cvd-oi-logical-snapshot-v1"
TABLES = ("trade_flow_observations", "oi_observations")


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
        connection.execute("PRAGMA query_only=ON")
    else:
        connection = sqlite3.connect(path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-262144")
    connection.row_factory = sqlite3.Row
    return connection


def _canonical_row(values: Iterable[Any]) -> bytes:
    encoded = [
        {"__bytes__": value.hex()} if isinstance(value, bytes) else value
        for value in values
    ]
    return (json.dumps(encoded, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _decode(values: list[Any]) -> list[Any]:
    return [bytes.fromhex(value["__bytes__"])
            if isinstance(value, dict) and set(value) == {"__bytes__"}
            else value for value in values]


def manifest(source: Path, chunk_rows: int) -> dict[str, Any]:
    result: dict[str, Any] = {"version": VERSION, "source": str(source),
                              "chunk_rows": chunk_rows, "tables": {}}
    with _connect(source, True) as connection:
        for table in TABLES:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if schema is None:
                raise ValueError(f"missing source table: {table}")
            max_rowid = int(connection.execute(
                f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0)
            # These raw tables are append-only. ROWID lookups are O(1) even on
            # the production trade table, unlike an unindexed MAX(timestamp).
            latest = connection.execute(
                f"SELECT source_ts_ms FROM {table} WHERE rowid=?", (max_rowid,)
            ).fetchone()
            earliest = connection.execute(
                f"SELECT source_ts_ms FROM {table} ORDER BY rowid LIMIT 1"
            ).fetchone()
            watermark = int(latest[0] or 0) if latest else 0
            indexes = [row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND sql IS NOT NULL ORDER BY name", (table,))]
            result["tables"][table] = {
                "create_sql": schema[0], "index_sql": indexes,
                "max_rowid": max_rowid, "source_watermark_ms": watermark,
                "min_source_ts_ms": earliest[0] if earliest else None,
                "max_source_ts_ms": watermark,
                "chunks": [
                    {"low_rowid": low, "high_rowid": min(max_rowid, low + chunk_rows - 1)}
                    for low in range(1, max_rowid + 1, chunk_rows)
                ],
            }
    return result


def export_chunk(source: Path, table: str, low: int, high: int, watermark: int) -> None:
    if table not in TABLES:
        raise ValueError("unsupported table")
    digest = hashlib.sha256(); count = 0
    raw = sys.stdout.buffer
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream, _connect(source, True) as connection:
        header = {"version": VERSION, "table": table, "low_rowid": low,
                  "high_rowid": high, "source_watermark_ms": watermark}
        stream.write((json.dumps({"header": header}, separators=(",", ":")) + "\n").encode())
        query = (f"SELECT rowid,* FROM {table} WHERE rowid>=? AND rowid<=? "
                 "AND source_ts_ms<=? ORDER BY rowid")
        for row in connection.execute(query, (low, high, watermark)):
            payload = _canonical_row(tuple(row))
            digest.update(payload); count += 1; stream.write(payload)
        stream.write((json.dumps({"trailer": {"rows": count,
                      "sha256": digest.hexdigest()}}, separators=(",", ":")) + "\n").encode())


def import_chunk(destination: Path, manifest_path: Path, table: str, chunk: Path) -> dict[str, Any]:
    specification = json.loads(manifest_path.read_text(encoding="utf-8-sig"))["tables"][table]
    digest = hashlib.sha256(); count = 0; trailer = None
    with _connect(destination) as connection, gzip.open(chunk, "rb") as stream:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None:
            connection.execute(specification["create_sql"])
        connection.execute(
            "CREATE TABLE IF NOT EXISTS logical_snapshot_chunks("
            "table_name TEXT,low_rowid INTEGER,high_rowid INTEGER,row_count INTEGER,"
            "fingerprint TEXT,PRIMARY KEY(table_name,low_rowid,high_rowid))")
        header = json.loads(stream.readline())["header"]
        existing = connection.execute(
            "SELECT row_count,fingerprint FROM logical_snapshot_chunks "
            "WHERE table_name=? AND low_rowid=? AND high_rowid=?",
            (table, header["low_rowid"], header["high_rowid"]),
        ).fetchone()
        if existing is not None:
            return {"table": table, "rows": int(existing[0]),
                    "sha256": str(existing[1]), "resumed": True}
        columns = len(connection.execute(f"PRAGMA table_info({table})").fetchall())
        insert_sql = f"INSERT OR ABORT INTO {table} VALUES({','.join('?' for _ in range(columns))})"
        batch: list[list[Any]] = []
        for line in stream:
            decoded = json.loads(line)
            if "trailer" in decoded:
                trailer = decoded["trailer"]
                break
            digest.update(line); count += 1; batch.append(_decode(decoded)[1:])
            if len(batch) >= 10_000:
                connection.executemany(insert_sql, batch); batch.clear()
        if batch:
            connection.executemany(
                insert_sql, batch,
            )
        if trailer is None or count != int(trailer["rows"]) or digest.hexdigest() != trailer["sha256"]:
            raise ValueError(f"chunk verification failed: {chunk}")
        connection.execute(
            "INSERT OR REPLACE INTO logical_snapshot_chunks VALUES(?,?,?,?,?)",
            (table, header["low_rowid"], header["high_rowid"], count, trailer["sha256"]),
        )
    return {"table": table, "rows": count, "sha256": trailer["sha256"]}


def verify(destination: Path, manifest_path: Path) -> dict[str, Any]:
    specification = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    report = {"version": VERSION, "tables": {}, "ok": True,
              "quick_check": None, "foreign_key_errors": []}
    with _connect(destination) as connection:
        for table, expected in specification["tables"].items():
            for statement in expected.get("index_sql", []):
                connection.execute(re.sub(
                    r"^CREATE\s+(UNIQUE\s+)?INDEX\s+", r"CREATE \1INDEX IF NOT EXISTS ",
                    statement, count=1, flags=re.IGNORECASE,
                ))
            row = connection.execute(
                f"SELECT COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms) FROM {table}"
            ).fetchone()
            exported_rows = connection.execute(
                "SELECT COALESCE(SUM(row_count),0) FROM logical_snapshot_chunks WHERE table_name=?",
                (table,),
            ).fetchone()[0]
            actual = {"row_count": int(row[0]), "exported_row_count": int(exported_rows),
                      "min_source_ts_ms": row[1],
                      "max_source_ts_ms": row[2]}
            actual["ok"] = (
                actual["row_count"] == actual["exported_row_count"]
                and actual["min_source_ts_ms"] == expected["min_source_ts_ms"]
                and actual["max_source_ts_ms"] == expected["max_source_ts_ms"])
            report["tables"][table] = actual
            report["ok"] = report["ok"] and actual["ok"]
        report["quick_check"] = connection.execute("PRAGMA quick_check").fetchone()[0]
        report["foreign_key_errors"] = [list(row) for row in connection.execute(
            "PRAGMA foreign_key_check")]
        report["ok"] = (report["ok"] and report["quick_check"] == "ok"
                        and not report["foreign_key_errors"])
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True)
    value = commands.add_parser("manifest"); value.add_argument("--source", type=Path, required=True); value.add_argument("--chunk-rows", type=int, default=500_000)
    value = commands.add_parser("export"); value.add_argument("--source", type=Path, required=True); value.add_argument("--table", choices=TABLES, required=True); value.add_argument("--low", type=int, required=True); value.add_argument("--high", type=int, required=True); value.add_argument("--watermark", type=int, required=True)
    value = commands.add_parser("import"); value.add_argument("--destination", type=Path, required=True); value.add_argument("--manifest", type=Path, required=True); value.add_argument("--table", choices=TABLES, required=True); value.add_argument("--chunk", type=Path, required=True)
    value = commands.add_parser("verify"); value.add_argument("--destination", type=Path, required=True); value.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "manifest": print(json.dumps(manifest(args.source, args.chunk_rows), sort_keys=True))
    elif args.command == "export": export_chunk(args.source, args.table, args.low, args.high, args.watermark)
    elif args.command == "import": print(json.dumps(import_chunk(args.destination, args.manifest, args.table, args.chunk), sort_keys=True))
    else:
        result = verify(args.destination, args.manifest); print(json.dumps(result, sort_keys=True))
        if not result["ok"]: raise SystemExit(1)


if __name__ == "__main__":
    main()
