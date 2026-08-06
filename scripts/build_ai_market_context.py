"""Build a deterministic AI market context from a local JSON fixture only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.quality import epoch
from dashboard.ai_market_analysis.versions import SUPPORTED_TIMEFRAMES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--mode", choices=("QUICK", "FULL", "POSITION_AWARE"), default="FULL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    datasets = payload.get("timeframes", payload)
    if not isinstance(datasets, dict):
        raise ValueError("fixture must be an object keyed by timeframe or contain timeframes")
    bounded = {tf: list(datasets.get(tf, [])) for tf in SUPPORTED_TIMEFRAMES if tf != "1W"}
    context = build_market_analysis_context(bounded, args.instrument, epoch(args.decision_time), args.mode)
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > 500_000:
        raise ValueError("context exceeds 500KB hard limit")
    args.output.write_text(encoded, encoding="utf-8")
    summary = {
        "context_id": context["context_id"],
        "input_fingerprints": context["provenance"]["input_snapshot_ids"],
        "quality_summary": context["data_quality"],
        "timeline_summary": {"current_phase": context["market_timeline"]["current_phase"],
                             "direction": context["market_timeline"]["breakout_direction"]},
        "performance_ms": round((time.perf_counter()-started)*1000, 3),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
