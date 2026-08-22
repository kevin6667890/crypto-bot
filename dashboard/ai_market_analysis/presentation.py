"""Atomic, compact and fail-closed projection for the AI-6A Shadow UI."""
from __future__ import annotations

import json, os
from datetime import datetime, timezone
from typing import Any

from .canonical import stable_hash
from .report_audit_policy import POLICY
from .report_registry_snapshot import validate_registry_snapshot
from .versions import (
    AI_FRESHNESS_POLICY_VERSION,
    AI_PRESENTATION_SCHEMA_VERSION,
    AI_PRESENTATION_VERSION,
    AI_REPORT_AUDIT_VERSION,
    SUPPORTED_INSTRUMENTS,
)
from .presentation_narrative import project_display_narrative

MODES = ("QUICK", "FULL", "POSITION_AWARE")
LANGUAGES = ("zh-CN", "en")
ELIGIBILITIES = (
    "AUDIT_PENDING", "AUDIT_PASSED_SHADOW_ONLY", "AUDIT_FAILED", "AUDIT_ERROR",
    "AUDIT_NOT_FOUND", "AUDIT_SCHEMA_UPGRADE_REQUIRED",
)
FRESHNESS = ("CURRENT", "AGING", "STALE", "SUPERSEDED", "UNKNOWN")
MAX_PRESENTATION_BYTES = 500_000
TARGET_PRESENTATION_BYTES = 250_000
MAX_PRESENTATION_DEPTH = 32
MAX_PRESENTATION_STRING_BYTES = 100_000


