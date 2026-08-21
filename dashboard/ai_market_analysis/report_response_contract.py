"""Single source of truth for the generated AI report response shape."""
from __future__ import annotations

from typing import Any

from .versions import AI_REPORT_RESPONSE_VERSION
from .provider_claim_pack import build_provider_claim_pack, provider_claim_pack_contract

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
    "level_id", "analysis_text", "representative_price", "zone_low", "zone_high", "observed_at", "source_fact",
    "asserted_role", "asserted_state", "asserted_strength",
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
# QUICK remains one provider request and one audit, but it is no longer a
# one-paragraph-only product surface.  These bounded sections give Research
# an auditable narrative while Workspace continues to consume QUICK_SUMMARY.
QUICK_SECTION_IDS = (
    "QUICK_SUMMARY", "CONCLUSION", "TF_15M", "TF_1H", "TF_4H", "TF_1D", "TF_1W",
    "ORDER_FLOW", "KEY_LEVELS", "SCENARIOS", "LIMITATIONS",
)
ALL_SECTION_IDS = (
    "QUICK_SUMMARY", "CONCLUSION", "MACRO_BACKGROUND", "RECENT_PROCESS", "MOVE_NATURE",
    "TF_15M", "TF_1H", "TF_4H", "TF_1D", "TF_1W", "ORDER_FLOW", "KEY_LEVELS",
    "SCENARIOS", "LIMITATIONS", "POSITION_PLAN",
)


def provider_reference_allowlists(compiled_context: dict[str, Any]) -> dict[str, list[str]]:
    """Return disjoint provider-facing identifier namespaces for one frozen context."""
    facts = compiled_context.get("facts", [])
    claim_pack = compiled_context.get("provider_claim_pack") or build_provider_claim_pack(compiled_context, compiled_context.get("mode", "QUICK"))
    packed_categories = claim_pack.get("fact_ids_by_category", {})
    packed_fact_ids = {str(value) for values in packed_categories.values() for value in values}
    fact_ids = sorted(packed_fact_ids or {str(item["fact_id"]) for item in facts if item.get("fact_id")})
    scenario_values = [
        item["value"] for item in facts
        if item.get("category") == "SCENARIO" and isinstance(item.get("value"), dict)
    ]
    return {
        "fact_refs": fact_ids,
        "macro_refs": sorted(set(claim_pack.get("macro_evidence_ids", [])) or {
            str(item["value"]["evidence_id"])
            for item in facts
            if item.get("category") == "MACRO" and isinstance(item.get("value"), dict)
            and item["value"].get("evidence_id")
        }),
        # FLOW/TIMEFRAME are scoped FACT identifiers, not additional response fields.
        "flow_refs": sorted(set(packed_categories.get("ORDER_FLOW", [])) or {
            str(item["fact_id"]) for item in facts
            if item.get("category") == "ORDER_FLOW" and item.get("fact_id")
        }),
        "level_refs": sorted({str(item["level_id"]) for item in claim_pack.get("levels", [])} or {
            str(item["value"]["level_id"])
            for item in facts
            if item.get("category") == "LEVEL" and isinstance(item.get("value"), dict)
            and item["value"].get("level_id")
        }),
        "scenario_refs": sorted({str(item["scenario_id"]) for item in claim_pack.get("scenarios", [])} or {
            str(item["value"]["scenario_id"])
            for item in facts
            if item.get("category") == "SCENARIO" and isinstance(item.get("value"), dict)
            and item["value"].get("scenario_id")
        }),
        "position_refs": sorted(set(packed_categories.get("POSITION", [])) or {
            str(item["fact_id"]) for item in facts
            if item.get("category") == "POSITION" and item.get("fact_id")
        }),
        "timeframe_refs": sorted(set(packed_categories.get("TIMEFRAME", [])) or {
            str(item["fact_id"]) for item in facts
            if item.get("category") == "TIMEFRAME" and item.get("fact_id")
        }),
        "source_phase_ids": sorted({
            str(value) for scenario in scenario_values for value in scenario.get("source_phase_ids", [])
        }),
        "source_event_ids": sorted({
            str(value) for scenario in scenario_values for value in scenario.get("source_event_ids", [])
        }),
    }


