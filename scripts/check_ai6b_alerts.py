from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.report_alerts import evaluate_alerts,load_alert_policy

def main()->int:
    parser=argparse.ArgumentParser(description="Evaluate bounded AI-6B alert metrics without side effects")
    parser.add_argument("--metrics",required=True,help="JSON file containing aggregate numeric metrics")
    parser.add_argument("--policy",default=str(ROOT/"config"/"ai6b_alert_policy.json"));args=parser.parse_args()
    metrics=json.loads(Path(args.metrics).read_text(encoding="utf-8"));policy=load_alert_policy(args.policy);events=evaluate_alerts(metrics,policy)
    print(json.dumps({"policy_version":policy["policy_version"],"stop_required":any(e["stop"] for e in events),"events":events},sort_keys=True));return 2 if events else 0
if __name__=="__main__":raise SystemExit(main())
