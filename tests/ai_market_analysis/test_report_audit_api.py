from dashboard.ai_market_analysis.report_audit_api import eligibility,ELIGIBILITY
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,migrate_audit_database
from dashboard.ai_market_analysis.report_repository import migrate_database

def test_eligibility_never_claims_production_ready(tmp_path):
    path=tmp_path/"a.db";migrate_database(path);migrate_audit_database(path);value=eligibility("missing",AuditRepository(path));assert value["eligibility"]=="AUDIT_NOT_FOUND" and value["shadow_only"] is True and "PRODUCTION_READY" not in ELIGIBILITY

def test_shadow_routes_are_present_without_frontend_integration():
    source=open("dashboard/paper_api.py",encoding="utf-8").read();assert "/audits/latest" in source and "/eligibility" in source and "/evaluation-runs" in source
    frontend=open("frontend/src/App.tsx",encoding="utf-8").read();assert "/audits/latest" not in frontend
