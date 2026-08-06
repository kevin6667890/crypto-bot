"""Framework-neutral Shadow-only AI-5 API facade."""
from __future__ import annotations
from typing import Any
from .report_audit_jobs import queue_audit
from .report_audit_repository import AuditRepository,freeze_report_bundle
from .report_repository import ReportRepository

ELIGIBILITY=("AUDIT_PENDING","AUDIT_PASSED_SHADOW_ONLY","AUDIT_FAILED","AUDIT_ERROR","AUDIT_NOT_FOUND")

def trigger_audit(report_id:str,reports:ReportRepository,audits:AuditRepository)->dict[str,Any]:
    try:audits.load_input(report_id)
    except KeyError:
        bundle=freeze_report_bundle(reports,report_id);audits.freeze_input(bundle)
    return queue_audit(audits,report_id)

def get_audit(audit_id:str,audits:AuditRepository)->dict[str,Any]:return audits.get_audit(audit_id)
def latest_audit(report_id:str,audits:AuditRepository)->dict[str,Any]|None:return audits.latest(report_id)

def eligibility(report_id:str,audits:AuditRepository)->dict[str,Any]:
    audit=audits.latest(report_id)
    if audit:
        state={"PASSED":"AUDIT_PASSED_SHADOW_ONLY","FAILED":"AUDIT_FAILED","ERROR":"AUDIT_ERROR"}[audit["status"]]
        return {"report_id":report_id,"eligibility":state,"audit_id":audit["audit_id"],"shadow_only":True}
    with audits.connect() as c:r=c.execute("SELECT event_type,audit_id FROM ai_report_audit_events WHERE report_id=? ORDER BY event_id DESC LIMIT 1",(report_id,)).fetchone()
    return {"report_id":report_id,"eligibility":"AUDIT_PENDING" if r else "AUDIT_NOT_FOUND","audit_id":r[1] if r else None,"shadow_only":True}

def create_evaluation_run(payload:dict[str,Any],audits:AuditRepository)->dict[str,Any]:
    if set(payload)-{"manifest"}:raise ValueError("unknown evaluation fields")
    manifest=payload.get("manifest")
    if not isinstance(manifest,dict) or len(manifest.get("cases",[]))>1000:raise ValueError("invalid evaluation manifest")
    return audits.create_evaluation_run(manifest)
