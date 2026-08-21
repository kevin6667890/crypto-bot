from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dashboard.ai_market_analysis.presentation_feed import _intelligence_projection, latest_workspace_brief, research_history
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository, migrate_audit_database
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_scheduler import ReportScheduler
from dashboard.ai_market_analysis.report_service import ReportService
from tests.ai_market_analysis.ai4_helpers import base_context
from tests.ai_market_analysis.test_ai6a_presentation import seeded


def test_latest_audit_passed_report_is_selected(tmp_path):
    reports, _, report = seeded(tmp_path)
    value = latest_workspace_brief(reports, "ETH-USDT-SWAP", "FULL")
    assert value["report_id"] == report["report_id"]
    assert value["display_eligible"] is True
    assert value["audit"]["status"] == "PASSED"
    assert value["provider"] == "fake" and value["model"]


def test_audit_failed_report_body_is_never_public(tmp_path):
    reports, _, _ = seeded(tmp_path, audit_status="FAILED")
    value = latest_workspace_brief(reports, "ETH-USDT-SWAP", "FULL")
    assert value["status"] == "NO_CURRENT_AUDITED_REPORT"
    assert value["display_eligible"] is False
    assert value["headline"] is None and value["executive_summary"] is None


def test_latest_failed_is_primary_even_when_an_old_valid_report_exists(tmp_path, monkeypatch):
    reports, _, report = seeded(tmp_path)
    from dashboard.ai_market_analysis import presentation_feed as feed
    original = feed.build_latest_presentation
    def latest_failed(*args, **kwargs):
        value = original(*args, **kwargs)
        value["latest_generated"] = {"report_id": "report_latest_failed", "eligibility": "AUDIT_FAILED"}
        return value
    monkeypatch.setattr(feed, "build_latest_presentation", latest_failed)
    value = latest_workspace_brief(reports, "ETH-USDT-SWAP", "FULL")
    assert value["primary_state"] == "LATEST_FAILED"
    assert value["last_display_eligible_report"]["report_id"] == report["report_id"]
    assert value["audit"]["status"] == "PASSED"  # retained only as historical metadata


def test_wrong_instrument_never_appears_in_history(tmp_path):
    reports, _, _ = seeded(tmp_path)
    assert research_history(reports, "SOL-USDT-SWAP")["items"] == []


def test_stale_and_unknown_are_not_presented_as_current(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path)
    from dashboard.ai_market_analysis import presentation_feed as feed
    original = feed.build_latest_presentation
    def stale(*args, **kwargs):
        value = original(*args, **kwargs); value["freshness"]["status"] = "STALE"; return value
    monkeypatch.setattr(feed, "build_latest_presentation", stale)
    value = latest_workspace_brief(reports, "ETH-USDT-SWAP", "FULL")
    assert value["status"] == "STALE_AUDITED_REPORT" and value["display_eligible"]


def test_wall_clock_age_marks_old_snapshot_stale(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path)
    from dashboard.ai_market_analysis import presentation_feed as feed
    original = feed.build_latest_presentation
    def old_snapshot(*args, **kwargs):
        value = original(*args, **kwargs); value["latest_confirmed_market_time"] = 0; return value
    monkeypatch.setattr(feed, "build_latest_presentation", old_snapshot)
    value = latest_workspace_brief(reports, "ETH-USDT-SWAP", "FULL")
    assert value["status"] == "STALE_AUDITED_REPORT"
    assert value["freshness"]["age_seconds"] > value["freshness"]["threshold_seconds"]


def test_auto_audit_is_queued_after_valid_report(tmp_path, monkeypatch):
    path = tmp_path / "auto-audit.db"
    migrate_database(path); migrate_audit_database(path)
    reports = ReportRepository(path)
    item = ReportService(reports).submit(base_context(), mode="QUICK")
    monkeypatch.setenv("AI_REPORT_AUTO_AUDIT_ENABLED", "true")
    ReportWorker(reports, lambda request: FakeAIReportProvider(request["model"])).run_once()
    report = reports.get_report(request_id=item["request_id"])
    audits = AuditRepository(path)
    with audits.connect() as connection:
        event = connection.execute("SELECT event_type FROM ai_report_audit_events WHERE report_id=? ORDER BY event_id DESC LIMIT 1", (report["report_id"],)).fetchone()
    assert event[0] == "AUDIT_QUEUED"


