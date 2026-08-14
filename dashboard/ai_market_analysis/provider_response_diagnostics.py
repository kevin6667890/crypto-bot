"""Bounded, secret-redacted persistence for real-provider response diagnostics."""
from __future__ import annotations

import json
import re
from typing import Any

from .canonical import canonical_json

MAX_SANITIZED_RESPONSE_BYTES = 300_000
DIAGNOSTIC_VERSION = "ai6b-provider-response-diagnostic-v1"
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "password", "private_key", "secret", "token"}
_TEXT_PATTERNS = (
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.I | re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_PROVIDER_TOKEN]"),
)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        for pattern, replacement in _TEXT_PATTERNS:
            value = pattern.sub(replacement, value)
    return value


def sanitize_provider_response(raw_text: str) -> str:
    """Preserve response evidence without ever persisting credential-shaped values."""
    try:
        sanitized = canonical_json(_redact_value(json.loads(raw_text)))
    except (TypeError, ValueError, json.JSONDecodeError):
        sanitized = str(raw_text)
        for pattern, replacement in _TEXT_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
    encoded = sanitized.encode("utf-8")
    if len(encoded) > MAX_SANITIZED_RESPONSE_BYTES:
        sanitized = encoded[:MAX_SANITIZED_RESPONSE_BYTES].decode("utf-8", errors="ignore") + "[TRUNCATED]"
    return sanitized


def reference_diagnostics(report: dict[str, Any], request: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    facts = registry.get("facts", [])
    allowed = {
        "fact_refs": {str(item["fact_id"]) for item in facts if item.get("fact_id")},
        "macro_refs": {str(item["evidence_id"]) for item in request.get("macro_items", []) if item.get("evidence_id")},
        "level_refs": {str(item["value"]["level_id"]) for item in facts if item.get("category") == "LEVEL" and isinstance(item.get("value"), dict) and item["value"].get("level_id")},
        "scenario_refs": {str(item["value"]["scenario_id"]) for item in facts if item.get("category") == "SCENARIO" and isinstance(item.get("value"), dict) and item["value"].get("scenario_id")},
        "position_refs": {str(item["fact_id"]) for item in facts if item.get("category") == "POSITION" and item.get("fact_id")},
    }
    used = {name: set() for name in allowed}
    for section in report.get("sections", []):
        for name in used:
            used[name].update(str(value) for value in section.get(name, []))
    for item in report.get("key_levels", []):
        used["fact_refs"].update(str(value) for value in item.get("fact_refs", []))
        used["level_refs"].update(str(value) for value in item.get("level_refs", []))
    for item in report.get("scenarios", []):
        used["fact_refs"].update(str(value) for value in item.get("fact_refs", []))
        used["level_refs"].update(str(value) for value in item.get("level_refs", []))
        used["scenario_refs"].add(str(item.get("scenario_id")))
    return {
        "version": DIAGNOSTIC_VERSION,
        "unknown_refs": {name: sorted(values - allowed[name]) for name, values in used.items()},
        "allowed_counts": {name: len(values) for name, values in allowed.items()},
    }
