"""Optional standalone tracked-thesis reevaluation worker."""
from __future__ import annotations

import logging
import os
import signal
import threading

from dashboard.thesis_tracking import ThesisTrackingSchedulerV1


def main() -> int:
    if os.getenv("THESIS_TRACKING_SCHEDULER_ENABLED", "false").lower() != "true":
        logging.getLogger(__name__).info("thesis tracking scheduler is disabled")
        return 0
    # Import the fully configured, version-dispatching service lazily so the
    # standalone worker uses exactly the same V1/V2 evaluators as the API.
    from dashboard.paper_api import THESIS_TRACKING_SERVICE as service
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
