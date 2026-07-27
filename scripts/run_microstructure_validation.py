"""Run bounded source-specific validation against genuine microstructure data.

This command reads only the microstructure sources and genuine mark labels.  It
does not load reserved OHLCV holdouts, construct strategies, promote features,
or access an order API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dashboard.microstructure import MicrostructureStore
from dashboard.microstructure_research import SourceSpecificEventStudy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database", type=Path,
        default=Path("data_cache/market_microstructure.db"))
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    report = SourceSpecificEventStudy(
        MicrostructureStore(arguments.database)).run_all_eligible()
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
