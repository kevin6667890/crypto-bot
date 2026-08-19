"""Durable, fail-closed AI-6B live-provider kill switch.

The switch is deliberately independent of process environment.  Once the
state file exists every subsequent provider call is refused, including calls
from an already-running worker. Recovery requires an exact evidence-bound,
durably archived state transition and is not exposed through the HTTP API.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
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
RECOVERY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


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


def recover(*, path: str | Path | None = None, expected_event: str,
            expected_sha256: str, approval_id: str, evidence_id: str) -> dict[str, Any]:
    """Perform an evidence-bound, durable ACTIVE -> RECOVERED transition.

    This is intentionally not an API reset. Operators must bind the transition
    to the exact immutable switch bytes and preserve both authorization and the
    original trip record on the same filesystem.
    """
    selected = switch_path(path)
    if not RECOVERY_ID.fullmatch(approval_id) or not RECOVERY_ID.fullmatch(evidence_id):
        raise ValueError("INVALID_RECOVERY_ID")
    if not selected.exists():
        raise ValueError("KILL_SWITCH_NOT_ACTIVE")
    raw = selected.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("KILL_SWITCH_HASH_MISMATCH")
    try:
        prior = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("UNREADABLE_KILL_SWITCH") from error
    if prior.get("event") != expected_event.strip().upper():
        raise ValueError("KILL_SWITCH_EVENT_MISMATCH")
    recovered_at = _now()
    suffix = recovered_at.replace(":", "").replace("-", "")
    archive = selected.with_name(f"{selected.stem}.recovered-{suffix}-{digest[:12]}.json")
    authorization = selected.with_name(f"{selected.stem}.recovery-{suffix}-{digest[:12]}.json")
    payload = {
        "schema_version": "ai6b-kill-switch-recovery-v1", "state": "RECOVERED",
        "recovered_at": recovered_at, "approval_id": approval_id,
        "evidence_id": evidence_id, "prior_event": prior.get("event"),
        "prior_evidence_id": prior.get("evidence_id"), "prior_switch_sha256": digest,
        "archive": archive.name,
    }
    handle = os.open(authorization, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":")); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(selected, archive)
    except Exception:
        authorization.unlink(missing_ok=True)
        raise
    return {**payload, "live_provider_disabled": False, "path": str(selected)}


def trip_if_armed(event: str, *, evidence_id: str | None = None) -> dict[str, Any] | None:
    if os.getenv("AI6B_KILL_SWITCH_AUTOMATION_ENABLED", "false").lower() != "true":
        return None
    return trip(event, evidence_id=evidence_id)
