from datetime import datetime, timedelta, timezone

from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_scheduler import ReportScheduler


def test_failed_window_does_not_block_the_next_cadence(monkeypatch, tmp_path):
    """A fail-closed report consumes only its own hourly scheduler window."""
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    prior = datetime(2026, 8, 22, 7, 45, 58, tzinfo=timezone.utc)
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO ai_report_requests("
            "request_id,request_identity,context_id,instrument,mode,language,"
            "prompt_version,provider,model,max_output_tokens,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("failed-window", "failed-window", "context", "ETH-USDT-SWAP", "QUICK", "zh-CN",
             "prompt", "deepseek", "deepseek-v4-flash", 1, prior.isoformat().replace("+00:00", "Z")),
        )
    repository.event("failed-window", "VALIDATION_FAILED", {"code": "NUMERIC_NOT_IN_REGISTRY"})

    now = prior + timedelta(seconds=3601)
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_SCHEDULER_INSTRUMENTS", "ETH-USDT-SWAP")
    monkeypatch.setattr("dashboard.ai_market_analysis.report_scheduler._iso_now", lambda: now)
    queued = []
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_: queued.append(payload) or {"created": True},
    )

    ReportScheduler(repository, tmp_path / "paper.db", None).tick()

    assert len(queued) == 1
    assert queued[0]["instrument"] == "ETH-USDT-SWAP"
    assert queued[0]["decision_time"] == now.isoformat().replace("+00:00", "Z")
