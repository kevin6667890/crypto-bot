from datetime import datetime, timedelta, timezone

from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_scheduler import ReportScheduler


def test_failed_window_does_not_block_the_next_cadence(monkeypatch, tmp_path):
    """A fail-closed report consumes only its own confirmed-4H window."""
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

    now = datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc)
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
    assert queued[0]["decision_time"] == "2026-08-22T12:00:00Z"


def test_confirmation_grace_never_labels_unconfirmed_boundary(monkeypatch, tmp_path):
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_SCHEDULER_CONFIRMATION_GRACE_SECONDS", "120")
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler._iso_now",
        lambda: datetime(2026, 8, 22, 12, 1, 59, tzinfo=timezone.utc),
    )
    queued = []
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_: queued.append(payload) or {
            "created": True, "canonical_snapshot_identity": "a" * 64,
        },
    )
    state = ReportScheduler(repository, tmp_path / "paper.db", None).tick()
    assert queued[0]["decision_time"] == "2026-08-22T08:00:00Z"
    assert state["scheduler_mode"] == "CONFIRMED_4H_CLOSE"
    assert state["estimated_automatic_reports_per_day"] == 6
    assert state["material_transition_trigger"] == "DISABLED"
