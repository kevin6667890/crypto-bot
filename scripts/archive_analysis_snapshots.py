"""Build and fully verify monthly analysis snapshot bundles offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.snapshot_bundle import (  # noqa: E402
    build_snapshot_bundles,
    verify_snapshot_bundle,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--source-database", type=Path, required=True)
    value.add_argument("--archive-directory", type=Path, required=True)
    value.add_argument("--compression", choices=("zstd", "gzip"), default="zstd")
    value.add_argument("--report", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    index = build_snapshot_bundles(
        args.source_database,
        args.archive_directory,
        compression=args.compression,
    )
    verifications = [
        verify_snapshot_bundle(args.archive_directory / item["path"])
        for item in index["bundles"]
    ]
    report = {**index, "bundle_verification": verifications}
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
