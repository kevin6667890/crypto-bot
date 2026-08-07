from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,freeze_report_bundle
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_repository import ReportRepository
from dashboard.ai_market_analysis.versions import AI_REPORT_AUDIT_POLICY_VERSION

def _json(path):return json.loads(Path(path).read_text(encoding="utf-8"))
def main()->int:
    p=argparse.ArgumentParser(description="Deterministically audit one frozen AI market report")
    p.add_argument("--database");p.add_argument("--report-id");p.add_argument("--report-fixture");p.add_argument("--context-fixture");p.add_argument("--policy-version")
    p.add_argument("--output",required=True);p.add_argument("--no-persist",action="store_true");p.add_argument("--connection-trace")
    for name in ("claims","numeric-audit","contradictions","repetition","coverage"):p.add_argument("--show-"+name,action="store_true")
    a=p.parse_args()
    if a.policy_version and a.policy_version!=AI_REPORT_AUDIT_POLICY_VERSION:p.error("unsupported policy version")
    opened=[]
    if a.connection_trace:
        def trace(event,args):
            if event=="sqlite3.connect":opened.append(str(args[0]))
        sys.addaudithook(trace)
    if a.database and a.report_id:bundle=freeze_report_bundle(ReportRepository(a.database),a.report_id)
    elif a.report_fixture:
        value=_json(a.report_fixture)
        if all(k in value for k in ("report","context","fact_registry")):bundle=value
        elif a.context_fixture:p.error("legacy report/context fixtures lack an immutable registry snapshot")
        else:p.error("fixture must be a frozen bundle or provide --context-fixture")
    else:p.error("use --database/--report-id or --report-fixture")
    audit=audit_report(bundle);Path(a.output).write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    if not a.no_persist and a.database:
        repo=AuditRepository(a.database);repo.freeze_input(bundle);repo.save_audit(audit)
    if a.connection_trace:Path(a.connection_trace).write_text(json.dumps(opened,ensure_ascii=False),encoding="utf-8")
    selected=[]
    for enabled,key in ((a.show_claims,"claim_audits"),(a.show_numeric_audit,"numeric_audits"),(a.show_contradictions,"contradiction_audits"),(a.show_repetition,"repetition_audit"),(a.show_coverage,"coverage_audit")):
        if enabled:selected.append({key:audit[key]})
    print(json.dumps(selected or {"audit_id":audit["audit_id"],"status":audit["status"],"score":audit["scorecard"]["overall"]},ensure_ascii=False));return 0 if audit["status"]=="PASSED" else 2
if __name__=="__main__":raise SystemExit(main())
