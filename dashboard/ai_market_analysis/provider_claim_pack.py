"""Deterministic evidence contract for real-provider narrative generation."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from .report_numeric_semantics import find_numeric_field, numeric_semantics

# Narrative labels intentionally contain no numeric glyphs or number words.
# The numeric auditor treats every number as a market claim, while exact
# timeframe identity remains available in deterministic facts/projections.
_TIMEFRAME_WORDS = {
    "15m": "超短周期", "15分钟": "超短周期", "15 分钟": "超短周期",
    "十五分钟": "超短周期", "十五 分钟": "超短周期",
    "1H": "小时周期", "1小时": "小时周期", "1 小时": "小时周期", "一小时": "小时周期",
    "4H": "中周期", "4小时": "中周期", "4 小时": "中周期", "四小时": "中周期",
    "1D": "日线", "1W": "周线",
}


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
        "representative_price": value.get("representative_price"), "zone_low": value.get("zone_low"),
        "zone_high": value.get("zone_high"), "observed_at": value.get("observed_at"),
        "source_fact": value.get("source_fact"),
        "asserted_role": value.get("role"), "asserted_state": value.get("state"), "asserted_strength": value.get("strength"),
        "asserted_timeframe": value.get("primary_timeframe"), "asserted_dynamic": value.get("dynamic"),
        "valid_until": value.get("valid_until"), "fact_refs": [fact["fact_id"]], "level_refs": [value["level_id"]],
    }


def build_provider_claim_pack(compiled_context: dict[str, Any], mode: str) -> dict[str, Any]:
    facts = list(compiled_context.get("facts", [])); by_category: dict[str, list[dict[str, Any]]] = {}
    for fact in facts: by_category.setdefault(str(fact.get("category")), []).append(fact)
    all_flow = list(by_category.get("ORDER_FLOW", []))
    current_flow = [item for item in all_flow if isinstance(item.get("value"), dict) and item["value"].get("phase") == "CURRENT"]
    active_flow = current_flow or all_flow[-1:]

    def confirmed_flow_direction(item: dict[str, Any]) -> bool:
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        qualities = {str(item.get("quality") or "").upper(), str(value.get("quality") or "").upper(), str(value.get("flow_quality") or "").upper()}
        if qualities & {"MISSING", "FLOW_UNAVAILABLE", "UNAVAILABLE", "UNKNOWN"}:
            return False
        cvd_status = str(value.get("cvd_status") or "").upper()
        flow_status = str(value.get("net_flow_status") or value.get("flow_status") or "").upper()
        if cvd_status in {"GAP_AFFECTED", "MISSING", "UNAVAILABLE", "UNKNOWN"}:
            return False
        if flow_status in {"GAP_AFFECTED", "MISSING", "UNAVAILABLE", "UNKNOWN"}:
            return False
        return value.get("cvd_delta") is not None or value.get("net_flow") is not None

    usable_flow = [
        item for item in all_flow if confirmed_flow_direction(item)
    ]
    flow_states = {str((item.get("value") or {}).get("flow_quality") or "") for item in active_flow if isinstance(item.get("value"), dict)}
    partial_flow_present = "FLOW_PARTIAL_USABLE" in flow_states or any(
        str(item.get("quality") or "").upper() in {"PARTIAL", "GAP_AFFECTED", "PARTIAL_AFTER_GAP", "MISSING", "UNKNOWN"}
        or (isinstance(item.get("value"), dict)
            and str(item["value"].get("quality") or "").upper() in {"PARTIAL", "GAP_AFFECTED", "PARTIAL_AFTER_GAP", "MISSING", "UNKNOWN"})
        for item in active_flow
    )
    partial_observations = [item for item in active_flow if isinstance(item.get("value"), dict)
                            and item["value"].get("flow_quality") == "FLOW_PARTIAL_USABLE"
                            and (item["value"].get("cvd_delta") is not None or item["value"].get("net_flow") is not None)]
    auxiliary_flow = [item for item in all_flow if isinstance(item.get("value"), dict) and "phase" not in item["value"]]
    by_category["ORDER_FLOW"] = usable_flow + [item for item in partial_observations if item not in usable_flow]
    if mode != "QUICK" and "FLOW_UNAVAILABLE" not in flow_states:
        by_category["ORDER_FLOW"] += [item for item in auxiliary_flow if item not in by_category["ORDER_FLOW"]]
    levels = [_level_projection(item) for item in by_category.get("LEVEL", [])
              if isinstance(item.get("value"), dict) and all(key in item["value"] for key in ("level_id", "role", "state", "strength"))]
    scenarios = [_scenario_projection(item) for item in by_category.get("SCENARIO", [])
                 if isinstance(item.get("value"), dict) and all(key in item["value"] for key in ("scenario_id", "type", "direction", "likelihood"))]
    macro_ids = [str(item["value"]["evidence_id"]) for item in by_category.get("MACRO", []) if isinstance(item.get("value"), dict) and item["value"].get("evidence_id")]
    unavailable_macro = next((str(item.get("value")) for item in facts if item.get("fact_id") == "MACRO_UNAVAILABLE"), "本次未加入已验证宏观证据。")
    facts_by_id = {str(item["fact_id"]): item for item in facts}
    eligible_fact_ids = {str(item["fact_id"]) for items in by_category.values() for item in items}
    numeric = []
    suppressed_flow_numeric_values = []
    for original in compiled_context.get("numeric_registry", []):
        source_fact_id = str(original["source_fact_id"])
        if source_fact_id not in eligible_fact_ids:
            if any(str(item.get("fact_id")) == source_fact_id for item in all_flow):
                suppressed_flow_numeric_values.append(str(original.get("exact_display", original["canonical_value"])))
            continue
        item = {"source_fact_id": source_fact_id, "canonical_value": original["canonical_value"],
                "exact_display": original.get("exact_display", str(original["canonical_value"])),
                "unit": original.get("unit")}
        semantic = {key: original.get(key) for key in ("semantic_field", "semantic_role", "semantic_namespace")}
        if not all(semantic.values()):
            fact = facts_by_id.get(source_fact_id, {})
            field = find_numeric_field(fact.get("value"), float(original["canonical_value"]))
            semantic = numeric_semantics(source_fact_id, field, original.get("unit"))
        numeric.append({**item, **semantic})
    return {
        "claim_pack_version": "ai6b-provider-claim-pack-v5", "mode": mode, "allowed_numeric_values": numeric,
        "suppressed_flow_numeric_values": sorted(set(suppressed_flow_numeric_values)),
        "levels": levels, "scenarios": scenarios, "macro_evidence_ids": macro_ids,
        "macro_unavailable_statement": None if macro_ids else unavailable_macro,
        "evidence_status": {"flow_available": bool(usable_flow) and not partial_flow_present,
                            "flow_partial": partial_flow_present,
                            "flow_coverage_state": ("FLOW_UNAVAILABLE" if "FLOW_UNAVAILABLE" in flow_states
                                                    else "FLOW_PARTIAL_USABLE" if partial_flow_present
                                                    else "FLOW_COMPLETE" if usable_flow else "FLOW_UNAVAILABLE"),
                            "macro_available": bool(macro_ids),
                            "levels_available": bool(levels), "scenarios_available": bool(scenarios)},
        "fact_ids_by_category": {category: [item["fact_id"] for item in items] for category, items in sorted(by_category.items())},
    }


def provider_claim_pack_contract(claim_pack: dict[str, Any]) -> dict[str, Any]:
    """Compact provider view derived from the canonical host grounding pack."""
    numeric = [
        [item["source_fact_id"], item["canonical_value"], item.get("unit"),
         item["semantic_namespace"], item["semantic_role"]]
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
        "allowed_numeric_tuple_fields": ["fact_id", "exact_value", "unit", "semantic_namespace", "semantic_role"],
        "allowed_numeric_values": numeric, "level_claim_slots": levels, "scenario_claim_slots": scenarios,
        "macro_evidence_ids": claim_pack["macro_evidence_ids"],
        "macro_unavailable_statement": claim_pack["macro_unavailable_statement"],
        "evidence_status": claim_pack["evidence_status"],
    }


def _section_categories(section_id: str) -> set[str]:
    if section_id == "QUICK_SUMMARY": return {"TIMELINE", "TIMEFRAME", "ORDER_FLOW", "LEVEL", "SCENARIO", "WARNING", "MACRO", "POSITION"}
    if section_id == "CONCLUSION": return {"TIMELINE", "TIMEFRAME", "LEVEL", "SCENARIO", "WARNING"}
    if section_id == "RECENT_PROCESS": return {"TIMELINE", "LEVEL", "ORDER_FLOW"}
    if section_id == "MOVE_NATURE": return {"TIMELINE", "TIMEFRAME", "ORDER_FLOW", "LEVEL"}
    if section_id.startswith("TF_"): return {"TIMEFRAME", "WARNING"}
    if section_id == "ORDER_FLOW": return {"ORDER_FLOW"}
    if section_id == "KEY_LEVELS": return {"LEVEL"}
    if section_id == "SCENARIOS": return {"SCENARIO", "LEVEL", "ORDER_FLOW"}
    if section_id == "MACRO_BACKGROUND": return {"MACRO"}
    if section_id == "POSITION_PLAN": return {"POSITION", "SCENARIO", "LEVEL"}
    return {"WARNING", "ORDER_FLOW", "TIMEFRAME", "SCENARIO", "LEVEL"}


def _narrative_text(value: str) -> str:
    result = str(value)
    result = re.sub(
        r"(?<!\d)(1[5-9]\d{8}|2\d{9})(?!\d)",
        lambda match: datetime.fromtimestamp(int(match.group(1)), timezone.utc).date().isoformat(),
        result,
    )
    for token, word in _TIMEFRAME_WORDS.items():
        result = re.sub(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", word, result, flags=re.I)
    # Keep mixed-sign deterministic flow observations in separate audit
    # claims. A comma chain would give every number the direction vocabulary
    # of every sibling observation.
    result = re.sub(r"，(?=FLOW_[A-Z0-9_]+\s+显示)", "。", result)
    result = re.sub(
        r"，(?:但|且|并且|同时|而)?(?=(?:成交量|CVD|OI|Funding|资金费率|Basis|基差|Liquidation|强平|爆仓))",
        "。",
        result,
        flags=re.I,
    )
    # Keep a volume-regime direction word from being applied to sibling
    # price-change numbers by the deterministic numeric-direction audit.
    result = re.sub(
        r"\uff0c(?=(?:\u4ef7\u683c\u53d8\u52a8(?:\u767e\u5206\u6bd4)?|\u4f46\s*(?:CVD|OI)))",
        "\u3002",
        result,
        flags=re.I,
    )
    # Level identity and membership are projected by the host below.  Provider
    # prose must not introduce an independently audited numeric count for the
    # same deterministic collection (for example, "两个支撑").
    result = re.sub(r"(?:[一二两三四五六七八九十]+|\d+)个(?=(?:支撑|压力|阻力|关键位))", "", result)
    return result


def _scenario_narrative_text(value: str) -> str:
    """Remove presentation-only counts from deterministic scenario prose."""
    result = str(value)
    result = re.sub(
        r"(?:存在|包括|共有)?\s*(?:[一二两三四五六七八九十]+|\d+)个?(情景|场景|路径)",
        r"存在\1",
        result,
    )
    return re.sub(r"(?<![\d.])\d+\s*[).、]\s*", "", result)


def _macro_limitation_text(value: str, statement: str | None) -> str:
    """Canonicalize only explicit no-macro status wording, never market claims."""
    result = str(value)
    if not statement:
        return result
    canonical = statement.rstrip("。")
    for wording in ("宏观证据未加入", "未加入宏观证据", "无已验证宏观证据", "宏观证据未纳入",
                    "宏观证据缺失", "缺少宏观证据"):
        result = result.replace(wording, canonical)
    return result


_FLOW_ASSERTION_TERMS = (
    "FLOW_PHASE_", "订单流数据", "订单流证据", "订单流显示", "订单流转变",
    "资金净流入", "资金净流出", "净流入", "净流出", "CVD", "OI",
    "成交量", "量能", "资金费率", "Funding", "基差", "Basis", "强平", "爆仓", "Liquidation",
)
_FLOW_LIMITATION = "订单流不可用，本轮不作为方向确认依据"
_FLOW_PARTIAL_LIMITATION = "订单流覆盖部分，确认度受限"


def _enforce_numeric_namespaces(value: str, claim_pack: dict[str, Any]) -> str:
    """Render provider numeric wording inside its deterministic semantic namespace."""
    result = str(value)
    from .report_claim_extractor import split_sentences
    allowed_flow_ids=set(claim_pack["fact_ids_by_category"].get("ORDER_FLOW",[]))
    cvd_values=[str(item.get("exact_display",item["canonical_value"])) for item in claim_pack["allowed_numeric_values"]
                if item.get("semantic_namespace")=="FLOW_CVD"]
    replaced=[]
    for sentence in split_sentences(result):
        phase_ids=set(re.findall(r"FLOW_PHASE_\d+",sentence))
        net_flow=any(term in sentence for term in ("资金净流入","资金净流出","净流入","净流出"))
        order_flow_wording=any(term in sentence for term in ("订单流", "资金净流"))
        price_change_wording=any(term in sentence for term in ("价格变化", "价格变动", "净正价格", "净负价格"))
        unsupported_phase=bool(phase_ids-allowed_flow_ids)
        unsupported_flow_claim=net_flow and not any(exact in sentence for exact in cvd_values)
        unavailable_flow=(claim_pack["evidence_status"].get("flow_coverage_state") == "FLOW_UNAVAILABLE"
                          and any(term in sentence for term in _FLOW_ASSERTION_TERMS))
        suppressed_flow_number=any(
            re.search(rf"(?<![\d.]){re.escape(exact)}(?![\d.])",sentence)
            for exact in claim_pack.get("suppressed_flow_numeric_values",[])
        )
        namespace_mismatch=order_flow_wording and price_change_wording
        replaced.append(_FLOW_LIMITATION if unsupported_phase or unsupported_flow_claim or unavailable_flow or suppressed_flow_number or namespace_mismatch else sentence)
    result = "。".join(dict.fromkeys(replaced)) + ("。" if replaced else "")
    for item in claim_pack["allowed_numeric_values"]:
        if item.get("semantic_namespace") != "PRICE_CHANGE":
            continue
        exact = str(item.get("exact_display", item["canonical_value"]))
        pattern = rf"(?:资金)?净(?:流入|流出)\s*{re.escape(exact)}"
        result = re.sub(pattern, f"价格变化比例 {exact}", result)
    return result


def _dedupe_section_bodies(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove repeated prose while deterministic projections retain all facts."""
    from .report_claim_extractor import split_sentences

    seen: set[str] = set(); output: list[dict[str, Any]] = []
    for original in sections:
        section = dict(original); retained: list[str] = []
        for sentence in split_sentences(str(section.get("body") or "")):
            key = re.sub(r"[\W_]+", "", re.sub(r"\d+(?:\.\d+)?", "#", sentence), flags=re.UNICODE)
            canonical_flow_limitation = sentence == _FLOW_LIMITATION
            if not canonical_flow_limitation and key in seen:
                continue
            if not canonical_flow_limitation:
                seen.add(key)
            retained.append(sentence)
        if retained:
            section["body"] = "\u3002".join(retained) + "\u3002"
        else:
            section["body"] = (
                f"{section.get('title') or section.get('section_id')}"
                "\u4e0d\u91cd\u590d\u524d\u8ff0\u5185\u5bb9\uff0c\u786e\u5b9a\u6027\u8bc1\u636e\u89c1\u7ed3\u6784\u5316\u6295\u5f71\u3002"
            )
        output.append(section)
    return output


