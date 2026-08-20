"""Bounded server-side scheduler for the durable automatic research cycle."""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.research_service import ResearchService


class DurableAutoResearchScheduler:
    """Persist cadence separately from durable cycle/job execution evidence."""

    def __init__(self, service: ResearchService, interval_hours: int,
                 clock=lambda: datetime.now(timezone.utc)) -> None:
        self.service = service
        self.interval_hours = max(1, int(interval_hours))
        self.clock = clock

    def initialize(self, is_enabled: bool) -> dict:
        return self.service.automatic_research.configure_scheduler(
            is_enabled, self.interval_hours, self.clock())

    def tick(self) -> dict:
        state = self.service.automatic_research.scheduler_state()
        if not state or not state["enabled"]:
            return {"triggered": False, "state": state}
        now = self.clock().astimezone(timezone.utc).replace(microsecond=0)
        due = datetime.fromisoformat(state["next_due_at"]).astimezone(timezone.utc)
        if due > now:
            return {"triggered": False, "state": state}
        cycle = self.service.automatic_research.start(requester="auto-research-scheduler")
        updated = self.service.automatic_research.record_scheduled_cycle(
            state["next_due_at"], int(cycle["id"]), self.interval_hours, now)
        return {"triggered": True, "cycle": cycle, "state": updated}

    def sleep_seconds(self, state: dict | None) -> float:
        if not state or not state.get("enabled"):
            return 60.0
        due = datetime.fromisoformat(state["next_due_at"]).astimezone(timezone.utc)
        return min(60.0, max(1.0, (due - self.clock().astimezone(timezone.utc)).total_seconds()))


def enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="enqueue one deduplicated cycle and wait for it")
    args = parser.parse_args()
    database = Path(os.getenv("PAPER_DB_PATH", "data_cache/paper_trades.db"))
    service = ResearchService(database)
    interval = max(1, int(os.getenv("AUTO_RESEARCH_INTERVAL_HOURS", "24")))
    if args.once:
        cycle = service.automatic_research.start()
        cycle_id = int(cycle["id"])
        while True:
            current = service.automatic_research.detail(cycle_id) or {}
            if current.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
                return 0 if current.get("status") == "COMPLETED" else 1
            time.sleep(2)
    scheduler = DurableAutoResearchScheduler(service, interval)
    state = scheduler.initialize(enabled("AUTO_RESEARCH_ENABLED"))
    while True:
        result = scheduler.tick()
        state = result.get("state") or state
        time.sleep(scheduler.sleep_seconds(state))


if __name__ == "__main__":
    raise SystemExit(main())
