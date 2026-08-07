import json,sqlite3
import pytest
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_repository import migrate_database
from .ai5_helpers import golden_bundle

def repo(tmp_path):
    path=tmp_path/"reports.db";migrate_database(path);migrate_audit_database(path);return AuditRepository(path)

def test_explicit_migration_and_append_only_idempotency(tmp_path):
    r=repo(tmp_path);bundle=golden_bundle();r.freeze_input(bundle);audit=audit_report(bundle,created_at="1970-01-01T00:00:00Z");assert r.save_audit(audit)==audit and r.save_audit(audit)["audit_id"]==audit["audit_id"]
    with r.connect() as c:assert c.execute("SELECT audit_status FROM ai_market_reports").fetchall()==[]

def test_audit_identity_conflict_rejected(tmp_path):
    r=repo(tmp_path);audit=audit_report(golden_bundle(),created_at="1970-01-01T00:00:00Z");r.save_audit(audit);changed={**audit,"warnings":["changed"]}
    with pytest.raises(ValueError,match="AUDIT_IDENTITY_CONFLICT"):r.save_audit(changed)

def test_events_append_and_restart_interrupt(tmp_path):
    r=repo(tmp_path);r.event("a","r","AUDIT_RUNNING",{});assert r.interrupt_running()==1 and r.latest_event("a")=="AUDIT_INTERRUPTED"

def test_query_plans_are_indexed_and_bounded(tmp_path):
    plans=repo(tmp_path).query_plans();assert plans and all(not any("SCAN ai_report_audits" in row or "SCAN ai_report_audit_events" in row for row in rows) for rows in plans.values())