def provider_reference_namespace_matrix() -> dict[str, dict[str, str]]:
    """Map every canonical reference-bearing provider field to one exact namespace."""
    fields = {
        "sections[].fact_refs": "fact_refs",
        "sections[].level_refs": "level_refs",
        "sections[].scenario_refs": "scenario_refs",
        "sections[].macro_refs": "macro_refs",
        "sections[].position_refs": "position_refs",
        "key_levels[].level_id": "level_refs",
        "key_levels[].fact_refs": "fact_refs",
        "key_levels[].level_refs": "level_refs",
        "scenarios[].scenario_id": "scenario_refs",
        "scenarios[].trigger_level_refs": "level_refs",
        "scenarios[].expected_path_level_refs": "level_refs",
        "scenarios[].target_level_refs": "level_refs",
        "scenarios[].invalidation_level_ref": "level_refs",
        "scenarios[].fact_refs": "fact_refs",
        "scenarios[].level_refs": "level_refs",
        "scenarios[].source_phase_ids": "source_phase_ids",
        "scenarios[].source_event_ids": "source_event_ids",
        "position_guidance.fact_refs": "position_refs",
        "position_guidance.original_invalidation.fact_ref": "position_refs",
        "citations[].evidence_id": "macro_refs",
    }
    return {
        field: {
            "namespace": namespace,
            "allowlist": f"allowed_reference_ids.{namespace}",
            "empty_set_behavior": "[] (or null only for a nullable scalar field)",
            "restriction": "exact allowlist member only; cross-namespace IDs forbidden",
        }
        for field, namespace in fields.items()
    }


def expected_section_manifest(mode: str, has_macro: bool, *, has_flow: bool = True,
                              has_long_term: bool = True) -> dict[str, Any]:
    """Return the one canonical required/forbidden section contract for a frozen context."""
    if mode == "QUICK":
        required = list(QUICK_SECTION_IDS)
    else:
        required = list(FULL_SECTION_IDS)
        if not has_flow:
            required.remove("ORDER_FLOW")
        if not has_long_term:
            required.remove("TF_1W")
        if has_macro:
            required.insert(1, "MACRO_BACKGROUND")
        if mode == "POSITION_AWARE":
            required.append("POSITION_PLAN")
    forbidden = [section_id for section_id in ALL_SECTION_IDS if section_id not in required]
    conditions = {
        "QUICK_SUMMARY": "required only in QUICK and is the compact Workspace projection",
        "MACRO_BACKGROUND": "required only in FULL/POSITION_AWARE with frozen macro evidence; otherwise forbidden",
        "ORDER_FLOW": "required in every report; when flow is unavailable, state that limitation without a directional flow claim",
        "TF_1W": "required in every report; when long-term evidence is unavailable, state that status without a directional long-term claim",
        "KEY_LEVELS": "required in FULL/POSITION_AWARE; use limitation text when levels are unavailable",
        "SCENARIOS": "required in FULL/POSITION_AWARE; use limitation text when scenarios are unavailable",
        "POSITION_PLAN": "required only in POSITION_AWARE; otherwise forbidden",
    }
    return {
        "mode": mode,
        "macro_evidence_available": bool(has_macro),
        "flow_evidence_available": bool(has_flow),
        "long_term_evidence_available": bool(has_long_term),
        "required_section_ids_in_exact_order": required,
        "forbidden_section_ids": forbidden,
        "conditional_section_rules": conditions,
        "unconstrained_conditional_sections": 0,
    }


