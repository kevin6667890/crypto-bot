import json

from dashboard.ai_market_analysis.report_jobs import ReportWorker, TokenBudget
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider, ProviderResult
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from tests.ai_market_analysis.ai4_helpers import base_context


def test_stale_provider_level_projection_is_replaced_before_validation(tmp_path, monkeypatch):
    database = tmp_path / "reports.db"
    monkeypatch.setenv("AI_REPORT_COST_STATUS", "B3_CONTROL_LEDGER")
    monkeypatch.setenv("AI_REPORT_INPUT_USD_PER_MILLION", "0.14")
    monkeypatch.setenv("AI_REPORT_OUTPUT_USD_PER_MILLION", "0.28")
    migrate_database(database)
    repository = ReportRepository(database)
    submitted = ReportService(repository).submit(base_context(), mode="QUICK", provider="deepseek", model="deepseek-v4-flash")

    class StaleLevelProvider:
        def generate(self, request):
            result = FakeAIReportProvider("deepseek-v4-flash").generate(request)
            report = json.loads(result.raw_text)
            report["scenarios"][0]["level_refs"] = ["stale-provider-level"]
            report["scenarios"][0]["trigger_level_refs"] = ["stale-provider-level"]
            return ProviderResult(json.dumps(report), "fixture-request", "deepseek-v4-flash", {}, "stop", 200, 1, "fixture")

    assert ReportWorker(repository, lambda _: StaleLevelProvider(), budget=TokenBudget()).run_once()
    report = repository.get_report(request_id=submitted["request_id"])
    assert report is not None
    assert "stale-provider-level" not in json.dumps(report["response"])
