"""Optional standalone tracked-thesis reevaluation worker."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import threading

from dashboard.market_context_v2 import BoundedMarketDataReaderV2
from dashboard.thesis_tracking import (
    CurrentFeatureEvaluatorV1, ThesisTrackingRepositoryV1,
    ThesisTrackingSchedulerV1, ThesisTrackingServiceV1,
)


def main() -> int:
    if os.getenv("THESIS_TRACKING_SCHEDULER_ENABLED", "false").lower() != "true":
        logging.getLogger(__name__).info("thesis tracking scheduler is disabled")
        return 0
    paper_db = Path(os.environ["PAPER_DB_PATH"])
    tracking_db = Path(os.environ["THESIS_TRACKING_DB_PATH"])
    repository = ThesisTrackingRepositoryV1(tracking_db)
    evaluator = CurrentFeatureEvaluatorV1(BoundedMarketDataReaderV2(paper_db))
    service = ThesisTrackingServiceV1(repository, None, evaluator)
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
