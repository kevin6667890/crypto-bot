"""Deterministic evidence-tier quality and report availability policy."""
from __future__ import annotations

from typing import Any

EVIDENCE_QUALITY_POLICY_VERSION = "evidence-quality-v1"
CORE_TIMEFRAMES = ("15m", "1H", "4H", "1D")


def _coverage_entry(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        quality = value.get("quality")
        if isinstance(quality, dict):
            return {**quality, **{k: v for k, v in value.items() if k != "quality"}}
        return {**value, "status": quality or value.get("status")}
    return {"status": "MISSING", "actual_bars": 0}


def _flow_quality(base: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    phases = list(base.get("order_flow_phases") or [])
    # Only the active rolling window controls current flow quality. Historical
    # event windows remain auditable but cannot permanently poison a report.
    current = next((item for item in reversed(phases) if item.get("phase") == "CURRENT"), None)
    coverage = (((current or {}).get("metrics") or {}).get("quality") or {}).get("flow_coverage") or {}
    state = str(coverage.get("state") or "FLOW_UNAVAILABLE")
    if state not in {"FLOW_COMPLETE", "FLOW_PARTIAL_USABLE", "FLOW_UNAVAILABLE"}:
        state = "FLOW_UNAVAILABLE"
    return state, coverage


def classify_evidence_quality(base: dict[str, Any], macro_context: dict[str, Any] | None = None) -> dict[str, Any]:
    coverage = base.get("timeframe_coverage") or {}
    core = {timeframe: _coverage_entry(coverage.get(timeframe)) for timeframe in CORE_TIMEFRAMES}
    core_statuses = {timeframe: str(item.get("status") or "MISSING") for timeframe, item in core.items()}
    missing = [tf for tf, status in core_statuses.items() if status in {"MISSING", "INVALID"}]
    impaired = [tf for tf, status in core_statuses.items() if status != "COMPLETE"]
    if missing:
        usable_count = sum(bool(core[tf].get("actual_bars")) for tf in CORE_TIMEFRAMES)
        core_quality = "UNAVAILABLE" if usable_count < 3 else "DEGRADED"
        availability = "ANALYSIS_UNAVAILABLE" if core_quality == "UNAVAILABLE" else "ANALYSIS_DEGRADED"
    elif impaired:
        core_quality, availability = "USABLE", "ANALYSIS_DEGRADED"
    else:
        core_quality, availability = "COMPLETE", "ANALYSIS_AVAILABLE"

    flow_quality, flow_coverage = _flow_quality(base)
    weekly = _coverage_entry(coverage.get("1W"))
    weekly_status = str(weekly.get("status") or "MISSING")
    long_term_quality = "COMPLETE" if weekly_status == "COMPLETE" else "PARTIAL" if int(weekly.get("actual_bars") or 0) else "UNAVAILABLE"

    macro = macro_context or base.get("macro_context") or {}
    items = list(macro.get("items") or [])
    macro_status = str(macro.get("status") or macro.get("quality") or "NOT_INCLUDED")
    if items and macro_status not in {"STALE", "MISSING", "UNAVAILABLE"}:
        macro_quality = "AVAILABLE"
    elif items:
        macro_quality = "STALE"
    else:
        macro_quality = "NOT_INCLUDED"

    return {
        "policy_version": EVIDENCE_QUALITY_POLICY_VERSION,
        "core_quality": core_quality,
        "flow_quality": flow_quality,
        "long_term_quality": long_term_quality,
        "macro_quality": macro_quality,
        "analysis_availability": availability,
        "core_timeframes": core,
        "flow_coverage": flow_coverage,
        "long_term": weekly,
    }
