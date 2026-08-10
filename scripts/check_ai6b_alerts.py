from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.report_alerts import evaluate_alerts,load_alert_policy
from dashboard.ai_market_analysis.live_provider_guard import trip

def main()->int:
    parser=argparse.ArgumentParser(description="Evaluate bounded AI-6B alert metrics without side effects")
    parser.add_argument("--metrics",required=True,help="JSON file containing aggregate numeric metrics")
    parser.add_argument("--policy",default=str(ROOT/"config"/"ai6b_alert_policy.json"));args=parser.parse_args()
    metrics=json.loads(Path(args.metrics).read_text(encoding="utf-8"));policy=load_alert_policy(args.policy);events=evaluate_alerts(metrics,policy)
    stop_events=[event for event in events if event["stop"]]
    if stop_events:
        mapping={"queue_growth":"QUEUE_RUNAWAY","retry_anomaly":"RUNAWAY_RETRY","budget_blocked":"BUDGET_BREACH","disk_growth":"DISK_CRITICAL","wal_growth":"DISK_CRITICAL","presentation_mismatch":"AUDIT_MISMATCH","position_access":"POSITION_LEAK","frontend_security":"CRITICAL_WARNING_HIDDEN","legacy_or_core_change":"ORDER_PATH_CHANGE","identity_mismatch":"CONTEXT_MISMATCH","unaudited_body":"UNAUDITED_BODY_DISPLAY","secret_exposure":"SECRET_EXPOSURE","duplicate_charge":"DUPLICATE_PROVIDER_CHARGE","database_corruption":"DB_CORRUPTION"}
        first=stop_events[0]
        killed=trip(mapping.get(first["alert_id"],"BUDGET_BREACH"),evidence_id=first["alert_id"])
    else:killed=None
    print(json.dumps({"policy_version":policy["policy_version"],"stop_required":bool(stop_events),"kill_switch":killed,"events":events},sort_keys=True));return 2 if events else 0
if __name__=="__main__":raise SystemExit(main())
