"""Shared canonical CVD/OI status semantics.

These are presentation-neutral source facts used by both Workspace history and
AI.  They describe observation completeness only; no status authorises a
filled value or synthetic trade.
"""
from __future__ import annotations

VALID_FLOW_STATUSES = {"VALID", "BACKFILLED_OFFICIAL", "ARCHIVED_CONFIRMED"}


def flow_status(status: str | None, has_value: bool) -> str:
    """Map storage quality to the stable VALID/MISSING/PARTIAL/GAP contract."""
    value = str(status or "MISSING")
    if value in VALID_FLOW_STATUSES and has_value:
        return "VALID"
    if value == "PARTIAL_AFTER_GAP" and has_value:
        return "PARTIAL_AFTER_GAP"
    if not has_value and value in {"MISSING", "SOURCE_UNAVAILABLE"}:
        return "MISSING"
    return "GAP"


def aggregate_flow_status(statuses: list[str]) -> str:
    if not statuses or set(statuses) == {"MISSING"}:
        return "MISSING"
    if set(statuses) == {"VALID"}:
        return "VALID"
    if set(statuses) <= {"VALID", "PARTIAL_AFTER_GAP"}:
        return "PARTIAL_AFTER_GAP"
    return "GAP"
