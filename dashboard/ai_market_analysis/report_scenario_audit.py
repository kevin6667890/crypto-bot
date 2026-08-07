"""Strict field-by-field audit of scenario projections against frozen facts."""
from __future__ import annotations
from typing import Any
from .versions import AI_REPORT_SCENARIO_AUDIT_VERSION

def _text(value:Any)->str:return " ".join(str(value or "").split()).casefold()

def audit_report_scenarios(report:dict[str,Any],fact_registry:dict[str,Any])->dict[str,Any]:
    facts={f["value"]["scenario_id"]:f for f in fact_registry.get("facts",[]) if f.get("category")=="SCENARIO" and isinstance(f.get("value"),dict) and f["value"].get("scenario_id")}
    projections=report.get("scenarios") if isinstance(report.get("scenarios"),list) else []
    by_id={};duplicates=set()
    for p in projections:
        sid=p.get("scenario_id") if isinstance(p,dict) else None
        if sid in by_id:duplicates.add(sid)
        if sid:by_id[sid]=p
    required=set(facts) if report.get("mode") in {"FULL","POSITION_AWARE"} else set(list(by_id)[:1])
    failures=[];audits=[];passed=0;total=0;invalidation_passed=0
    for sid in sorted(required):
        fact=facts.get(sid);p=by_id.get(sid);codes=[];field_results={}
        if not fact or not p:
            codes.append("SCENARIO_PROJECTION_MISSING");total+=16
        else:
            s=fact["value"];trigger=s.get("trigger") or {};confirmation=s.get("confirmation") or {};invalid=s.get("invalidation") or {}
            confirmation_rule=confirmation.get("rule") if isinstance(confirmation,dict) else confirmation
            checks=[
             ("type",p.get("scenario_type")==s.get("type"),"SCENARIO_TYPE_MISMATCH"),
             ("direction",p.get("direction")==s.get("direction"),"SCENARIO_DIRECTION_MISMATCH"),
             ("trigger",_text(p.get("trigger_text"))==_text(trigger.get("rule")) and p.get("trigger_level_refs",[])==trigger.get("level_ids",[]),"SCENARIO_TRIGGER_MISMATCH"),
             ("confirmation",bool(_text(p.get("confirmation_text"))),"SCENARIO_CONFIRMATION_MISSING"),
             ("confirmation_match",_text(p.get("confirmation_text"))==_text(confirmation_rule),"SCENARIO_CONFIRMATION_MISMATCH"),
             ("expected_path",p.get("expected_path_level_refs",[])==s.get("expected_path",[]),"SCENARIO_EXPECTED_PATH_MISMATCH"),
             ("targets_present",bool(p.get("target_level_refs")),"SCENARIO_TARGET_MISSING"),
             ("targets",p.get("target_level_refs",[])==s.get("targets",[]),"SCENARIO_TARGET_MISMATCH"),
             ("invalidation_level",p.get("invalidation_level_ref")==invalid.get("level_id"),"SCENARIO_INVALIDATION_LEVEL_MISMATCH"),
             ("invalidation_timeframe",p.get("invalidation_timeframe")==invalid.get("timeframe"),"SCENARIO_INVALIDATION_TIMEFRAME_MISMATCH"),
             ("confirmed_close",p.get("confirmed_close_required")==( "confirmed" in _text(invalid.get("rule"))),"SCENARIO_CONFIRMED_CLOSE_MISSING"),
             ("volume",_text(p.get("volume_confirmation_text"))==_text(s.get("volume_confirmation")),"SCENARIO_VOLUME_CONDITION_MISMATCH" if p.get("volume_confirmation_text") else "SCENARIO_VOLUME_CONDITION_MISSING"),
             ("cvd",_text(p.get("cvd_confirmation_text"))==_text(s.get("cvd_confirmation")),"SCENARIO_CVD_CONDITION_MISMATCH" if p.get("cvd_confirmation_text") else "SCENARIO_CVD_CONDITION_MISSING"),
             ("oi",_text(p.get("oi_confirmation_text"))==_text(s.get("oi_confirmation")),"SCENARIO_OI_CONDITION_MISMATCH" if p.get("oi_confirmation_text") else "SCENARIO_OI_CONDITION_MISSING"),
             ("funding_basis",_text(p.get("funding_basis_confirmation_text"))==_text(s.get("funding_basis_confirmation")),"SCENARIO_FUNDING_BASIS_MISMATCH"),
             ("counterevidence",_text(p.get("contradicting_evidence_text"))==_text("; ".join(map(str,s.get("contradicting_evidence",[])))),"SCENARIO_COUNTEREVIDENCE_OMITTED"),
             ("sources",set(p.get("level_refs",[]))==set(s.get("source_level_ids",[])) and set(p.get("source_phase_ids",[]))==set(s.get("source_phase_ids",[])),"SCENARIO_SOURCE_REFERENCE_MISMATCH"),
            ]
            for name,ok,code in checks:field_results[name]=ok;codes.extend([] if ok else [code])
            total+=len(checks);passed+=sum(1 for _,ok,_ in checks if ok)
            if all(field_results.get(x) for x in ("invalidation_level","invalidation_timeframe","confirmed_close")):invalidation_passed+=1
        if sid in duplicates:codes.append("SCENARIO_PROJECTION_MISSING")
        failures.extend(codes);audits.append({"scenario_id":sid,"fact_id":fact.get("fact_id") if fact else None,"status":"PASSED" if not codes else "FAILED","field_results":field_results,"failure_codes":sorted(set(codes))})
    if report.get("mode") in {"FULL","POSITION_AWARE"} and set(by_id)!=set(facts):failures.append("SCENARIO_PROJECTION_MISSING")
    coverage=1.0 if total==0 else passed/total;invalidation=1.0 if not required else invalidation_passed/len(required)
    return {"version":AI_REPORT_SCENARIO_AUDIT_VERSION,"audits":audits,"required_scenario_count":len(required),"field_coverage":round(coverage,3),"invalidation_coverage":round(invalidation,3),"failure_codes":sorted(set(failures))}
