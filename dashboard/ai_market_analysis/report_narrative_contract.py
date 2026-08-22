"""Host-owned typed narrative and section assignment contract.

The provider owns wording.  The host owns which evidence and semantic role a
section may carry, and the audit consumes the same typed identity instead of
inferring duplicate meaning from text similarity alone.
"""
from __future__ import annotations

import re
from typing import Any

NARRATIVE_CONTRACT_VERSION = "ai-report-narrative-contract-v2"

TIMEFRAME_SCOPE = {
    "TF_15M": "15M", "TF_1H": "1H", "TF_4H": "4H",
    "TF_1D": "1D", "TF_1W": "1W",
}
TIMEFRAME_PREFIX = {
    "TF_15M": "TF15_", "TF_1H": "TF1H_", "TF_4H": "TF4H_",
    "TF_1D": "TF1D_", "TF_1W": "TF1W_",
}

SECTION_CLAIM_PLAN: dict[str, dict[str, Any]] = {
    "QUICK_SUMMARY": {"scope": "GLOBAL", "role": "SUMMARY",
        "claim_types": ("MARKET_STATE", "CROSS_TIMEFRAME_SYNTHESIS", "LIMITATION", "COVERAGE_METADATA")},
    "CONCLUSION": {"scope": "GLOBAL", "role": "SYNTHESIS",
        "claim_types": ("MARKET_STATE", "CROSS_TIMEFRAME_SYNTHESIS")},
    "RECENT_PROCESS": {"scope": "GLOBAL", "role": "DETAIL",
        "claim_types": ("MARKET_STATE", "IMPULSE", "PULLBACK", "COMPRESSION")},
    "MOVE_NATURE": {"scope": "GLOBAL", "role": "SYNTHESIS",
        "claim_types": ("IMPULSE", "PULLBACK", "COMPRESSION", "VOLUME", "FLOW", "OI", "PRICE_OI_RELATION")},
    "TF_15M": {"scope": "15M", "role": "DETAIL", "claim_types": ("TIMEFRAME_STRUCTURE", "MOMENTUM", "EXTENSION", "VOLUME")},
    "TF_1H": {"scope": "1H", "role": "DETAIL", "claim_types": ("TIMEFRAME_STRUCTURE", "MOMENTUM", "EXTENSION", "VOLUME")},
    "TF_4H": {"scope": "4H", "role": "DETAIL", "claim_types": ("TIMEFRAME_STRUCTURE", "MOMENTUM", "EXTENSION", "VOLUME")},
    "TF_1D": {"scope": "1D", "role": "DETAIL", "claim_types": ("TIMEFRAME_STRUCTURE", "MOMENTUM", "EXTENSION", "VOLUME")},
    "TF_1W": {"scope": "1W", "role": "DETAIL", "claim_types": ("TIMEFRAME_STRUCTURE", "MOMENTUM", "EXTENSION", "VOLUME")},
    "ORDER_FLOW": {"scope": "FLOW", "role": "DETAIL",
        "claim_types": ("FLOW", "OI", "PRICE_OI_RELATION", "LIMITATION", "COVERAGE_METADATA")},
    "KEY_LEVELS": {"scope": "PRICE_MAP", "role": "EVIDENCE",
        "claim_types": ("LEVEL", "LIMITATION", "COVERAGE_METADATA")},
    "SCENARIOS": {"scope": "SCENARIO", "role": "CONDITION",
        "claim_types": ("SCENARIO", "TRIGGER", "INVALIDATION", "LIMITATION")},
    "LIMITATIONS": {"scope": "GLOBAL", "role": "LIMITATION",
        "claim_types": ("LIMITATION", "COVERAGE_METADATA")},
    "MACRO_BACKGROUND": {"scope": "GLOBAL", "role": "EVIDENCE", "claim_types": ("COVERAGE_METADATA",)},
    "POSITION_PLAN": {"scope": "GLOBAL", "role": "CONDITION", "claim_types": ("SCENARIO", "TRIGGER", "INVALIDATION")},
}

_ESSENTIAL_WARNING_IDS = {
    "DATA_QUALITY", "ANALYSIS_AVAILABILITY", "EVIDENCE_FLOW_QUALITY",
    "LONG_TERM_QUALITY", "MACRO_UNAVAILABLE",
}


