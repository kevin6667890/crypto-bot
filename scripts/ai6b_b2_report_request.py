#!/usr/bin/env python3
"""Canonical HTTP request serializer for the AI-6B B2 production canary."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

REPORT_ENDPOINT = "/api/ai-market-analysis/v1/reports"
REPORT_METHOD = "POST"


def serialize_b2_report_request(
    instrument: str,
    mode: str,
    position_source: str,
    decision_time: datetime | str,
) -> dict[str, Any]:
    """Return the exact B2 body; authorization is deliberately out of scope."""
    if isinstance(decision_time, datetime):
        value = decision_time.isoformat().replace("+00:00", "Z")
    else:
        value = decision_time
    return {
        "instrument": instrument,
        "decision_time": value,
        "mode": mode,
        "language": "zh-CN",
        "position_source": position_source,
        "provider": "fake",
        "model": "fake-ai4",
    }


def serialize_b2_http_request(
    instrument: str,
    mode: str,
    position_source: str,
    decision_time: datetime | str,
    *,
    authorization: str,
) -> dict[str, Any]:
    body = serialize_b2_report_request(instrument,mode,position_source,decision_time)
    wire = json.dumps(body,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    return {
        "method": REPORT_METHOD,
        "endpoint": REPORT_ENDPOINT,
        "headers": {
            "Authorization": f"Bearer {authorization}",
            "Content-Type": "application/json",
            "Content-Length": str(len(wire)),
        },
        "body": wire,
    }
