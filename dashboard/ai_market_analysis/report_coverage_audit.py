"""Mode coverage aggregated from strict scenario fields and frozen warnings."""
from __future__ import annotations
from typing import Any
from .versions import AI_REPORT_COVERAGE_AUDIT_VERSION
from .report_scenario_audit import audit_report_scenarios
from .report_response_contract import expected_section_manifest

FULL_SECTIONS={"CONCLUSION","RECENT_PROCESS","MOVE_NATURE","TF_15M","TF_1H","TF_4H","TF_1D","TF_1W","ORDER_FLOW","KEY_LEVELS","SCENARIOS","LIMITATIONS"}

def audit_coverage(report:dict[str,Any],claims:list[dict[str,Any]],context:dict[str,Any],facts:list[dict[str,Any]])->dict[str,Any]:
    mode=report.get("mode");sections={s.get("section_id") for s in report.get("sections",[])};codes=[]
    fact_map={str(item.get("fact_id")):item.get("value") for item in facts}
    flow_available=fact_map.get("EVIDENCE_FLOW_QUALITY")!="FLOW_UNAVAILABLE"
    long_term_available=fact_map.get("LONG_TERM_QUALITY") in {"COMPLETE","PARTIAL"}
    macro_available=any(item.get("category")=="MACRO" for item in facts)
    required=set(expected_section_manifest(
        mode,macro_available,has_flow=flow_available,has_long_term=long_term_available
    )["required_section_ids_in_exact_order"])
    if mode=="POSITION_AWARE":required.add("POSITION_PLAN")
    missing=sorted(required-sections)
    if missing:codes.append("REQUIRED_SECTION_MISSING")
    strict=audit_report_scenarios(report,{"facts":facts});required_scenarios=strict["required_scenario_count"]
    covered=sum(1 for item in strict["audits"] if item["status"]=="PASSED");scenario_ratio=strict["field_coverage"]
    if scenario_ratio<1:codes.append("SCENARIO_INCOMPLETE")
    position_required=mode=="POSITION_AWARE" and context.get("position_context",{}).get("source")!="NONE"
    position_valid=not position_required or any(c["section_id"]=="POSITION_PLAN" and "POSITION_ORIGINAL_STOP" in c.get("fact_refs",[]) for c in claims)
    required_invalidations=required_scenarios+(1 if position_required else 0)
    covered_invalidations=round(strict["invalidation_coverage"]*required_scenarios)+(1 if position_valid and position_required else 0)
    invalid_ratio=1.0 if required_invalidations==0 else covered_invalidations/required_invalidations
    if invalid_ratio<1:codes.append("INVALIDATION_MISSING")
    warnings=[f for f in facts if f["category"]=="WARNING" and f["fact_id"].startswith(("DATA_","MACRO_UNAVAILABLE"))]
    warning_sections={"QUICK_SUMMARY", "LIMITATIONS"} if mode=="QUICK" else {"LIMITATIONS"}
    warning_refs={r for c in claims if c["section_id"] in warning_sections for r in c.get("fact_refs",[])}
    critical=[f for f in warnings if f["fact_id"] in {"DATA_QUALITY","CORE_QUALITY","ANALYSIS_AVAILABILITY"} or (f["fact_id"].startswith("DATA_WARNING_") and any(x in str(f["value"]).upper() for x in ("GAP","STALE","MISSING")))]
    covered_warnings=sum(1 for f in critical if f["fact_id"] in warning_refs);warning_ratio=1.0 if not critical else covered_warnings/len(critical)
    if warning_ratio<1:codes.append("CRITICAL_WARNING_OMITTED")
    return {"version":AI_REPORT_COVERAGE_AUDIT_VERSION,"source":"STRICT_SCENARIO_AUDIT","missing_sections":missing,"scenario_required":required_scenarios,"scenario_covered":covered,"scenario_completeness":scenario_ratio,"required_invalidations":required_invalidations,"covered_invalidations":covered_invalidations,"invalidation_coverage":invalid_ratio,"critical_warnings":len(critical),"covered_critical_warnings":covered_warnings,"critical_warning_coverage":warning_ratio,"failure_codes":sorted(set(codes))}
