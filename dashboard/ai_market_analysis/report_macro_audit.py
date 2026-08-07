"""Frozen causal macro evidence support."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from .versions import AI_REPORT_MACRO_AUDIT_VERSION

MACRO_TERMS=("美联储","Fed","ETF","CPI","降息","监管利好","风险资产情绪")

def _dt(value:str):return datetime.fromisoformat(value.replace("Z","+00:00"))

def audit_macro(claims:list[dict[str,Any]],macro:dict[str,Any],decision_time:str)->dict[str,Any]:
    items={x["evidence_id"]:x for x in macro.get("items",[])};codes=[];audits=[]
    for item in items.values():
        if _dt(item["published_at"])>_dt(decision_time) or (item.get("event_time") and _dt(item["event_time"])>_dt(decision_time)):codes.append("UNSUPPORTED_MACRO")
    for claim in claims:
        text=claim["original_text"];refs=claim.get("macro_refs",[])
        is_macro=claim["claim_type"]=="MACRO" or any(x.lower() in text.lower() for x in MACRO_TERMS)
        code=None
        if is_macro and not items and not any(x in text for x in ("未加入已验证宏观证据","无已验证宏观证据")):code="UNSUPPORTED_MACRO"
        elif is_macro and items and (not refs or not set(refs)<=set(items)):code="UNSUPPORTED_MACRO"
        elif any("http" in text and item.get("source_url","") not in text for item in items.values()):code="UNSUPPORTED_MACRO"
        if code:codes.append(code)
        if is_macro:audits.append({"claim_id":claim["claim_id"],"refs":refs,"supported":not bool(code),"code":code})
    return {"version":AI_REPORT_MACRO_AUDIT_VERSION,"evidence_count":len(items),"audits":audits,"failure_codes":sorted(set(codes))}
