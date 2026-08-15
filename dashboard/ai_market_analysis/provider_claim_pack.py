"""Deterministic evidence contract for real-provider narrative generation."""
from __future__ import annotations

import re
from typing import Any

# Narrative labels intentionally contain no numeric glyphs or number words.
# The numeric auditor treats every number as a market claim, while exact
# timeframe identity remains available in deterministic facts/projections.
_TIMEFRAME_WORDS = {"15m": "超短周期", "1H": "小时周期", "4H": "中周期", "1D": "日线", "1W": "周线"}


def _scenario_projection(fact: dict[str, Any], narrative: dict[str, Any] | None = None) -> dict[str, Any]:
    value = fact["value"]; trigger = value.get("trigger") or {}; confirmation = value.get("confirmation") or {}; invalidation = value.get("invalidation") or {}
    confirmation_rule = confirmation.get("rule") if isinstance(confirmation, dict) else confirmation
    return {
        "scenario_id": value["scenario_id"], "scenario_type": value["type"], "direction": value["direction"],
        "likelihood": value["likelihood"], "summary": str((narrative or {}).get("summary") or f"{value['type']} conditional path"),
        "trigger_text": trigger.get("rule"), "trigger_level_refs": trigger.get("level_ids", []),
        "confirmation_text": confirmation_rule, "expected_path_text": " -> ".join(value.get("expected_path", [])),
        "expected_path_level_refs": value.get("expected_path", []), "target_level_refs": value.get("targets", []),
        "invalidation_text": invalidation.get("rule"), "invalidation_level_ref": invalidation.get("level_id"),
        "invalidation_timeframe": invalidation.get("timeframe"),
        "confirmed_close_required": "confirmed" in str(invalidation.get("rule") or "").casefold(),
        "volume_confirmation_text": value.get("volume_confirmation"), "cvd_confirmation_text": value.get("cvd_confirmation"),
        "oi_confirmation_text": value.get("oi_confirmation"), "funding_basis_confirmation_text": value.get("funding_basis_confirmation"),
        "contradicting_evidence_text": "; ".join(map(str, value.get("contradicting_evidence", []))),
        "fact_refs": [fact["fact_id"]], "level_refs": value.get("source_level_ids", []),
        "source_phase_ids": value.get("source_phase_ids", []), "source_event_ids": value.get("source_event_ids", []),
        "uncertainty_markers": [value.get("likelihood")],
    }


def _level_projection(fact: dict[str, Any], narrative: dict[str, Any] | None = None) -> dict[str, Any]:
    value = fact["value"]
    return {
        "level_id": value["level_id"], "analysis_text": str((narrative or {}).get("analysis_text") or "Registry-grounded key level."),
        "asserted_role": value.get("role"), "asserted_state": value.get("state"), "asserted_strength": value.get("strength"),
        "asserted_timeframe": value.get("primary_timeframe"), "asserted_dynamic": value.get("dynamic"),
        "valid_until": value.get("valid_until"), "fact_refs": [fact["fact_id"]], "level_refs": [value["level_id"]],
    }


