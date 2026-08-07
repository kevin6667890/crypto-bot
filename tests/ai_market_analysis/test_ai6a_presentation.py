from __future__ import annotations

import copy
import json

import pytest

from dashboard.ai_market_analysis.presentation import (
    MAX_PRESENTATION_BYTES,
    PresentationError,
    build_latest_presentation,
    build_report_presentation,
    validate_selection,
)
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository, freeze_report_bundle, migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from tests.ai_market_analysis.ai4_helpers import base_context, position_plan


def seeded(tmp_path, *, mode="FULL", position=False, audit_status="PASSED"):
    path = tmp_path / "reports.db"
    migrate_database(path)
    migrate_audit_database(path)
    reports, audits = ReportRepository(path), AuditRepository(path)
    submitted = ReportService(reports).submit(
        base_context(), mode=mode,
        position_source="USER_DECLARED" if position else "NONE",
        position_plan=position_plan() if position else None, current_mark=1900,
    )
    ReportWorker(reports, lambda request: FakeAIReportProvider(request["model"])).run_once()
    report = reports.get_report(request_id=submitted["request_id"])
    if audit_status:
        bundle = freeze_report_bundle(reports, report["report_id"])
        audit = audit_report(bundle, created_at="2027-11-01T00:01:00Z")
        if audit_status != "PASSED":
            audit = copy.deepcopy(audit)
            audit["status"] = audit_status
            audit["promotion_eligible"] = False
            audit["hard_failures"] = ["UNSUPPORTED_CLAIM"] if audit_status == "FAILED" else ["AUDIT_INTERNAL_ERROR"]
            audit["audit_id"] += "_" + audit_status.lower()
        audits.save_audit(audit)
    return reports, audits, report


def test_passed_projection_is_atomic_compact_and_stable(tmp_path):
    reports, _, report = seeded(tmp_path)
    first = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    second = build_report_presentation(reports, report["report_id"], instrument="ETH-USDT-SWAP", mode="FULL")
    assert first["eligibility"] == "AUDIT_PASSED_SHADOW_ONLY"
    assert first["report"]["context_id"] == first["context_id"]
    assert first["audit_summary"]["status"] == "PASSED"
    assert first["presentation_id"] == second["presentation_id"]
    assert len(json.dumps(first, ensure_ascii=False).encode()) < MAX_PRESENTATION_BYTES
    assert len(first["referenced_facts"]) < len(freeze_report_bundle(reports, report["report_id"])["fact_registry"]["facts"])


@pytest.mark.parametrize("status,eligibility", [
    (None, "AUDIT_NOT_FOUND"), ("FAILED", "AUDIT_FAILED"), ("ERROR", "AUDIT_ERROR"),
])
def test_non_passed_reports_fail_closed(tmp_path, status, eligibility):
    reports, _, _ = seeded(tmp_path, audit_status=status)
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["eligibility"] == eligibility
    assert value["report"] is None
    assert value["referenced_facts"] == []


def test_pending_event_fails_closed(tmp_path):
    reports, audits, report = seeded(tmp_path, audit_status=None)
    audits.event("audit_pending", report["report_id"], "AUDIT_QUEUED")
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["eligibility"] == "AUDIT_PENDING" and value["report"] is None


