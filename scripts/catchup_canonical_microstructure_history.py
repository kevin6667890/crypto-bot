#!/usr/bin/env python3
"""Apply an explicit bounded production delta to a prebuilt canonical DB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dashboard.canonical_microstructure_history import INSTRUMENTS
from dashboard.canonical_realtime import CanonicalRealtimeWriter


MINUTE_MS = 60_000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--chunk-minutes", type=int, default=60)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.start_ms >= args.end_ms:
        raise SystemExit("catch-up range must be non-empty")
    if not 1 <= args.chunk_minutes <= 120:
        raise SystemExit("chunk-minutes must be between 1 and 120")
    writer = CanonicalRealtimeWriter(args.source, args.canonical, args.commit)
    chunks: list[dict] = []
    width = args.chunk_minutes * MINUTE_MS
    for instrument in INSTRUMENTS:
        cursor = args.start_ms // MINUTE_MS * MINUTE_MS
        while cursor < args.end_ms:
            end = min(args.end_ms, cursor + width)
            result = writer.sync(instrument, cursor, end)
            item = {"instrument": instrument, "start_ms": cursor,
                    "end_ms": end, **result}
            chunks.append(item)
            print(json.dumps(item, sort_keys=True), flush=True)
            cursor = end
    report = {
        "source": str(args.source), "canonical": str(args.canonical),
        "start_ms": args.start_ms, "end_ms": args.end_ms,
        "generated_commit": args.commit, "chunk_minutes": args.chunk_minutes,
        "chunks": chunks,
        "synthetic_rows": 0, "interpolated_rows": 0, "forward_filled_rows": 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
