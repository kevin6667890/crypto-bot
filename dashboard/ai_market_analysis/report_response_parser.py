"""Strict JSON report response parser."""
from __future__ import annotations
import json
from typing import Any

TOP_FIELDS={"schema_version","context_id","request_id","mode","language","headline","market_phase","directional_bias","confidence","sections","key_levels","scenarios","position_guidance","unsupported_claims","data_warnings","citations","model","prompt_version","audit_status"}
SECTION_FIELDS={"section_id","title","body","fact_refs","level_refs","scenario_refs","macro_refs","position_refs","uncertainties"}


class ReportParseError(ValueError): pass


def parse_report_response(raw: str) -> dict[str, Any]:
    try: value=json.loads(raw)
    except json.JSONDecodeError as error: raise ReportParseError(f"invalid JSON at {error.pos}") from None
    if not isinstance(value,dict): raise ReportParseError("response must be an object")
    if set(value)!=TOP_FIELDS: raise ReportParseError(f"response fields mismatch: {sorted(set(value)^TOP_FIELDS)}")
    if not isinstance(value["sections"],list): raise ReportParseError("sections must be an array")
    for section in value["sections"]:
        if not isinstance(section,dict) or set(section)!=SECTION_FIELDS: raise ReportParseError("section fields mismatch")
    return value
