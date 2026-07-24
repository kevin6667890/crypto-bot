"""Run the genuine OKX trade/CVD backfill with durable resume evidence."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.microstructure import INSTRUMENTS, MicrostructureStore
from dashboard.microstructure_backfill import OfficialBackfill


TERMINAL_STATUSES = {
    "SOURCE_RETENTION_BOUNDARY_REACHED",
    "COMPLETE_SOURCE_RANGE",
    "REPEATED_PAGE_SOURCE_LIMITATION",
    "NON_ADVANCING_CURSOR_SOURCE_LIMITATION",
    "NON_MONOTONIC_SOURCE_TIMESTAMP",
}


def iso_utc(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--wall-clock-seconds", type=int, default=7_200)
    parser.add_argument("--batch-pages", type=int, default=10)
    parser.add_argument("--instrument", action="append", choices=INSTRUMENTS)
    parser.add_argument("--aggregate", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.wall_clock_seconds <= 7_200:
        parser.error("--wall-clock-seconds must be between 1 and 7200")
    if args.batch_pages < 1:
        parser.error("--batch-pages must be positive")

    store = MicrostructureStore(args.database)
    backfill = OfficialBackfill(store)
    instruments = tuple(args.instrument or INSTRUMENTS)
    started = time.monotonic()
    deadline = started + args.wall_clock_seconds
    summaries: dict[str, dict[str, object]] = {}

    for instrument in instruments:
        with store.connect(readonly=True) as connection:
            checkpoint = connection.execute(
                """SELECT cursor,last_source_ts_ms FROM collection_checkpoints
                   WHERE lane='trades' AND instrument=?""", (instrument,)).fetchone()
            bounds = connection.execute(
                """SELECT MIN(source_ts_ms),MAX(source_ts_ms)
                   FROM trade_flow_observations WHERE instrument=?""",
                (instrument,)).fetchone()
        summaries[instrument] = {
            "instrument": instrument,
            "total_batches": 0,
            "total_pages": 0,
            "fetched_trades": 0,
            "inserted_unique_trades": 0,
            "duplicate_rows_ignored": 0,
            "earliest_timestamp_before": bounds[0],
            "earliest_timestamp_before_utc": iso_utc(bounds[0]),
            "latest_timestamp_before": bounds[1],
            "latest_timestamp_before_utc": iso_utc(bounds[1]),
            "source_cursor_before": checkpoint[0] if checkpoint else None,
            "source_earliest_before": checkpoint[1] if checkpoint else None,
            "batch_trace": [],
            "retries": 0,
            "terminal_status": None,
        }

    active = list(instruments)
    while active and time.monotonic() < deadline:
        for instrument in list(active):
            if time.monotonic() >= deadline:
                break
            summary = summaries[instrument]
            batch_started = time.monotonic()
            result = backfill.backfill_trades(
                instrument, max_pages=args.batch_pages)
            trace = {
                "batch": int(summary["total_batches"]) + 1,
                "cursor_before": result["cursor_before"],
                "cursor_after": result["cursor"],
                "source_earliest_before": (
                    summary["batch_trace"][-1]["source_earliest_after"]
                    if summary["batch_trace"] else summary["source_earliest_before"]),
                "source_earliest_after": result["earliest_ms"],
                "pages": result["pages"],
                "fetched": result["fetched_trades"],
                "inserted": result["inserted"],
                "duplicates": result["duplicate_rows_ignored"],
                "retries": result["retries"],
                "status": result["completeness"],
                "elapsed_seconds": round(time.monotonic() - batch_started, 3),
            }
            previous = trace["source_earliest_before"]
            current = trace["source_earliest_after"]
            if (result["pages"] and previous is not None and current is not None
                    and int(current) >= int(previous)):
                trace["status"] = "NON_MONOTONIC_SOURCE_TIMESTAMP"
                result["completeness"] = trace["status"]
            summary["batch_trace"].append(trace)
            summary["total_batches"] = int(summary["total_batches"]) + 1
            summary["total_pages"] = int(summary["total_pages"]) + result["pages"]
            summary["fetched_trades"] = (
                int(summary["fetched_trades"]) + result["fetched_trades"])
            summary["inserted_unique_trades"] = (
                int(summary["inserted_unique_trades"]) + result["inserted"])
            summary["duplicate_rows_ignored"] = (
                int(summary["duplicate_rows_ignored"])
                + result["duplicate_rows_ignored"])
            summary["retries"] = int(summary["retries"]) + result["retries"]
            print(json.dumps({"batch": trace}, sort_keys=True), flush=True)
            if result["completeness"] in TERMINAL_STATUSES:
                summary["terminal_status"] = result["completeness"]
                active.remove(instrument)

    elapsed = time.monotonic() - started
    for instrument in instruments:
        summary = summaries[instrument]
        if summary["terminal_status"] is None:
            summary["terminal_status"] = "WALL_CLOCK_SAFETY_BUDGET_REACHED"
            with store.connect(readonly=True) as connection:
                checkpoint = connection.execute(
                    """SELECT cursor,last_source_ts_ms FROM collection_checkpoints
                       WHERE lane='trades' AND instrument=?""",
                    (instrument,)).fetchone()
            store.checkpoint(
                "trades", instrument,
                cursor=checkpoint[0] if checkpoint else None,
                last_source_ts_ms=checkpoint[1] if checkpoint else None,
                status="WALL_CLOCK_SAFETY_BUDGET_REACHED",
                metadata={
                    "resumable": True,
                    "total_batches": summary["total_batches"],
                    "total_pages": summary["total_pages"],
                    "fetched_trades": summary["fetched_trades"],
                    "inserted_unique_trades": summary["inserted_unique_trades"],
                    "duplicate_rows_ignored": summary["duplicate_rows_ignored"],
                },
            )
        with store.connect(readonly=True) as connection:
            checkpoint = connection.execute(
                """SELECT cursor,last_source_ts_ms FROM collection_checkpoints
                   WHERE lane='trades' AND instrument=?""",
                (instrument,)).fetchone()
            bounds = connection.execute(
                """SELECT MIN(source_ts_ms),MAX(source_ts_ms)
                   FROM trade_flow_observations WHERE instrument=?""",
                (instrument,)).fetchone()
        summary["cursor"] = checkpoint[0] if checkpoint else None
        summary["source_earliest_after"] = checkpoint[1] if checkpoint else None
        summary["earliest_timestamp_after"] = bounds[0]
        summary["earliest_timestamp_after_utc"] = iso_utc(bounds[0])
        summary["latest_timestamp"] = bounds[1]
        summary["latest_timestamp_utc"] = iso_utc(bounds[1])
        summary["elapsed_seconds"] = round(elapsed, 3)

    if args.aggregate:
        store.aggregate_all()
    print(json.dumps({
        "database": str(args.database),
        "wall_clock_budget_seconds": args.wall_clock_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "instruments": summaries,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
