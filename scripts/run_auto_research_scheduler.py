"""Bounded server-side scheduler for the durable automatic research cycle."""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.research_service import ResearchService


def enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="enqueue one deduplicated cycle and wait for it")
    args = parser.parse_args()
    database = Path(os.getenv("PAPER_DB_PATH", "data_cache/paper_trades.db"))
    service = ResearchService(database)
    interval = max(1, int(os.getenv("AUTO_RESEARCH_INTERVAL_HOURS", "24")))
    if not args.once and not enabled("AUTO_RESEARCH_ENABLED"):
        while True:
            time.sleep(60)
    while True:
        cycle = service.automatic_research.start()
        cycle_id = int(cycle["id"])
        while True:
            current = service.automatic_research.detail(cycle_id) or {}
            if current.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                break
            time.sleep(2)
        if args.once:
            return 0 if current.get("status") == "COMPLETED" else 1
        target = datetime.now(timezone.utc) + timedelta(hours=interval)
        while datetime.now(timezone.utc) < target:
            time.sleep(min(60, max(1, (target-datetime.now(timezone.utc)).total_seconds())))


if __name__ == "__main__":
    raise SystemExit(main())
