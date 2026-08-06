from __future__ import annotations
import argparse
from dashboard.ai_market_analysis.report_repository import DEFAULT_REPORT_DB,migrate_database
from dashboard.ai_market_analysis.report_audit_repository import migrate_audit_database

def main()->int:
    p=argparse.ArgumentParser(description="Explicitly migrate the isolated AI report database")
    p.add_argument("--database",default=str(DEFAULT_REPORT_DB));p.add_argument("--include-audit",action="store_true");a=p.parse_args();result={"report":migrate_database(a.database)}
    if a.include_audit:result["audit"]=migrate_audit_database(a.database)
    print(result);return 0
if __name__=="__main__":raise SystemExit(main())
