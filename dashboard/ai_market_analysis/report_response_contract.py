"""Single source of truth for the generated AI report response shape."""
from __future__ import annotations

from typing import Any

from .versions import AI_REPORT_RESPONSE_VERSION

SERVICE_REQUEST_ID_SENTINEL = "__SERVICE_REQUEST_ID__"
SERVICE_SOURCE_VERSIONS_SENTINEL = "__SERVICE_SOURCE_VERSIONS__"

TOP_FIELDS = (
    "schema_version", "source_versions", "context_id", "request_id", "mode", "language",
    "headline", "market_phase", "directional_bias", "confidence", "sections", "key_levels",
    "scenarios", "position_guidance", "unsupported_claims", "data_warnings", "citations",
    "model", "prompt_version", "audit_status",
)
SECTION_FIELDS = (
    "section_id", "title", "body", "fact_refs", "level_refs", "scenario_refs", "macro_refs",
    "position_refs", "uncertainties",
)
LEVEL_PROJECTION_FIELDS = (
    "level_id", "analysis_text", "asserted_role", "asserted_state", "asserted_strength",
    "asserted_timeframe", "asserted_dynamic", "valid_until", "fact_refs", "level_refs",
)
SCENARIO_PROJECTION_FIELDS = (
    "scenario_id", "scenario_type", "direction", "likelihood", "summary", "trigger_text",
    "trigger_level_refs", "confirmation_text", "expected_path_text", "expected_path_level_refs",
    "target_level_refs", "invalidation_text", "invalidation_level_ref", "invalidation_timeframe",
    "confirmed_close_required", "volume_confirmation_text", "cvd_confirmation_text",
    "oi_confirmation_text", "funding_basis_confirmation_text", "contradicting_evidence_text",
    "fact_refs", "level_refs", "source_phase_ids", "source_event_ids", "uncertainty_markers",
)
FULL_SECTION_IDS = (
    "CONCLUSION", "RECENT_PROCESS", "MOVE_NATURE", "TF_15M", "TF_1H", "TF_4H", "TF_1D",
    "TF_1W", "ORDER_FLOW", "KEY_LEVELS", "SCENARIOS", "LIMITATIONS",
)


def expected_section_ids(mode: str, has_macro: bool) -> list[str]:
    if mode == "QUICK":
        return ["QUICK_SUMMARY"]
    result = list(FULL_SECTION_IDS)
    if has_macro:
        result.insert(1, "MACRO_BACKGROUND")
    if mode == "POSITION_AWARE":
        result.append("POSITION_PLAN")
    return result


def response_metadata_contract(*, context_id: str, mode: str, language: str, model: str,
                               prompt_version: str, source_versions: dict[str, Any]) -> dict[str, Any]:
    """Return immutable values the provider must copy, except the circular request-id sentinel."""
    return {
        "schema_version": AI_REPORT_RESPONSE_VERSION,
        "source_versions": SERVICE_SOURCE_VERSIONS_SENTINEL,
        "context_id": context_id,
        "request_id": SERVICE_REQUEST_ID_SENTINEL,
        "mode": mode,
        "language": language,
        "model": model,
        "prompt_version": prompt_version,
        "audit_status": "PENDING",
    }


def provider_json_schema(metadata: dict[str, Any], compiled_context: dict[str, Any]) -> dict[str, Any]:
    """Build a compact machine-readable prompt contract from canonical field constants.

    DeepSeek JSON Output guarantees a JSON object, not arbitrary JSON Schema enforcement.
    The strict local parser remains authoritative for this compact contract.
    """
    has_macro = any(
        item.get("category") == "MACRO" and item.get("fact_id") != "MACRO_UNAVAILABLE"
        for item in compiled_context.get("facts", [])
    )
    mode = str(metadata["mode"])
    sections = expected_section_ids(mode, has_macro)
    confidence_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    max_rank = confidence_rank.get(str(compiled_context.get("max_confidence")), 0)
    confidence = [name for name, rank in confidence_rank.items() if rank <= max_rank]
    return {
        "contract_version": "ai-market-report-provider-contract-v1",
        "output": "one JSON object only",
        "additional_fields_allowed": False,
        "exact_top_level_fields": list(TOP_FIELDS),
        "immutable_values": metadata,
        "top_level_types": {
            "headline": "non-empty string", "market_phase": "allowed enum",
            "directional_bias": "allowed enum", "confidence": "allowed enum",
            "sections": "array", "key_levels": "array", "scenarios": "array",
            "position_guidance": "object only for POSITION_AWARE; otherwise null",
            "unsupported_claims": "string array", "data_warnings": "string array",
            "citations": "array of exact {evidence_id:string} objects",
        },
        "allowed_values": {
            "market_phase": compiled_context.get("allowed_market_phases", []),
            "directional_bias": compiled_context.get("allowed_directional_biases", []),
            "confidence": confidence,
        },
        "section_order": sections,
        "exact_section_fields": list(SECTION_FIELDS),
        "section_ref_fields_are_string_arrays": list(SECTION_FIELDS[3:]),
        "exact_level_projection_fields": list(LEVEL_PROJECTION_FIELDS),
        "exact_scenario_projection_fields": list(SCENARIO_PROJECTION_FIELDS),
        "position_guidance": (
            {"exact_fields": ["source", "fact_refs", "original_invalidation"],
             "original_invalidation_exact_fields": ["stop", "fact_ref", "timeframe", "thesis"]}
            if mode == "POSITION_AWARE" else None
        ),
        "identifier_rule": "all refs must copy existing identifiers from FACT_REGISTRY_JSON",
    }
