from __future__ import annotations
import sqlite3
import pytest
from dashboard.ai_market_analysis.report_jobs import ConcurrencyGate,ReportWorker,TokenBudget
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context

@pytest.fixture
def repo(tmp_path):
    migrate_database(tmp_path/"r.db");return ReportRepository(tmp_path/"r.db")

def submit(repo,mode="FULL"):return ReportService(repo).submit(base_context(),mode=mode)

def test_migration_empty_and_idempotent(tmp_path):
    path=tmp_path/"r.db";migrate_database(path);migrate_database(path);connection=sqlite3.connect(path);assert connection.execute("select count(*) from ai_report_migrations").fetchone()[0]==4

def test_import_does_not_migrate(tmp_path):
    repository=ReportRepository(tmp_path/"missing.db");assert repository.schema_version() is None and not repository.path.exists()

def test_context_immutable_and_conflict(repo):
    item=submit(repo);context=repo.load_context(item["context_id"]);context["instrument"]="SOL-USDT-SWAP"
    with pytest.raises(ValueError):repo.save_context(context)

def test_request_idempotent_and_completed_reuse(repo):
    a=submit(repo);b=submit(repo);assert a["request_id"]==b["request_id"] and b["created"] is False
    ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"])).run_once();assert submit(repo)["status_code"]==200

def test_events_attempts_report_pending(repo):
    item=submit(repo);ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"])).run_once();assert repo.status(item["request_id"])["status"]=="COMPLETED" and repo.get_report(request_id=item["request_id"])["audit_status"]=="PENDING"

def test_json_repair_success(repo):
    item=submit(repo,"QUICK");ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"],"repair_success")).run_once();assert repo.status(item["request_id"])["status"]=="COMPLETED"

def test_retry_and_final_failure(repo):
    item=submit(repo);worker=ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"],"429"));worker.run_once();assert repo.status(item["request_id"])["status"]=="RETRY_SCHEDULED";worker.run_once();worker.run_once();assert repo.status(item["request_id"])["status"]=="FAILED_FINAL"

def test_401_no_retry(repo):
    item=submit(repo);ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"],"401")).run_once();assert repo.status(item["request_id"])["status"]=="FAILED_FINAL"

def test_validation_failure_not_completed(repo):
    item=submit(repo);ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"],"hallucinated_number")).run_once();assert repo.status(item["request_id"])["status"]=="VALIDATION_FAILED" and repo.get_report(request_id=item["request_id"]) is None

def test_singleflight_and_concurrency():
    gate=ConcurrencyGate(2);assert gate.acquire("a","ETH") and not gate.acquire("b","ETH") and gate.acquire("b","BTC") and not gate.acquire("c","SOL");gate.release("a","ETH");assert gate.acquire("c","SOL")

def test_restart_interrupts(repo):
    item=submit(repo);repo.event(item["request_id"],"RUNNING",{});assert ReportWorker(repo,lambda r:FakeAIReportProvider()).recover()==1 and repo.status(item["request_id"])["status"]=="INTERRUPTED"

def test_budget_blocked(repo,monkeypatch):
    monkeypatch.setenv("AI_REPORT_DAILY_TOTAL_TOKENS","1");item=submit(repo);ReportWorker(repo,lambda r:FakeAIReportProvider(),budget=TokenBudget()).run_once();assert repo.status(item["request_id"])["status"]=="BUDGET_BLOCKED"
