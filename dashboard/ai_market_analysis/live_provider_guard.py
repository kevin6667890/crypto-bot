"""Durable, fail-closed AI-6B live-provider kill switch.

The switch is deliberately independent of process environment.  Once the
state file exists every subsequent provider call is refused, including calls
from an already-running worker.  Reset is intentionally not implemented.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .report_provider import ProviderError

DEFAULT_KILL_SWITCH = Path("/var/lib/ai-report/live-provider-disabled.json")
HARD_STOP_EVENTS = frozenset({
    "WRONG_SYMBOL", "WRONG_MODE", "CONTEXT_MISMATCH", "AUDIT_MISMATCH",
    "REGISTRY_MISMATCH", "UNAUDITED_BODY_DISPLAY", "POSITION_LEAK",
    "SECRET_EXPOSURE", "DUPLICATE_PROVIDER_CHARGE", "BUDGET_BREACH",
    "RUNAWAY_RETRY", "QUEUE_RUNAWAY", "CRITICAL_WARNING_HIDDEN",
    "ORDER_PATH_CHANGE", "ROUTER_CHANGE", "COLLECTOR_CHANGE",
    "AGGREGATION_CHANGE", "DB_CORRUPTION", "DISK_CRITICAL",
    "UNSUPPORTED_NUMERIC_CLAIM", "REFERENCE_SUPPORT_FAILURE",
    "UNKNOWN_CHARGE_AUTOMATIC_RETRY", "SCHEMA_CORRUPTION",
    "PROVIDER_OUTPUT_TRUNCATION", "UNEXPECTED_POSITION_DATA",
})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def switch_path(path: str | Path | None = None) -> Path:
    return Path(path or os.getenv("AI_REPORT_KILL_SWITCH_FILE", str(DEFAULT_KILL_SWITCH))).resolve()


def status(path: str | Path | None = None) -> dict[str, Any]:
    selected = switch_path(path)
    if not selected.exists():
        return {"live_provider_disabled": False, "path": str(selected)}
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {"event": "UNREADABLE_KILL_SWITCH", "tripped_at": None}
    return {"live_provider_disabled": True, "path": str(selected), **value}


def trip(event: str, *, path: str | Path | None = None, evidence_id: str | None = None) -> dict[str, Any]:
    normalized = event.strip().upper()
    if normalized not in HARD_STOP_EVENTS:
        raise ValueError("UNKNOWN_HARD_STOP_EVENT")
    selected = switch_path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    if selected.exists():
        return status(selected)
    payload = {
        "schema_version": "ai6b-kill-switch-v1",
        "live_provider_disabled": True,
        "event": normalized,
        "tripped_at": _now(),
        "evidence_id": evidence_id,
    }
    handle, temporary_name = tempfile.mkstemp(prefix=".kill-switch-", dir=selected.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        try:
            os.link(temporary_name, selected)
        except FileExistsError:
            pass
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return status(selected)


def assert_live_provider_allowed(path: str | Path | None = None) -> None:
    if os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "false").lower() != "true":
        raise ProviderError("LIVE_PROVIDER_DISABLED", retryable=False)
    if switch_path(path).exists():
        raise ProviderError("LIVE_PROVIDER_KILL_SWITCHED", retryable=False)


def trip_if_armed(event: str, *, evidence_id: str | None = None) -> dict[str, Any] | None:
    if os.getenv("AI6B_KILL_SWITCH_AUTOMATION_ENABLED", "false").lower() != "true":
        return None
    return trip(event, evidence_id=evidence_id)