def test_scheduler_queues_once_per_cadence(tmp_path, monkeypatch):
    path = tmp_path / "scheduler.db"; migrate_database(path)
    reports = ReportRepository(path); queued = []
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    last = {"value": None}
    def submit(payload, *_):
        queued.append(payload); last["value"] = datetime.now(timezone.utc); return {"created": True}
    monkeypatch.setattr("dashboard.ai_market_analysis.report_scheduler.submit_report", submit)
    scheduler = ReportScheduler(reports, tmp_path / "paper.db", None)
    monkeypatch.setattr(scheduler, "_last_submission", lambda _instrument: last["value"])
    first = scheduler.tick(); second = scheduler.tick()
    assert len(queued) == 1
    assert first["last_tick"] and first["next_tick"] and second["last_tick"]
    assert all(item["mode"] == "QUICK" and item["position_source"] == "NONE" for item in queued)


def test_scheduler_exposes_cadence_relative_staleness(tmp_path, monkeypatch):
    path = tmp_path / "scheduler-staleness.db"; migrate_database(path); migrate_audit_database(path)
    reports = ReportRepository(path)
    monkeypatch.setenv("AI_REPORT_SCHEDULER_INSTRUMENTS", "ETH-USDT-SWAP")
    monkeypatch.setenv("AI_REPORT_SCHEDULER_CADENCE_SECONDS", "3600")
    scheduler = ReportScheduler(reports, tmp_path / "paper.db", None)
    state = scheduler.state()
    stale = state["report_staleness"]
    assert stale == [{"instrument": "ETH-USDT-SWAP", "last_display_eligible_report": None,
                      "age_seconds": None, "expected_refresh_interval_seconds": 3600,
                      "warning_after_seconds": 7200, "critical_after_seconds": 14400,
                      "status": "AI_REPORT_STALE_CRITICAL"}]


def test_provider_attempt_cap_disables_retry(tmp_path, monkeypatch):
    path = tmp_path / "attempt-cap.db"; migrate_database(path)
    reports = ReportRepository(path); item = ReportService(reports).submit(base_context(), mode="QUICK")
    monkeypatch.setenv("AI_REPORT_PROVIDER_ATTEMPT_MAX", "1")
    ReportWorker(reports, lambda request: FakeAIReportProvider(request["model"], "429")).run_once()
    assert reports.status(item["request_id"])["status"] == "FAILED_FINAL"


def test_intelligence_projection_keeps_five_frames_when_flow_is_unavailable():
    frames = []
    for timeframe in ("15m", "1H", "4H", "1D", "1W"):
        frames.append({
            "timeframe": timeframe,
            "deterministic_intelligence": {
                "role": "TACTICAL", "state": "HIGH_LEVEL_COMPRESSION",
                "extension_state": "EXTENDED", "momentum": {"state": "MOMENTUM_COOLING"},
                "tactical": {"state": "HIGH_LEVEL_COMPRESSION"},
                "volume": {"state": "VOLUME_NORMAL"},
            },
        })
    base = {
        "timeframe_structures": frames,
        "multi_timeframe_summary": {"alignment": "CONFLICTED", "conflicts": ["SETUP_COOLING"],
                                    "dominant_context": "HIGHER_TIMEFRAME_EXTENSION"},
        "order_flow_phases": [],
    }
    scenarios = [{"trigger_text": "confirmed close above local high",
                  "invalidation_text": "confirmed close below tactical support"}]
    value = _intelligence_projection(base, {"flow_quality": "FLOW_UNAVAILABLE"}, [], [], scenarios)
    assert set(value["timeframes"]) == {"15m", "1H", "4H", "1D", "1W"}
    assert value["flow_oi"]["flow_state"] == "FLOW_UNAVAILABLE"
    assert value["tactical"]["trigger"] == scenarios[0]["trigger_text"]
