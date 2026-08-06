"""Mode-aware section, scenario, invalidation and warning disclosure."""
from __future__ import annotations
from typing import Any
from .versions import AI_REPORT_COVERAGE_AUDIT_VERSION

FULL_SECTIONS={"CONCLUSION","RECENT_PROCESS","MOVE_NATURE","TF_15M","TF_1H","TF_4H","TF_1D","TF_1W","ORDER_FLOW","KEY_LEVELS","SCENARIOS","LIMITATIONS"}

def audit_coverage(report:dict[str,Any],claims:list[dict[str,Any]],context:dict[str,Any],facts:list[dict[str,Any]])->dict[str,Any]:
    mode=report.get("mode");sections={s.get("section_id") for s in report.get("sections",[])};codes=[]
    required={"QUICK_SUMMARY"} if mode=="QUICK" else set(FULL_SECTIONS)
    if mode=="POSITION_AWARE":required.add("POSITION_PLAN")
    missing=sorted(required-sections)
    if missing:codes.append("REQUIRED_SECTION_MISSING")
    scenarios=[f for f in facts if f["category"]=="SCENARIO"]
    scenario_refs={r for c in claims for r in c.get("scenario_refs",[])}
    required_scenarios=len(scenarios) if mode in {"FULL","POSITION_AWARE"} else min(1,len(scenarios))
    covered=sum(1 for f in scenarios if isinstance(f["value"],dict) and f["value"].get("scenario_id") in scenario_refs)
    scenario_ratio=1.0 if required_scenarios==0 else min(1.0,covered/required_scenarios)
    scenario_text=" ".join(c["original_text"] for c in claims if c["section_id"] in {"SCENARIOS","QUICK_SUMMARY"})
    if required_scenarios and (scenario_ratio<1 or (mode!="QUICK" and (not all(x in scenario_text for x in ("路径一","路径二","路径三")) or "触发" not in scenario_text))):codes.append("SCENARIO_INCOMPLETE")
    invalid_claims=[c for c in claims if c["claim_type"]=="INVALIDATION" or "失效" in c["original_text"]]
    required_invalidations=(1 if mode=="QUICK" else 1+required_scenarios+(1 if mode=="POSITION_AWARE" else 0))
    covered_invalidations=len({tuple(c.get("fact_refs",[])) for c in invalid_claims if c.get("fact_refs")})
    # A scenario fact embeds one immutable invalidation per referenced scenario.
    covered_invalidations=max(covered_invalidations,covered+(1 if any(c["section_id"]=="LIMITATIONS" for c in invalid_claims) else 0)
      +(1 if mode=="POSITION_AWARE" and any(c["section_id"]=="POSITION_PLAN" and "失效" in c["original_text"] and c.get("fact_refs") for c in claims) else 0))
    invalid_ratio=1.0 if required_invalidations==0 else min(1.0,covered_invalidations/required_invalidations)
    if invalid_ratio<1:codes.append("INVALIDATION_MISSING")
    warnings=[f for f in facts if f["category"]=="WARNING" and f["fact_id"].startswith(("DATA_","MACRO_UNAVAILABLE"))]
    warning_refs={r for c in claims if c["section_id"] in ({"QUICK_SUMMARY"} if mode=="QUICK" else {"LIMITATIONS"}) for r in c.get("fact_refs",[])}
    critical=[f for f in warnings if f["fact_id"]=="DATA_QUALITY" or any(x in str(f["value"]).upper() for x in ("GAP","STALE","MISSING","FORWARD","PARTIAL"))]
    covered_warnings=sum(1 for f in critical if f["fact_id"] in warning_refs)
    warning_ratio=1.0 if not critical else covered_warnings/len(critical)
    if warning_ratio<1:codes.append("CRITICAL_WARNING_OMITTED")
    return {"version":AI_REPORT_COVERAGE_AUDIT_VERSION,"missing_sections":missing,"scenario_required":required_scenarios,
      "scenario_covered":covered,"scenario_completeness":scenario_ratio,"required_invalidations":required_invalidations,
      "covered_invalidations":covered_invalidations,"invalidation_coverage":invalid_ratio,"critical_warnings":len(critical),
      "covered_critical_warnings":covered_warnings,"critical_warning_coverage":warning_ratio,"failure_codes":sorted(set(codes))}
