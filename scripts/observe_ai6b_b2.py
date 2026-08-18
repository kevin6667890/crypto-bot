#!/usr/bin/env python3
"""Read-only minute sampler for B2 operator-provided SQLite/JSON snapshots."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _count(connection: sqlite3.Connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def sample(ai_database: Path, paper_database: Path | None, operations: dict[str, Any], paper_baseline: int) -> dict[str, Any]:
    uri = f"file:{ai_database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        reports = _count(connection, "SELECT COUNT(*) FROM ai_market_reports")
        attempts = _count(connection, "SELECT COUNT(*) FROM ai_report_attempts")
        requests = _count(connection, "SELECT COUNT(*) FROM ai_report_requests")
        audits = _count(connection, "SELECT COUNT(*) FROM ai_report_audits")
        queue = _count(connection, "SELECT COUNT(*) FROM ai_report_request_events e WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_request_events x WHERE x.request_id=e.request_id) AND e.event_type IN ('QUEUED','RETRY_SCHEDULED','INTERRUPTED')")
        providers = {str(row[0]): int(row[1]) for row in connection.execute("SELECT provider,COUNT(*) FROM ai_report_attempts GROUP BY provider")}
        warnings = sum(bool(json.loads(row[0]).get("data_warnings"))
                       for row in connection.execute("SELECT response_json FROM ai_market_reports"))
        stale = int(operations.get("stale_presentations", 0))
    paper_orders = 0
    if paper_database:
        paper_uri = f"file:{paper_database.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(paper_uri, uri=True) as connection:
            paper_orders = _count(connection, "SELECT COUNT(*) FROM paper_trades")
    return {
        "sampled_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "report_requests": requests, "report_attempts": attempts, "reports": reports, "audits": audits,
        "presentation_calls": int(operations.get("presentation_calls", 0)), "queue_depth": queue,
        "warnings": warnings, "stale": stale, "frontend_errors": int(operations.get("frontend_errors", 0)),
        "provider_type": sorted(providers), "fake_provider_calls": providers.get("fake", 0),
        "live_provider_calls": sum(value for name, value in providers.items() if name != "fake"),
        "paper_orders": paper_orders, "paper_orders_delta": paper_orders - paper_baseline,
        "router": operations.get("router"), "collector": operations.get("collector"),
        "aggregation": operations.get("aggregation"), "old_ai_brief": operations.get("old_ai_brief"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai-db", type=Path, required=True)
    parser.add_argument("--paper-db", type=Path)
    parser.add_argument("--paper-orders-baseline", type=int, default=0)
    parser.add_argument("--operations-json", type=Path, required=True, help="read-only operator-exported snapshot")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    exit_code = 0
    with args.output.open("a", encoding="utf-8") as stream:
        for index in range(args.samples):
            operations = json.loads(args.operations_json.read_text(encoding="utf-8"))
            value = sample(args.ai_db, args.paper_db, operations, args.paper_orders_baseline)
            stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"); stream.flush()
            print(json.dumps(value, separators=(",", ":")))
            if value["live_provider_calls"] != 0:
                exit_code = 2
            if value["paper_orders_delta"] != 0:
                exit_code = 3
            if index + 1 < args.samples:
                time.sleep(max(1, args.interval_seconds))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
