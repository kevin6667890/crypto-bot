from types import SimpleNamespace

from dashboard import paper_api


def test_paper_scheduler_tick_survives_transient_database_lock(monkeypatch):
    class LockedService:
        last_cycle_duration_ms = None

        @staticmethod
        def cycle():
            raise paper_api.sqlite3.OperationalError("database is locked")

    events = []
    monkeypatch.setattr(paper_api, "SERVICE", LockedService())
    monkeypatch.setattr(
        paper_api,
        "log_event",
        lambda _logger, level, component, event, **fields: events.append(
            (level, component, event, fields)
        ),
    )

    assert paper_api.paper_scheduler_tick() is False
    assert events == [
        (
            "ERROR",
            "paper_scheduler",
            "cycle_failed",
            {"error_type": "OperationalError"},
        )
    ]


def test_paper_scheduler_tick_records_success(monkeypatch):
    service = SimpleNamespace(last_cycle_duration_ms=123, cycle=lambda: None)
    events = []
    monkeypatch.setattr(paper_api, "SERVICE", service)
    monkeypatch.setattr(
        paper_api,
        "log_event",
        lambda _logger, level, component, event, **fields: events.append(
            (level, component, event, fields)
        ),
    )

    assert paper_api.paper_scheduler_tick() is True
    assert events == [
        (
            "INFO",
            "paper_scheduler",
            "cycle_completed",
            {"duration_ms": 123},
        )
    ]
