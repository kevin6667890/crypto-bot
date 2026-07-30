"""Bounded raw-trade prune; dry-run unless --apply is explicit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure_lifecycle import (  # noqa: E402
    load_raw_trade_manifest,
    prune_archived_raw_trades,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--offhost-ack", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-rows", type=int, default=50_000)
    parser.add_argument("--wall-clock-seconds", type=float, default=20)
    parser.add_argument("--queue-depth", type=int, default=0)
    parser.add_argument("--writer-lag-ms", type=int, default=0)
    parser.add_argument("--critical-gap-count", type=int, default=0)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.max_rows <= 0 or args.max_rows > 100_000:
        parser.error("--max-rows must be between 1 and 100000")
    manifest = load_raw_trade_manifest(args.manifest)
    ack = json.loads(args.offhost_ack.read_text(encoding="utf-8"))
    if args.resume and args.checkpoint and args.checkpoint.exists():
        saved = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        if saved.get("archive_sha256") != manifest.get("archive_sha256"):
            raise SystemExit("checkpoint belongs to a different archive")
        if saved.get("status") == "ARCHIVED_CONFIRMED":
            print(json.dumps(saved, sort_keys=True, indent=2))
            return 0
    report = prune_archived_raw_trades(
        args.database,
        manifest,
        ack,
        apply=args.apply,
        max_rows=args.max_rows,
        wall_clock_seconds=args.wall_clock_seconds,
        queue_depth=args.queue_depth,
        writer_lag_ms=args.writer_lag_ms,
        critical_gap_count=args.critical_gap_count,
    )
    report.update(
        {
            "archive_sha256": manifest["archive_sha256"],
            "checkpoint_time": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
        }
    )
    serialized = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.checkpoint:
        args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.checkpoint.with_suffix(args.checkpoint.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.checkpoint)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
