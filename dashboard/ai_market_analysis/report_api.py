"""Framework-neutral validation/facade used by the versioned Shadow HTTP endpoints."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from .context_adapter import build_market_analysis_context
from .position_plan_models import normalize_user_position_plan
from .readonly_adapter import MAX_ORDERFLOW_QUERY_SECONDS,ReadOnlyOrderflowAdapter
from .report_health import report_health
from .report_repository import ReportRepository
from .report_service import ReportService
from .versions import SUPPORTED_INSTRUMENTS,SUPPORTED_TIMEFRAMES
from dashboard.market_context_v2 import BoundedMarketDataReaderV2

REPORT_BODY_FIELDS={"instrument","decision_time","mode","language","position_source","position_plan_id","inline_position_plan","macro_evidence_set_id","inline_macro_evidence","provider","model"}

def _walk(value:Any,depth:int=0)->None:
    if depth>8:raise ValueError("JSON depth exceeds limit")
    if isinstance(value,str) and len(value)>4000:raise ValueError("string exceeds limit")
    if isinstance(value,dict):
        if len(value)>80:raise ValueError("object exceeds field limit")
        for x in value.values():_walk(x,depth+1)
    elif isinstance(value,list):
        if len(value)>100:raise ValueError("array exceeds item limit")
        for x in value:_walk(x,depth+1)

def validate_report_body(payload:dict[str,Any])->dict[str,Any]:
    _walk(payload)
    unknown=set(payload)-REPORT_BODY_FIELDS
    if unknown:raise ValueError(f"unknown report fields: {sorted(unknown)}")
    instrument=payload.get("instrument")
    if instrument not in SUPPORTED_INSTRUMENTS:raise ValueError("unsupported instrument")
    decision=str(payload.get("decision_time") or "")
    try:dt=datetime.fromisoformat(decision.replace("Z","+00:00"))
    except ValueError:raise ValueError("invalid decision_time") from None
    if dt.tzinfo is None or dt>datetime.now(timezone.utc):raise ValueError("decision_time must be timezone-aware and not future")
    if payload.get("position_plan_id") is not None and payload.get("inline_position_plan") is not None:raise ValueError("position plan inputs are mutually exclusive")
    if payload.get("macro_evidence_set_id") is not None and payload.get("inline_macro_evidence") is not None:raise ValueError("macro inputs are mutually exclusive")
    return payload

def build_base_context_from_stores(payload:dict[str,Any],paper_db:str|Path,micro_db:str|Path|None)->dict[str,Any]:
    decision=int(datetime.fromisoformat(payload["decision_time"].replace("Z","+00:00")).timestamp());instrument=payload["instrument"]
    reader=BoundedMarketDataReaderV2(paper_db,micro_db);datasets={tf:reader.candles(instrument,tf,decision,1500 if tf=="1D" else 512) for tf in SUPPORTED_TIMEFRAMES if tf!="1W"}
    orderflow=None
    if micro_db and Path(micro_db).exists():
        raw_start=min((int(row["ts"]) for rows in datasets.values() for row in rows),default=decision-30*86400)
        start=max(raw_start,decision-MAX_ORDERFLOW_QUERY_SECONDS)
        orderflow=ReadOnlyOrderflowAdapter(micro_db).read(instrument,start,decision,"4H")
    return build_market_analysis_context(datasets,instrument,decision,payload.get("mode","FULL"),orderflow=orderflow)

def submit_report(payload:dict[str,Any],repository:ReportRepository,paper_db:str|Path,micro_db:str|Path|None)->dict[str,Any]:
    value=validate_report_body(payload);position=value.get("inline_position_plan")
    if value.get("position_plan_id"):position=repository.load_position_plan(value["position_plan_id"])
    macro=value.get("inline_macro_evidence") or []
    if value.get("macro_evidence_set_id"):macro=repository.load_macro_set(value["macro_evidence_set_id"])["items"]
    base=build_base_context_from_stores(value,paper_db,micro_db)
    return ReportService(repository,str(paper_db)).submit(base,mode=value.get("mode","FULL"),language=value.get("language","zh-CN"),position_source=value.get("position_source","NONE"),position_plan=position,macro_evidence=macro,provider=value.get("provider","fake"),model=value.get("model","fake-ai4"))

def save_position_plan(payload:dict[str,Any],repository:ReportRepository)->dict[str,Any]:
    _walk(payload)
    plan=normalize_user_position_plan(payload)
    if plan.get("supersedes_plan_id"):
        old=repository.load_position_plan(plan["supersedes_plan_id"])
        if old["instrument"]!=plan["instrument"]:raise ValueError("superseded plan instrument mismatch")
    repository.save_position_plan(plan);return {k:plan[k] for k in ("plan_id","plan_version","instrument","source","effective_at","supersedes_plan_id","status","payload_hash")}
