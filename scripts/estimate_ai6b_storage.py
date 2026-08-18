from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.storage_budget import project_capacity

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--filesystem-total-bytes",type=int,required=True);p.add_argument("--filesystem-used-bytes",type=int,required=True);p.add_argument("--current-microstructure-bytes",type=int,required=True);p.add_argument("--microstructure-coverage-days",type=float,required=True);p.add_argument("--raw-retention-days",type=int,default=30);p.add_argument("--live-requests-per-day",type=int,default=10);p.add_argument("--observed-non-ai-daily-growth-bytes",type=int,default=0);p.add_argument("--output");a=p.parse_args()
    value=project_capacity(filesystem_total_bytes=a.filesystem_total_bytes,filesystem_used_bytes=a.filesystem_used_bytes,current_microstructure_bytes=a.current_microstructure_bytes,microstructure_coverage_days=a.microstructure_coverage_days,raw_retention_days=a.raw_retention_days,live_requests_per_day=a.live_requests_per_day,observed_non_ai_daily_growth_bytes=a.observed_non_ai_daily_growth_bytes)
    encoded=json.dumps(value,sort_keys=True,indent=2)+"\n"
    if a.output:Path(a.output).write_text(encoded,encoding="utf-8")
    print(json.dumps(value,sort_keys=True));return 0 if value["within_24h_budget"] and value["within_30d_hot_and_90d_logical_budget"] else 2
if __name__=="__main__":raise SystemExit(main())
