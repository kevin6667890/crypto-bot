#!/usr/bin/env python3
"""Daily, read-only CVD/OI research-readiness check."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.research_readiness import (  # noqa: E402
    FEATURE_GROUPS,
    INSTRUMENTS,
    READY_PENDING,
    evaluate_readiness,
)


EXIT_ERROR = 2
EXIT_COLLECTING = 10
EXIT_APPROACHING = 11
EXIT_BLOCKED = 20


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", required=True,
        help="Path to an offline/local SQLite database (opened mode=ro).")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--previous-result", type=Path)
    parser.add_argument("--human-readable", action="store_true")
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat an empty selection/result as an execution error.")
    parser.add_argument("--instrument", action="append", choices=INSTRUMENTS)
    parser.add_argument("--feature-group", action="append", choices=FEATURE_GROUPS)
    return parser


def _states(payload: dict[str, Any]) -> dict[str, str]:
    return {
        f"{row['feature_group']}|{row['instrument']}": row["status"]
        for row in payload.get("results", [])
    }


def _notification(
    previous: dict[str, Any], current: dict[str, Any],
) -> dict[str, Any] | None:
    before = _states(previous)
    after = _states(current)
    changes = [
        {"key": key, "previous_status": before.get(key),
         "current_status": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    ]
    if not changes:
        return None
    digest = hashlib.sha256(json.dumps(
        changes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": current["schema_version"],
        "notification_id": digest,
        "notification_type": "LOCAL_READINESS_STATUS_CHANGE",
        "changes": changes,
        "delivery": "local_file_only",
        "sent": False,
    }


def _notification_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.notification.json")


def _write_if_changed(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return
    path.write_text(encoded, encoding="utf-8")


def _exit_code(payload: dict[str, Any]) -> int:
    states = {row["status"] for row in payload["results"]}
    if states and states <= {READY_PENDING}:
        return 0
    if states & {"BLOCKED_DATA_QUALITY", "BLOCKED_CRITICAL_GAP", "STALE_SOURCE"}:
        return EXIT_BLOCKED
    if "APPROACHING_READINESS" in states:
        return EXIT_APPROACHING
    return EXIT_COLLECTING


def _print_human(payload: dict[str, Any]) -> None:
    print(f"Readiness {payload['schema_version']} at {payload['evaluated_at']}")
    for row in payload["results"]:
        reasons = ", ".join(row["blocking_reasons"]) or "none"
        print(
            f"{row['instrument']:3} | {row['feature_group']:12} | "
            f"{row['status']} | {reasons}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = evaluate_readiness(
            args.database,
            instruments=args.instrument,
            feature_groups=args.feature_group,
        )
        if args.strict and not payload["results"]:
            raise ValueError("readiness result is empty")
        if args.output_json:
            _write_if_changed(args.output_json, payload)
        if args.previous_result:
            previous = json.loads(args.previous_result.read_text(encoding="utf-8"))
            notification = _notification(previous, payload)
            if notification is not None:
                if not args.output_json:
                    raise ValueError(
                        "--output-json is required to place a notification artifact")
                _write_if_changed(_notification_path(args.output_json), notification)
            elif args.output_json:
                stale_notification = _notification_path(args.output_json)
                if stale_notification.is_file():
                    stale_notification.unlink()
        if args.human_readable:
            _print_human(payload)
        elif not args.output_json:
            print(json.dumps(payload, sort_keys=True))
        return _exit_code(payload)
    except Exception as exc:  # CLI boundary: stable error exit and no traceback
        print(json.dumps({
            "schema_version": "microstructure-research-readiness-v1",
            "error": type(exc).__name__,
            "message": str(exc),
        }, sort_keys=True), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
