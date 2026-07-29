"""Bounded read-only production acceptance evidence for Crypto-Bot."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    return connection


def continuity(rows: list[int], step: int) -> dict[str, Any]:
    differences = [right - left for left, right in zip(rows, rows[1:])]
    return {
        "rows": len(rows),
        "first": rows[0] if rows else None,
        "last": rows[-1] if rows else None,
        "duplicates": len(rows) - len(set(rows)),
        "out_of_order": sum(value <= 0 for value in differences),
        "missing_intervals": sum(value != step for value in differences),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paper", type=Path)
    parser.add_argument("micro", type=Path)
    args = parser.parse_args()
    output: dict[str, Any] = {"generated_at_unix": int(time.time())}
    with connect(args.paper) as paper:
        output["paper_orders"] = {
            row["status"]: int(row["rows"]) for row in paper.execute(
                "SELECT status,COUNT(*) rows FROM paper_trades GROUP BY status")}
        output["paper_orders_total"] = sum(output["paper_orders"].values())
        output["flow_raw_counts"] = {
            table: int(paper.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("flow_trade_buckets", "oi_snapshots")
        }
        output["kline_last_1000"] = {}
        for instrument in ("BTC-USDT", "ETH-USDT", "SOL-USDT"):
            values = [
                int(row[0]) for row in paper.execute(
                    """SELECT ts FROM historical_candles
                       WHERE instrument=? AND timeframe='15m' AND confirmed=1
                       ORDER BY ts DESC LIMIT 1000""", (instrument,))
            ][::-1]
            output["kline_last_1000"][instrument] = continuity(values, 900)
    with connect(args.micro) as micro:
        output["micro_table_row_counts"] = {
            row["table_name"]: int(row["row_count"])
            for row in micro.execute(
                "SELECT table_name,row_count FROM table_row_counts")}
        tables = {
            "trades": ("trade_flow_observations", "BTC-USDT-SWAP"),
            "oi": ("oi_observations", "BTC-USDT-SWAP"),
            "mark": ("mark_price_observations", "BTC-USDT-SWAP"),
            "index": ("index_price_observations", "BTC-USDT"),
        }
        output["micro_latest"] = {
            lane: micro.execute(
                f"""SELECT source_ts_ms FROM {table} WHERE instrument=?
                    ORDER BY source_ts_ms DESC LIMIT 1""",
                (instrument,)).fetchone()[0]
            for lane, (table, instrument) in tables.items()
        }
        cutoff = int(time.time() * 1000) - 86_400_000
        output["gaps_detected_last_24h"] = [
            dict(row) for row in micro.execute(
                """SELECT lane,instrument,COUNT(*) rows,
                          SUM(CASE WHEN resolved_at_ms IS NULL THEN 1 ELSE 0 END)
                            unresolved
                   FROM collection_gaps WHERE detected_at_ms>=?
                   GROUP BY lane,instrument ORDER BY lane,instrument""",
                (cutoff,))
        ]
        output["journal_size_limit"] = micro.execute(
            "PRAGMA journal_size_limit").fetchone()[0]
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
