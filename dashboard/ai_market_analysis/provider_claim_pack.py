"""Deterministic evidence contract for real-provider narrative generation."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from .report_numeric_semantics import find_numeric_field, numeric_semantics
from .report_narrative_contract import (
    SECTION_CLAIM_PLAN, claim_role, narrative_claim_type, provider_section_claim_plan, section_fact_ids,
    sentence_mentions_foreign_scope,
)

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
    price_oi_facts = [item for item in all_flow if isinstance(item.get("value"), dict)
                      and item["value"].get("evidence_kind") == "PRICE_OI"
                      and item["value"].get("state") not in {None, "INSUFFICIENT_DATA"}
                      and str(item["value"].get("quality") or "").upper() not in {"MISSING", "UNAVAILABLE", "UNKNOWN"}]
    flow_states = {
        ("FLOW_UNAVAILABLE" if str(item.get("quality") or "").upper() in {"MISSING", "UNAVAILABLE", "UNKNOWN"}
         else str((item.get("value") or {}).get("flow_quality") or ""))
        for item in active_flow if isinstance(item.get("value"), dict)
    }
    partial_flow_present = "FLOW_PARTIAL_USABLE" in flow_states or any(
        str(item.get("quality") or "").upper() in {"PARTIAL", "GAP_AFFECTED", "PARTIAL_AFTER_GAP", "MISSING", "UNKNOWN"}
        or (isinstance(item.get("value"), dict)
            and str(item["value"].get("quality") or "").upper() in {"PARTIAL", "GAP_AFFECTED", "PARTIAL_AFTER_GAP", "MISSING", "UNKNOWN"})
        for item in active_flow
    )
    partial_observations = [item for item in active_flow if isinstance(item.get("value"), dict)
                            and item["value"].get("flow_quality") == "FLOW_PARTIAL_USABLE"]
    auxiliary_flow = [item for item in all_flow if isinstance(item.get("value"), dict) and "phase" not in item["value"]]
    by_category["ORDER_FLOW"] = usable_flow + [item for item in partial_observations if item not in usable_flow]
    by_category["ORDER_FLOW"] += [item for item in price_oi_facts if item not in by_category["ORDER_FLOW"]]
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
        "claim_pack_version": "ai6b-provider-claim-pack-v6", "mode": mode, "allowed_numeric_values": numeric,
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
        "facts_by_id": {str(item["fact_id"]): item.get("value") for item in facts
                        if str(item["fact_id"]).endswith(("_SUMMARY","_STRUCTURE"))
                        or item["fact_id"] in {"TIMEFRAME_ALIGNMENT","TIMEFRAME_EXTENSION"}},
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
        "section_claim_plan": provider_section_claim_plan(tuple(SECTION_CLAIM_PLAN)),
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
# This is also the fallback when an unsupported level claim replaces the
# no-scenario disclosure below.  Keep an explicit limitation/invalidation
# disclosure here: QUICK reports without deterministic scenarios must still
# satisfy the fail-closed invalidation contract after grounding.
_UNSUPPORTED_LEVEL_LIMITATION = "本轮限制展示未经注册表支持的数值位置"
_LEVEL_CLAIM_TERMS = ("支撑", "压力", "阻力", "关键位", "关键位置")


_NEGATIVE_LEVEL_RE = re.compile(
    r"(?:\bno\s+(?:registered|reliable|key)\s+(?:key\s+)?levels?\b|"
    r"\b(?:no|without)\s+(?:reliable|registered)\s+(?:support|resistance|levels?)\b|"
    r"(?:\u65e0|\u6ca1\u6709).{0,12}(?:\u6ce8\u518c|\u53ef\u9760|\u5173\u952e).{0,12}(?:\u4ef7\u4f4d|\u4f4d\u7f6e|\u6c34\u5e73))",
    re.I,
)
_MACRO_NARRATIVE_RE = re.compile(
    r"(?:\bmacro\b|\bFed\b|\bETF\b|\bCPI\b|\bneutral\b|"
    r"\u5b8f\u89c2|\u7f8e\u8054\u50a8|\u964d\u606f|\u98ce\u9669\u504f\u597d)",
    re.I,
)
_MACRO_COVERAGE_RE = re.compile(
    r"(?:\u672c\u6b21\u672a\u52a0\u5165\u5df2\u9a8c\u8bc1\u5b8f\u89c2\u8bc1\u636e|"
    r"\u5b8f\u89c2\u8bc1\u636e\u672a\u52a0\u5165|\bnot\s+included\s+in\s+(?:this\s+)?analysis\b)",
    re.I,
)


def _registered_levels_sentence(claim_pack: dict[str, Any]) -> str:
    """A host-authored bounded replacement for a contradictory no-level claim."""
    return "\u5f53\u524d\u5173\u952e\u4f4d\u7f6e\u5df2\u7531\u5df2\u6ce8\u518c\u7684\u786e\u5b9a\u6027\u4ef7\u683c\u5730\u56fe\u63d0\u4f9b\uff0c\u652f\u6491\u4e0e\u538b\u529b\u89c1\u7ed3\u6784\u5316\u5173\u952e\u4f4d\u3002"


def _apply_narrative_boundaries(value: str, claim_pack: dict[str, Any]) -> str:
    """Remove provider prose outside the deterministic evidence namespace.

    This is pre-audit grounding, not an audit exception: a provider cannot
    contradict available levels or interpret macro without macro evidence.
    """
    from .report_claim_extractor import split_sentences
    levels_available = bool(claim_pack["evidence_status"].get("levels_available"))
    macro_available = bool(claim_pack["evidence_status"].get("macro_available"))
    canonical_macro = str(claim_pack.get("macro_unavailable_statement") or "").rstrip("\u3002.")
    retained: list[str] = []
    for sentence in split_sentences(str(value or "")):
        if levels_available and _NEGATIVE_LEVEL_RE.search(sentence):
            retained.append(_registered_levels_sentence(claim_pack))
            continue
        # A claim-pack exact coverage disclosure is deterministic metadata;
        # every other macro sentence is an unsupported market interpretation.
        if (not macro_available and _MACRO_NARRATIVE_RE.search(sentence)
                and canonical_macro not in sentence and not _MACRO_COVERAGE_RE.search(sentence)):
            continue
        retained.append(sentence)
    return "\u3002".join(dict.fromkeys(retained)) + ("\u3002" if retained else "")


def bind_level_fact_refs(text: str, claim_pack: dict[str, Any]) -> tuple[list[str], bool]:
    """Return exact LEVEL facts for numeric level prose, or fail closed.

    A provider may only retain a numeric support/resistance statement when every
    number in that statement resolves to a projected deterministic level.  The
    returned boolean means the statement must be suppressed, never papered over
    with unrelated timeframe or warning facts.
    """
    value = str(text or "")
    if not any(term in value for term in _LEVEL_CLAIM_TERMS):
        return [], False
    slots = list(claim_pack.get("levels") or [])
    numbers = [float(item) for item in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", value)]
    if not numbers:
        return [str(item["fact_refs"][0]) for item in slots if item.get("fact_refs")], False
    matched: list[str] = []
    for number in numbers:
        candidates = []
        for slot in slots:
            for key in ("representative_price", "zone_low", "zone_high"):
                candidate = slot.get(key)
                if isinstance(candidate, (int, float)) and abs(float(candidate) - number) <= 0.0001:
                    candidates.append(str(slot["fact_refs"][0]))
                    break
        if not candidates:
            return [], True
        matched.extend(candidates)
    return list(dict.fromkeys(matched)), False


def _enforce_numeric_namespaces(value: str, claim_pack: dict[str, Any], section_id: str | None = None) -> str:
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
        unavailable_flow=(section_id in {None,"QUICK_SUMMARY","MOVE_NATURE","ORDER_FLOW"}
                          and claim_pack["evidence_status"].get("flow_coverage_state") == "FLOW_UNAVAILABLE"
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


def _host_section_fallback(section: dict[str, Any], claim_pack: dict[str, Any]) -> str:
    """Natural, evidence-derived fallback when section ownership removes all prose."""
    section_id=str(section.get("section_id"));facts=claim_pack.get("facts_by_id",{})
    if section_id=="CONCLUSION":
        alignment=facts.get("TIMEFRAME_ALIGNMENT") or {};state=str(alignment.get("alignment") or "") if isinstance(alignment,dict) else ""
        if state=="CONFLICTED":
            return "短周期结构与高周期背景存在冲突；战术回撤仍需确认，而高周期伸展使趋势延续与位置风险并存。"
        return "多周期结构保持一致，当前结论由各周期的独立结构证据共同支持。"
    if section_id=="ORDER_FLOW":return f"{_FLOW_LIMITATION}。"
    if section_id=="SCENARIOS" and claim_pack.get("scenarios"):
        return "；".join(str(item.get("summary") or item.get("scenario_type")) for item in claim_pack["scenarios"]) + "。"
    prefix={"TF_15M":"TF15_","TF_1H":"TF1H_","TF_4H":"TF4H_","TF_1D":"TF1D_","TF_1W":"TF1W_"}.get(section_id)
    label={"TF_15M":"超短周期","TF_1H":"小时周期","TF_4H":"中周期","TF_1D":"日线","TF_1W":"周线"}.get(section_id,section.get("title") or section_id)
    if prefix:
        summary=facts.get(prefix+"SUMMARY") or {};structure=facts.get(prefix+"STRUCTURE") or {}
        swing={"LH_LL":"更低高点与更低低点","HH_HL":"更高高点与更高低点","RANGE":"区间震荡"}.get(str(summary.get("swing_structure")),"结构方向尚未明确") if isinstance(summary,dict) else "结构方向尚未明确"
        momentum={"MOMENTUM_RESET":"动量处于重置","MOMENTUM_COOLING":"动量正在降温","MOMENTUM_REACCELERATING":"动量重新增强","MOMENTUM_EXPANDING":"动量仍在扩张","MOMENTUM_STABLE":"动量保持稳定"}.get(str(structure.get("momentum_state")),"动量信号有限") if isinstance(structure,dict) else "动量信号有限"
        extension={"EXTENDED":"位置已经伸展","HIGHLY_EXTENDED":"位置处于高伸展","NORMAL":"位置未见异常伸展"}.get(str(structure.get("extension_state")),"位置伸展状态待确认") if isinstance(structure,dict) else "位置伸展状态待确认"
        return f"{label}呈现{swing}，{momentum}，{extension}；该证据用于解释本周期自身状态。"
    if section_id=="LIMITATIONS":return "本节仅披露本轮证据覆盖与审计边界。"
    return f"{section.get('title') or section_id}仅保留本节允许的已注册证据。"


def _dedupe_section_bodies(sections: list[dict[str, Any]], claim_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Deduplicate by section role/scope instead of global string identity."""
    from .report_claim_extractor import split_sentences

    occurrences: dict[str,list[str]]={}
    for section in sections:
        for sentence in split_sentences(str(section.get("body") or "")):
            key=re.sub(r"[\W_]+","",re.sub(r"\d+(?:\.\d+)?","#",sentence),flags=re.UNICODE)
            occurrences.setdefault(key,[]).append(str(section.get("section_id")))
    seen: dict[str, tuple[str,str,set[str]]] = {}; output: list[dict[str, Any]] = []
    for original in sections:
        section = dict(original); retained: list[str] = [];section_id=str(section.get("section_id"))
        plan=SECTION_CLAIM_PLAN.get(section_id,{"scope":"GLOBAL","role":"DETAIL"});evidence=set(section.get("fact_refs",[]))
        for sentence in split_sentences(str(section.get("body") or "")):
            key = re.sub(r"[\W_]+", "", re.sub(r"\d+(?:\.\d+)?", "#", sentence), flags=re.UNICODE)
            sentence_role=claim_role(section_id,sentence)
            owners=occurrences.get(key,[])
            canonical_owner=("ORDER_FLOW" if "ORDER_FLOW" in owners and "订单流" in sentence
                             else "LIMITATIONS" if "LIMITATIONS" in owners else owners[0])
            if sentence_role=="LIMITATION" and section_id!=canonical_owner:continue
            prior=seen.get(key)
            if prior:
                prior_scope,prior_role,prior_evidence=prior
                same_identity=prior_scope==plan["scope"] and prior_role==plan["role"] and prior_evidence==evidence
                redundant_global=(prior_scope==plan["scope"]=="GLOBAL" and {prior_role,plan["role"]}<={"SUMMARY","SYNTHESIS"})
                repeated_limitation=plan["role"]=="LIMITATION" or prior_role=="LIMITATION"
                no_new_detail=("DETAIL" in {prior_role,plan["role"]} and prior_evidence==evidence)
                if same_identity or redundant_global or repeated_limitation or no_new_detail:continue
            seen[key]=(str(plan["scope"]),sentence_role,evidence)
            retained.append(sentence)
        if retained:
            section["body"] = "\u3002".join(retained) + "\u3002"
        else:
            section["body"] = _host_section_fallback(section,claim_pack)
        output.append(section)
    return output


