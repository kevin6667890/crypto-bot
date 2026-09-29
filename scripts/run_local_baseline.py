"""Run the fixed, local-only OHLCV baseline with strict temporal splits.

The script reads an immutable SQLite export and never downloads candles or
writes to the source database.  It is intentionally a baseline, not an
optimizer: the same default strategy parameters, costs and split dates are
used on every invocation.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Permit ``python scripts/run_local_baseline.py`` without requiring callers to
# set PYTHONPATH themselves.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backtest_engine import run_backtest
from dashboard.strategy_rules import DEFAULT_PARAMETERS, validate_parameters


DEFAULT_DATABASE = Path("data/production-export/2026-09-29/paper_trades.db")
FRAMES = (("15m", 900), ("1H", 3600), ("4H", 14400))


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def load_rows(connection: sqlite3.Connection, instrument: str, timeframe: str, start: int, end: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT ts, open, high, low, close, volume, confirmed
               FROM historical_candles
               WHERE instrument=? AND timeframe=? AND confirmed=1 AND ts>=? AND ts<?
               ORDER BY ts""",
            (instrument, timeframe, start, end),
        )
    ]


def gate(rows: list[dict[str, Any]], step: int, name: str) -> dict[str, int | str]:
    gaps = sum(int(current["ts"]) - int(previous["ts"]) != step for previous, current in zip(rows, rows[1:]))
    expected = (int(rows[-1]["ts"]) - int(rows[0]["ts"])) // step + 1 if rows else 0
    return {"frame": name, "rows": len(rows), "expected_rows": expected, "gaps": gaps, "duplicates": len(rows) - len({int(row["ts"]) for row in rows})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--instrument", default="BTC-USDT", choices=("BTC-USDT", "ETH-USDT", "SOL-USDT"))
    parser.add_argument("--output", type=Path, help="Optional JSON report path; source database remains read-only.")
    args = parser.parse_args()

    # Development / untouched holdout / final out-of-time period.
    start, boundary_1, boundary_2, finish = (timestamp(value) for value in ("2024-01-01", "2025-01-01", "2025-07-01", "2026-01-01"))
    source = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    datasets = {frame: load_rows(source, args.instrument, frame, start, finish) for frame, _ in FRAMES}
    gates = [gate(datasets[frame], step, frame) for frame, step in FRAMES]
    if any(item["gaps"] or item["duplicates"] for item in gates):
        raise SystemExit(f"Data gate failed: {gates}")

    parameters = validate_parameters(DEFAULT_PARAMETERS)
    windows = {
        "development": (start, boundary_1 - 1),
        "holdout": (boundary_1, boundary_2 - 1),
        "oot": (boundary_2, finish - 1),
    }
    report: dict[str, Any] = {
        "protocol_version": "local-confirmed-ohlcv-baseline-v1",
        "dataset": {"database": str(args.database), "instrument": args.instrument, "range": "[2024-01-01, 2026-01-01)", "gates": gates},
        "execution": {"fill": "next bar open", "trading_fee": DEFAULT_PARAMETERS["trading_fee"], "adverse_slippage": DEFAULT_PARAMETERS["slippage"]},
        "parameters": DEFAULT_PARAMETERS,
        "windows": {},
    }
    for name, (window_start, window_end) in windows.items():
        report["windows"][name] = run_backtest(
            datasets["15m"], args.instrument, "15m", parameters, window_start, window_end,
            timeframe_datasets={"1H": datasets["1H"], "4H": datasets["4H"]}, include_details=False,
        )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
