"""AI-6B B4 blocker regression: transport interruption classification and
paid-attempt identity persistence at the send boundary."""
from __future__ import annotations

import json
import sqlite3
from http.client import IncompleteRead

import pytest

import dashboard.ai_market_analysis.deepseek_report_provider as provider_module
from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.report_jobs import ReportWorker, TokenBudget
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider, ProviderError
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context

_CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "model": "deepseek-v4-flash",
    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


class _FakeHTTPResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}", read_error=None):
        self.status = status
        self._body = body
        self._read_error = read_error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amt=-1):
        if self._read_error is not None:
            raise self._read_error
        return self._body


def _provider(monkeypatch: pytest.MonkeyPatch, tmp_path, response) -> DeepSeekAIReportProvider:
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE", str(tmp_path / "kill-switch.json"))
    monkeypatch.setattr(provider_module, "urlopen", lambda req, timeout: response)
    return DeepSeekAIReportProvider(model="deepseek-v4-flash", api_key="test-key")


def _live_request(provider: str, tmp_path) -> dict:
    path = tmp_path / "r.db"
    repo = ReportRepository(path)
    return repo, ReportService(repo).submit(
        base_context(), mode="FULL", provider=provider, model="deepseek-v4-flash"
    )


@pytest.fixture
def repo(tmp_path):
    migrate_database(tmp_path / "r.db")
    return ReportRepository(tmp_path / "r.db")


def test_incomplete_read_is_provider_response_interrupted(monkeypatch, tmp_path):
    provider = _provider(monkeypatch, tmp_path,
        _FakeHTTPResponse(read_error=IncompleteRead(b"x" * 12267, 1000000)))
    with pytest.raises(ProviderError) as raised:
        provider.generate({"max_output_tokens": 200000, "messages": [{"role": "user", "content": "hi"}]})
    error = raised.value
    assert error.code == "PROVIDER_RESPONSE_INTERRUPTED"
    assert error.retryable is False
    assert error.request_body_sent is True
    assert error.provider_accepted is True
    assert error.charge_state == "UNKNOWN_CHARGE_STATE"


def test_connection_reset_during_body_is_provider_response_interrupted(monkeypatch, tmp_path):
    provider = _provider(monkeypatch, tmp_path,
        _FakeHTTPResponse(read_error=ConnectionResetError("reset during body")))
    with pytest.raises(ProviderError) as raised:
        provider.generate({"max_output_tokens": 200000, "messages": [{"role": "user", "content": "hi"}]})
    error = raised.value
    assert error.code == "PROVIDER_RESPONSE_INTERRUPTED"
    assert error.charge_state == "UNKNOWN_CHARGE_STATE"
    assert error.retryable is False


def test_transport_lifecycle_events_emitted_on_success(monkeypatch, tmp_path):
    body = json.dumps(_CHAT_COMPLETION, separators=(",", ":")).encode("utf-8")
    provider = _provider(monkeypatch, tmp_path, _FakeHTTPResponse(body=body))
    events: list[str] = []
    result = provider.generate({
        "max_output_tokens": 200000, "messages": [{"role": "user", "content": "hi"}],
        "on_transport_event": events.append,
    })
    assert result.usage["prompt_tokens"] == 10
    assert events == ["RESPONSE_HEADERS_RECEIVED", "BODY_STREAMING", "USAGE_RECONCILED"]


def test_submitted_attempt_persisted_before_provider_call(repo):
    calls: list[str] = []

    class _CrashingProvider:
        provider_name = "fake"

        def generate(self, request):
            calls.append(request["request_id"])
            raise RuntimeError("worker crash mid-call")

    item = ReportService(repo).submit(base_context(), mode="FULL")
    worker = ReportWorker(repo, lambda _request: _CrashingProvider())
    with pytest.raises(RuntimeError):
        worker.run_once()
    assert calls == [item["request_id"]]
    with repo.connect() as connection:
        row = connection.execute(
            "SELECT lifecycle_state,charge_state,completed_at,failure_code FROM ai_report_attempts WHERE request_id=?",
            (item["request_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "SUBMITTED" and row[1] is None and row[2] is None and row[3] is None


def test_provider_response_interrupted_persists_attempt_and_fails_final(repo):
    class _InterruptedProvider:
        provider_name = "fake"

        def generate(self, request):
            raise ProviderError("PROVIDER_RESPONSE_INTERRUPTED", retryable=False,
                                request_body_sent=True, provider_accepted=True,
                                charge_state="UNKNOWN_CHARGE_STATE")

    item = ReportService(repo).submit(base_context(), mode="FULL")
    assert ReportWorker(repo, lambda _request: _InterruptedProvider()).run_once() is True
    assert repo.status(item["request_id"])["status"] == "FAILED_FINAL"
    assert "RETRY_SCHEDULED" not in [e["event_type"] for e in repo.status(item["request_id"])["events"]]
    with repo.connect() as connection:
        row = connection.execute(
            "SELECT lifecycle_state,charge_state,failure_code FROM ai_report_attempts WHERE request_id=?",
            (item["request_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "FAILED" and row[1] == "UNKNOWN_CHARGE_STATE" and row[2] == "PROVIDER_RESPONSE_INTERRUPTED"


def test_success_updates_submitted_row_without_duplicate(repo):
    item = ReportService(repo).submit(base_context(), mode="FULL")
    worker = ReportWorker(repo, lambda request: FakeAIReportProvider(request["model"]))
    assert worker.run_once() is True
    with repo.connect() as connection:
        rows = connection.execute(
            "SELECT lifecycle_state,charge_state,attempt_number FROM ai_report_attempts WHERE request_id=?",
            (item["request_id"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "SUCCEEDED" and rows[0][1] is None and rows[0][2] == 1


def test_interrupted_live_request_records_unknown_charge_and_trips_kill_switch(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("AI6B_KILL_SWITCH_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE", str(tmp_path / "live-provider-disabled.json"))
    item = ReportService(repo).submit(base_context(), mode="FULL", provider="deepseek", model="deepseek-v4-flash")
    worker = ReportWorker(repo, lambda request: FakeAIReportProvider(request["model"]))
    repo.event(item["request_id"], "RUNNING", {"attempt": 1})
    assert worker.recover() == 1
    assert repo.status(item["request_id"])["status"] == "INTERRUPTED"
    assert worker.run_once() is True
    status = repo.status(item["request_id"])
    assert status["status"] == "FAILED_FINAL"
    assert "INTERRUPTED_LIVE_CALL_CHARGE_UNCERTAIN" in status["events"][-1]["payload_json"]
    with repo.connect() as connection:
        row = connection.execute(
            "SELECT lifecycle_state,charge_state,failure_code,attempt_number FROM ai_report_attempts WHERE request_id=?",
            (item["request_id"],),
        ).fetchone()
    assert row is not None
    assert row[0] == "UNKNOWN" and row[1] == "UNKNOWN_CHARGE_STATE"
    assert row[2] == "INTERRUPTED_LIVE_CALL_CHARGE_UNCERTAIN" and row[3] == 1
    switch = tmp_path / "live-provider-disabled.json"
    assert switch.exists()
    assert json.loads(switch.read_text(encoding="utf-8"))["event"] == "DUPLICATE_PROVIDER_CHARGE"


def test_migration_adds_lifecycle_and_charge_state_columns(tmp_path):
    path = tmp_path / "r.db"
    migrate_database(path)
    migrate_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_report_migrations").fetchone()[0] == 6
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_report_attempts)")}
    assert {"lifecycle_state", "charge_state"} <= columns
