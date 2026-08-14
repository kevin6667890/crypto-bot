from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.ai_market_analysis.canonical import canonical_json
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.presentation import build_report_presentation
from dashboard.ai_market_analysis.report_audit_jobs import AuditWorker, queue_audit
from dashboard.ai_market_analysis.report_audit_repository import (
    AuditRepository, freeze_report_bundle, migrate_audit_database,
)
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from dashboard.ai_market_analysis.report_jobs import ReportWorker, TokenBudget
from dashboard.ai_market_analysis.report_prompt_templates import compile_prompt
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider, ProviderResult
from dashboard.ai_market_analysis.report_response_contract import (
    LEVEL_PROJECTION_FIELDS,
    SCENARIO_PROJECTION_FIELDS,
    SECTION_FIELDS,
    SERVICE_REQUEST_ID_SENTINEL,
    TOP_FIELDS,
    provider_json_schema,
    response_metadata_contract,
)
from dashboard.ai_market_analysis.report_response_parser import ReportParseError, parse_report_response
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION
from tests.ai_market_analysis.ai4_helpers import base_context
from tests.ai_market_analysis.test_report_provider_validation import setup


ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC = ROOT / "tests/fixtures/ai6b_b3_real_schema_failure_diagnostic.json"


def test_historical_real_response_recovery_boundary_is_immutable_and_no_recall():
    value = json.loads(DIAGNOSTIC.read_text(encoding="utf-8"))
    assert value["raw_response_recoverable"] is False
    assert value["raw_response_hash"] == "9890f3bc3d20f41d9f67e8d6cf0c22aa45751266568be2ab8cfc4075c8c8d514"
    assert value["first_schema_mismatch"] == "audit_status"
    assert value["schema_mismatch_count"] == "UNKNOWN_RAW_NOT_PERSISTED"
    assert value["schema_mismatch_count_lower_bound"] == 12
    assert value["provider_call_required_to_recover_more"] is False


def test_reconstructed_historical_alternate_shape_still_fails_closed():
    value = {name: None for name in ("current_phase", "direction", "invalidation_condition")}
    with pytest.raises(ReportParseError, match="response fields mismatch"):
        parse_report_response(canonical_json(value), expected_request_id="request_expected")


def test_provider_schema_is_generated_from_canonical_parser_constants():
    base = base_context()
    registry = build_fact_registry(build_enriched_context(
        base, none_position_context("ETH-USDT-SWAP"), freeze_macro_evidence_set([], base["decision_time"]),
    ))
    compiled = compile_report_context(registry, "QUICK")
    metadata = response_metadata_contract(
        context_id=compiled["context_id"], mode="QUICK", language="zh-CN",
        model="deepseek-v4-flash", prompt_version=AI_REPORT_PROMPT_VERSION,
        source_versions={"fixture": "v1"},
    )
    schema = provider_json_schema(metadata, compiled)
    assert schema["exact_top_level_fields"] == list(TOP_FIELDS)
    assert schema["exact_section_fields"] == list(SECTION_FIELDS)
    assert schema["exact_level_projection_fields"] == list(LEVEL_PROJECTION_FIELDS)
    assert schema["exact_scenario_projection_fields"] == list(SCENARIO_PROJECTION_FIELDS)
    assert schema["immutable_values"]["request_id"] == SERVICE_REQUEST_ID_SENTINEL
    prompt = compile_prompt(compiled, "QUICK", metadata)
    assert "CANONICAL_RESPONSE_JSON_SCHEMA" in prompt["messages"][1]["content"]
    assert '"additional_fields_allowed":false' in prompt["messages"][1]["content"]
    assert '"current_phase"' not in prompt["messages"][1]["content"].split("FACT_REGISTRY_JSON:", 1)[0]


def test_request_id_sentinel_is_the_only_deterministic_provider_normalization():
    request, _registry = setup("QUICK")
    value = json.loads(FakeAIReportProvider().generate(request).raw_text)
    value["request_id"] = SERVICE_REQUEST_ID_SENTINEL
    parsed = parse_report_response(canonical_json(value), expected_request_id=request["request_id"])
    assert parsed["request_id"] == request["request_id"]
    value["request_id"] = "request_wrong"
    with pytest.raises(ReportParseError, match="request_id contract mismatch"):
        parse_report_response(canonical_json(value), expected_request_id=request["request_id"])


def test_real_style_sentinel_response_completes_validation_audit_and_presentation(tmp_path, monkeypatch):
    path = tmp_path / "reports.db"
    migrate_database(path)
    migrate_audit_database(path)
    reports, audits = ReportRepository(path), AuditRepository(path)
    monkeypatch.setenv("AI_REPORT_COST_STATUS", "B3_CONTROL_LEDGER")
    monkeypatch.setenv("AI_REPORT_INPUT_USD_PER_MILLION", "0.14")
    monkeypatch.setenv("AI_REPORT_OUTPUT_USD_PER_MILLION", "0.28")
    submitted = ReportService(reports).submit(
        base_context(), mode="QUICK", position_source="NONE",
        provider="deepseek", model="deepseek-v4-flash",
    )

    class RealStyleProvider:
        def generate(self, request):
            generated = FakeAIReportProvider("deepseek-v4-flash").generate(request)
            value = json.loads(generated.raw_text)
            value["request_id"] = SERVICE_REQUEST_ID_SENTINEL
            raw = canonical_json(value)
            return ProviderResult(raw, "provider-fixture", "deepseek-v4-flash", generated.usage,
                                  "stop", 200, 2, "fixture-hash")

    budget = TokenBudget()
    assert ReportWorker(reports, lambda _request: RealStyleProvider(), budget=budget).run_once() is True
    assert reports.status(submitted["request_id"])["status"] == "COMPLETED"
    report = reports.get_report(request_id=submitted["request_id"])
    assert report is not None
    audits.freeze_input(freeze_report_bundle(reports, report["report_id"]))
    queue_audit(audits, report["report_id"])
    assert AuditWorker(audits).run_once() is True
    audit = audits.latest(report["report_id"])
    assert audit["status"] == "PASSED"
    presentation = build_report_presentation(reports, report["report_id"], instrument="ETH-USDT-SWAP", mode="QUICK")
    assert presentation["eligibility"] == "AUDIT_PASSED_SHADOW_ONLY"
    assert presentation["report"] is not None
