from __future__ import annotations
import argparse,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from dashboard.ai_market_analysis.report_audit_jobs import AuditWorker
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository

def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--database",required=True);parser.add_argument("--once",action="store_true");args=parser.parse_args()
    if os.getenv("AI_REPORT_AUDIT_WORKER_ENABLED","false").lower()!="true":print("AI audit worker disabled");return 0
    worker=AuditWorker(AuditRepository(args.database));worker.recover()
    while True:
        did=worker.run_once()
        if args.once:return 0
        if not did:time.sleep(1)
if __name__=="__main__":raise SystemExit(main())
