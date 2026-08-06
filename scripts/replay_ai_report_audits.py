from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ai_market_analysis.report_audit_repository import freeze_report_bundle
from dashboard.ai_market_analysis.report_repository import ReportRepository
from dashboard.ai_market_analysis.report_replay import replay
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--database");p.add_argument("--report-id");p.add_argument("--bundle-fixture");p.add_argument("--runs",type=int,default=20);p.add_argument("--output",required=True);a=p.parse_args()
    bundle=json.loads(Path(a.bundle_fixture).read_text(encoding="utf-8")) if a.bundle_fixture else freeze_report_bundle(ReportRepository(a.database),a.report_id)
    result=replay(bundle,a.runs);Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8");print(json.dumps(result));return 0 if result["deterministic"] else 2
if __name__=="__main__":raise SystemExit(main())
