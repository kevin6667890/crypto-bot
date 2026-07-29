"""Print bounded, read-only SQLite plans for production query-path auditing."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


MICRO_QUERIES = {
    "coverage_trade_group": """
        SELECT instrument,COUNT(*) rows,MIN(source_ts_ms),MAX(source_ts_ms)
        FROM trade_flow_observations GROUP BY instrument
    """,
    "health_trade_bounds": """
        SELECT MIN(source_ts_ms),MAX(source_ts_ms)
        FROM trade_flow_observations WHERE instrument=?
    """,
    "eligibility_trade_stats": """
        SELECT MIN(source_ts_ms),MAX(source_ts_ms),COUNT(*)
        FROM trade_flow_observations WHERE instrument=?
    """,
    "eligibility_mark_labels": """
        SELECT source_ts_ms FROM mark_price_observations
        WHERE instrument=? AND state='confirmed' ORDER BY source_ts_ms
    """,
    "summary_coverage": """
        SELECT lane,instrument,row_count,earliest_ms,latest_ms,
               generated_at_ms,data_as_of_ms,refreshing
        FROM source_runtime_summary ORDER BY lane,instrument
    """,
}

PAPER_QUERIES = {
    "operations_data_coverage": """
        SELECT instrument,timeframe,COUNT(*) rows,MIN(ts),MAX(ts)
        FROM historical_candles WHERE confirmed=1
        GROUP BY instrument,timeframe ORDER BY instrument,timeframe
    """,
    "operations_recent_jobs": """
        SELECT * FROM research_jobs ORDER BY id DESC LIMIT 30
    """,
    "operations_open_alerts": """
        SELECT * FROM system_alerts
        ORDER BY CASE status WHEN 'open' THEN 0
          WHEN 'acknowledged' THEN 1 ELSE 2 END,last_seen DESC LIMIT 100
    """,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("micro", "paper"))
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    queries = MICRO_QUERIES if args.kind == "micro" else PAPER_QUERIES
    connection = sqlite3.connect(
        f"file:{args.database}?mode=ro", uri=True, timeout=1)
    try:
        for name, query in queries.items():
            print(f"[{name}]")
            parameters = (
                ("BTC-USDT-SWAP",)
                if "?" in query else ())
            try:
                for row in connection.execute(
                    f"EXPLAIN QUERY PLAN {query}", parameters):
                    print("|".join(str(value) for value in row))
            except sqlite3.OperationalError as error:
                print(f"unavailable:{error}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
