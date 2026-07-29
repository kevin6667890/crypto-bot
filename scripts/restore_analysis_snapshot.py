"""Restore one exact analysis snapshot from a verified offline bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.snapshot_bundle import restore_snapshot  # noqa: E402


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--archive", type=Path, required=True)
    value.add_argument("--snapshot-id", type=int, required=True)
    value.add_argument("--output", type=Path)
    value.add_argument("--verify", action="store_true")
    modes = value.add_mutually_exclusive_group(required=True)
    modes.add_argument("--metadata-only", action="store_true")
    modes.add_argument("--payload", action="store_true")
    value.add_argument("--source-database", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    metadata, payload = restore_snapshot(
        args.archive, args.snapshot_id, verify=args.verify
    )
    if args.source_database:
        source = args.source_database.resolve()
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute(
                """SELECT id,created_at,instrument FROM analysis_snapshots
                   WHERE id=?""",
                (args.snapshot_id,),
            ).fetchone()
        if row is None:
            raise SystemExit("source database does not contain the requested snapshot")
        if tuple(row) != (
            metadata["snapshot_id"],
            metadata["created_at"],
            metadata["instrument"],
        ):
            raise SystemExit("source database snapshot identity mismatch")
    output = (
        json.dumps(metadata, sort_keys=True, indent=2) + "\n"
        if args.metadata_only
        else payload
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
