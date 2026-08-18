"""Strict JSON report response parser."""
from __future__ import annotations
import json
from typing import Any
from .report_response_contract import (
    LEVEL_PROJECTION_FIELDS, SCENARIO_PROJECTION_FIELDS, SECTION_FIELDS,
    SERVICE_REQUEST_ID_SENTINEL, SERVICE_SOURCE_VERSIONS_SENTINEL, TOP_FIELDS,
)


class ReportParseError(ValueError): pass


def parse_report_response(raw: str, *, expected_request_id: str | None = None,
                          expected_source_versions: dict[str, Any] | None = None) -> dict[str, Any]:
    try: value=json.loads(raw)
    except json.JSONDecodeError as error: raise ReportParseError(f"invalid JSON at {error.pos}") from None
    if not isinstance(value,dict): raise ReportParseError("response must be an object")
    if set(value)!=set(TOP_FIELDS): raise ReportParseError(f"response fields mismatch: {sorted(set(value)^set(TOP_FIELDS))}")
    if value["request_id"] == SERVICE_REQUEST_ID_SENTINEL:
        if not expected_request_id: raise ReportParseError("service request id unavailable")
        value["request_id"] = expected_request_id
    elif expected_request_id and value["request_id"] != expected_request_id:
        raise ReportParseError("request_id contract mismatch")
    if value["source_versions"] == SERVICE_SOURCE_VERSIONS_SENTINEL:
        if expected_source_versions is None: raise ReportParseError("service source versions unavailable")
        value["source_versions"] = expected_source_versions
    elif expected_source_versions is not None and value["source_versions"] != expected_source_versions:
        raise ReportParseError("source_versions contract mismatch")
    if not isinstance(value["sections"],list): raise ReportParseError("sections must be an array")
    for section in value["sections"]:
        if not isinstance(section,dict) or set(section)!=set(SECTION_FIELDS): raise ReportParseError("section fields mismatch")
    if not isinstance(value["key_levels"],list) or any(not isinstance(item,dict) or set(item)!=set(LEVEL_PROJECTION_FIELDS) for item in value["key_levels"]):
        raise ReportParseError("key level projection fields mismatch")
    if not isinstance(value["scenarios"],list) or any(not isinstance(item,dict) or set(item)!=set(SCENARIO_PROJECTION_FIELDS) for item in value["scenarios"]):
        raise ReportParseError("scenario projection fields mismatch")
    return value
