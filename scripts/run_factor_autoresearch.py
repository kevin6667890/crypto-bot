"""Run bounded, snapshot-only Phase 6F factor AutoResearch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.factor_autoresearch import (
    ControlledInterruption,
    FactorAutoResearch,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument(
        "--interrupt-after", choices=("generation",), default=None)
    arguments = parser.parse_args()
    runner = FactorAutoResearch(
        arguments.snapshot, arguments.ledger, workers=arguments.workers)
    try:
        report = runner.run(
            interrupt_after=arguments.interrupt_after,
            report_path=arguments.report, library_path=arguments.library)
    except ControlledInterruption as error:
        print(json.dumps({
            "status": "CONTROLLED_INTERRUPTION",
            "run_id": runner.run_id,
            "message": str(error),
        }, sort_keys=True))
        raise SystemExit(75)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
