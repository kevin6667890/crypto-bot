"""Strict field-by-field audit of report key-level projections."""
from __future__ import annotations
from typing import Any
from .versions import AI_REPORT_LEVEL_AUDIT_VERSION

STRENGTH={"WEAK":0,"MODERATE":1,"STRONG":2,"MAJOR":3}

def audit_report_levels(report:dict[str,Any],fact_registry:dict[str,Any])->dict[str,Any]:
    facts={f["value"]["level_id"]:f for f in fact_registry.get("facts",[]) if f.get("category")=="LEVEL" and isinstance(f.get("value"),dict) and f["value"].get("level_id")}
    projections=report.get("key_levels") if isinstance(report.get("key_levels"),list) else []
    by_id={p.get("level_id"):p for p in projections if isinstance(p,dict) and p.get("level_id")}
    referenced={x for section in report.get("sections",[]) for x in section.get("level_refs",[])}
    required=(referenced|set(by_id)) if report.get("mode") in {"FULL","POSITION_AWARE"} else (referenced|set(by_id))
    failures=[];audits=[];passed_fields=0;total_fields=0
    for level_id in sorted(required):
        fact=facts.get(level_id);projection=by_id.get(level_id);codes=[]
        if not fact or not projection:
            codes.append("LEVEL_PROJECTION_MISSING");total_fields+=9
        else:
            value=fact["value"]
            checks=[
              ("role",projection.get("asserted_role")==value.get("role"),"LEVEL_ROLE_MISMATCH"),
              ("state",projection.get("asserted_state")==value.get("state"),"LEVEL_STATE_MISMATCH"),
              ("strength",STRENGTH.get(projection.get("asserted_strength"),-1)<=STRENGTH.get(value.get("strength"),-1),"LEVEL_STRENGTH_EXAGGERATED"),
              ("timeframe",projection.get("asserted_timeframe") in value.get("timeframes",[]),"LEVEL_TIMEFRAME_MISMATCH"),
              ("dynamic",projection.get("asserted_dynamic")==value.get("dynamic"),"LEVEL_DYNAMIC_STATIC_MISMATCH"),
              ("valid_until",projection.get("valid_until")==value.get("valid_until"),"LEVEL_VALIDITY_MISMATCH"),
              ("zone_low",projection.get("asserted_zone_low",value.get("zone_low"))==value.get("zone_low"),"LEVEL_ZONE_MISMATCH"),
              ("zone_high",projection.get("asserted_zone_high",value.get("zone_high"))==value.get("zone_high"),"LEVEL_ZONE_MISMATCH"),
              ("references",level_id in projection.get("level_refs",[]) and fact["fact_id"] in projection.get("fact_refs",[]),"LEVEL_PROJECTION_MISSING"),
            ]
            total_fields+=len(checks);passed_fields+=sum(1 for _,ok,_ in checks if ok);codes.extend(code for _,ok,code in checks if not ok)
            text=str(projection.get("analysis_text","")).upper()
            contradictions=((value.get("state")=="FLIPPED" and "UNBROKEN RESISTANCE" in text) or (value.get("state")=="BROKEN" and "INTACT SUPPORT" in text) or (value.get("state")=="UNCONFIRMED" and "CONFIRMED" in text))
            if contradictions:codes.append("LEVEL_PROJECTION_TEXT_CONTRADICTION")
        failures.extend(codes);audits.append({"level_id":level_id,"fact_id":fact.get("fact_id") if fact else None,"status":"PASSED" if not codes else "FAILED","failure_codes":sorted(set(codes)),"resolved_zone":{"low":fact["value"].get("zone_low"),"high":fact["value"].get("zone_high")} if fact else None})
    unknown=set(by_id)-set(facts)
    if unknown:failures.append("LEVEL_PROJECTION_MISSING")
    coverage=1.0 if total_fields==0 else passed_fields/total_fields
    return {"version":AI_REPORT_LEVEL_AUDIT_VERSION,"audits":audits,"required_level_count":len(required),"field_coverage":round(coverage,3),"failure_codes":sorted(set(failures))}
