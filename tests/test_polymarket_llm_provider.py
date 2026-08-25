from __future__ import annotations

import pytest

from dashboard.polymarket.llm_provider import OpenAICompatibleProvider, ProviderError, redact_secrets


class _Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _NonJsonResponse(_Response):
    def json(self):
        raise ValueError("not JSON")


def test_preflight_classifies_auth_failure_without_secret(monkeypatch):
    secret = "sk-abcdef1234567890"
    provider = OpenAICompatibleProvider("deepseek", "https://provider.test", "model-a", secret)
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.get", lambda *args, **kwargs: _Response(401))
    result = provider.preflight()
    assert result == {"provider": "deepseek", "model": "model-a", "credential_present": "yes", "endpoint_reachable": True, "auth_status": "AUTH_FAILED", "model_available": False, "status": "NOT_READY"}
    assert secret not in str(result)


def test_provider_missing_credential_is_not_ready_and_cannot_generate():
    provider = OpenAICompatibleProvider("deepseek", "https://provider.test", "model-a", None)
    assert provider.preflight()["auth_status"] == "CREDENTIAL_MISSING"
    with pytest.raises(ProviderError, match="CREDENTIAL_MISSING"):
        provider.generate_structured_forecast({"safe": "request"})


def test_ready_preflight_requires_named_model(monkeypatch):
    provider = OpenAICompatibleProvider("custom", "https://provider.test", "model-a", "sk-abcdef1234567890")
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.get", lambda *args, **kwargs: _Response(200, {"data": [{"id": "model-a"}]}))
    assert provider.preflight()["status"] == "READY"


def test_secret_redaction_is_defensive():
    secret = "sk-abcdef1234567890"
    assert secret not in redact_secrets(f"bad auth {secret}", secret)


def test_deepseek_request_explicitly_disables_thinking_and_is_nonstreaming(monkeypatch):
    captured = {}
    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _Response(200, {"model": "deepseek-v4-pro", "choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}]})
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.post", fake_post)
    result = OpenAICompatibleProvider("deepseek", "https://provider.test", "deepseek-v4-pro", "sk-abcdef1234567890").generate_structured_forecast({"JSON": "test"})
    assert result.content == '{"ok":true}'
    assert captured["stream"] is False
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["response_format"] == {"type": "json_object"}


def test_reasoning_only_response_is_not_mistaken_for_valid_empty_content(monkeypatch):
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.post", lambda *a, **k: _Response(200, {"choices": [{"message": {"content": None, "reasoning_content": "hidden"}}]}))
    with pytest.raises(ProviderError) as exc:
        OpenAICompatibleProvider("deepseek", "https://provider.test", "deepseek-v4-pro", "sk-abcdef1234567890").generate_structured_forecast({})
    assert exc.value.code == "EMPTY_CONTENT_WITH_REASONING"
    assert exc.value.diagnostic["reasoning_content_length"] == 6


def test_response_diagnostic_records_actual_message_shape(monkeypatch):
    payload = {"model": "deepseek-v4-pro", "usage": {"completion_tokens": 7}, "choices": [{"finish_reason": "stop", "message": {"content": "{}", "reasoning_content": None}}]}
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.post", lambda *a, **k: _Response(200, payload))
    result = OpenAICompatibleProvider("deepseek", "https://provider.test", "deepseek-v4-pro", "sk-abcdef1234567890").generate_structured_forecast({})
    assert result.raw_payload == payload
    assert result.diagnostic["message_keys"] == ["content", "reasoning_content"]
    assert result.diagnostic["content_type"] == "str"


def test_non_json_rate_limit_is_classified_without_format_fallback_signal(monkeypatch):
    monkeypatch.setattr("dashboard.polymarket.llm_provider.requests.post", lambda *a, **k: _NonJsonResponse(429))
    with pytest.raises(ProviderError) as exc:
        OpenAICompatibleProvider("deepseek", "https://provider.test", "deepseek-v4-pro", "sk-abcdef1234567890").generate_structured_forecast({})
    assert exc.value.code == "RATE_LIMIT"
