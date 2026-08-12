#!/usr/bin/env python3
"""Apply the single bounded liquidation lookup index with hash approval."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from dashboard.microstructure import (
    LIQUIDATION_QUERY_INDEX_NAME,
    LIQUIDATION_QUERY_INDEX_SQL,
)

MIGRATION_SQL = LIQUIDATION_QUERY_INDEX_SQL + ";"
MIGRATION_SHA256 = hashlib.sha256(MIGRATION_SQL.encode("utf-8")).hexdigest()
PLAN_SQL = """SELECT source_ts_ms,side,size,price,reliability_note
FROM liquidation_observations
WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<?
ORDER BY source_ts_ms"""


def _content_sha256(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    cursor = connection.execute(
        "SELECT * FROM liquidation_observations ORDER BY uniqueness_key"
    )
    for row in cursor:
        digest.update(json.dumps(tuple(row),ensure_ascii=False,separators=(",",":"),
                                 default=str).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _state(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms) "
        "FROM liquidation_observations"
    ).fetchone()
    plan = [str(item[3]) for item in connection.execute(
        "EXPLAIN QUERY PLAN " + PLAN_SQL,
        ("BTC-USDT-SWAP",0,366*86_400_000),
    )]
    indexes = {
        str(item[1]): [str(column[2]) for column in connection.execute(
            f"PRAGMA index_info({json.dumps(str(item[1]))})"
        )]
        for item in connection.execute("PRAGMA index_list(liquidation_observations)")
    }
    return {
        "row_count": int(row[0]), "min_source_ts_ms": row[1], "max_source_ts_ms": row[2],
        "content_sha256": _content_sha256(connection), "indexes": indexes, "plan": plan,
    }


def inspect_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True,timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        return _state(connection)
    finally:
        connection.close()


def apply_index(path: Path,expected_sha256: str) -> dict[str, Any]:
    if expected_sha256 != MIGRATION_SHA256:
        raise ValueError("MIGRATION_SHA256_MISMATCH")
    size_before = path.stat().st_size
    connection = sqlite3.connect(path,timeout=5)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        before = _state(connection)
        started = time.monotonic()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(LIQUIDATION_QUERY_INDEX_SQL)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        duration = time.monotonic()-started
        after = _state(connection)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    preserved = all(before[key] == after[key] for key in (
        "row_count","min_source_ts_ms","max_source_ts_ms","content_sha256"
    ))
    return {
        "migration_sha256": MIGRATION_SHA256,
        "index_name": LIQUIDATION_QUERY_INDEX_NAME,
        "duration_seconds": round(duration,6),
        "size_before": size_before, "size_after": path.stat().st_size,
        "before": before, "after": after, "rows_preserved": preserved,
        "integrity_check": integrity,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run",action="store_true")
    group.add_argument("--apply",action="store_true")
    parser.add_argument("--expected-sha256")
    args=parser.parse_args()
    if args.dry_run:
        result={"dry_run":True,"migration_sha256":MIGRATION_SHA256,
                "database":str(args.database.resolve()),"state":inspect_database(args.database)}
    else:
        if not args.expected_sha256:
            parser.error("--expected-sha256 is required with --apply")
        result=apply_index(args.database,args.expected_sha256)
    print(json.dumps(result,sort_keys=True,separators=(",",":")))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
