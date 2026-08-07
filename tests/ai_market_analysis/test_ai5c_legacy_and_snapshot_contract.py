from __future__ import annotations
import copy,json,sqlite3
import pytest
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_api import eligibility
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository,migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from .ai4_helpers import base_context
from .ai5_helpers import golden_bundle

def test_v1_report_without_snapshot_cannot_be_promoted():
    bundle=golden_bundle();bundle["report"]["schema_version"]="ai-market-report-response-v1";bundle.pop("registry_snapshot_id");bundle.pop("registry_snapshot")
    audit=audit_report(bundle);assert audit["status"]=="ERROR" and audit["hard_failures"]==["REGISTRY_SNAPSHOT_NOT_FOUND"] and audit["promotion_eligible"] is False

def test_persisted_v1_audit_returns_schema_upgrade_required(tmp_path):
    path=tmp_path/"legacy.db";migrate_database(path);migrate_audit_database(path);repo=AuditRepository(path);bundle=golden_bundle();audit=audit_report(bundle);audit["audit_schema_version"]="ai-report-audit-v1";audit["report_id"]="legacy_report";audit["audit_id"]="legacy_audit"
    with repo.connect() as conn:
        conn.execute("INSERT INTO ai_report_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("legacy_audit","legacy_report",audit["request_id"],audit["context_id"],audit["report_hash"],audit["context_hash"],"ai-report-audit-v1","ai-report-audit-policy-v1","PASSED",100,1,json.dumps(audit),"legacy_hash","2026-01-01T00:00:00Z"))
    assert eligibility("legacy_report",repo)["eligibility"]=="AUDIT_SCHEMA_UPGRADE_REQUIRED"

def test_same_request_and_hash_snapshot_is_idempotent(tmp_path):
    path=tmp_path/"snapshot.db";migrate_database(path);repo=ReportRepository(path);bundle=golden_bundle();snapshot=bundle["registry_snapshot"]
    repo.save_registry_snapshot(snapshot);assert repo.save_registry_snapshot(copy.deepcopy(snapshot))["registry_snapshot_id"]==snapshot["registry_snapshot_id"]

def test_same_request_with_different_snapshot_payload_conflicts(tmp_path):
    path=tmp_path/"snapshot.db";migrate_database(path);repo=ReportRepository(path);bundle=golden_bundle();snapshot=bundle["registry_snapshot"];repo.save_registry_snapshot(snapshot);other=copy.deepcopy(snapshot);other["registry_snapshot_id"]="different"
    with pytest.raises(ValueError,match="REGISTRY_IDENTITY_CONFLICT"):repo.save_registry_snapshot(other)

def test_legacy_report_and_pending_state_are_not_rewritten(tmp_path):
    path=tmp_path/"legacy.db";migrate_database(path);migrate_audit_database(path)
    with sqlite3.connect(path) as conn:
        columns={x[1] for x in conn.execute("PRAGMA table_info(ai_market_reports)")};assert "audit_status" in columns
        sql=conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_market_reports'").fetchone()[0];assert "CHECK(audit_status='PENDING')" in sql

def test_request_snapshot_provider_attempt_and_audit_share_prompt_hash(tmp_path):
    path=tmp_path/"identity.db";migrate_database(path);repo=ReportRepository(path);submitted=ReportService(repo).submit(base_context());request=repo.status(submitted["request_id"]);snapshot=repo.load_registry_snapshot(registry_snapshot_id=request["registry_snapshot_id"])
    ReportWorker(repo,lambda item:FakeAIReportProvider(item["model"])).run_once()
    with repo.connect() as conn:attempt=conn.execute("SELECT prompt_hash FROM ai_report_attempts WHERE request_id=?",(submitted["request_id"],)).fetchone()[0]
    assert snapshot["prompt_hash"]==attempt==submitted["prompt_hash"]

def test_snapshot_identity_excludes_created_at_database_path_and_json_order():
    bundle=golden_bundle();snapshot=bundle["registry_snapshot"];assert not ({"created_at","rowid","database_path","runtime","machine"}&set(snapshot["identity_input"]))
    assert stable_hash(snapshot["fact_registry"])==stable_hash({k:snapshot["fact_registry"][k] for k in reversed(list(snapshot["fact_registry"]))})
