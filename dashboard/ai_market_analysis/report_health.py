"""Sanitized health view for the isolated report subsystem."""
from __future__ import annotations
import os
from pathlib import Path
from pathlib import Path
from .report_repository import ReportRepository
from .report_jobs import ConcurrencyGate
from .live_provider_guard import status as kill_switch_status

def report_health(repository:ReportRepository,gate:ConcurrencyGate|None=None)->dict:
    gate=gate or ConcurrencyGate();enabled=os.getenv("AI_MARKET_REPORTS_ENABLED","false").lower()=="true"
    secret_file=os.getenv("AI_REPORT_API_KEY_FILE","");secret_present=bool(secret_file and Path(secret_file).is_file())
    killed=kill_switch_status();base={"enabled":enabled,"shadow_only":os.getenv("AI_MARKET_REPORT_SHADOW_ONLY","true").lower()=="true","worker_enabled":os.getenv("AI_MARKET_REPORT_WORKER_ENABLED","false").lower()=="true","audit_enabled":os.getenv("AI_REPORT_AUDIT_ENABLED","false").lower()=="true","audit_worker_enabled":os.getenv("AI_REPORT_AUDIT_WORKER_ENABLED","false").lower()=="true","auto_audit_enabled":os.getenv("AI_REPORT_AUTO_AUDIT_ENABLED","false").lower()=="true","evaluation_enabled":os.getenv("AI_REPORT_EVALUATION_ENABLED","false").lower()=="true","provider_configured":bool(os.getenv("AI_REPORT_MODEL")) and (os.getenv("AI_REPORT_PROVIDER","fake")=="fake" or secret_present),"secret_present":secret_present,"live_provider_allowed":os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED","false").lower()=="true" and not killed["live_provider_disabled"],"kill_switch":{"live_provider_disabled":killed["live_provider_disabled"],"event":killed.get("event"),"tripped_at":killed.get("tripped_at")},"global_concurrency":len(gate.active),"per_instrument_active":sorted(gate.instruments),"schema_version":repository.schema_version(),"db_size":repository.path.stat().st_size if repository.path.exists() else 0}
    if not repository.path.exists() or repository.schema_version() is None:return {**base,"queue_depth":0,"active_requests":0,"oldest_queued_age":None,"completed_count":0,"failed_count":0,"budget_blocked_count":0,"last_success":None,"last_failure":None,"daily_tokens":{"input":0,"output":0,"total":0}}
    with repository.connect() as c:
        counts={k:c.execute("SELECT COUNT(*) FROM ai_report_request_events WHERE event_type=?",(v,)).fetchone()[0] for k,v in (("completed_count","COMPLETED"),("failed_count","FAILED_FINAL"),("budget_blocked_count","BUDGET_BLOCKED"))}
        queue=len(repository.queued());active=c.execute("SELECT COUNT(DISTINCT request_id) FROM ai_report_request_events WHERE event_type='RUNNING'").fetchone()[0]
        success=c.execute("SELECT MAX(created_at) FROM ai_report_request_events WHERE event_type='COMPLETED'").fetchone()[0];failure=c.execute("SELECT MAX(created_at) FROM ai_report_request_events WHERE event_type IN ('FAILED_FINAL','VALIDATION_FAILED')").fetchone()[0]
    return {**base,**counts,"queue_depth":queue,"active_requests":active,"oldest_queued_age":None,"last_success":success,"last_failure":failure,"daily_tokens":repository.daily_tokens()}