def provider_section_claim_plan(section_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Compact provider-facing plan; no registry facts are duplicated here."""
    return {
        "version": NARRATIVE_CONTRACT_VERSION,
        "sections": {
            section_id: {
                "scope": SECTION_CLAIM_PLAN[section_id]["scope"],
                "role": SECTION_CLAIM_PLAN[section_id]["role"],
                "allowed_claim_types": list(SECTION_CLAIM_PLAN[section_id]["claim_types"]),
            }
            for section_id in section_ids
        },
        "rules": [
            "use only the assigned section scope and claim types",
            "summary compresses detail evidence and must not copy detail prose verbatim",
            "synthesis explains relationships between at least two timeframe scopes",
            "timeframe detail adds scope-specific structure, momentum, extension, or volume evidence",
            "limitations belong in LIMITATIONS unless locally necessary to qualify unavailable evidence",
        ],
    }


def section_scope(section_id: str, scenario_refs: list[str] | None = None) -> str:
    if section_id == "SCENARIOS" and scenario_refs and len(scenario_refs) == 1:
        return f"SCENARIO_{scenario_refs[0]}"
    return str(SECTION_CLAIM_PLAN.get(section_id, {"scope": "GLOBAL"})["scope"])


def claim_scope(section_id: str, text: str, scenario_refs: list[str] | None = None) -> str:
    aliases = {
        "15M": ("超短周期", "15m", "15分钟"), "1H": ("小时周期", "1H", "1小时"),
        "4H": ("中周期", "4H", "4小时"), "1D": ("日线", "1D", "1日"),
        "1W": ("周线", "1W", "1周"),
    }
    mentioned={scope for scope,terms in aliases.items() if any(re.search(re.escape(term),text,re.I) for term in terms)}
    return next(iter(mentioned)) if len(mentioned)==1 else section_scope(section_id,scenario_refs)


def claim_role(section_id: str, text: str) -> str:
    if section_id == "SCENARIOS":
        if any(term in text for term in ("失效", "无效", "跌破", "invalidation")):
            return "INVALIDATION"
        if any(term in text for term in ("触发", "突破", "trigger")):
            return "TRIGGER"
        return "CONDITION"
    if any(term in text for term in ("证据不足", "不可用", "未纳入", "未加入", "限制")):
        return "LIMITATION"
    return str(SECTION_CLAIM_PLAN.get(section_id, {"role": "DETAIL"})["role"])


def narrative_claim_type(section_id: str, text: str) -> str:
    lower = text.casefold()
    if any(term in text for term in ("未纳入", "未加入", "覆盖", "未经审计", "可用性")):
        return "COVERAGE_METADATA"
    if any(term in text for term in ("证据不足", "不可用", "无法判断", "无法提供", "限制")):
        return "LIMITATION"
    if section_id == "SCENARIOS":
        if any(term in text for term in ("失效", "无效", "跌破")): return "INVALIDATION"
        if any(term in text for term in ("触发", "突破")): return "TRIGGER"
        return "SCENARIO"
    if section_id.startswith("TF_"):
        if any(term in text for term in ("成交量", "量能", "放量", "缩量")): return "VOLUME"
        if any(term in text for term in ("伸展", "延伸", "extended", "extension")): return "EXTENSION"
        if any(term in text for term in ("动量", "momentum")): return "MOMENTUM"
        return "TIMEFRAME_STRUCTURE"
    if section_id == "KEY_LEVELS" or any(term in text for term in ("支撑", "压力", "阻力", "关键位")):
        return "LEVEL"
    if "price" in lower and "oi" in lower or "价格" in text and "OI" in text:
        return "PRICE_OI_RELATION"
    if "OI" in text or "持仓量" in text or "未平仓量" in text: return "OI"
    if "CVD" in text or "订单流" in text or "净流" in text: return "FLOW"
    if any(term in text for term in ("成交量", "量能", "放量", "缩量")): return "VOLUME"
    if any(term in text for term in ("冲动", "推进", "impulse")): return "IMPULSE"
    if any(term in text for term in ("回踩", "回撤", "pullback")): return "PULLBACK"
    if any(term in text for term in ("压缩", "整理", "compression")): return "COMPRESSION"
    if any(term in text for term in ("伸展", "延伸", "extended", "extension")): return "EXTENSION"
    if any(term in text for term in ("动量", "momentum")): return "MOMENTUM"
    if section_id in {"QUICK_SUMMARY", "CONCLUSION"}: return "CROSS_TIMEFRAME_SYNTHESIS"
    return "MARKET_STATE"


def semantic_key(claim_type: str, scope: str, text: str, source_fact_ids: list[str]) -> str:
    if claim_type == "LIMITATION" or claim_type == "COVERAGE_METADATA":
        if any(term in text for term in ("订单流", "CVD", "OI")): topic = "FLOW"
        elif any(term in text for term in ("宏观", "macro")): topic = "MACRO"
        elif any(term in text for term in ("审计", "audit")): topic = "AUDIT"
        elif any(term in text for term in ("失效", "情景")): topic = "SCENARIO"
        else: topic = "GENERAL"
        return f"{claim_type}:{topic}"
    if claim_type == "CROSS_TIMEFRAME_SYNTHESIS":
        scopes = sorted({scope_for_fact_id(ref) for ref in source_fact_ids if scope_for_fact_id(ref)})
        return "CROSS_TIMEFRAME_SYNTHESIS:" + "+".join(scopes or ["GLOBAL"])
    return f"{claim_type}:{scope}"


def scope_for_fact_id(fact_id: str) -> str | None:
    return next((scope for prefix, scope in (
        ("TF15_", "15M"), ("TF1H_", "1H"), ("TF4H_", "4H"),
        ("TF1D_", "1D"), ("TF1W_", "1W"),
    ) if fact_id.startswith(prefix)), None)


def section_fact_ids(section_id: str, by_category: dict[str, list[str]]) -> list[str]:
    """Deterministically bind only evidence owned by this section."""
    if section_id in TIMEFRAME_PREFIX:
        prefix = TIMEFRAME_PREFIX[section_id]
        return [fact_id for fact_id in by_category.get("TIMEFRAME", []) if fact_id.startswith(prefix)]
    if section_id == "ORDER_FLOW":
        return list(dict.fromkeys(by_category.get("ORDER_FLOW", []) + [
            fact_id for fact_id in by_category.get("WARNING", []) if fact_id == "EVIDENCE_FLOW_QUALITY"
        ]))
    if section_id == "KEY_LEVELS": return list(by_category.get("LEVEL", []))
    if section_id == "SCENARIOS": return list(dict.fromkeys(
        by_category.get("SCENARIO", []) + by_category.get("LEVEL", []) + by_category.get("ORDER_FLOW", [])
    ))
    if section_id == "LIMITATIONS":
        return [fact_id for fact_id in by_category.get("WARNING", []) if fact_id in _ESSENTIAL_WARNING_IDS or fact_id.startswith("DATA_WARNING_")]
    if section_id == "MACRO_BACKGROUND": return list(by_category.get("MACRO", []))
    if section_id == "POSITION_PLAN": return list(dict.fromkeys(
        by_category.get("POSITION", []) + by_category.get("SCENARIO", []) + by_category.get("LEVEL", [])
    ))
    if section_id in {"QUICK_SUMMARY", "CONCLUSION", "RECENT_PROCESS", "MOVE_NATURE"}:
        facts = list(by_category.get("TIMELINE", []))
        timeframe = list(by_category.get("TIMEFRAME", []))
        if section_id in {"QUICK_SUMMARY", "CONCLUSION"}:
            timeframe = [fact_id for fact_id in timeframe if fact_id.endswith(("_SUMMARY", "_STRUCTURE")) or fact_id in {"TIMEFRAME_ALIGNMENT", "TIMEFRAME_EXTENSION"}]
        facts.extend(timeframe)
        if section_id == "QUICK_SUMMARY":
            facts.extend(by_category.get("SCENARIO", [])); facts.extend(by_category.get("LEVEL", []))
        if section_id == "MOVE_NATURE": facts.extend(by_category.get("ORDER_FLOW", []))
        return list(dict.fromkeys(facts))
    return []


def sentence_mentions_foreign_scope(section_id: str, text: str) -> bool:
    """Reject a detail sentence that is explicitly owned by another timeframe."""
    own = TIMEFRAME_SCOPE.get(section_id)
    if not own: return False
    aliases = {
        "15M": ("超短周期", "15m", "15分钟"), "1H": ("小时周期", "1H", "1小时"),
        "4H": ("中周期", "4H", "4小时"), "1D": ("日线", "1D", "1日"),
        "1W": ("周线", "1W", "1周"),
    }
    mentioned = {scope for scope, terms in aliases.items() if any(re.search(re.escape(term), text, re.I) for term in terms)}
    return bool(mentioned and own not in mentioned)
