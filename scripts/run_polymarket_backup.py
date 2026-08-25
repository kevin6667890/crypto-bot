"""Capacity-guarded SQLite online backup with bounded calendar retention."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from dashboard.polymarket.operations import online_backup, verify_backup
from dashboard.polymarket.repository import PolymarketRepository


BACKUP_PREFIX = "polymarket_research-"
BACKUP_SUFFIX = ".sqlite"


def _timestamp(path: Path) -> datetime | None:
    name = path.name
    if not (name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX)):
        return None
    raw = name[len(BACKUP_PREFIX) : -len(BACKUP_SUFFIX)]
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def retention_set(paths: list[Path], daily: int, weekly: int) -> set[Path]:
    dated = sorted(((stamp, path) for path in paths if (stamp := _timestamp(path))), reverse=True)
    keep: set[Path] = set()
    days: set[object] = set()
    for stamp, path in dated:
        if len(days) >= daily:
            break
        if stamp.date() not in days:
            days.add(stamp.date())
            keep.add(path)
    weeks: set[tuple[int, int]] = set()
    for stamp, path in dated:
        week = stamp.isocalendar()[:2]
        if len(weeks) >= weekly:
            break
        if week not in weeks:
            weeks.add(week)
            keep.add(path)
    return keep


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup-directory", type=Path, required=True)
    parser.add_argument("--manifest-directory", type=Path, required=True)
    parser.add_argument("--retain-daily", type=int, default=3)
    parser.add_argument("--retain-weekly", type=int, default=4)
    parser.add_argument("--headroom-bytes", type=int, default=1_073_741_824)
    args = parser.parse_args()
    if args.retain_daily < 1 or args.retain_weekly < 0 or args.headroom_bytes < 0:
        raise ValueError("INVALID_BACKUP_POLICY")

    db_size = args.db.stat().st_size
    free_before = shutil.disk_usage(args.backup_directory.parent).free
    required = db_size + args.headroom_bytes
    if free_before < required:
        print(json.dumps({"status": "SKIPPED_LOW_DISK_SPACE", "free_bytes": free_before,
                          "required_bytes": required, "db_size_bytes": db_size}, indent=2))
        return 3

    result = online_backup(PolymarketRepository(args.db), args.backup_directory)
    backup_path = Path(result["path"])
    if not result["verify"]["valid"] or not verify_backup(backup_path)["valid"]:
        raise RuntimeError("NEW_BACKUP_VERIFICATION_FAILED")

    args.manifest_directory.mkdir(parents=True, exist_ok=True)
    source_manifest = Path(result["manifest_path"])
    manifest_path = args.manifest_directory / source_manifest.name
    source_manifest.replace(manifest_path)

    candidates = [path for path in args.backup_directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if _timestamp(path)]
    keep = retention_set(candidates, args.retain_daily, args.retain_weekly)
    removed: list[str] = []
    for path in candidates:
        if path in keep:
            continue
        manifest = args.manifest_directory / f"{path.name}.sha256.json"
        path.unlink()
        manifest.unlink(missing_ok=True)
        removed.append(path.name)

    result.update({"status": "VERIFIED", "manifest_path": str(manifest_path),
                   "retention": {"daily": args.retain_daily, "weekly": args.retain_weekly,
                                 "kept": sorted(path.name for path in keep), "removed": sorted(removed)}})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