def expected_section_ids(mode: str, has_macro: bool) -> list[str]:
    return list(expected_section_manifest(mode, has_macro)["required_section_ids_in_exact_order"])


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
    evidence_status = (compiled_context.get("provider_claim_pack") or {}).get("evidence_status", {})
    fact_values = {str(item.get("fact_id")): item.get("value") for item in compiled_context.get("facts", [])}
    long_term_quality = fact_values.get("LONG_TERM_QUALITY")
    section_manifest = expected_section_manifest(
        mode, has_macro,
        has_flow=evidence_status.get("flow_coverage_state") != "FLOW_UNAVAILABLE",
        has_long_term=long_term_quality in {None, "COMPLETE", "PARTIAL"},
    )
    sections = section_manifest["required_section_ids_in_exact_order"]
    confidence_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    max_rank = confidence_rank.get(str(compiled_context.get("max_confidence")), 0)
    confidence = [name for name, rank in confidence_rank.items() if rank <= max_rank]
    reference_allowlists = provider_reference_allowlists(compiled_context)
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
            "citations": "array of exact {evidence_id:string} objects; evidence_id uses macro_refs namespace only",
        },
        "allowed_values": {
            "market_phase": compiled_context.get("allowed_market_phases", []),
            "directional_bias": compiled_context.get("allowed_directional_biases", []),
            "confidence": confidence,
        },
        "expected_section_manifest": section_manifest,
        "section_order": sections,
        "section_contract_rules": [
            "emit every required section exactly once and in required_section_ids_in_exact_order",
            "never emit any forbidden_section_ids, even to explain unavailable evidence",
            "put unavailable-evidence status text only in an already-required section such as LIMITATIONS or QUICK_SUMMARY",
        ],
        "exact_section_fields": list(SECTION_FIELDS),
        "section_ref_fields_are_string_arrays": list(SECTION_FIELDS[3:]),
        "exact_level_projection_fields": list(LEVEL_PROJECTION_FIELDS),
        "exact_scenario_projection_fields": list(SCENARIO_PROJECTION_FIELDS),
        "position_guidance": (
            {"exact_fields": ["source", "fact_refs", "original_invalidation"],
             "original_invalidation_exact_fields": ["stop", "fact_ref", "timeframe", "thesis"]}
            if mode == "POSITION_AWARE" else None
        ),
        "allowed_reference_ids": reference_allowlists,
        "reference_namespace_matrix": provider_reference_namespace_matrix(),
        "reference_contract_summary": {
            "unconstrained_reference_fields": 0,
            "cross_namespace_provider_paths": 0,
            "citations_must_equal_section_macro_refs": True,
        },
        "provider_claim_pack": provider_claim_pack_contract(
            compiled_context.get("provider_claim_pack") or build_provider_claim_pack(compiled_context, mode)
        ),
        "claim_pack_rules": [
            "copy numeric values only from provider_claim_pack.allowed_numeric_values and never round or recalculate",
            "never derive percentages, differences, averages, ratios, or any other numeric value",
            "never use ASCII digits as list or paragraph numbering in narrative text",
            "never mention an indicator period unless its exact numeric value is present in allowed_numeric_values",
            "key_levels and scenarios are immutable deterministic projections; narrative may explain but never alter them",
            "when evidence_status is false, emit only the supplied limitation/status statement for that domain",
            "do not write ASCII timeframe tokens in narrative; use Chinese timeframe words to avoid numeric ambiguity",
            "host attaches strict evidence references from the claim pack; do not invent support relationships",
        ],
        "identifier_rules": [
            "each ref field may contain only exact IDs from its own allowed_reference_ids namespace",
            "flow_refs and timeframe_refs are scoped subsets of fact_refs and may appear only in fact_refs",
            "an empty allowlist requires [] and forbids factual claims that require that evidence namespace",
            "status FACT IDs such as MACRO_UNAVAILABLE are never macro_refs",
            "citations[].evidence_id may use only macro_refs and citations must equal the union of section macro_refs",
            "cross-namespace references are forbidden",
        ],
    }
