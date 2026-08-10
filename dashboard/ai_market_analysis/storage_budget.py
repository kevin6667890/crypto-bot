"""Conservative capacity projection for AI-6B and existing production growth."""
from __future__ import annotations
from typing import Any
from .report_repository import (MAX_CONTEXT_BYTES,MAX_POSITION_PLAN_BYTES,MAX_MACRO_SET_BYTES,
    MAX_REGISTRY_SNAPSHOT_BYTES,MAX_REPORT_JSON_BYTES,MAX_GENERATED_TEXT_BYTES)
from .report_audit_repository import MAX_AUDIT_INPUT_BYTES,MAX_AUDIT_PAYLOAD_BYTES

SQLITE_OVERHEAD_FACTOR=1.15
HOT_RETENTION_DAYS=30
LOGICAL_RETENTION_DAYS=90
WAL_PEAK_RESERVE_BYTES=512*1024**2
DOCKER_IMAGE_CACHE_RESERVE_BYTES=8*1024**3
SYSTEM_SAFETY_RESERVE_BYTES=15*1024**3

def maximum_ai_bytes_per_request()->int:
    payload=MAX_CONTEXT_BYTES+MAX_POSITION_PLAN_BYTES+MAX_MACRO_SET_BYTES+MAX_REGISTRY_SNAPSHOT_BYTES+MAX_REPORT_JSON_BYTES+MAX_GENERATED_TEXT_BYTES+MAX_AUDIT_INPUT_BYTES+MAX_AUDIT_PAYLOAD_BYTES
    return int(payload*SQLITE_OVERHEAD_FACTOR)

def project_capacity(*,filesystem_total_bytes:int,filesystem_used_bytes:int,current_microstructure_bytes:int,
                     microstructure_coverage_days:float,raw_retention_days:int=30,live_requests_per_day:int=10,
                     observed_non_ai_daily_growth_bytes:int=0)->dict[str,Any]:
    if min(filesystem_total_bytes,current_microstructure_bytes,microstructure_coverage_days)<=0:raise ValueError("CAPACITY_INPUT_INVALID")
    daily_micro=current_microstructure_bytes/microstructure_coverage_days
    projected_non_ai_daily=max(daily_micro,observed_non_ai_daily_growth_bytes)
    remaining_hot_days=max(0.0,raw_retention_days-microstructure_coverage_days)
    ai_daily=maximum_ai_bytes_per_request()*live_requests_per_day
    hot_ai=ai_daily*HOT_RETENTION_DAYS
    archive_ai=ai_daily*(LOGICAL_RETENTION_DAYS-HOT_RETENTION_DAYS)
    logical_90d=hot_ai+archive_ai
    backup_temporary=hot_ai*2
    projected_24h=filesystem_used_bytes+projected_non_ai_daily+ai_daily
    projected_local_30d=(filesystem_used_bytes+projected_non_ai_daily*remaining_hot_days+
                         logical_90d+WAL_PEAK_RESERVE_BYTES+DOCKER_IMAGE_CACHE_RESERVE_BYTES+backup_temporary)
    limit=filesystem_total_bytes-SYSTEM_SAFETY_RESERVE_BYTES
    return {"daily_microstructure_growth_bytes":int(daily_micro),"maximum_ai_bytes_per_request":maximum_ai_bytes_per_request(),
            "projected_non_ai_daily_growth_bytes":int(projected_non_ai_daily),"observed_non_ai_daily_growth_bytes":observed_non_ai_daily_growth_bytes,
            "existing_hot_coverage_days":microstructure_coverage_days,"remaining_hot_retention_days":remaining_hot_days,
            "maximum_ai_daily_growth_bytes":ai_daily,"projected_used_after_24h_bytes":int(projected_24h),
            "ai_hot_30d_bytes":hot_ai,"ai_archive_days_31_to_90_bytes":archive_ai,
            "ai_logical_90d_total_bytes":logical_90d,"wal_peak_reserve_bytes":WAL_PEAK_RESERVE_BYTES,
            "docker_image_cache_reserve_bytes":DOCKER_IMAGE_CACHE_RESERVE_BYTES,
            "backup_temporary_space_bytes":backup_temporary,"system_safety_reserve_bytes":SYSTEM_SAFETY_RESERVE_BYTES,
            "projected_used_with_30d_hot_and_90d_logical_bytes":int(projected_local_30d),"safety_limit_bytes":limit,
            "local_safety_margin_bytes":int(limit-projected_local_30d),
            "within_24h_budget":projected_24h<=limit,"within_30d_hot_and_90d_logical_budget":projected_local_30d<=limit,
            "external_archive_required_before_day_91":True,"raw_retention_days_input":raw_retention_days}