def build_provider_claim_pack(compiled_context: dict[str, Any], mode: str) -> dict[str, Any]:
    facts = list(compiled_context.get("facts", [])); by_category: dict[str, list[dict[str, Any]]] = {}
    for fact in facts: by_category.setdefault(str(fact.get("category")), []).append(fact)
    levels = [_level_projection(item) for item in by_category.get("LEVEL", [])
              if isinstance(item.get("value"), dict) and all(key in item["value"] for key in ("level_id", "role", "state", "strength"))]
    scenarios = [_scenario_projection(item) for item in by_category.get("SCENARIO", [])
                 if isinstance(item.get("value"), dict) and all(key in item["value"] for key in ("scenario_id", "type", "direction", "likelihood"))]
    macro_ids = [str(item["value"]["evidence_id"]) for item in by_category.get("MACRO", []) if isinstance(item.get("value"), dict) and item["value"].get("evidence_id")]
    unavailable_macro = next((str(item.get("value")) for item in facts if item.get("fact_id") == "MACRO_UNAVAILABLE"), "本次未加入已验证宏观证据。")
    numeric = [{"source_fact_id": item["source_fact_id"], "canonical_value": item["canonical_value"],
                "exact_display": item.get("exact_display", str(item["canonical_value"])), "unit": item.get("unit")}
               for item in compiled_context.get("numeric_registry", [])]
    return {
        "claim_pack_version": "ai6b-provider-claim-pack-v1", "mode": mode, "allowed_numeric_values": numeric,
        "levels": levels, "scenarios": scenarios, "macro_evidence_ids": macro_ids,
        "macro_unavailable_statement": None if macro_ids else unavailable_macro,
        "evidence_status": {"flow_available": bool(by_category.get("ORDER_FLOW")), "macro_available": bool(macro_ids),
                            "levels_available": bool(levels), "scenarios_available": bool(scenarios)},
        "fact_ids_by_category": {category: [item["fact_id"] for item in items] for category, items in sorted(by_category.items())},
    }


def provider_claim_pack_contract(claim_pack: dict[str, Any]) -> dict[str, Any]:
    """Compact provider view derived from the canonical host grounding pack."""
    numeric = [
        [item["source_fact_id"], item["canonical_value"], item.get("unit")]
        for item in claim_pack["allowed_numeric_values"]
    ]
    levels = [
        {"level_id": item["level_id"], "fact_id": item["fact_refs"][0], "role": item["asserted_role"],
         "state": item["asserted_state"], "strength": item["asserted_strength"],
         "timeframe": item["asserted_timeframe"], "dynamic": item["asserted_dynamic"],
         "valid_until": item["valid_until"]}
        for item in claim_pack["levels"]
    ]
    scenarios = [
        {"scenario_id": item["scenario_id"], "fact_id": item["fact_refs"][0],
         "type": item["scenario_type"], "direction": item["direction"], "likelihood": item["likelihood"],
         "trigger": {"text": item["trigger_text"], "level_refs": item["trigger_level_refs"]},
         "confirmation": item["confirmation_text"],
         "path": {"text": item["expected_path_text"], "level_refs": item["expected_path_level_refs"]},
         "target_level_refs": item["target_level_refs"],
         "invalidation": {"text": item["invalidation_text"], "level_ref": item["invalidation_level_ref"],
                          "timeframe": item["invalidation_timeframe"]},
         "order_flow_conditions": [value for value in (
             item["volume_confirmation_text"], item["cvd_confirmation_text"], item["oi_confirmation_text"],
             item["funding_basis_confirmation_text"], item["contradicting_evidence_text"]
         ) if value]}
        for item in claim_pack["scenarios"]
    ]
    return {
        "claim_pack_version": claim_pack["claim_pack_version"], "mode": claim_pack["mode"],
        "allowed_numeric_tuple_fields": ["fact_id", "exact_value", "unit"],
        "allowed_numeric_values": numeric, "level_claim_slots": levels, "scenario_claim_slots": scenarios,
        "macro_evidence_ids": claim_pack["macro_evidence_ids"],
        "macro_unavailable_statement": claim_pack["macro_unavailable_statement"],
        "evidence_status": claim_pack["evidence_status"],
    }


def _section_categories(section_id: str) -> set[str]:
    if section_id == "QUICK_SUMMARY": return {"TIMELINE", "TIMEFRAME", "ORDER_FLOW", "LEVEL", "SCENARIO", "WARNING", "MACRO", "POSITION"}
    if section_id == "CONCLUSION": return {"TIMELINE", "TIMEFRAME", "LEVEL", "SCENARIO", "WARNING"}
    if section_id == "RECENT_PROCESS": return {"TIMELINE", "TIMEFRAME", "ORDER_FLOW", "LEVEL", "SCENARIO", "WARNING"}
    if section_id == "MOVE_NATURE": return {"TIMELINE", "TIMEFRAME", "ORDER_FLOW", "LEVEL", "WARNING"}
    if section_id.startswith("TF_"): return {"TIMEFRAME", "WARNING"}
    if section_id == "ORDER_FLOW": return {"ORDER_FLOW", "WARNING"}
    if section_id == "KEY_LEVELS": return {"LEVEL"}
    if section_id == "SCENARIOS": return {"SCENARIO", "LEVEL", "ORDER_FLOW"}
    if section_id == "MACRO_BACKGROUND": return {"MACRO"}
    if section_id == "POSITION_PLAN": return {"POSITION", "SCENARIO", "LEVEL"}
    return {"WARNING", "ORDER_FLOW", "TIMEFRAME", "SCENARIO", "LEVEL"}