def test_old_passed_remains_displayable_while_new_report_is_pending(tmp_path):
    reports, _, report = seeded(tmp_path)
    with reports.connect() as conn:
        request = dict(conn.execute("SELECT * FROM ai_report_requests WHERE request_id=?", (report["request_id"],)).fetchone())
        request["request_id"] = request["request_identity"] = "request_new_pending"
        request["created_at"] = "2027-11-01T00:02:00Z"
        columns = tuple(request)
        conn.execute(f"INSERT INTO ai_report_requests({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(request.values()))
        values = dict(conn.execute("SELECT * FROM ai_market_reports WHERE report_id=?", (report["report_id"],)).fetchone())
        values.update(report_id="report_new_pending", request_id=request["request_id"], created_at="2027-11-01T00:03:00Z")
        columns = tuple(values)
        conn.execute(f"INSERT INTO ai_market_reports({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(values.values()))
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["report_id"] == report["report_id"]
    assert value["latest_generated"]["report_id"] == "report_new_pending"
    assert value["latest_generated"]["eligibility"] == "AUDIT_NOT_FOUND"
    assert value["freshness"]["status"] == "CURRENT"


def test_old_audit_schema_fails_closed(tmp_path):
    reports, audits, _ = seeded(tmp_path)
    with audits.connect() as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM ai_report_audits").fetchone()[0])
        payload["audit_schema_version"] = "ai-report-audit-v1"
        conn.execute("UPDATE ai_report_audits SET payload_json=?,payload_hash=?", (json.dumps(payload), stable_hash({k:v for k,v in payload.items() if k!="created_at"})))
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["eligibility"] == "AUDIT_SCHEMA_UPGRADE_REQUIRED" and value["report"] is None


def test_freshness_uses_context_watermark_not_browser_clock(tmp_path):
    reports, _, report = seeded(tmp_path)
    with reports.connect() as conn:
        row = dict(conn.execute("SELECT * FROM ai_market_contexts WHERE context_id=?", (report["context_id"],)).fetchone())
        context = json.loads(row["payload_json"])
        original = context["decision_time"]
        context["enriched_context_id"] = row["context_id"] = "enriched_new_watermark"
        context["decision_time"] = row["decision_time"] = "2027-11-01T02:00:00Z"
        for structure in context["base_context"].get("timeframe_structures", []):
            if isinstance(structure.get("latest_confirmed_bar_timestamp"), int):
                structure["latest_confirmed_bar_timestamp"] += 3600
        row["payload_json"] = json.dumps(context)
        row["payload_hash"] = "test-only-new-watermark"
        row["created_at"] = "2027-11-01T02:00:00Z"
        columns = tuple(row)
        conn.execute(f"INSERT INTO ai_market_contexts({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(row.values()))
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["freshness"]["status"] == "STALE"
    assert value["decision_time"] == original


def test_strict_instrument_mode_and_sol_isolation(tmp_path):
    reports, _, report = seeded(tmp_path)
    for instrument in ("", "ETH-USDT", "DOGE-USDT-SWAP"):
        with pytest.raises(PresentationError, match="INVALID_INSTRUMENT"):
            validate_selection(instrument, "FULL", "zh-CN")
    with pytest.raises(PresentationError, match="PRESENTATION_SELECTION_MISMATCH"):
        build_report_presentation(reports, report["report_id"], instrument="SOL-USDT-SWAP", mode="FULL")
    with pytest.raises(PresentationError, match="PRESENTATION_SELECTION_MISMATCH"):
        build_report_presentation(reports, report["report_id"], instrument="ETH-USDT-SWAP", mode="POSITION_AWARE")


def test_audit_payload_tampering_is_rejected(tmp_path):
    reports, audits, report = seeded(tmp_path)
    with audits.connect() as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM ai_report_audits").fetchone()[0])
        payload["report_hash"] = "forged"
        conn.execute("UPDATE ai_report_audits SET payload_json=?", (json.dumps(payload),))
    with pytest.raises(PresentationError, match="AUDIT_PAYLOAD_HASH_MISMATCH"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")


def test_report_audit_hash_mismatch_is_rejected(tmp_path):
    reports, _, report = seeded(tmp_path)
    with reports.connect() as conn:
        conn.execute("UPDATE ai_market_reports SET response_hash='forged' WHERE report_id=?", (report["report_id"],))
    with pytest.raises(PresentationError, match="REPORT_AUDIT_HASH_MISMATCH"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")


def test_position_payload_is_redacted_by_default(tmp_path):
    reports, _, _ = seeded(tmp_path, mode="POSITION_AWARE", position=True)
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "POSITION_AWARE")
    assert value["position_summary"]["source"] == "USER_DECLARED"
    assert value["position_summary"]["sensitive_details_available"] is True
    assert "original_quantity" not in value["position_summary"]
    assert "average_cost" not in value["position_summary"]


def test_query_plan_uses_presentation_indexes(tmp_path):
    reports, _, _ = seeded(tmp_path)
    with reports.connect() as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT p.* FROM ai_market_reports p JOIN ai_report_requests r ON r.request_id=p.request_id WHERE r.instrument=? AND p.mode=? AND p.language=? ORDER BY p.created_at DESC,p.report_id DESC LIMIT 50", ("ETH-USDT-SWAP", "FULL", "zh-CN")).fetchall()
    assert any("INDEX" in str(tuple(row)) for row in plan)
