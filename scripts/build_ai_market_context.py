"""Build a deterministic AI market context from fixtures and/or read-only SQLite."""
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
from dashboard.ai_market_analysis.readonly_adapter import ReadOnlyOrderflowAdapter
from dashboard.market_context_v2 import BoundedMarketDataReaderV2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--market-database", type=Path)
    parser.add_argument("--microstructure-database", type=Path)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--mode", choices=("QUICK", "FULL", "POSITION_AWARE"), default="FULL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--explain-query-plan", action="store_true")
    args = parser.parse_args()
    if args.fixture is None and args.market_database is None:
        parser.error("one of --fixture or --market-database is required")
    started = time.perf_counter()
    decision=epoch(args.decision_time); payload={}
    if args.fixture:
        payload=json.loads(args.fixture.read_text(encoding="utf-8"))
        datasets=payload.get("timeframes",payload)
        if not isinstance(datasets,dict): raise ValueError("fixture must be an object keyed by timeframe or contain timeframes")
        bounded={tf:list(datasets.get(tf,[])) for tf in SUPPORTED_TIMEFRAMES if tf!="1W"}
    else:
        reader=BoundedMarketDataReaderV2(args.market_database,args.microstructure_database)
        bounded={tf:reader.candles(args.instrument,tf,decision,1500 if tf=="1D" else 512) for tf in SUPPORTED_TIMEFRAMES if tf!="1W"}
    orderflow=payload.get("orderflow")
    query_plan=[]
    if args.microstructure_database:
        start=min((int(row.get("ts",decision)) for rows in bounded.values() for row in rows),default=decision-30*86400)
        adapter=ReadOnlyOrderflowAdapter(args.microstructure_database)
        orderflow=adapter.read(args.instrument,start,decision,"4H")
        query_plan=[str(row[-1]) for row in adapter.query_plans]
    auxiliary=payload.get("auxiliary",{})
    context = build_market_analysis_context(bounded,args.instrument,decision,args.mode,orderflow=orderflow,auxiliary=auxiliary)
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
        "phase_windows": [{"phase_id":p["phase_id"],"phase":p["phase"],"start":p["start"],"end":p["end"]} for p in context["order_flow_phases"]],
        "order_flow_attribution": [{"phase":p["phase"],**p["attribution"]} for p in context["order_flow_phases"]],
        "level_zones": [{"level_id":l["level_id"],"zone_low":l["zone_low"],"zone_high":l["zone_high"],"role":l["role"],"state":l["state"]} for l in context["key_levels"]],
        "scenarios": [{"scenario_id":s["scenario_id"],"type":s["type"],"likelihood":s["likelihood"]} for s in context["scenario_tree"]["scenarios"]],
        "data_warnings": context["unsupported_claims"],
        "query_plan": query_plan if args.explain_query_plan else [],
        "performance_ms": round((time.perf_counter()-started)*1000, 3),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