def _narrative_text(value: str) -> str:
    result = str(value)
    for token, word in _TIMEFRAME_WORDS.items():
        result = re.sub(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", word, result, flags=re.I)
    # Level identity and membership are projected by the host below.  Provider
    # prose must not introduce an independently audited numeric count for the
    # same deterministic collection (for example, "两个支撑").
    result = re.sub(r"(?:[一二两三四五六七八九十]+|\d+)个(?=(?:支撑|压力|阻力|关键位))", "", result)
    return result


def _macro_limitation_text(value: str, statement: str | None) -> str:
    """Canonicalize only explicit no-macro status wording, never market claims."""
    result = str(value)
    if not statement:
        return result
    canonical = statement.rstrip("。")
    for wording in ("宏观证据未加入", "未加入宏观证据", "无已验证宏观证据", "宏观证据未纳入"):
        result = result.replace(wording, canonical)
    return result


def ground_provider_report(report: dict[str, Any], claim_pack: dict[str, Any]) -> dict[str, Any]:
    """Attach deterministic evidence/projections while retaining provider narrative text."""
    by_category = claim_pack["fact_ids_by_category"]; level_ids = [item["level_id"] for item in claim_pack["levels"]]
    scenario_ids = [item["scenario_id"] for item in claim_pack["scenarios"]]; macro_ids = list(claim_pack["macro_evidence_ids"])
    position_ids = list(by_category.get("POSITION", [])); provider_levels = {item.get("level_id"): item for item in report.get("key_levels", [])}
    provider_scenarios = {item.get("scenario_id"): item for item in report.get("scenarios", [])}; grounded = dict(report)
    macro_statement = claim_pack.get("macro_unavailable_statement")
    grounded["headline"] = _macro_limitation_text(_narrative_text(report["headline"]), macro_statement); sections = []
    for original in report.get("sections", []):
        section = dict(original); categories = _section_categories(str(section.get("section_id")))
        section["body"] = _macro_limitation_text(_narrative_text(section["body"]), macro_statement)
        if (not scenario_ids and section.get("section_id") in {"QUICK_SUMMARY", "SCENARIOS"}
                and "失效" not in section["body"] and "限制" not in section["body"]):
            section["body"] = section["body"].rstrip("。") + "。证据不足，当前没有可审计的情景失效路径。"
        section["fact_refs"] = [fact_id for category in sorted(categories) for fact_id in by_category.get(category, [])]
        section["level_refs"] = level_ids if "LEVEL" in categories else []
        section["scenario_refs"] = scenario_ids if "SCENARIO" in categories else []
        section["macro_refs"] = macro_ids if "MACRO" in categories else []
        section["position_refs"] = position_ids if "POSITION" in categories else []
        sections.append(section)
    grounded["sections"] = sections
    grounded["key_levels"] = [
        {**item, "analysis_text": _narrative_text(str(provider_levels.get(item["level_id"], {}).get("analysis_text") or item["analysis_text"]))}
        for item in claim_pack["levels"]
    ]
    grounded["scenarios"] = [
        {**item, "summary": _narrative_text(str(provider_scenarios.get(item["scenario_id"], {}).get("summary") or item["summary"]))}
        for item in claim_pack["scenarios"]
    ]
    grounded["citations"] = [{"evidence_id": evidence_id} for evidence_id in macro_ids]
    return grounded
