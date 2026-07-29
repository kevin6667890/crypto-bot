"""No-network tests for bounded local operations trend telemetry."""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from dashboard.operations_trends import read_operations_trends
from scripts import capture_operations_trends as capture


def replies():
    return {
        "operations": ({
            "service": {"status": "RUNNING"},
            "wal_size_bytes": 2048,
            "maintenance": {
                "last_duration_ms": 12.5,
                "checkpoint_duration_ms": 4.5,
            },
            "collector": {"queue_depth": 3},
        }, 10.0),
        "health": ({
            "live_lag_seconds": 7,
            "critical_gap_count": 0,
        }, 20.0),
        "coverage": ({}, 30.0),
        "eligibility": ({}, 40.0),
    }


def install_replies(monkeypatch: pytest.MonkeyPatch, values=None) -> None:
    payloads = replies() if values is None else values
    monkeypatch.setattr(
        capture, "request_json",
        lambda _base, path, _timeout: payloads[
            next(name for name, endpoint in capture.ENDPOINTS.items()
                 if endpoint == path)])
    monkeypatch.setattr(capture, "local_iowait", lambda: 2.0)


def test_capture_is_minute_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_replies(monkeypatch)
    with sqlite3.connect(tmp_path / "trends.db") as connection:
        capture.capture_sample(connection, base_url="http://localhost", now=1_800_000_001)
        capture.capture_sample(connection, base_url="http://localhost", now=1_800_000_059)
        row = connection.execute(
            "SELECT COUNT(*), wal_size_bytes, queue_depth FROM operations_trends"
        ).fetchone()
    assert row == (1, 2048, 3)


def test_retention_keeps_at_most_seven_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_replies(monkeypatch)
    now = 1_800_000_000
    with sqlite3.connect(tmp_path / "trends.db") as connection:
        capture.initialize(connection)
        connection.executemany(
            """INSERT INTO operations_trends(
                   minute_ts, captured_at, service_state)
               VALUES(?, 'old', 'RUNNING')""",
            [(now - capture.RETENTION_SECONDS - 60,),
             (now - capture.RETENTION_SECONDS,)])
        capture.capture_sample(connection, base_url="http://localhost", now=now)
        timestamps = [
            row[0] for row in connection.execute(
                "SELECT minute_ts FROM operations_trends ORDER BY minute_ts")]
    assert timestamps == [now]


def test_partial_query_failure_does_not_claim_collector_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = replies()
    values["coverage"] = (None, None)
    install_replies(monkeypatch, values)
    with sqlite3.connect(tmp_path / "trends.db") as connection:
        capture.capture_sample(connection, base_url="http://localhost", now=1_800_000_000)
        state = connection.execute(
            "SELECT service_state FROM operations_trends").fetchone()[0]
    assert state == "PARTIAL_QUERY_FAILURE"
    assert state != "STOPPED"


def test_request_timeout_is_bounded_and_credentials_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []
    def timeout(*_args, **kwargs):
        observed.append(kwargs["timeout"])
        raise TimeoutError
    monkeypatch.setattr(capture, "urlopen", timeout)
    assert capture.request_json("http://localhost", "/api/operations/summary", 1.25) == (None, None)
    assert observed == [1.25]
    with pytest.raises(ValueError, match="Credentials"):
        capture.request_json("http://user:secret@localhost", "/api/operations/summary", 1)


def test_read_only_24_hour_aggregation_and_disabled_state(tmp_path: Path) -> None:
    missing = read_operations_trends(db_path=tmp_path / "missing.db", now=1_800_000_000)
    assert missing == {
        "enabled": False, "window": "24h", "points": [],
        "latency": {"p50_ms": None, "p95_ms": None}}

    database = tmp_path / "trends.db"
    now = 1_800_000_000
    with sqlite3.connect(database) as connection:
        capture.initialize(connection)
        for index, age in enumerate((86_460, 86_340, 3_600, 0)):
            connection.execute(
                """INSERT INTO operations_trends(
                       minute_ts, captured_at, health_latency_ms,
                       service_state, critical_gap_count)
                   VALUES(?, 'sample', ?, 'RUNNING', ?)""",
                (now - age, index + 1, int(age == 0)))
    result = read_operations_trends("24h", db_path=database, now=now)
    assert [point["timestamp"] for point in result["points"]] == [
        now - 86_340, now - 3_600, now]
    assert result["latency"] == {"p50_ms": 3.0, "p95_ms": 4.0}
    assert result["points"][-1]["anomaly"] is True


def test_telemetry_schema_contains_no_credentials_and_uses_bounded_queries() -> None:
    assert "token" not in capture.SCHEMA.lower()
    assert "password" not in capture.SCHEMA.lower()
    source = inspect.getsource(capture) + inspect.getsource(read_operations_trends)
    assert "SELECT *" not in source.upper()
    assert "WHERE minute_ts >= ?" in source
