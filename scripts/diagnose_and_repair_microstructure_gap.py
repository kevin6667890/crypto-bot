"""Diagnose and safely repair a bounded canonical CVD/OI aggregate gap."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure_gap_repair import AggregateGapRepair


def timestamp_ms(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def active_lock(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("status") == "ACTIVE"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--source", choices=("cvd", "oi"), required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--resolution", default="1m", choices=("1m",))
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--rebuild-aggregates", action="store_true")
    parser.add_argument("--official-backfill", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-manifest", type=Path)
    parser.add_argument("--max-rows", type=int, default=10_000)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--maintenance-lock", type=Path)
    args = parser.parse_args()
    if args.max_rows < 1:
        parser.error("--max-rows must be positive")
    if args.apply and not active_lock(args.maintenance_lock):
        parser.error("--apply requires an ACTIVE JSON --maintenance-lock")

    start_ms, end_ms = timestamp_ms(args.start), timestamp_ms(args.end)
    if args.resume and args.checkpoint and args.checkpoint.is_file():
        try:
            checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            checkpoint = {}
        if (
            checkpoint.get("status") == "applied"
            and checkpoint.get("instrument") == args.instrument
            and checkpoint.get("source") == args.source
            and checkpoint.get("start_ms") == start_ms
            and checkpoint.get("end_ms") == end_ms
        ):
            print(json.dumps({
                "status": "ALREADY_APPLIED",
                "checkpoint": str(args.checkpoint),
            }, indent=2, sort_keys=True))
            return
    repair = AggregateGapRepair(args.database)
    report = repair.diagnose(
        args.instrument, args.source, start_ms, end_ms
    )
    report.update({
        "mode": "apply" if args.apply else "dry-run",
        "database": str(args.database),
        "resolution": args.resolution,
        "orders_modified": False,
        "official_backfill_rows": 0,
    })
    if args.official_backfill:
        if report["unrecoverable_bucket_count"]:
            report["official_backfill"] = {
                "status": "NOT_APPLIED",
                "reason": (
                    "No bounded official source is configured for this exact "
                    "gap; the raw gap remains explicit."
                ),
            }
        else:
            report["official_backfill"] = {
                "status": "NOT_REQUIRED", "reason": "raw data is complete"
            }
    if args.apply and args.rebuild_aggregates:
        report.update(repair.rebuild(
            args.instrument, args.source, start_ms, end_ms,
            max_rows=args.max_rows,
        ))
    if args.verify:
        report["verification"] = repair.diagnose(
            args.instrument, args.source, start_ms, end_ms
        )
    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.output_manifest:
        args.output_manifest.write_text(serialized + "\n", encoding="utf-8")
    if args.checkpoint:
        args.checkpoint.write_text(json.dumps({
            "status": "applied" if args.apply else "diagnosed",
            "instrument": args.instrument,
            "source": args.source,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }, sort_keys=True) + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
