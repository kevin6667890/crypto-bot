from datetime import datetime, timedelta, timezone

from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_scheduler import ReportScheduler


def _base(payload):
    return {
        "instrument": payload["instrument"], "decision_time": payload["decision_time"],
        "latest_confirmed_market_time": payload["decision_time"],
        "canonical_market_snapshot": {"snapshot_identity": "a" * 64},
        "timeframe_structures": [], "timeframe_coverage": {},
        "multi_timeframe_summary": {}, "market_timeline": {},
        "order_flow_phases": [], "key_levels": [],
        "scenario_tree": {"status": "NOT_IMPLEMENTED", "scenarios": []},
        "data_quality": {"overall": "MISSING", "missing_sources": ["15m"]},
    }


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
        lambda payload, *_, **__: queued.append(payload) or {
            "created": True, "request_id": "request-12h",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores",
        lambda payload, *_: _base(payload),
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
        lambda payload, *_, **__: queued.append(payload) or {
            "created": True, "request_id": "request-grace",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores",
        lambda payload, *_: _base(payload),
    )
    state = ReportScheduler(repository, tmp_path / "paper.db", None).tick()
    assert queued[0]["decision_time"] == "2026-08-22T08:00:00Z"
    assert state["scheduler_mode"] == "CONFIRMED_4H_CLOSE"
    assert state["estimated_automatic_reports_per_day"] == 6
    assert state["material_transition_trigger"] == "DETERMINISTIC_GATE_V1"
    assert state["event_trigger"] == "DISABLED"
    assert state["material_gate_enabled"] is True


def test_no_material_change_is_persisted_and_does_not_submit(monkeypatch, tmp_path):
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    times = iter((
        datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 16, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 16, 3, tzinfo=timezone.utc),
    ))
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setattr("dashboard.ai_market_analysis.report_scheduler._iso_now", lambda: next(times))
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores",
        lambda payload, *_: _base(payload),
    )
    submitted = []
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_, **__: submitted.append(payload) or {
            "created": True, "request_id": "only-request",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    scheduler = ReportScheduler(repository, tmp_path / "paper.db", None)
    assert scheduler.tick()["last_evaluation_outcome"] == "QUEUED"
    state = scheduler.tick()
    assert len(submitted) == 1
    assert state["last_evaluation_outcome"] == "SKIPPED_NO_MATERIAL_CHANGE"
    assert state["facts_as_of"] == "2026-08-22T16:00:00Z"
    assert state["next_evaluation"] == "2026-08-22T20:02:00Z"
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT outcome FROM ai_report_generation_decisions ORDER BY confirmed_4h_close"
        ).fetchall()
    assert [row[0] for row in rows] == ["QUEUED", "SKIPPED_NO_MATERIAL_CHANGE"]


def test_facts_as_of_comes_from_canonical_context_not_scheduler_boundary(monkeypatch, tmp_path):
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler._iso_now",
        lambda: datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
    )
    canonical_facts_as_of = "2026-08-22T11:45:00Z"

    def build(payload, *_):
        value = _base(payload)
        value["latest_confirmed_market_time"] = canonical_facts_as_of
        return value

    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores", build,
    )
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_, **__: {
            "created": True, "request_id": "request-facts-as-of",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    state = ReportScheduler(repository, tmp_path / "paper.db", None).tick()
    assert state["facts_as_of"] == canonical_facts_as_of
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT confirmed_4h_close,facts_as_of FROM ai_report_generation_decisions"
        ).fetchone()
    assert tuple(row) == ("2026-08-22T12:00:00Z", canonical_facts_as_of)


def test_two_schedulers_and_restart_share_persistent_boundary_dedupe(monkeypatch, tmp_path):
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler._iso_now",
        lambda: datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores",
        lambda payload, *_: _base(payload),
    )
    submitted = []
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_, **__: submitted.append(payload) or {
            "created": True, "request_id": "single-request",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    ReportScheduler(repository, tmp_path / "paper.db", None).tick()
    ReportScheduler(ReportRepository(database), tmp_path / "paper.db", None).tick()
    assert len(submitted) == 1
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_report_generation_decisions").fetchone()[0] == 1


def test_material_transition_at_next_boundary_submits_again(monkeypatch, tmp_path):
    database = tmp_path / "reports.db"
    migrate_database(database)
    repository = ReportRepository(database)
    times = iter((
        datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 12, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 16, 3, tzinfo=timezone.utc),
        datetime(2026, 8, 22, 16, 3, tzinfo=timezone.utc),
    ))
    monkeypatch.setenv("AI_REPORT_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("AI_MARKET_REPORTS_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setattr("dashboard.ai_market_analysis.report_scheduler._iso_now", lambda: next(times))
    def build(payload, *_):
        value = _base(payload)
        state = "IMPULSE_UP" if payload["decision_time"].endswith("12:00:00Z") else "FAILED_BREAKOUT"
        value["timeframe_structures"] = [{
            "timeframe": "4H", "trend_classification": "BULL",
            "structure_classification": state, "confidence": "HIGH",
            "deterministic_intelligence": {"state": state, "extension_state": "NORMAL",
                                             "momentum": {}, "volume": {}},
        }]
        return value
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.build_base_context_from_stores", build,
    )
    submitted = []
    monkeypatch.setattr(
        "dashboard.ai_market_analysis.report_scheduler.submit_report",
        lambda payload, *_, **__: submitted.append(payload) or {
            "created": True, "request_id": f"request-{len(submitted)}",
            "canonical_snapshot_identity": "a" * 64,
        },
    )
    scheduler = ReportScheduler(repository, tmp_path / "paper.db", None)
    scheduler.tick(); state = scheduler.tick()
    assert len(submitted) == 2
    assert state["last_evaluation_outcome"] == "QUEUED"
