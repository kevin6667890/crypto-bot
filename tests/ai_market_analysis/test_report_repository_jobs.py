from __future__ import annotations
import sqlite3
import pytest
from dashboard.ai_market_analysis.report_jobs import ConcurrencyGate,ReportWorker,TokenBudget,provider_budget_chargeable
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider,ProviderResult
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context

@pytest.fixture
def repo(tmp_path):
    migrate_database(tmp_path/"r.db");return ReportRepository(tmp_path/"r.db")

def submit(repo,mode="FULL"):return ReportService(repo).submit(base_context(),mode=mode)

def test_migration_empty_and_idempotent(tmp_path):
    path=tmp_path/"r.db";migrate_database(path);migrate_database(path);connection=sqlite3.connect(path);assert connection.execute("select count(*) from ai_report_migrations").fetchone()[0]==6

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

def test_json_schema_failure_is_final_without_provider_repair(repo):
    provider=FakeAIReportProvider("fake","repair_success");item=submit(repo,"QUICK");ReportWorker(repo,lambda _r:provider).run_once();assert repo.status(item["request_id"])["status"]=="FAILED_FINAL";assert provider.calls==1

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

def test_fake_tokens_are_telemetry_but_not_paid_budget(repo,monkeypatch):
    monkeypatch.setenv("AI_REPORT_DAILY_INPUT_TOKENS","1")
    monkeypatch.setenv("AI_REPORT_DAILY_OUTPUT_TOKENS","1")
    monkeypatch.setenv("AI_REPORT_DAILY_TOTAL_TOKENS","1")
    monkeypatch.setenv("AI_REPORT_DAILY_CURRENCY_CAP_USD","0")
    monkeypatch.setenv("AI_REPORT_COST_STATUS","AUDITED")
    monkeypatch.setenv("AI_REPORT_INPUT_USD_PER_MILLION","1")
    monkeypatch.setenv("AI_REPORT_OUTPUT_USD_PER_MILLION","1")
    provider=FakeAIReportProvider();items=[submit(repo,"FULL"),submit(repo,"QUICK")]
    worker=ReportWorker(repo,lambda _request:provider,budget=TokenBudget())
    assert worker.run_once() is True and worker.run_once() is True
    assert all(repo.status(item["request_id"])["status"]=="COMPLETED" for item in items)
    assert provider.calls==2
    assert repo.daily_tokens()["output"]>0
    assert repo.daily_tokens(chargeable_only=True)=={"input":0,"output":0,"total":0}

def test_paid_budget_classification_needs_no_history_migration(repo):
    with repo.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_report_migrations").fetchone()[0]==6
        columns={row[1] for row in connection.execute("PRAGMA table_info(ai_report_attempts)")}
    assert "budget_chargeable" not in columns
    assert repo.daily_tokens(chargeable_only=True)=={"input":0,"output":0,"total":0}

def test_real_provider_still_enters_paid_budget(repo,monkeypatch):
    monkeypatch.setenv("AI_REPORT_DAILY_TOTAL_TOKENS","1")
    item=ReportService(repo).submit(base_context(),mode="FULL",provider="deepseek",model="bounded-live")
    worker=ReportWorker(repo,lambda _request:pytest.fail("budget must block before provider"),budget=TokenBudget())
    assert worker.run_once() is True
    assert repo.status(item["request_id"])["status"]=="BUDGET_BLOCKED"
    assert 'DAILY_TOTAL_TOKEN_CAP' in repo.status(item["request_id"])["events"][-1]["payload_json"]


def test_zero_live_call_limit_keeps_other_paid_provider_guards(repo, monkeypatch):
    monkeypatch.setattr(repo, "daily_live_provider_usage", lambda: {
        "calls": 999, "input": 0, "output": 0, "total": 0,
        "estimated_cost": 0, "unaudited_cost_attempts": 0,
    })
    budget = TokenBudget()
    budget.live_calls = 0
    budget.cost_status = "B3_CONTROL_LEDGER"
    budget.input_price = __import__('decimal').Decimal("0.14")
    budget.output_price = __import__('decimal').Decimal("0.28")

    assert budget.reason(repo, "BTC-USDT-SWAP", 1, 1, "deepseek") is None
    budget.request_input = 0
    assert budget.reason(repo, "BTC-USDT-SWAP", 1, 1, "deepseek") == "REQUEST_INPUT_TOKEN_CAP"

@pytest.mark.parametrize("provider",["fake","mock","local","dry-run","dry_run","test"])
def test_non_external_provider_classes_are_not_chargeable(provider):
    assert provider_budget_chargeable(provider) is False

def test_unknown_and_real_provider_classes_fail_safe_as_chargeable():
    assert provider_budget_chargeable("deepseek") is True
    assert provider_budget_chargeable("future-paid-provider") is True

def test_charged_length_truncation_is_distinct_and_never_retried(repo):
    class TruncatedProvider:
        calls=0
        def generate(self,_request):
            self.calls+=1
            return ProviderResult('{"schema_version":"ai-market-report-response-v4"',"paid-id","deepseek-v4-flash",
                {"prompt_tokens":5118,"completion_tokens":3000,"total_tokens":8118},"length",200,10,"raw-hash")
    provider=TruncatedProvider()
    item=ReportService(repo).submit(base_context(),mode="QUICK",provider="deepseek",model="deepseek-v4-flash")
    monkey_budget=TokenBudget();monkey_budget.cost_status="B3_CONTROL_LEDGER"
    monkey_budget.input_price=__import__('decimal').Decimal("0.14")
    monkey_budget.output_price=__import__('decimal').Decimal("0.28")
    worker=ReportWorker(repo,lambda _request:provider,budget=monkey_budget)
    assert worker.run_once() is True
    assert provider.calls==1
    status=repo.status(item["request_id"])
    assert status["status"]=="FAILED_FINAL"
    assert 'PROVIDER_OUTPUT_TRUNCATED' in status["events"][-1]["payload_json"]
    with repo.connect() as connection:
        attempt=connection.execute("SELECT failure_code,finish_reason,parse_status FROM ai_report_attempts WHERE request_id=?",(item["request_id"],)).fetchone()
    assert tuple(attempt)==("PROVIDER_OUTPUT_TRUNCATED","length","FAILED")
    assert repo.get_report(request_id=item["request_id"]) is None
    assert worker.run_once() is False

def test_complete_json_below_new_quick_cap_still_completes(repo):
    item=submit(repo,"QUICK")
    provider=FakeAIReportProvider()
    assert ReportWorker(repo,lambda _request:provider).run_once() is True
    assert repo.status(item["request_id"])["status"]=="COMPLETED"
    assert provider.calls==1
