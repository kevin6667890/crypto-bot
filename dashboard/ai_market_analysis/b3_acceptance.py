"""Fail-closed acceptance helpers used by B3 stubs and the future live runner."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


NEGATIVE_FAILURES = {
    "invalid_json": "INVALID_JSON",
    "markdown_wrapper": "MARKDOWN_WRAPPER_FORBIDDEN",
    "schema_missing_field": "SCHEMA_MISSING_FIELD",
    "extra_unsupported_number": "UNSUPPORTED_NUMERIC_CLAIM",
    "wrong_symbol": "WRONG_SYMBOL",
    "wrong_timeframe": "WRONG_TIMEFRAME",
    "stale_context_reference": "CONTEXT_MISMATCH",
    "hallucinated_level": "UNSUPPORTED_NUMERIC_CLAIM",
    "hallucinated_macro_fact": "REFERENCE_SUPPORT_FAILURE",
    "unsupported_scenario_probability": "UNSUPPORTED_NUMERIC_CLAIM",
    "truncated_output": "TRUNCATED_OUTPUT",
    "timeout": "UNKNOWN_CHARGE_STATE",
    "http_401": "HTTP_401",
    "http_403": "HTTP_403",
    "http_429": "UNKNOWN_CHARGE_STATE",
    "http_500": "UNKNOWN_CHARGE_STATE",
    "connection_reset": "UNKNOWN_CHARGE_STATE",
    "duplicate_response": "DUPLICATE_RESPONSE",
    "delayed_response": "STALE_RESPONSE",
}


def evaluate_stub_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    kind = fixture.get("fixture_type")
    code = NEGATIVE_FAILURES.get(str(kind))
    if code is None:
        return {
            "status": "FAILED_CLOSED",
            "failure_code": "UNKNOWN_FIXTURE_TYPE",
            "audit_passed": False,
            "presentation_body_allowed": False,
            "automatic_retry_allowed": False,
        }
    return {
        "status": "FAILED_CLOSED",
        "failure_code": code,
        "audit_passed": False,
        "presentation_body_allowed": False,
        "automatic_retry_allowed": False,
        "kill_switch_required": kind in {
            "extra_unsupported_number", "wrong_symbol", "wrong_timeframe",
            "stale_context_reference", "hallucinated_level", "hallucinated_macro_fact",
            "unsupported_scenario_probability", "duplicate_response",
        },
    }


def validate_numeric_grounding(
    numeric_claims: list[dict[str, Any]], numeric_registry: list[dict[str, Any]]
) -> dict[str, Any]:
    """Require every factual number to identify an exact frozen registry entry."""
    registry = {str(item["source_fact_id"]): item for item in numeric_registry}
    failures: list[dict[str, Any]] = []
    covered_types = {
        "price", "percentage", "RSI", "MA", "ATR", "CVD", "OI", "funding",
        "basis", "liquidation", "level", "target", "stop", "probability",
    }
    for claim in numeric_claims:
        if claim.get("content_class") == "NON_FACTUAL":
            continue
        numeric_type = str(claim.get("numeric_type"))
        source_id = claim.get("source_fact_id")
        source = registry.get(str(source_id))
        if numeric_type not in covered_types or not source:
            failures.append({"claim_id": claim.get("claim_id"), "code": "UNSUPPORTED_NUMERIC_CLAIM"})
            continue
        try:
            value_matches = Decimal(str(claim.get("canonical_value"))) == Decimal(str(source.get("canonical_value")))
        except (InvalidOperation, TypeError):
            value_matches = False
        if not value_matches or claim.get("unit") != source.get("unit"):
            failures.append({"claim_id": claim.get("claim_id"), "code": "NUMERIC_REGISTRY_MISMATCH"})
    return {
        "validator_version": "ai6b-b3-grounding-v1",
        "status": "PASSED" if not failures else "FAILED",
        "claim_count": len(numeric_claims),
        "failures": failures,
    }


def validate_reference_support(
    factual_claims: list[dict[str, Any]],
    *,
    fact_ids: set[str],
    numeric_ids: set[str],
    macro_ids: set[str],
) -> dict[str, Any]:
    failures = []
    allowed = fact_ids | numeric_ids | macro_ids
    for claim in factual_claims:
        if claim.get("content_class") == "NON_FACTUAL":
            continue
        refs = set(map(str, claim.get("support_refs") or []))
        if not refs or not refs <= allowed:
            failures.append({"claim_id": claim.get("claim_id"), "code": "REFERENCE_SUPPORT_FAILURE"})
    return {
        "validator_version": "ai6b-b3-reference-support-v1",
        "status": "PASSED" if not failures else "FAILED",
        "factual_claim_count": len(factual_claims),
        "failures": failures,
    }


def acceptance_gate(checks: dict[str, Any]) -> dict[str, Any]:
    required = (
        "context_freeze", "context_id", "registry_snapshot", "prompt_identity",
        "provider_reservation", "provider_response", "schema_parse", "numeric_grounding",
        "reference_support", "level_coverage", "scenario_coverage", "warning_coverage",
        "audit", "immutable_persistence", "presentation",
    )
    failed = [name for name in required if checks.get(name) != "PASSED"]
    return {
        "status": "PASSED" if not failed else "FAILED_CLOSED",
        "failed_checks": failed,
        "presentation_body_allowed": not failed and checks.get("audit") == "PASSED",
    }
