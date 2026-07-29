"""Create one verified UTC-day × instrument raw-trade archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure_lifecycle import archive_raw_trade_day  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--archive-directory", type=Path, required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--utc-day", required=True)
    parser.add_argument("--compression", choices=("gzip",), default="gzip")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = archive_raw_trade_day(
        args.database,
        args.archive_directory,
        instrument=args.instrument,
        utc_day=args.utc_day,
        compression=args.compression,
    )
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
