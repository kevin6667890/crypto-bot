"""Minimal, secret-safe OpenAI-compatible provider boundary.

This module deliberately knows nothing about markets or prices.  Its sole job is
to turn an already price-blind structured request into a raw model response and
to provide a bounded readiness check before a cohort tries to forecast.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from .models import canonical_json, stable_hash


class ProviderError(RuntimeError):
    """A safe, stable provider failure classification (never includes a key)."""

    def __init__(self, code: str, diagnostic: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.diagnostic = dict(diagnostic or {})
        super().__init__(code)


@dataclass(frozen=True)
class ProviderResult:
    content: str
    diagnostic: dict[str, Any]
    # This is the provider's complete response object, retained so the
    # append-only attempt ledger can audit the actual response shape.  It never
    # contains a credential because credentials are request headers only.
    raw_payload: Mapping[str, Any] | None = None


DEEPSEEK_PROVIDER_POLICY_VERSION = "deepseek-v4-pro-nonthinking-json-then-text-v1"
DEEPSEEK_PRIMARY_FORMAT = "json_object"
DEEPSEEK_FALLBACK_FORMAT = "text"
DEEPSEEK_FALLBACK_FAILURES = frozenset({"EMPTY_CONTENT", "INVALID_JSON"})


def provider_policy_hash() -> str:
    return stable_hash({
        "version": DEEPSEEK_PROVIDER_POLICY_VERSION,
        "attempts": [DEEPSEEK_PRIMARY_FORMAT, DEEPSEEK_FALLBACK_FORMAT],
        "fallback_only_for": sorted(DEEPSEEK_FALLBACK_FAILURES),
        "max_attempts": 2,
        "fallback_is_probability_blind": True,
    })


_KEY_PATTERN = re.compile(r"(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        # The process environment remains a supported configuration source.
        pass


def redact_secrets(value: str | None, known_secret: str | None = None) -> str | None:
    """Defence in depth for logs/ledger failure strings and raw error text."""
    if value is None:
        return None
    safe = str(value)
    if known_secret:
        safe = safe.replace(known_secret, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", safe)


def _http_failure_code(status_code: int) -> str | None:
    if status_code == 401:
        return "AUTH_FAILED"
    if status_code == 403:
        return "AUTH_FORBIDDEN"
    if status_code == 429:
        return "RATE_LIMIT"
    if not 200 <= status_code < 300:
        return f"HTTP_{status_code}"
    return None


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    provider: str
    base_url: str
    model: str
    api_key: str | None
    timeout_seconds: int = 45

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleProvider":
        _load_dotenv()
        provider = os.getenv("POLYMARKET_LLM_PROVIDER", "deepseek").strip().lower() or "deepseek"
        if provider == "deepseek":
            default_url, default_model, credential_names = "https://api.deepseek.com", "deepseek-v4-pro", ("POLYMARKET_LLM_API_KEY", "DEEPSEEK_API_KEY")
        elif provider in {"openai", "openai-compatible", "openai_compatible"}:
            default_url, default_model, credential_names = "https://api.openai.com/v1", "gpt-4o-mini", ("POLYMARKET_LLM_API_KEY", "OPENAI_API_KEY")
        else:
            # A custom endpoint is still OpenAI-compatible, but must be explicit.
            default_url, default_model, credential_names = "", "", ("POLYMARKET_LLM_API_KEY",)
        key = next((os.getenv(name) for name in credential_names if os.getenv(name)), None)
        return cls(
            provider=provider,
            base_url=os.getenv("POLYMARKET_LLM_BASE_URL", default_url).rstrip("/"),
            model=os.getenv("POLYMARKET_LLM_MODEL", default_model),
            api_key=key,
            timeout_seconds=int(os.getenv("POLYMARKET_LLM_TIMEOUT_SECONDS", "45")),
        )

    def identity(self) -> dict[str, str]:
        """Safe immutable identity suitable for an append-only forecast ledger."""
        return {"provider": self.provider, "model": self.model, "model_version": "provider-not-exposed", "endpoint": self.base_url}

    def _configuration_error(self) -> str | None:
        if not self.api_key:
            return "CREDENTIAL_MISSING"
        if not self.base_url.startswith(("https://", "http://")):
            return "ENDPOINT_INVALID"
        if not self.model:
            return "MODEL_NOT_CONFIGURED"
        return None

    def preflight(self) -> dict[str, Any]:
        """Check the configured endpoint once; do not create a forecast or retry."""
        output: dict[str, Any] = {"provider": self.provider, "model": self.model, "credential_present": "yes" if self.api_key else "no", "endpoint_reachable": False, "auth_status": "NOT_CHECKED", "model_available": False, "status": "NOT_READY"}
        issue = self._configuration_error()
        if issue:
            output["auth_status"] = issue
            return output
        try:
            response = requests.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout_seconds)
        except requests.RequestException:
            output["auth_status"] = "ENDPOINT_UNREACHABLE"
            return output
        output["endpoint_reachable"] = True
        if response.status_code == 401:
            output["auth_status"] = "AUTH_FAILED"
            return output
        if response.status_code in (403,):
            output["auth_status"] = "AUTH_FORBIDDEN"
            return output
        if not 200 <= response.status_code < 300:
            output["auth_status"] = f"HTTP_{response.status_code}"
            return output
        output["auth_status"] = "AUTH_OK"
        try:
            payload = response.json()
            models = payload.get("data", [])
            output["model_available"] = any(isinstance(item, Mapping) and item.get("id") == self.model for item in models)
        except (ValueError, AttributeError):
            output["auth_status"] = "MODELS_RESPONSE_INVALID"
            return output
        output["status"] = "READY" if output["model_available"] else "NOT_READY"
        return output

    def generate_structured_forecast(self, request: Mapping[str, Any], *, response_format: str = "json_object") -> ProviderResult:
        """Exactly one OpenAI-compatible request; errors are classified and safe."""
        issue = self._configuration_error()
        if issue:
            raise ProviderError(issue)
        if response_format not in {"json_object", "text"}:
            raise ValueError("unsupported_response_format")
        body: dict[str, Any] = {"model": self.model, "stream": False, "temperature": 0.1, "max_tokens": 300,
                "thinking": {"type": "disabled"},
                "messages": [{"role": "system", "content": "Return exactly one valid JSON object and nothing else. JSON is required; do not use Markdown or code fences. Example: {\"probability_yes\":0.57,\"confidence\":\"LOW\",\"evidence_refs\":[\"example-id\"],\"uncertainties\":[\"example\"],\"summary\":\"example\"}."}, {"role": "user", "content": canonical_json(dict(request))}]}
        if response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                timeout=self.timeout_seconds,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
        except requests.Timeout as exc:
            raise ProviderError("TIMEOUT") from exc
        except requests.RequestException as exc:
            raise ProviderError("ENDPOINT_UNREACHABLE") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            diagnostic = {"http_status": response.status_code, "thinking_mode": "disabled", "response_format": response_format, "response_top_level_keys": [], "content_length": 0}
            raise ProviderError(_http_failure_code(response.status_code) or "PROVIDER_RESPONSE_INVALID", diagnostic) from exc
        message: Mapping[str, Any] = {}
        choice: Mapping[str, Any] = {}
        if isinstance(payload, Mapping) and isinstance(payload.get("choices"), list) and payload["choices"] and isinstance(payload["choices"][0], Mapping):
            choice = payload["choices"][0]
            if isinstance(choice.get("message"), Mapping): message = choice["message"]
        # Chat-completions content is the only accepted forecast channel.  The
        # other fields are captured strictly for diagnostic purposes: silently
        # treating reasoning as a forecast would leak hidden reasoning into the
        # research record and make an incompatible endpoint appear healthy.
        content, reasoning = message.get("content"), message.get("reasoning_content")
        diagnostic = {"http_status": response.status_code, "model": payload.get("model") if isinstance(payload, Mapping) else None, "endpoint": self.base_url, "thinking_mode": "disabled", "response_format": response_format, "finish_reason": choice.get("finish_reason"), "content_type": type(content).__name__, "content_is_null": content is None, "content_repr_length": len(repr(content)), "content_length": len(content) if isinstance(content, str) else 0, "reasoning_content_present": reasoning is not None, "reasoning_content_length": len(reasoning) if isinstance(reasoning, str) else 0, "response_top_level_keys": sorted(payload.keys()) if isinstance(payload, Mapping) else [], "choice_keys": sorted(choice.keys()), "message_keys": sorted(message.keys()), "usage": payload.get("usage") if isinstance(payload, Mapping) else None, "response_hash": stable_hash(payload) if isinstance(payload, Mapping) else None}
        failure_code = _http_failure_code(response.status_code)
        if failure_code:
            raise ProviderError(failure_code, diagnostic)
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("EMPTY_CONTENT_WITH_REASONING" if reasoning is not None else "EMPTY_CONTENT", diagnostic)
        return ProviderResult(content, diagnostic, payload)


def configured_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider.from_environment()


def generate_structured_forecast(request: Mapping[str, Any]) -> ProviderResult:
    """Convenience interface used by the single-model forecast path."""
    return configured_provider().generate_structured_forecast(request)
