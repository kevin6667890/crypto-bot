"""Build a new compact paper database from a verified offline source copy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.snapshot_bundle import build_compact_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--archive-directory", type=Path, required=True)
    parser.add_argument("--output-database", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = build_compact_database(
        args.source_database,
        args.archive_directory,
        args.output_database,
    )
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    if (
        report["quick_check"] != "ok"
        or report["foreign_key_violations"]
        or not report["schema_compatible"]
        or not report["non_snapshot_tables_match"]
        or not report["snapshot_ids_and_metadata_match"]
        or not report["archive_mapping_complete"]
        or report["user_version_old"] != report["user_version_new"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
