"""Optional standalone tracked-thesis reevaluation worker."""
from __future__ import annotations

import logging
import os
import signal
import threading

from dashboard.thesis_tracking import ThesisTrackingSchedulerV1
from dashboard.thesis_tracking_runtime import build_tracking_service_from_environment


def main() -> int:
    if os.getenv("THESIS_TRACKING_SCHEDULER_ENABLED", "false").lower() != "true":
        logging.getLogger(__name__).info("thesis tracking scheduler is disabled")
        return 0
    # Build only tracking dependencies. Importing the API application here
    # would initialize unrelated product stores and violate worker isolation.
    service = build_tracking_service_from_environment()
    scheduler = ThesisTrackingSchedulerV1(
        service, cadence_seconds=int(os.getenv("THESIS_TRACKING_SCHEDULER_CADENCE_SECONDS", "900")))
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    scheduler.enabled = True
    while not stopped.is_set():
        scheduler.tick()
        stopped.wait(scheduler.cadence_seconds)
    scheduler.enabled = False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
