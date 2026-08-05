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
            bound = connection.execute(
                f"SELECT MAX(rowid),MAX(source_ts_ms),MIN(source_ts_ms),COUNT(*) FROM {table}"
            ).fetchone()
            max_rowid = int(bound[0] or 0)
            watermark = int(bound[1] or 0)
            bounded = connection.execute(
                f"SELECT COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms) FROM {table} "
                "WHERE rowid<=? AND source_ts_ms<=?", (max_rowid, watermark)
            ).fetchone()
            indexes = [row[0] for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND sql IS NOT NULL ORDER BY name", (table,))]
            result["tables"][table] = {
                "create_sql": schema[0], "index_sql": indexes,
                "max_rowid": max_rowid, "source_watermark_ms": watermark,
                "row_count": int(bounded[0]), "min_source_ts_ms": bounded[1],
                "max_source_ts_ms": bounded[2],
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
    specification = json.loads(manifest_path.read_text(encoding="utf-8"))["tables"][table]
    digest = hashlib.sha256(); count = 0; trailer = None
    with _connect(destination) as connection, gzip.open(chunk, "rb") as stream:
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
    specification = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = {"version": VERSION, "tables": {}, "ok": True}
    with _connect(destination) as connection:
        for table, expected in specification["tables"].items():
            row = connection.execute(
                f"SELECT COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms) FROM {table}"
            ).fetchone()
            actual = {"row_count": int(row[0]), "min_source_ts_ms": row[1],
                      "max_source_ts_ms": row[2]}
            actual["ok"] = all(actual[key] == expected[key] for key in actual if key != "ok")
            report["tables"][table] = actual
            report["ok"] = report["ok"] and actual["ok"]
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