def ground_provider_report(report: dict[str, Any], claim_pack: dict[str, Any]) -> dict[str, Any]:
    """Attach deterministic evidence/projections while retaining provider narrative text."""
    by_category = claim_pack["fact_ids_by_category"]; level_ids = [item["level_id"] for item in claim_pack["levels"]]
    scenario_ids = [item["scenario_id"] for item in claim_pack["scenarios"]]; macro_ids = list(claim_pack["macro_evidence_ids"])
    position_ids = list(by_category.get("POSITION", [])); provider_levels = {item.get("level_id"): item for item in report.get("key_levels", [])}
    provider_scenarios = {item.get("scenario_id"): item for item in report.get("scenarios", [])}; grounded = dict(report)
    macro_statement = claim_pack.get("macro_unavailable_statement")
    section_ids = {str(item.get("section_id")) for item in report.get("sections", [])}
    empty_scenario_limitation_section = "QUICK_SUMMARY" if "QUICK_SUMMARY" in section_ids else "LIMITATIONS"
    grounded["headline"] = _enforce_numeric_namespaces(
        _macro_limitation_text(_narrative_text(report["headline"]), macro_statement), claim_pack
    ); sections = []
    for original in report.get("sections", []):
        section = dict(original); categories = _section_categories(str(section.get("section_id")))
        section["title"] = _narrative_text(str(section.get("title") or section.get("section_id") or ""))
        section["body"] = _enforce_numeric_namespaces(
            _macro_limitation_text(_narrative_text(section["body"]), macro_statement), claim_pack
        )
        if claim_pack["evidence_status"].get("flow_partial") and section.get("section_id") in {"QUICK_SUMMARY", "MOVE_NATURE", "ORDER_FLOW"}:
            section["body"] = section["body"].replace(
                "\u8ba2\u5355\u6d41\u6570\u636e\u663e\u793a", "\u90e8\u5206\u53ef\u7528\u7684\u8ba2\u5355\u6d41\u8bc1\u636e\u663e\u793a"
            ).replace(
                "\u8ba2\u5355\u6d41\u8f6c\u53d8\u663e\u793a", "\u90e8\u5206\u53ef\u7528\u7684\u8ba2\u5355\u6d41\u8f6c\u53d8\u8bc1\u636e\u663e\u793a"
            )
            if _FLOW_PARTIAL_LIMITATION not in section["body"]:
                section["body"] = section["body"].rstrip("。") + f"。{_FLOW_PARTIAL_LIMITATION}。"
        if section.get("section_id") == "SCENARIOS":
            section["body"] = _scenario_narrative_text(section["body"])
        if (claim_pack["evidence_status"].get("flow_coverage_state") == "FLOW_UNAVAILABLE"
                and section.get("section_id") in {"MOVE_NATURE", "ORDER_FLOW"}):
            section["body"] = f"{_FLOW_LIMITATION}。"
        if (not scenario_ids and section.get("section_id") == empty_scenario_limitation_section
                and "失效" not in section["body"] and "限制" not in section["body"]):
            section["body"] = section["body"].rstrip("。") + "。证据不足，当前没有可审计的情景失效路径。"
        section["fact_refs"] = [fact_id for category in sorted(categories) for fact_id in by_category.get(category, [])]
        section["level_refs"] = level_ids if "LEVEL" in categories else []
        section["scenario_refs"] = scenario_ids if "SCENARIO" in categories else []
        section["macro_refs"] = macro_ids if "MACRO" in categories else []
        section["position_refs"] = position_ids if "POSITION" in categories else []
        sections.append(section)
    grounded["sections"] = _dedupe_section_bodies(sections)
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
