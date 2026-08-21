"""AI-4 structural, citation and conservative numeric validation (not an AI-5 audit)."""
from __future__ import annotations
import re
from typing import Any
from .versions import AI_REPORT_BASIC_VALIDATION_VERSION, AI_REPORT_RESPONSE_VERSION
from .report_numeric_normalizer import normalize_numbers
from .report_response_contract import LEVEL_PROJECTION_FIELDS, SCENARIO_PROJECTION_FIELDS, expected_section_manifest
from .report_identity import REPORT_PIPELINE_VERSIONS
from .provider_response_diagnostics import allowed_level_references

NUMBER_RE=re.compile(r"(?<![A-Za-z_\d])[-+]?\d+(?:\.\d+)?%?(?![A-Za-z_\d])")
TIMEFRAME_RE=re.compile(r"(?i)(?:15m|1H|4H|1D|1W|15分钟|1小时|4小时)")
DATE_RE=re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?")
VERSION_RE=re.compile(r"(?:v\d+|\b(?:CONCLUSION|TF_15M|TF_1H|TF_4H|TF_1D|TF_1W)\b)",re.I)
ORDER_PATTERNS=("立即下单","马上买入","马上卖出","自动交易","开仓","加杠杆","借钱交易","必须卖一半")
GUARANTEE_PATTERNS=("保证收益","确定上涨","确定下跌","必然上涨","必然下跌")


class ReportValidationError(ValueError):
    def __init__(self, code: str, details: list[str] | None=None):
        super().__init__(code); self.code=code; self.details=details or []


def expected_sections(mode: str, has_macro: bool, registry: dict[str, Any] | None = None) -> list[str]:
    registry = registry or {}
    pack = registry.get("provider_claim_pack") or {}
    evidence = pack.get("evidence_status") or {}
    fact_values = {str(item.get("fact_id")): item.get("value") for item in registry.get("facts", [])}
    return list(expected_section_manifest(
        mode, has_macro,
        has_flow=evidence.get("flow_coverage_state", "FLOW_COMPLETE") != "FLOW_UNAVAILABLE",
        has_long_term=fact_values.get("LONG_TERM_QUALITY") in {None, "COMPLETE", "PARTIAL"},
    )["required_section_ids_in_exact_order"])


def _numbers(text: str) -> list[float]:
    return [float(item["value"]) for item in normalize_numbers(text) if "value" in item]


def _numeric_allowed(value: float, registry: list[dict[str,Any]]) -> bool:
    return any(abs(float(item["canonical_value"])-value)<=float(item.get("absolute_tolerance",0)) or
               abs(round(float(item["canonical_value"]))-value)<=float(item.get("absolute_tolerance",0))
               for item in registry)