class PresentationError(RuntimeError):
    """Safe coded presentation failure; messages never include storage details."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def _loads(value: str | None, default: Any = None) -> Any:
    return json.loads(value) if value else default


def _iso_epoch(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def validate_selection(instrument: str, mode: str, language: str) -> None:
    if instrument not in SUPPORTED_INSTRUMENTS:
        raise PresentationError("INVALID_INSTRUMENT", 400)
    if mode not in MODES:
        raise PresentationError("INVALID_MODE", 400)
    if language not in LANGUAGES:
        raise PresentationError("INVALID_LANGUAGE", 400)


def _audit_eligibility(audit: dict[str, Any] | None, event: str | None) -> str:
    if audit:
        if (audit.get("audit_schema_version") != AI_REPORT_AUDIT_VERSION
                or audit.get("audit_policy_version") != POLICY["version"]):
            return "AUDIT_SCHEMA_UPGRADE_REQUIRED"
        return {
            "PASSED": "AUDIT_PASSED_SHADOW_ONLY",
            "FAILED": "AUDIT_FAILED",
            "ERROR": "AUDIT_ERROR",
        }.get(str(audit.get("status")), "AUDIT_ERROR")
    return "AUDIT_PENDING" if event in {
        "AUDIT_QUEUED", "AUDIT_RUNNING", "AUDIT_INTERRUPTED", "AUDIT_CANCEL_REQUESTED"
    } else "AUDIT_NOT_FOUND"


def _compact_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return {k: fact.get(k) for k in (
        "fact_id", "category", "claim_scope", "label", "display_value", "value", "unit",
        "timestamp", "quality", "source", "context_pointer", "provenance",
    ) if k in fact}


def _refs(report: dict[str, Any], key: str) -> set[str]:
    result: set[str] = set()
    for section in report.get("sections", []):
        values = section.get(key, [])
        if isinstance(values, list):
            result.update(str(value) for value in values)
    return result


def _latest_market_time(context: dict[str, Any]) -> int | None:
    base = context.get("base_context", {})
    values: list[int] = []
    for structure in base.get("timeframe_structures", []):
        stamp = structure.get("latest_confirmed_bar_timestamp")
        if isinstance(stamp, (int, float)):
            values.append(int(stamp))
    facts = base.get("timeframe_facts", {})
    if isinstance(facts, dict):
        for value in facts.values():
            stamp = value.get("latest_confirmed_bar_timestamp") if isinstance(value, dict) else None
            if isinstance(stamp, (int, float)):
                values.append(int(stamp))
    return max(values) if values else _iso_epoch(context.get("decision_time"))


def _freshness(*, decision_time: Any, report_watermark: int | None,
               current_watermark: int | None, superseded: bool,
               quality: str | None) -> dict[str, Any]:
    if superseded:
        state, bars = "SUPERSEDED", None
    elif report_watermark is None or current_watermark is None:
        state, bars = "UNKNOWN", None
    else:
        bars = max(0, (current_watermark - report_watermark) // 900)
        if bars == 0 and quality not in {"MISSING", "UNKNOWN"}:
            state = "CURRENT"
        elif bars <= 2:
            state = "AGING"
        else:
            state = "STALE"
    return {
        "status": state,
        "policy_version": AI_FRESHNESS_POLICY_VERSION,
        "confirmed_15m_bars_behind": bars,
        "decision_time": decision_time,
        "report_market_time": report_watermark,
        "current_market_time": current_watermark,
        "quality": quality or "UNKNOWN",
    }


def _audit_summary(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not audit:
        return None
    score = audit.get("scorecard", {})
    return {
        "audit_id": audit.get("audit_id"), "status": audit.get("status"),
        "overall_score": score.get("overall"), "promotion_eligible": bool(audit.get("promotion_eligible")),
        "ratios": score.get("ratios", {}), "hard_failures": audit.get("hard_failures", []),
        "hard_failure_count": len(audit.get("hard_failures", [])), "warnings": audit.get("warnings", []),
        "policy_version": audit.get("audit_policy_version"), "audited_at": audit.get("created_at"),
    }


def _position_summary(context: dict[str, Any]) -> dict[str, Any]:
    position = context.get("position_context") or {"source": "NONE"}
    # Quantity/cost/stop are deliberately not returned in the initial presentation payload.
    return {k: position.get(k) for k in (
        "source", "side", "status", "original_timeframe", "plan_completed", "plan_completion_ratio",
        "discipline_warnings", "limitations",
    ) if k in position} | {
        "sensitive_details_available": position.get("source") in {"PAPER", "USER_DECLARED"}
    }


def _health(conn: Any) -> dict[str, Any]:
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0]); page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    active = count("SELECT COUNT(*) FROM ai_report_request_events e WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_request_events x WHERE x.request_id=e.request_id) AND e.event_type IN ('QUEUED','RUNNING','RETRY_SCHEDULED','INTERRUPTED')")
    queued = conn.execute("SELECT MIN(r.created_at) FROM ai_report_requests r JOIN ai_report_request_events e ON e.request_id=r.request_id WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_request_events x WHERE x.request_id=e.request_id) AND e.event_type IN ('QUEUED','RETRY_SCHEDULED','INTERRUPTED')").fetchone()[0]
    tokens = conn.execute("SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0) FROM ai_report_attempts WHERE started_at>=?", (datetime.now(timezone.utc).date().isoformat()+"T00:00:00Z",)).fetchone()
    last_report = conn.execute("SELECT MAX(created_at) FROM ai_market_reports").fetchone()[0]
    last_audit = conn.execute("SELECT MAX(created_at) FROM ai_report_audits WHERE status='PASSED'").fetchone()[0]
    queued_age = None
    if queued:
        stamp = _iso_epoch(queued)
        queued_age = max(0, int(datetime.now(timezone.utc).timestamp()) - stamp) if stamp is not None else None
    return {
        "reports_enabled": os.getenv("AI_MARKET_REPORTS_ENABLED", "false").lower() == "true", "shadow_only": True,
        "worker_enabled": os.getenv("AI_MARKET_REPORT_WORKER_ENABLED", "false").lower() == "true",
        "audit_enabled": os.getenv("AI_REPORT_AUDIT_ENABLED", "false").lower() == "true",
        "provider_configured": bool(os.getenv("AI_REPORT_MODEL")),
        "live_provider_allowed": os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "false").lower() == "true",
        "queue_depth": count("SELECT COUNT(*) FROM ai_report_request_events e WHERE e.event_id=(SELECT MAX(x.event_id) FROM ai_report_request_events x WHERE x.request_id=e.request_id) AND e.event_type IN ('QUEUED','RETRY_SCHEDULED','INTERRUPTED')"),
        "active_requests": active, "oldest_queued_age": queued_age,
        "last_report_success": last_report, "last_audit_success": last_audit,
        "failed_count": count("SELECT COUNT(*) FROM ai_report_request_events WHERE event_type='FAILED_FINAL'"),
        "budget_blocked": count("SELECT COUNT(*) FROM ai_report_request_events WHERE event_type='BUDGET_BLOCKED'"),
        "daily_tokens": {"input": tokens[0], "output": tokens[1], "total": tokens[2]},
        "db_size": page_count * page_size,
        "schema_versions": {"report": "ai-market-report-db-v1", "presentation": AI_PRESENTATION_SCHEMA_VERSION},
    }


def _row_report(row: Any) -> dict[str, Any]:
    value = dict(row)
    value["response"] = _loads(value.pop("response_json"), {})
    return value


def _read_audit(conn: Any, report_id: str) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute(
        "SELECT * FROM ai_report_audits WHERE report_id=? ORDER BY created_at DESC,audit_id DESC LIMIT 1",
        (report_id,),
    ).fetchone()
    event = conn.execute(
        "SELECT event_type FROM ai_report_audit_events WHERE report_id=? ORDER BY event_id DESC LIMIT 1",
        (report_id,),
    ).fetchone()
    audit = _loads(row["payload_json"]) if row else None
    if audit is not None:
        if stable_hash({k: v for k, v in audit.items() if k != "created_at"}) != row["payload_hash"]:
            raise PresentationError("AUDIT_PAYLOAD_HASH_MISMATCH")
        audit["_stored_payload_hash"] = row["payload_hash"]
        audit["_stored_audit_id"] = row["audit_id"]
        audit["_stored_report_hash"] = row["report_hash"]
        audit["_stored_context_id"] = row["context_id"]
        audit["_stored_report_id"] = row["report_id"]
    return (audit, event[0] if event else None)


def _latest_rows(conn: Any, instrument: str, mode: str, language: str) -> list[Any]:
    return conn.execute(
        "SELECT p.*,r.instrument,r.registry_snapshot_id FROM ai_market_reports p "
        "JOIN ai_report_requests r ON r.request_id=p.request_id "
        "WHERE r.instrument=? AND p.mode=? AND p.language=? ORDER BY p.created_at DESC,p.report_id DESC LIMIT 50",
        (instrument, mode, language),
    ).fetchall()


def build_latest_presentation(repository: Any, instrument: str, mode: str,
                              language: str = "zh-CN") -> dict[str, Any]:
    validate_selection(instrument, mode, language)
    with repository.connect() as conn:
        conn.execute("BEGIN")
        rows = _latest_rows(conn, instrument, mode, language)
        if not rows:
            raise PresentationError("PRESENTATION_NOT_FOUND", 404)
        latest = _row_report(rows[0])
        latest_audit, latest_event = _read_audit(conn, latest["report_id"])
        latest_status = {
            "report_id": latest["report_id"], "request_id": latest["request_id"],
            "eligibility": _audit_eligibility(latest_audit, latest_event), "queue_status": latest_event,
            "decision_time": latest["response"].get("decision_time") or None,
        }
        selected = latest
        selected_audit, selected_event = latest_audit, latest_event
        if latest_status["eligibility"] != "AUDIT_PASSED_SHADOW_ONLY":
            selected = None
            for row in rows[1:]:
                candidate = _row_report(row)
                audit, event = _read_audit(conn, candidate["report_id"])
                if _audit_eligibility(audit, event) == "AUDIT_PASSED_SHADOW_ONLY":
                    selected, selected_audit, selected_event = candidate, audit, event
                    break
            if selected is None:
                return _status_only(latest, latest_audit, latest_event, latest_status, mode, language, conn)
        # A newer pending/failed report does not supersede a passed report. Only
        # another newer passed audit may produce SUPERSEDED.
        return _project(conn, selected, selected_audit, selected_event, latest_status, superseded=False)


def build_report_presentation(repository: Any, report_id: str, *, instrument: str,
                              mode: str, language: str = "zh-CN") -> dict[str, Any]:
    validate_selection(instrument, mode, language)
    with repository.connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT p.*,r.instrument,r.registry_snapshot_id FROM ai_market_reports p "
            "JOIN ai_report_requests r ON r.request_id=p.request_id WHERE p.report_id=?",
            (report_id,),
        ).fetchone()
        if not row:
            raise PresentationError("PRESENTATION_NOT_FOUND", 404)
        report = _row_report(row)
        if report["instrument"] != instrument or report["mode"] != mode or report["language"] != language:
            raise PresentationError("PRESENTATION_SELECTION_MISMATCH", 404)
        audit, event = _read_audit(conn, report_id)
        latest = _latest_rows(conn, instrument, mode, language)
        latest_status = {"report_id": latest[0]["report_id"] if latest else report_id,
                         "eligibility": _audit_eligibility(*_read_audit(conn, latest[0]["report_id"])) if latest else _audit_eligibility(audit, event)}
        if _audit_eligibility(audit, event) != "AUDIT_PASSED_SHADOW_ONLY":
            return _status_only(report, audit, event, latest_status, mode, language, conn)
        newer_passed = any(
            r["report_id"] != report_id and _audit_eligibility(*_read_audit(conn, r["report_id"])) == "AUDIT_PASSED_SHADOW_ONLY"
            for r in latest if r["created_at"] > report["created_at"]
        )
        return _project(conn, report, audit, event, latest_status, superseded=newer_passed)


def _status_only(report: dict[str, Any], audit: dict[str, Any] | None, event: str | None,
                 latest_status: dict[str, Any], mode: str, language: str, conn: Any) -> dict[str, Any]:
    eligibility = _audit_eligibility(audit, event)
    value = {
        "presentation_schema_version": AI_PRESENTATION_SCHEMA_VERSION,
        "presentation_id": stable_hash({"report_id": report["report_id"], "report_hash": report["response_hash"],
            "audit_id": audit.get("audit_id") if audit else None, "audit_payload_hash": audit.get("_stored_payload_hash") if audit else None,
            "registry_snapshot_id": report.get("registry_snapshot_id"), "eligibility": eligibility,
            "freshness_policy_version": AI_FRESHNESS_POLICY_VERSION, "presentation_version": AI_PRESENTATION_VERSION}),
        "instrument": report["instrument"], "mode": mode, "language": language,
        "report_id": report["report_id"], "request_id": report["request_id"], "context_id": report["context_id"],
        "registry_snapshot_id": report.get("registry_snapshot_id"), "audit_id": audit.get("audit_id") if audit else None,
        "eligibility": eligibility, "latest_generated": latest_status, "freshness": {"status": "UNKNOWN", "policy_version": AI_FRESHNESS_POLICY_VERSION},
        "report": None, "audit_summary": _audit_summary(audit), "referenced_facts": [], "referenced_levels": [],
        "referenced_scenarios": [], "referenced_macro": [], "position_summary": None, "data_warnings": [],
        "health_summary": _health(conn), "source_versions": {}, "presentation_hash": None,
    }
    value["presentation_hash"] = stable_hash({k: v for k, v in value.items() if k != "presentation_hash"})
    return _bounded(value)


def _project(conn: Any, report: dict[str, Any], audit: dict[str, Any] | None, event: str | None,
             latest_status: dict[str, Any], *, superseded: bool) -> dict[str, Any]:
    if not audit:
        raise PresentationError("AUDIT_NOT_FOUND")
    # The persisted response is the immutable audit input.  The UI receives a
    # separate post-audit projection so Provider-era PENDING wording cannot
    # contradict the audit that made this report display-eligible.
    response = project_display_narrative(report["response"], audit_status=str(audit.get("status") or ""))
    if audit.get("_stored_audit_id") != audit.get("audit_id"):
        raise PresentationError("AUDIT_ID_MISMATCH")
    if response.get("directional_bias") not in {"BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN"}:
        raise PresentationError("UNSUPPORTED_PRESENTATION_ENUM")
    if response.get("confidence") not in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}:
        raise PresentationError("UNSUPPORTED_PRESENTATION_ENUM")
    if audit.get("_stored_report_id") != report["report_id"] or audit.get("_stored_context_id") != report["context_id"]:
        raise PresentationError("REPORT_AUDIT_CONTEXT_MISMATCH")
    if audit.get("report_id") != report["report_id"] or audit.get("report_hash") != report["response_hash"]:
        raise PresentationError("REPORT_AUDIT_HASH_MISMATCH")
    if audit.get("context_id") != report["context_id"] or response.get("context_id") != report["context_id"]:
        raise PresentationError("REPORT_AUDIT_CONTEXT_MISMATCH")
    snapshot_row = conn.execute("SELECT * FROM ai_report_registry_snapshots WHERE registry_snapshot_id=? AND request_id=?",
                                (report.get("registry_snapshot_id"), report["request_id"])).fetchone()
    context_row = conn.execute("SELECT payload_json,quality,decision_time FROM ai_market_contexts WHERE context_id=? AND instrument=?",
                               (report["context_id"], report["instrument"])).fetchone()
    if not snapshot_row or not context_row:
        raise PresentationError("REGISTRY_OR_CONTEXT_MISMATCH")
    snapshot = dict(snapshot_row)
    registry = _loads(snapshot.pop("fact_registry_json"), {})
    snapshot["fact_registry"] = registry
    snapshot["numeric_registry"] = _loads(snapshot.pop("numeric_registry_json"), [])
    snapshot["source_versions"] = _loads(snapshot.pop("source_versions_json"), {})
    snapshot["identity_input"] = {k: snapshot[k] for k in (
        "snapshot_version", "enriched_context_id", "fact_registry_hash", "numeric_registry_hash",
        "prompt_hash", "fact_registry_version", "numeric_registry_version", "source_versions_hash",
    )}
    if validate_registry_snapshot(snapshot):
        raise PresentationError("REGISTRY_IDENTITY_MISMATCH")
    if audit.get("registry_snapshot_id") != snapshot["registry_snapshot_id"] or snapshot["enriched_context_id"] != report["context_id"]:
        raise PresentationError("REGISTRY_IDENTITY_MISMATCH")
    if (audit.get("fact_registry_hash") != snapshot["fact_registry_hash"]
            or audit.get("numeric_registry_hash") != snapshot["numeric_registry_hash"]
            or audit.get("prompt_hash") != snapshot["prompt_hash"]):
        raise PresentationError("REGISTRY_AUDIT_MISMATCH")
    context = _loads(context_row[0], {})
    if (os.getenv("AI6B_PRIVACY_SCOPE_ENFORCED", "false").lower() == "true"
            and context.get("position_context", {}).get("source") not in {"NONE", "PAPER"}):
        from .live_provider_guard import trip_if_armed
        trip_if_armed("POSITION_LEAK", evidence_id="POSITION_SOURCE_OUTSIDE_CANARY_SCOPE")
        raise PresentationError("POSITION_SOURCE_OUTSIDE_CANARY_SCOPE", 403)
    fact_ids = _refs(response, "fact_refs")
    facts = [_compact_fact(f) for f in registry.get("facts", []) if f.get("fact_id") in fact_ids]
    level_ids, scenario_ids, macro_ids = (_refs(response, name) for name in ("level_refs", "scenario_refs", "macro_refs"))
    levels = [x for x in response.get("key_levels", []) if x.get("level_id") in level_ids]
    scenarios = [x for x in response.get("scenarios", []) if x.get("scenario_id") in scenario_ids]
    macro = [x for x in context.get("macro_context", {}).get("items", []) if x.get("evidence_id") in macro_ids or x.get("id") in macro_ids]
    watermark = _latest_market_time(context)
    current = conn.execute("SELECT payload_json FROM ai_market_contexts WHERE instrument=? ORDER BY decision_time DESC,context_id DESC LIMIT 1",
                           (report["instrument"],)).fetchone()
    current_watermark = _latest_market_time(_loads(current[0], {})) if current else None
    freshness = _freshness(decision_time=context.get("decision_time"), report_watermark=watermark,
                           current_watermark=current_watermark, superseded=superseded, quality=context_row[1])
    presentation_input = {
        "report_id": report["report_id"], "report_hash": report["response_hash"], "audit_id": audit["audit_id"],
        "audit_payload_hash": audit.get("_stored_payload_hash"), "registry_snapshot_id": snapshot["registry_snapshot_id"],
        "eligibility": "AUDIT_PASSED_SHADOW_ONLY", "freshness_policy_version": AI_FRESHNESS_POLICY_VERSION,
        "freshness_status": freshness["status"], "presentation_version": AI_PRESENTATION_VERSION,
    }
    value = {
        "presentation_schema_version": AI_PRESENTATION_SCHEMA_VERSION, "presentation_id": stable_hash(presentation_input),
        "instrument": report["instrument"], "mode": report["mode"], "language": report["language"],
        "report_id": report["report_id"], "request_id": report["request_id"], "context_id": report["context_id"],
        "registry_snapshot_id": snapshot["registry_snapshot_id"], "audit_id": audit["audit_id"],
        "report_schema_version": response.get("schema_version"), "audit_schema_version": audit.get("audit_schema_version"),
        "decision_time": context.get("decision_time"), "latest_confirmed_market_time": watermark,
        "generated_at": report.get("created_at"), "audited_at": audit.get("created_at"),
        "eligibility": "AUDIT_PASSED_SHADOW_ONLY", "freshness": freshness,
        "latest_generated": latest_status, "report": response, "audit_summary": _audit_summary(audit),
        "referenced_facts": facts, "referenced_levels": levels, "referenced_scenarios": scenarios,
        "referenced_macro": macro, "position_summary": _position_summary(context),
        "data_warnings": response.get("data_warnings", []), "health_summary": _health(conn),
        "source_versions": response.get("source_versions", {}), "presentation_hash": None,
    }
    value["presentation_hash"] = stable_hash({k: v for k, v in value.items() if k != "presentation_hash"})
    return _bounded(value)


def _bounded(value: dict[str, Any]) -> dict[str, Any]:
    _validate_payload_shape(value)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_PRESENTATION_BYTES:
        raise PresentationError("PRESENTATION_PAYLOAD_TOO_LARGE", 413)
    return value


def _validate_payload_shape(value: Any, depth: int = 0) -> None:
    if depth > MAX_PRESENTATION_DEPTH:
        raise PresentationError("PRESENTATION_JSON_TOO_DEEP", 413)
    if isinstance(value, str) and len(value.encode("utf-8")) > MAX_PRESENTATION_STRING_BYTES:
        raise PresentationError("PRESENTATION_STRING_TOO_LONG", 413)
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_payload_shape(key, depth + 1); _validate_payload_shape(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_payload_shape(item, depth + 1)


def position_details(repository: Any, report_id: str, *, instrument: str, mode: str) -> dict[str, Any]:
    """Explicit authenticated second fetch for sensitive position fields."""
    validate_selection(instrument, mode, "zh-CN")
    with repository.connect() as conn:
        row = conn.execute("SELECT p.context_id,r.instrument,p.mode FROM ai_market_reports p JOIN ai_report_requests r ON r.request_id=p.request_id WHERE p.report_id=?", (report_id,)).fetchone()
        if not row or row["instrument"] != instrument or row["mode"] != mode:
            raise PresentationError("PRESENTATION_SELECTION_MISMATCH", 404)
        audit, event = _read_audit(conn, report_id)
        if _audit_eligibility(audit, event) != "AUDIT_PASSED_SHADOW_ONLY":
            raise PresentationError("POSITION_DETAILS_NOT_ELIGIBLE", 403)
        context = conn.execute("SELECT payload_json FROM ai_market_contexts WHERE context_id=?", (row["context_id"],)).fetchone()
        position = _loads(context[0], {}).get("position_context", {}) if context else {}
        if os.getenv("AI6B_PRIVACY_SCOPE_ENFORCED","false").lower()=="true" and position.get("source") not in {"NONE","PAPER"}:
            from .live_provider_guard import trip_if_armed
            trip_if_armed("POSITION_LEAK",evidence_id="POSITION_SOURCE_OUTSIDE_CANARY_SCOPE")
            raise PresentationError("POSITION_SOURCE_OUTSIDE_CANARY_SCOPE",403)
        return {k: position.get(k) for k in ("source", "side", "average_cost", "original_quantity", "remaining_quantity", "original_stop", "original_targets", "original_timeframe", "plan_completed", "plan_completion_ratio", "discipline_warnings") if k in position}