def ground_provider_report(report: dict[str, Any], claim_pack: dict[str, Any]) -> dict[str, Any]:
    """Attach deterministic evidence/projections while retaining provider narrative text."""
    from .report_claim_extractor import split_sentences
    by_category = claim_pack["fact_ids_by_category"]; level_ids = [item["level_id"] for item in claim_pack["levels"]]
    scenario_ids = [item["scenario_id"] for item in claim_pack["scenarios"]]; macro_ids = list(claim_pack["macro_evidence_ids"])
    position_ids = list(by_category.get("POSITION", [])); provider_levels = {item.get("level_id"): item for item in report.get("key_levels", [])}
    provider_scenarios = {item.get("scenario_id"): item for item in report.get("scenarios", [])}; grounded = dict(report)
    macro_statement = claim_pack.get("macro_unavailable_statement")
    section_ids = {str(item.get("section_id")) for item in report.get("sections", [])}
    empty_scenario_limitation_section = "QUICK_SUMMARY" if "QUICK_SUMMARY" in section_ids else "LIMITATIONS"
    grounded["headline"] = _apply_narrative_boundaries(_enforce_numeric_namespaces(
        _macro_limitation_text(_narrative_text(report["headline"]), macro_statement), claim_pack
    ), claim_pack); sections = []
    for original in report.get("sections", []):
        section = dict(original); categories = _section_categories(str(section.get("section_id")))
        section["title"] = _narrative_text(str(section.get("title") or section.get("section_id") or ""))
        section["body"] = _apply_narrative_boundaries(_enforce_numeric_namespaces(
            _macro_limitation_text(_narrative_text(section["body"]), macro_statement), claim_pack,
            str(section.get("section_id")),
        ), claim_pack)
        if section.get("section_id") in SECTION_CLAIM_PLAN:
            section_plan=SECTION_CLAIM_PLAN[str(section.get("section_id"))]
            owned=[]; unsupported_unowned_level=False
            for sentence in split_sentences(section["body"]):
                sentence_type=narrative_claim_type(str(section.get("section_id")),sentence)
                accepted=(not sentence_mentions_foreign_scope(str(section.get("section_id")),sentence)
                          and sentence_type in section_plan["claim_types"])
                if accepted:
                    owned.append(sentence)
                elif sentence_type=="LEVEL" and bind_level_fact_refs(sentence,claim_pack)[1]:
                    unsupported_unowned_level=True
            if unsupported_unowned_level:
                owned.append(_UNSUPPORTED_LEVEL_LIMITATION)
            section["body"]="。".join(owned)+( "。" if owned else "")
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
                and "失效" not in section["body"]):
            section["body"] = section["body"].rstrip("。") + "。证据不足，当前没有可审计的情景失效路径。"
        level_fact_ids, suppress_unsupported_level = (bind_level_fact_refs(section["body"], claim_pack)
                                                       if "LEVEL" in categories else ([],False))
        if suppress_unsupported_level:
            section["body"] = _UNSUPPORTED_LEVEL_LIMITATION + "。"
            level_fact_ids = []
        category_fact_ids = section_fact_ids(str(section.get("section_id")),by_category)
        if "LEVEL" in categories and level_fact_ids:
            category_fact_ids.extend(level_fact_ids)
        section["fact_refs"] = list(dict.fromkeys(category_fact_ids))
        fact_to_level = {str(item["fact_refs"][0]): str(item["level_id"]) for item in claim_pack["levels"] if item.get("fact_refs")}
        section["level_refs"] = [fact_to_level[fact_id] for fact_id in level_fact_ids if fact_id in fact_to_level]
        section["scenario_refs"] = scenario_ids if "SCENARIO" in categories else []
        section["macro_refs"] = macro_ids if "MACRO" in categories else []
        section["position_refs"] = position_ids if "POSITION" in categories else []
        sections.append(section)
    grounded["sections"] = _dedupe_section_bodies(sections,claim_pack)
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
