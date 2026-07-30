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
from dashboard.storage_guard import update_storage_lifecycle_state  # noqa: E402


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
    update_storage_lifecycle_state(
        args.database.resolve().parent,
        raw_retention_status="ARCHIVE_VERIFIED",
        last_archive=report["archive_time"],
        last_archive_sha256=report["archive_sha256"],
        last_archive_window={
            "instrument": report["instrument"],
            "utc_day": report["utc_day"],
            "row_count": report["row_count"],
        },
        prune_backlog=report["row_count"],
    )
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
