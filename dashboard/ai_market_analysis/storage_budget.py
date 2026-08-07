"""Conservative capacity projection for AI-6B and existing production growth."""
from __future__ import annotations
from typing import Any
from .report_repository import (MAX_CONTEXT_BYTES,MAX_POSITION_PLAN_BYTES,MAX_MACRO_SET_BYTES,
    MAX_REGISTRY_SNAPSHOT_BYTES,MAX_REPORT_JSON_BYTES,MAX_GENERATED_TEXT_BYTES)
from .report_audit_repository import MAX_AUDIT_INPUT_BYTES,MAX_AUDIT_PAYLOAD_BYTES

SQLITE_OVERHEAD_FACTOR=1.15
SAFETY_RESERVE_BYTES=5*1024**3

def maximum_ai_bytes_per_request()->int:
    payload=MAX_CONTEXT_BYTES+MAX_POSITION_PLAN_BYTES+MAX_MACRO_SET_BYTES+MAX_REGISTRY_SNAPSHOT_BYTES+MAX_REPORT_JSON_BYTES+MAX_GENERATED_TEXT_BYTES+MAX_AUDIT_INPUT_BYTES+MAX_AUDIT_PAYLOAD_BYTES
    return int(payload*SQLITE_OVERHEAD_FACTOR)

def project_capacity(*,filesystem_total_bytes:int,filesystem_used_bytes:int,current_microstructure_bytes:int,
                     microstructure_coverage_days:float,raw_retention_days:int=90,live_requests_per_day:int=10)->dict[str,Any]:
    if min(filesystem_total_bytes,current_microstructure_bytes,microstructure_coverage_days)<=0:raise ValueError("CAPACITY_INPUT_INVALID")
    daily_micro=current_microstructure_bytes/microstructure_coverage_days
    remaining_raw_days=max(0.0,raw_retention_days-microstructure_coverage_days)
    ai_daily=maximum_ai_bytes_per_request()*live_requests_per_day
    projected_24h=filesystem_used_bytes+daily_micro+ai_daily
    projected_90d=filesystem_used_bytes+daily_micro*remaining_raw_days+ai_daily*raw_retention_days
    limit=filesystem_total_bytes-SAFETY_RESERVE_BYTES
    return {"daily_microstructure_growth_bytes":int(daily_micro),"maximum_ai_bytes_per_request":maximum_ai_bytes_per_request(),
            "maximum_ai_daily_growth_bytes":ai_daily,"projected_used_after_24h_bytes":int(projected_24h),
            "projected_used_at_90d_retention_bytes":int(projected_90d),"safety_limit_bytes":limit,
            "within_24h_budget":projected_24h<=limit,"within_90d_budget":projected_90d<=limit}