def validate_report(report: dict[str,Any], request: dict[str,Any], registry: dict[str,Any]) -> dict[str,Any]:
    checks={"version":AI_REPORT_BASIC_VALIDATION_VERSION}
    exact=(("schema_version",AI_REPORT_RESPONSE_VERSION),("context_id",request["context_id"]),("request_id",request["request_id"]),
           ("mode",request["mode"]),("language",request["language"]),("prompt_version",request["prompt_version"]),("model",request["model"]),("audit_status","PENDING"))
    for field,want in exact:
        if report.get(field)!=want: raise ReportValidationError(f"{field.upper()}_MISMATCH")
    if report.get("source_versions")!=request.get("source_versions",REPORT_PIPELINE_VERSIONS):raise ReportValidationError("SOURCE_VERSIONS_MISMATCH")
    if not str(report.get("headline") or "").strip(): raise ReportValidationError("EMPTY_HEADLINE")
    allowed_phases=set(registry["allowed_market_phases"])
    if report["market_phase"] not in allowed_phases: raise ReportValidationError("MARKET_PHASE_NOT_ALLOWED")
    if report["directional_bias"] not in registry["allowed_directional_biases"]: raise ReportValidationError("DIRECTION_NOT_ALLOWED")
    rank={"LOW":0,"MEDIUM":1,"HIGH":2}
    if report["confidence"] not in rank or rank[report["confidence"]]>rank[registry["max_confidence"]]: raise ReportValidationError("CONFIDENCE_EXCEEDS_CAP")
    macro_items=request["macro_items"]
    wanted=expected_sections(report["mode"],bool(macro_items),request.get("compiled_context")); got=[s["section_id"] for s in report["sections"]]
    if got!=wanted: raise ReportValidationError("SECTION_ORDER_OR_COMPLETENESS",[str(got),str(wanted)])
    fact_ids={f["fact_id"] for f in registry["facts"]}
    level_ids=allowed_level_references(registry["facts"])
    scenario_ids={f["value"].get("scenario_id") for f in registry["facts"] if f["category"]=="SCENARIO" and isinstance(f["value"],dict)}
    macro_ids={i["evidence_id"] for i in macro_items}
    position_ids={x for x in fact_ids if x.startswith("POSITION_")}
    text=report["headline"]+"\n"+"\n".join(s["title"]+"\n"+s["body"] for s in report["sections"])
    for section in report["sections"]:
        if not section["body"].strip(): raise ReportValidationError("EMPTY_SECTION")
        if not set(section["fact_refs"])<=fact_ids: raise ReportValidationError("UNKNOWN_FACT_REF")
        if not set(section["level_refs"])<=level_ids: raise ReportValidationError("UNKNOWN_LEVEL_REF")
        if not set(section["scenario_refs"])<=scenario_ids: raise ReportValidationError("UNKNOWN_SCENARIO_REF")
        if not set(section["macro_refs"])<=macro_ids: raise ReportValidationError("UNKNOWN_MACRO_REF")
        if not set(section["position_refs"])<=position_ids: raise ReportValidationError("UNKNOWN_POSITION_REF")
    level_projection_fields=set(LEVEL_PROJECTION_FIELDS)
    scenario_projection_fields=set(SCENARIO_PROJECTION_FIELDS)
    projections=report.get("key_levels")
    if not isinstance(projections,list) or any(not level_projection_fields<=set(x) for x in projections):raise ReportValidationError("LEVEL_PROJECTION_INVALID")
    if any(x["level_id"] not in level_ids or not set(x["level_refs"])<={x["level_id"]} or not set(x["fact_refs"])<=fact_ids for x in projections):raise ReportValidationError("UNKNOWN_LEVEL_REF")
    scenarios=report.get("scenarios")
    if (not isinstance(scenarios,list) or (scenario_ids and not scenarios)
            or any(not scenario_projection_fields<=set(x) for x in scenarios)):
        raise ReportValidationError("SCENARIO_PROJECTION_INVALID")
    if any(x["scenario_id"] not in scenario_ids or not set(x["level_refs"])<=level_ids or not set(x["fact_refs"])<=fact_ids for x in scenarios):raise ReportValidationError("UNKNOWN_SCENARIO_REF")
    if report["mode"] in {"FULL","POSITION_AWARE"} and {x["scenario_id"] for x in scenarios}!=scenario_ids:raise ReportValidationError("SCENARIO_PROJECTION_INCOMPLETE")
    citation_ids=[]
    for citation in report["citations"]:
        if not isinstance(citation,dict) or set(citation)!={"evidence_id"}:raise ReportValidationError("INVALID_CITATION")
        citation_ids.append(citation["evidence_id"])
    if not set(citation_ids)<=macro_ids or set(citation_ids)!={ref for section in report["sections"] for ref in section["macro_refs"]}:raise ReportValidationError("UNKNOWN_MACRO_REF")
    if any(re.search(r"(?:概率|胜率)\s*(?:为|约|:|：)?\s*\d",text) for _ in [0]): raise ReportValidationError("EXACT_PROBABILITY_FORBIDDEN")
    if any(x in text for x in ORDER_PATTERNS): raise ReportValidationError("ORDER_INSTRUCTION_FORBIDDEN")
    if any(x in text for x in GUARANTEE_PATTERNS): raise ReportValidationError("GUARANTEE_FORBIDDEN")
    if request["position_source"]=="NONE" and (report["position_guidance"] is not None or any(x in text for x in ("你的多单","减仓","止损移动","卖一半"))): raise ReportValidationError("UNPROVIDED_POSITION")
    if not macro_items and ("MACRO_BACKGROUND" in got or any(s["macro_refs"] for s in report["sections"])): raise ReportValidationError("UNPROVIDED_MACRO")
    bad=[str(n) for n in _numbers(text) if not _numeric_allowed(n,registry["numeric_registry"])]
    if bad: raise ReportValidationError("NUMERIC_NOT_IN_REGISTRY",bad[:20])
    if scenarios and all(str(item.get("invalidation_text") or "").strip() for item in scenarios):
        return {"version":AI_REPORT_BASIC_VALIDATION_VERSION,"status":"VALID","checks":25}
    if not any(s["section_id"] in {"LIMITATIONS","QUICK_SUMMARY"} and ("失效" in s["body"] or "限制" in s["body"]) for s in report["sections"]): raise ReportValidationError("EMPTY_INVALIDATION")
    return {"version":AI_REPORT_BASIC_VALIDATION_VERSION,"status":"VALID","checks":25}


def assemble_generated_text(report: dict[str,Any]) -> str:
    return report["headline"]+"\n\n"+"\n\n".join(f"{section['title']}\n{section['body']}" for section in report["sections"])

def resolve_citations(report:dict[str,Any],macro_items:list[dict[str,Any]])->dict[str,Any]:
    lookup={item["evidence_id"]:item for item in macro_items}
    report={**report,"citations":[{"evidence_id":item["evidence_id"],"title":item["title"],"source_url":item["source_url"],"publisher":item["publisher"],"published_at":item["published_at"]} for citation in report["citations"] for item in [lookup[citation["evidence_id"]]] ]}
    return report
