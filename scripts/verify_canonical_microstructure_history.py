#!/usr/bin/env python3
"""Produce a deterministic, read-only quality report for canonical history."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


RESOLUTIONS = ("5m", "15m", "1h", "4h", "1D")
COMPLETE = ("VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED")


def gap_segments(connection: sqlite3.Connection, table: str) -> list[dict]:
    output: list[dict] = []
    for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
        current: dict | None = None
        for row in connection.execute(
            f"""SELECT bucket_ms,status,gap_reason FROM {table}
                 WHERE instrument=? AND status NOT IN (?,?,?) ORDER BY bucket_ms""",
            (instrument, *COMPLETE),
        ):
            timestamp, status, reason = int(row[0]), str(row[1]), row[2]
            if (current is None or timestamp != current["end_ms_exclusive"]
                    or status != current["status"] or reason != current["gap_reason"]):
                current = {
                    "instrument": instrument, "status": status,
                    "gap_reason": reason, "start_ms": timestamp,
                    "end_ms_exclusive": timestamp + 60_000, "minutes": 1,
                }
                output.append(current)
            else:
                current["end_ms_exclusive"] += 60_000
                current["minutes"] += 1
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def grouped(connection: sqlite3.Connection, table: str, columns: str) -> list[dict]:
    rows = connection.execute(
        f"SELECT {columns}, COUNT(*) AS rows FROM {table} GROUP BY {columns} ORDER BY {columns}"
    ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    database = args.database.resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
    foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]

    report: dict = {
        "database": str(database),
        "size_bytes": database.stat().st_size,
        "sha256": sha256(database),
        "quick_check": quick_check,
        "foreign_key_errors": foreign_key_errors,
        "metadata": {
            row[0]: json.loads(row[1])
            for row in connection.execute("SELECT key,value_json FROM canonical_metadata ORDER BY key")
        },
        "counts": {
            "cvd_1m": grouped(connection, "cvd_1m", "instrument,status"),
            "oi_1m": grouped(connection, "oi_1m", "instrument,status"),
            "cvd_higher": grouped(
                connection, "cvd_higher_timeframes", "instrument,resolution,status"
            ),
            "oi_higher": grouped(
                connection, "oi_higher_timeframes", "instrument,resolution,status"
            ),
        },
        "fingerprint_conflicts": {},
        "reconciliation": {},
        "gap_segments": {
            "cvd_1m": gap_segments(connection, "cvd_1m"),
            "oi_1m": gap_segments(connection, "oi_1m"),
        },
    }

    schema = [
        [row[0], row[1], row[2], row[3]] for row in connection.execute(
            """SELECT type,name,tbl_name,sql FROM sqlite_schema
                 WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
        )
    ]
    report["schema_sha256"] = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report["source_assets"] = [
        dict(row) for row in connection.execute(
            """SELECT instrument,source,start_ms,end_ms,earliest_ms,latest_ms,
                      row_count,unique_identity_count,duplicate_count,
                      out_of_order_count,timestamp_error_count,trade_id_conflict_count,
                      source_fingerprint,ingestion_source,official_backfill_manifest_id,
                      gap_status
                 FROM source_assets ORDER BY source,instrument,start_ms"""
        )
    ]
    report["official_backfills"] = [
        dict(row) for row in connection.execute(
            """SELECT manifest_id,source,instrument,requested_start_ms,
                      requested_end_ms,endpoint_or_file,request_count,row_count,
                      unique_row_count,overlap_status,dedupe_key
                 FROM official_backfill_manifests ORDER BY source,instrument,manifest_id"""
        )
    ]

    for table in ("cvd_1m", "oi_1m", "cvd_higher_timeframes", "oi_higher_timeframes"):
        report["fingerprint_conflicts"][table] = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE status='CONFLICT'"
        ).fetchone()[0]

    placeholders = ",".join("?" for _ in COMPLETE)
    cvd_mismatches = connection.execute(
        f"""
        SELECT instrument,utc_date,delta_sum,final_cumulative
          FROM daily_reconciliation
         WHERE series='cvd' AND status IN ({placeholders})
           AND ABS(COALESCE(delta_sum,0)-COALESCE(final_cumulative,0)) > 0.000001
         ORDER BY instrument,utc_date
        """,
        COMPLETE,
    ).fetchall()
    report["reconciliation"]["complete_cvd_daily_mismatches"] = [
        dict(row) for row in cvd_mismatches
    ]
    report["reconciliation"]["oi_cross_gap_derived_rows"] = connection.execute(
        """
        SELECT COUNT(*) FROM oi_higher_timeframes
         WHERE confirmed_oi IS NOT NULL
           AND status NOT IN ('VALID','BACKFILLED_OFFICIAL','ARCHIVED_CONFIRMED')
        """
    ).fetchone()[0]
    report["reconciliation"]["cvd_arithmetic_violations"] = connection.execute(
        """SELECT COUNT(*) FROM cvd_1m WHERE signed_delta IS NOT NULL
             AND ABS((buy_volume-sell_volume)-signed_delta)>0.000001"""
    ).fetchone()[0]
    report["reconciliation"]["oi_observation_time_violations"] = connection.execute(
        """SELECT COUNT(*) FROM oi_1m WHERE confirmed_oi IS NOT NULL
             AND (observation_ts_ms<bucket_ms OR observation_ts_ms>=bucket_ms+60000)"""
    ).fetchone()[0]
    report["reconciliation"]["generated_commits"] = [
        row[0] for row in connection.execute(
            "SELECT DISTINCT generated_commit FROM cvd_1m ORDER BY generated_commit"
        )
    ]

    completeness: list[dict] = []
    for series, table in (("cvd", "cvd_1m"), ("oi", "oi_1m")):
        for row in connection.execute(
            f"""
            SELECT instrument,COUNT(*) total,
                   SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END) complete
              FROM {table} GROUP BY instrument ORDER BY instrument
            """,
            COMPLETE,
        ):
            completeness.append(
                {
                    "series": series,
                    "instrument": row[0],
                    "resolution": "1m",
                    "total": row[1],
                    "complete": row[2],
                    "complete_percent": round(100 * row[2] / row[1], 6),
                }
            )
        higher = f"{series}_higher_timeframes"
        for row in connection.execute(
            f"""
            SELECT instrument,resolution,COUNT(*) total,
                   SUM(CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END) complete
              FROM {higher} GROUP BY instrument,resolution ORDER BY instrument,resolution
            """,
            COMPLETE,
        ):
            completeness.append(
                {
                    "series": series,
                    "instrument": row[0],
                    "resolution": row[1],
                    "total": row[2],
                    "complete": row[3],
                    "complete_percent": round(100 * row[3] / row[2], 6),
                }
            )
    report["completeness"] = completeness
    connection.close()

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if quick_check == ["ok"] and not foreign_key_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
