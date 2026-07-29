"""Capture bounded, minute-level operations summaries into a local SQLite DB.

Disabled by default. Example:
  python scripts/capture_operations_trends.py --enabled --once
  python scripts/capture_operations_trends.py --enabled
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".runtime" / "operations_trends.db"
RETENTION_SECONDS = 7 * 24 * 60 * 60
ENDPOINTS = {
    "operations": "/api/operations/summary",
    "health": "/api/research/microstructure/health",
    "coverage": "/api/research/microstructure/coverage",
    "eligibility": "/api/research/microstructure/eligibility",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations_trends(
    minute_ts INTEGER PRIMARY KEY,
    captured_at TEXT NOT NULL,
    health_latency_ms REAL,
    coverage_latency_ms REAL,
    eligibility_latency_ms REAL,
    wal_size_bytes INTEGER,
    maintenance_duration_ms REAL,
    checkpoint_duration_ms REAL,
    queue_depth INTEGER,
    live_lag_seconds REAL,
    iowait_percent REAL,
    critical_gap_count INTEGER,
    service_state TEXT NOT NULL
);
"""


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(SCHEMA)


def request_json(base_url: str, path: str, timeout: float) -> tuple[dict[str, Any] | None, float | None]:
    target = urlsplit(base_url)
    if target.username or target.password:
        raise ValueError("Credentials are not accepted in the operations trend base URL")
    started = time.monotonic()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "crypto-bot-local-trends/1"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
            return (payload if isinstance(payload, dict) else None,
                    round((time.monotonic() - started) * 1000, 3))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None, None


def nested_number(payload: dict[str, Any] | None, *paths: tuple[str, ...]) -> float | None:
    for path in paths:
        value: Any = payload
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def capture_sample(
    connection: sqlite3.Connection,
    *,
    base_url: str,
    timeout: float = 3.0,
    now: int | None = None,
) -> int:
    timestamp = int(time.time()) if now is None else int(now)
    minute_ts = timestamp - timestamp % 60
    replies = {name: request_json(base_url, path, timeout)
               for name, path in ENDPOINTS.items()}
    operations = replies["operations"][0]
    health = replies["health"][0]
    coverage = replies["coverage"][0]
    service_state = str(
        ((operations or {}).get("service") or {}).get("status") or "UNAVAILABLE")
    if operations is not None and any(replies[name][0] is None for name in ("health", "coverage", "eligibility")):
        service_state = "PARTIAL_QUERY_FAILURE"

    critical_gap = nested_number(
        health, ("critical_gap_count",), ("gaps", "critical_count"),
        ("unresolved_gaps", "critical"))
    if critical_gap is None and coverage is not None:
        critical_gap = nested_number(coverage, ("critical_gap_count",))

    initialize(connection)
    connection.execute(
        """INSERT INTO operations_trends(
               minute_ts, captured_at, health_latency_ms, coverage_latency_ms,
               eligibility_latency_ms, wal_size_bytes, maintenance_duration_ms,
               checkpoint_duration_ms, queue_depth, live_lag_seconds,
               iowait_percent, critical_gap_count, service_state)
           VALUES(?, datetime(?, 'unixepoch'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(minute_ts) DO UPDATE SET
               captured_at=excluded.captured_at,
               health_latency_ms=excluded.health_latency_ms,
               coverage_latency_ms=excluded.coverage_latency_ms,
               eligibility_latency_ms=excluded.eligibility_latency_ms,
               wal_size_bytes=excluded.wal_size_bytes,
               maintenance_duration_ms=excluded.maintenance_duration_ms,
               checkpoint_duration_ms=excluded.checkpoint_duration_ms,
               queue_depth=excluded.queue_depth,
               live_lag_seconds=excluded.live_lag_seconds,
               iowait_percent=excluded.iowait_percent,
               critical_gap_count=excluded.critical_gap_count,
               service_state=excluded.service_state""",
        (
            minute_ts, timestamp, replies["health"][1], replies["coverage"][1],
            replies["eligibility"][1],
            nested_number(operations, ("wal_size_bytes",)),
            nested_number(operations, ("maintenance", "last_duration_ms")),
            nested_number(operations, ("maintenance", "checkpoint_duration_ms")),
            nested_number(operations, ("collector", "queue_depth")),
            nested_number(health, ("live_lag_seconds",), ("collector", "live_lag_seconds")),
            nested_number(
                operations,
                ("iowait_percent",),
                ("system", "iowait_percent"),
                ("host", "iowait_percent"),
            ),
            int(critical_gap) if critical_gap is not None else None,
            service_state,
        ),
    )
    connection.execute(
        "DELETE FROM operations_trends WHERE minute_ts <= ?",
        (minute_ts - RETENTION_SECONDS,),
    )
    connection.commit()
    return minute_ts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled", action="store_true", help="enable local capture (default: false)")
    parser.add_argument("--once", action="store_true", help="capture one minute and exit")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    if not args.enabled:
        print("operations trend capture disabled (enabled=false)")
        return 0
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db) as connection:
        while True:
            captured = capture_sample(
                connection, base_url=args.base_url,
                timeout=max(.1, min(args.timeout, 10.0)))
            print(f"captured operations trend minute={captured}")
            if args.once:
                return 0
            time.sleep(max(1, 60 - int(time.time()) % 60))


if __name__ == "__main__":
    raise SystemExit(main())
