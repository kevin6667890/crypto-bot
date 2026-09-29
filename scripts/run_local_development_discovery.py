"""Screen the bounded V2 price-only candidate grid on 2024 development data.

2025 is deliberately not loaded by this script: it remains the untouched
holdout/OOT evidence for only those candidates that survive this screen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_v2_registry import FIXED_EXECUTION, plan

DEFAULT_DATABASE = Path("data/production-export/2026-09-29/paper_trades.db")


def ts(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=Path("data/local-research/development-discovery-v2-2024.json"))
    args = parser.parse_args()
    start, end = ts("2024-01-01"), ts("2025-01-01")
    db = sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    candles = [dict(row) for row in db.execute(
        "SELECT ts,open,high,low,close,volume,confirmed FROM historical_candles "
        "WHERE instrument='BTC-USDT' AND timeframe='15m' AND confirmed=1 AND ts>=? AND ts<? ORDER BY ts", (start, end)
    )]
    if len(candles) != 35136 or any(int(b["ts"]) - int(a["ts"]) != 900 for a, b in zip(candles, candles[1:])):
        raise SystemExit("Development OHLCV gate failed; no candidates were evaluated.")
    fingerprint = hashlib.sha256(json.dumps(
        [(x["ts"], x["open"], x["high"], x["low"], x["close"], x["volume"]) for x in candles], separators=(",", ":")
    ).encode()).hexdigest()
    execution = DiscoveryExecutionConfig(**{key: FIXED_EXECUTION[key] for key in ("initial_capital", "risk_per_trade", "trading_fee", "slippage", "cooldown_bars")})
    folds = [("2024-h1", ts("2024-01-01"), ts("2024-05-01"), ts("2024-05-01"), ts("2024-07-01")),
             ("2024-q3", ts("2024-01-01"), ts("2024-07-01"), ts("2024-07-01"), ts("2024-09-01")),
             ("2024-q4a", ts("2024-01-01"), ts("2024-09-01"), ts("2024-09-01"), ts("2024-11-01")),
             ("2024-q4b", ts("2024-01-01"), ts("2024-11-01"), ts("2024-11-01"), ts("2025-01-01"))]
    definitions, _, sampling = plan(36)
    candidates = []
    for number, (template, parameters) in enumerate(definitions, 1):
        evaluations = []
        for label, _, _, validation_start, validation_end in folds:
            result = run_discovery_candidate_backtest(candles, "BTC-USDT", "15m", template, parameters,
                validation_start, validation_end - 900, execution, fingerprint)
            evaluations.append({"fold": label, "metrics": result["metrics"], "evidence": result["discovery_evidence"]})
        metrics = [x["metrics"] for x in evaluations]
        candidates.append({"number": number, "template": template, "parameters": parameters, "folds": evaluations,
            "summary": {"median_return": sorted(x["total_return"] for x in metrics)[len(metrics)//2],
                        "worst_return": min(x["total_return"] for x in metrics),
                        "profitable_folds": sum(x["total_return"] > 0 for x in metrics),
                        "total_trades": sum(x["total_trades"] for x in metrics),
                        "worst_drawdown": max(x["maximum_drawdown"] for x in metrics)}})
    candidates.sort(key=lambda x: (x["summary"]["profitable_folds"], x["summary"]["median_return"], x["summary"]["worst_return"]), reverse=True)
    report = {"protocol_version": "local-development-only-discovery-v1", "dataset_fingerprint": fingerprint,
              "source": str(args.database), "development_range": "[2024-01-01, 2025-01-01)",
              "holdout_and_oot_accessed": False, "execution": execution.__dict__, "sampling": sampling,
              "folds": [{"label": x[0], "validation": [x[3], x[4]]} for x in folds], "candidates": candidates}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "candidate_count": len(candidates), "top": candidates[0]["summary"] if candidates else None}, indent=2))


if __name__ == "__main__":
    main()
