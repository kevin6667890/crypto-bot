from dashboard.ai_market_analysis.report_audit_jobs import AuditWorker,queue_audit,audit_enabled
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,migrate_audit_database
from dashboard.ai_market_analysis.report_repository import migrate_database
from .ai5_helpers import golden_bundle

def test_flags_default_off(monkeypatch):
    for name in ("AI_REPORT_AUDIT_ENABLED","AI_REPORT_AUDIT_WORKER_ENABLED","AI_REPORT_AUTO_AUDIT_ENABLED","AI_REPORT_EVALUATION_ENABLED"):
        monkeypatch.delenv(name,raising=False);assert not audit_enabled(name)

def test_single_flight_worker_and_no_report_mutation(tmp_path):
    path=tmp_path/"a.db";migrate_database(path);migrate_audit_database(path);r=AuditRepository(path);b=golden_bundle();r.freeze_input(b)
    a=queue_audit(r,b["report_id"]);same=queue_audit(r,b["report_id"]);assert a["audit_id"]==same["audit_id"]
    assert AuditWorker(r).run_once() and r.latest(b["report_id"])["status"]=="PASSED"
    with r.connect() as c:assert c.execute("SELECT COUNT(*) FROM ai_market_reports").fetchone()[0]==0
