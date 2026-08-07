from __future__ import annotations

import copy
import json
import socket
import sqlite3
from datetime import datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import pytest

from dashboard import paper_api
from dashboard.ai_market_analysis import presentation as projection
from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.presentation import PresentationError, build_latest_presentation, build_report_presentation, position_details, validate_selection
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository, freeze_report_bundle
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_service import ReportService
from tests.ai_market_analysis.ai4_helpers import base_context, macro_items
from tests.ai_market_analysis.test_ai6a_presentation import seeded
from tests.ai_market_analysis.test_ai6a_presentation_handler import handler


def _rewrite_audit(audits: AuditRepository, mutate) -> None:
    with audits.connect() as conn:
        row = conn.execute("SELECT audit_id,payload_json FROM ai_report_audits ORDER BY created_at DESC LIMIT 1").fetchone()
        payload = json.loads(row["payload_json"]); mutate(payload)
        digest = stable_hash({key: value for key, value in payload.items() if key != "created_at"})
        conn.execute("UPDATE ai_report_audits SET payload_json=?,payload_hash=? WHERE audit_id=?", (json.dumps(payload), digest, row["audit_id"]))


def _second_passed(reports, audits: AuditRepository):
    context = base_context()
    original = datetime.fromisoformat(context["decision_time"].replace("Z", "+00:00"))
    context["decision_time"] = (original + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    context["context_id"] = "ai6ac-future-base-context"
    submitted = ReportService(reports).submit(context, mode="FULL")
    ReportWorker(reports, lambda request: FakeAIReportProvider(request["model"])).run_once()
    report = reports.get_report(request_id=submitted["request_id"])
    audit = audit_report(freeze_report_bundle(reports, report["report_id"]), created_at="2099-01-01T00:00:00Z")
    audits.save_audit(audit)
    with reports.connect() as conn:
        conn.execute("UPDATE ai_market_reports SET created_at='2099-01-01T00:00:00Z' WHERE report_id=?", (report["report_id"],))
    return report


def test_matrix_01_passed_returns_body(tmp_path):
    reports, _, _ = seeded(tmp_path); value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL"); assert value["eligibility"]=="AUDIT_PASSED_SHADOW_ONLY" and value["report"] is not None

def test_matrix_02_pending_body_is_null(tmp_path):
    reports, audits, report=seeded(tmp_path,audit_status=None);audits.event("pending",report["report_id"],"AUDIT_QUEUED");value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");assert value["eligibility"]=="AUDIT_PENDING" and value["report"] is None

def test_matrix_03_failed_body_is_null(tmp_path):
    reports, _, _=seeded(tmp_path,audit_status="FAILED");value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");assert value["eligibility"]=="AUDIT_FAILED" and value["report"] is None

def test_matrix_04_error_body_is_null(tmp_path):
    reports, _, _=seeded(tmp_path,audit_status="ERROR");value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");assert value["eligibility"]=="AUDIT_ERROR" and value["report"] is None

def test_matrix_05_schema_upgrade_body_is_null(tmp_path):
    reports,audits,_=seeded(tmp_path);_rewrite_audit(audits,lambda payload:payload.update(audit_schema_version="legacy"));value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");assert value["eligibility"]=="AUDIT_SCHEMA_UPGRADE_REQUIRED" and value["report"] is None

def test_matrix_06_wrong_instrument_rejected():
    with pytest.raises(PresentationError,match="INVALID_INSTRUMENT"):validate_selection("ETH-USDT","FULL","zh-CN")

def test_matrix_07_wrong_mode_rejected():
    with pytest.raises(PresentationError,match="INVALID_MODE"):validate_selection("ETH-USDT-SWAP","POSITION","zh-CN")

def test_matrix_08_sol_never_maps_to_eth(tmp_path):
    reports,_,report=seeded(tmp_path)
    with pytest.raises(PresentationError,match="PRESENTATION_SELECTION_MISMATCH"):build_report_presentation(reports,report["report_id"],instrument="SOL-USDT-SWAP",mode="FULL")

def test_matrix_09_report_audit_hash_mismatch_rejected(tmp_path):
    reports,_,report=seeded(tmp_path)
    with reports.connect() as conn:conn.execute("UPDATE ai_market_reports SET response_hash='forged' WHERE report_id=?",(report["report_id"],))
    with pytest.raises(PresentationError,match="REPORT_AUDIT_HASH_MISMATCH"):build_latest_presentation(reports,"ETH-USDT-SWAP","FULL")


def test_matrix_10_context_mismatch_rejected(tmp_path):
    reports, audits, _ = seeded(tmp_path)
    _rewrite_audit(audits, lambda payload: payload.update(context_id="forged-context"))
    with pytest.raises(PresentationError, match="CONTEXT_MISMATCH"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")


def test_matrix_11_registry_mismatch_rejected(tmp_path):
    reports, audits, _ = seeded(tmp_path)
    _rewrite_audit(audits, lambda payload: payload.update(registry_snapshot_id="forged-registry"))
    with pytest.raises(PresentationError, match="REGISTRY"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")


def test_matrix_12_latest_audit_is_selected(tmp_path):
    reports, audits, report = seeded(tmp_path)
    latest = copy.deepcopy(audits.latest(report["report_id"])); latest.update(audit_id="latest-failed-audit", status="FAILED", promotion_eligible=False, hard_failures=["LEVEL_PROJECTION_MISSING"], created_at="2099-01-01T00:00:00Z")
    audits.save_audit(latest)
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["audit_id"] == "latest-failed-audit" and value["eligibility"] == "AUDIT_FAILED" and value["report"] is None


def test_matrix_13_old_passed_with_new_pending(tmp_path):
    from tests.ai_market_analysis.test_ai6a_presentation import test_old_passed_remains_displayable_while_new_report_is_pending
    test_old_passed_remains_displayable_while_new_report_is_pending(tmp_path)


def test_matrix_14_newer_passed_supersedes_old_report(tmp_path):
    reports, audits, old = seeded(tmp_path)
    new = _second_passed(reports, audits)
    value = build_report_presentation(reports, old["report_id"], instrument="ETH-USDT-SWAP", mode="FULL")
    assert value["freshness"]["status"] == "SUPERSEDED" and value["latest_generated"]["report_id"] == new["report_id"]


def test_matrix_15_stale_uses_confirmed_watermark(tmp_path):
    from tests.ai_market_analysis.test_ai6a_presentation import test_freshness_uses_context_watermark_not_browser_clock
    test_freshness_uses_context_watermark_not_browser_clock(tmp_path)


def test_matrix_16_unknown_freshness_without_watermark(tmp_path):
    reports, _, report = seeded(tmp_path)
    with reports.connect() as conn:
        row = conn.execute("SELECT payload_json FROM ai_market_contexts WHERE context_id=?", (report["context_id"],)).fetchone(); context = json.loads(row[0])
        def clear(value):
            if isinstance(value, dict):
                for key in list(value):
                    if key == "latest_confirmed_bar_timestamp": value[key] = None
                    else: clear(value[key])
            elif isinstance(value, list):
                for item in value: clear(item)
        clear(context); context["decision_time"] = None
        conn.execute("UPDATE ai_market_contexts SET payload_json=? WHERE context_id=?", (json.dumps(context), report["context_id"]))
    assert build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")["freshness"]["status"] == "UNKNOWN"


def test_matrix_17_position_is_initially_redacted(tmp_path):
    reports,_,_=seeded(tmp_path,mode="POSITION_AWARE",position=True);summary=build_latest_presentation(reports,"ETH-USDT-SWAP","POSITION_AWARE")["position_summary"];assert summary["sensitive_details_available"] and "average_cost" not in summary and "original_quantity" not in summary


def test_matrix_18_referenced_facts_are_bounded(tmp_path):
    reports,_,report=seeded(tmp_path);value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL");registry=freeze_report_bundle(reports,report["report_id"])["fact_registry"]["facts"];assert 0<len(value["referenced_facts"])<len(registry)


def test_matrix_19_referenced_macro_is_bounded(tmp_path):
    reports, audits, _ = seeded(tmp_path, audit_status=None)
    # The fixture has no referenced macro IDs; unrelated macro history must remain absent.
    value = build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")
    assert value["referenced_macro"] == [] and value["report"] is None


def test_matrix_20_payload_over_500kb_is_rejected(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path)
    monkeypatch.setattr(projection, "MAX_PRESENTATION_BYTES", 1)
    with pytest.raises(PresentationError, match="PRESENTATION_PAYLOAD_TOO_LARGE"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")


def test_matrix_21_admin_token_authorization(monkeypatch):
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED","true");monkeypatch.setenv("ADMIN_TOKEN","secret");item,captured=handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL");item.do_GET();assert captured[0][0]==HTTPStatus.UNAUTHORIZED


def test_matrix_22_rate_limit_is_enforced(monkeypatch):
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", "true"); monkeypatch.setenv("ADMIN_TOKEN", "secret")
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL", {"Authorization": "Bearer secret"})
    item._limited = lambda *_: captured.append((HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate limited"})) or True
    item.do_GET()
    assert captured == [(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate limited"})]


def test_matrix_23_feature_disabled(monkeypatch):
    monkeypatch.delenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED",raising=False);item,captured=handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL");item.do_GET();assert captured[0][1]["error"]["code"]=="PRESENTATION_DISABLED"


def test_matrix_24_explain_query_plan_uses_index(tmp_path):
    reports,_,_=seeded(tmp_path)
    with reports.connect() as conn:plan=conn.execute("EXPLAIN QUERY PLAN SELECT p.* FROM ai_market_reports p JOIN ai_report_requests r ON r.request_id=p.request_id WHERE r.instrument=? AND p.mode=? AND p.language=? ORDER BY p.created_at DESC,p.report_id DESC LIMIT 50",("ETH-USDT-SWAP","FULL","zh-CN")).fetchall()
    assert any("INDEX" in str(tuple(row)) for row in plan)


def test_matrix_25_does_not_open_paper_db():
    source=Path(projection.__file__).read_text(encoding="utf-8");assert "paper_trades" not in source and "paper_db" not in source


def test_matrix_26_does_not_open_microstructure_db():
    source=Path(projection.__file__).read_text(encoding="utf-8");assert "microstructure" not in source and "raw_trades" not in source


def test_matrix_27_projection_performs_no_network_io(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path)
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network attempted")))
    assert build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")["report"] is not None


def test_matrix_28_internal_error_is_sanitized(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path)
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", "true"); monkeypatch.setenv("ADMIN_TOKEN", "secret")
    monkeypatch.setattr(paper_api, "AI_REPORT_REPOSITORY", reports); monkeypatch.setattr(paper_api, "build_latest_presentation", lambda *_: (_ for _ in ()).throw(RuntimeError("C:\\secret\\reports.db API_KEY=hidden")))
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL", {"Authorization": "Bearer secret"}); item._limited = lambda *_: False; item.do_GET()
    rendered = json.dumps(captured)
    assert captured[0][0] == HTTPStatus.INTERNAL_SERVER_ERROR and "secret" not in rendered and "API_KEY" not in rendered


def test_matrix_29_presentation_identity_is_stable(tmp_path):
    reports,_,report=seeded(tmp_path);first=build_report_presentation(reports,report["report_id"],instrument="ETH-USDT-SWAP",mode="FULL");second=build_report_presentation(reports,report["report_id"],instrument="ETH-USDT-SWAP",mode="FULL");assert first["presentation_id"]==second["presentation_id"]


def test_matrix_30_future_records_do_not_mutate_old_report_body(tmp_path):
    reports, audits, old = seeded(tmp_path)
    before = build_report_presentation(reports, old["report_id"], instrument="ETH-USDT-SWAP", mode="FULL")["report"]
    _second_passed(reports, audits)
    after = build_report_presentation(reports, old["report_id"], instrument="ETH-USDT-SWAP", mode="FULL")["report"]
    assert after == before


# Additional AI-6AC backend security cases; these are intentionally separate from the 30-row matrix.
def test_security_wrong_language_rejected():
    with pytest.raises(PresentationError, match="INVALID_LANGUAGE"): validate_selection("ETH-USDT-SWAP", "FULL", "fr")

def test_security_forged_audit_id_rejected(tmp_path):
    reports, audits, _ = seeded(tmp_path); _rewrite_audit(audits, lambda payload: payload.update(audit_id="forged-audit"))
    with pytest.raises(PresentationError, match="AUDIT_ID_MISMATCH"): build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")

def test_security_forged_registry_snapshot_id_rejected(tmp_path):
    test_matrix_11_registry_mismatch_rejected(tmp_path)

def test_security_audit_payload_hash_mismatch(tmp_path):
    reports, audits, _ = seeded(tmp_path)
    with audits.connect() as conn: conn.execute("UPDATE ai_report_audits SET payload_hash='forged'")
    with pytest.raises(PresentationError, match="AUDIT_PAYLOAD_HASH_MISMATCH"): build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")

def test_security_report_hash_mismatch(tmp_path):
    reports, _, report = seeded(tmp_path)
    with reports.connect() as conn: conn.execute("UPDATE ai_market_reports SET response_hash='forged' WHERE report_id=?", (report["report_id"],))
    with pytest.raises(PresentationError, match="REPORT_AUDIT_HASH_MISMATCH"): build_latest_presentation(reports, "ETH-USDT-SWAP", "FULL")

def test_security_source_version_mismatch(tmp_path):
    reports, _, report = seeded(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="REGISTRY_SNAPSHOT_MUTATED"):
        with reports.connect() as conn: conn.execute("UPDATE ai_report_registry_snapshots SET source_versions_hash='forged' WHERE request_id=?", (report["request_id"],))

def test_security_position_instrument_mismatch(tmp_path):
    reports, _, report = seeded(tmp_path, mode="POSITION_AWARE", position=True)
    with pytest.raises(PresentationError, match="PRESENTATION_SELECTION_MISMATCH"): position_details(reports, report["report_id"], instrument="SOL-USDT-SWAP", mode="POSITION_AWARE")

def test_security_position_mode_mismatch(tmp_path):
    reports, _, report = seeded(tmp_path, mode="POSITION_AWARE", position=True)
    with pytest.raises(PresentationError, match="PRESENTATION_SELECTION_MISMATCH"): position_details(reports, report["report_id"], instrument="ETH-USDT-SWAP", mode="FULL")

def test_security_token_query_parameter_rejected(monkeypatch):
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", "true"); monkeypatch.setenv("ADMIN_TOKEN", "secret")
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL&token=secret"); item.do_GET(); assert captured[0][0] == HTTPStatus.UNAUTHORIZED

def test_security_deep_json_rejected():
    value = {}; cursor = value
    for _ in range(40): cursor["child"] = {}; cursor = cursor["child"]
    with pytest.raises(PresentationError, match="PRESENTATION_JSON_TOO_DEEP"): projection._bounded(value)

def test_security_overlong_string_rejected():
    with pytest.raises(PresentationError, match="PRESENTATION_STRING_TOO_LONG"): projection._bounded({"value": "x" * 100_001})

def test_security_unsupported_enum_rejected(tmp_path):
    reports, audits, report = seeded(tmp_path)
    with reports.connect() as conn:
        row = conn.execute("SELECT response_json FROM ai_market_reports WHERE report_id=?", (report["report_id"],)).fetchone(); body=json.loads(row[0]);body["directional_bias"]="SIDEWAYS_RAW";digest=stable_hash(body);conn.execute("UPDATE ai_market_reports SET response_json=?,response_hash=? WHERE report_id=?",(json.dumps(body),digest,report["report_id"]))
    _rewrite_audit(audits, lambda payload: payload.update(report_hash=digest))
    with pytest.raises(PresentationError, match="UNSUPPORTED_PRESENTATION_ENUM"): build_latest_presentation(reports,"ETH-USDT-SWAP","FULL")

def test_security_failed_hard_failure_summary_is_bounded(tmp_path):
    reports, _, _ = seeded(tmp_path, audit_status="FAILED"); value=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL"); assert value["report"] is None and value["audit_summary"]["hard_failure_count"] == len(value["audit_summary"]["hard_failures"]) <= 20

def test_security_health_has_no_path_or_secret(tmp_path):
    reports, _, _ = seeded(tmp_path); health=build_latest_presentation(reports,"ETH-USDT-SWAP","FULL")["health_summary"]; rendered=json.dumps(health).lower(); assert "path" not in rendered and "secret" not in rendered and "token=" not in rendered

def test_security_transaction_is_explicit():
    source=Path(projection.__file__).read_text(encoding="utf-8"); assert source.count('conn.execute("BEGIN")') >= 2

def test_security_projection_does_not_import_market_databases():
    source=Path(projection.__file__).read_text(encoding="utf-8"); assert "paper_trades" not in source and "microstructure" not in source
