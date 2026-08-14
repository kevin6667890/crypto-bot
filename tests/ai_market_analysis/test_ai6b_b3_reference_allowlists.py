from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.ai_market_analysis.canonical import canonical_json
from dashboard.ai_market_analysis.report_basic_validation import ReportValidationError, validate_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider, ProviderResult
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_response_contract import (
    provider_json_schema, provider_reference_allowlists, provider_reference_namespace_matrix,
    response_metadata_contract,
)
from dashboard.ai_market_analysis.provider_response_diagnostics import reference_diagnostics, sanitize_provider_response
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION
from tests.ai_market_analysis.ai4_helpers import base_context
from tests.ai_market_analysis.test_report_provider_validation import setup


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = ROOT / "tests/fixtures/ai6b_b3_unknown_macro_ref_diagnostic.json"


def _compiled_with_every_namespace():
    return {
        "context_id": "ctx", "mode": "QUICK", "allowed_market_phases": ["MIXED"],
        "allowed_directional_biases": ["NEUTRAL"], "max_confidence": "LOW",
        "facts": [
            {"fact_id": "FACT_1", "category": "WARNING", "value": "status"},
            {"fact_id": "MACRO_01", "category": "MACRO", "value": {"evidence_id": "macro-1"}},
            {"fact_id": "FLOW_01", "category": "ORDER_FLOW", "value": {}},
            {"fact_id": "LEVEL_FACT", "category": "LEVEL", "value": {"level_id": "level-1"}},
            {"fact_id": "SCENARIO_FACT", "category": "SCENARIO", "value": {
                "scenario_id": "scenario-1", "source_phase_ids": ["phase-1"],
                "source_event_ids": ["event-1"],
            }},
            {"fact_id": "POSITION_SOURCE", "category": "POSITION", "value": "NONE"},
            {"fact_id": "TF15_SUMMARY", "category": "TIMEFRAME", "value": {}},
        ],
    }


def test_historical_unknown_macro_value_is_explicitly_unrecoverable():
    value = json.loads(HISTORICAL.read_text(encoding="utf-8"))
    assert value["failure_code"] == "UNKNOWN_MACRO_REF"
    assert value["allowed_macro_refs"] == []
    assert value["unknown_macro_ref_value"] == "UNRECOVERABLE_FROM_PERSISTED_EVIDENCE"
    assert value["raw_response_recoverable"] is False


def test_provider_reference_allowlists_are_disjoint_and_exact():
    allowed = provider_reference_allowlists(_compiled_with_every_namespace())
    assert allowed["macro_refs"] == ["macro-1"]
    assert allowed["flow_refs"] == ["FLOW_01"]
    assert allowed["level_refs"] == ["level-1"]
    assert allowed["scenario_refs"] == ["scenario-1"]
    assert allowed["position_refs"] == ["POSITION_SOURCE"]
    assert allowed["timeframe_refs"] == ["TF15_SUMMARY"]
    assert allowed["source_phase_ids"] == ["phase-1"]
    assert allowed["source_event_ids"] == ["event-1"]
    assert "MACRO_01" in allowed["fact_refs"] and "macro-1" not in allowed["fact_refs"]


def test_empty_namespaces_are_explicit_empty_arrays_and_status_fact_is_not_macro_ref():
    compiled = _compiled_with_every_namespace()
    compiled["facts"] = [{"fact_id": "MACRO_UNAVAILABLE", "category": "WARNING", "value": "unavailable"}]
    metadata = response_metadata_contract(
        context_id="ctx", mode="QUICK", language="zh-CN", model="deepseek-v4-flash",
        prompt_version=AI_REPORT_PROMPT_VERSION, source_versions={"fixture": "v1"},
    )
    schema = provider_json_schema(metadata, compiled)
    refs = schema["allowed_reference_ids"]
    assert refs["fact_refs"] == ["MACRO_UNAVAILABLE"]
    assert refs["macro_refs"] == refs["flow_refs"] == refs["level_refs"] == refs["scenario_refs"] == []
    assert "cross-namespace references are forbidden" in schema["identifier_rules"]


def test_canonical_reference_namespace_matrix_has_no_unconstrained_path():
    matrix = provider_reference_namespace_matrix()
    expected = {
        "sections[].fact_refs", "sections[].level_refs", "sections[].scenario_refs",
        "sections[].macro_refs", "sections[].position_refs", "key_levels[].level_id",
        "key_levels[].fact_refs", "key_levels[].level_refs", "scenarios[].scenario_id",
        "scenarios[].trigger_level_refs", "scenarios[].expected_path_level_refs",
        "scenarios[].target_level_refs", "scenarios[].invalidation_level_ref",
        "scenarios[].fact_refs", "scenarios[].level_refs", "scenarios[].source_phase_ids",
        "scenarios[].source_event_ids", "position_guidance.fact_refs",
        "position_guidance.original_invalidation.fact_ref", "citations[].evidence_id",
    }
    assert set(matrix) == expected
    assert matrix["citations[].evidence_id"]["namespace"] == "macro_refs"
    assert all(item["allowlist"].startswith("allowed_reference_ids.") for item in matrix.values())
    schema = provider_json_schema(
        response_metadata_contract(context_id="ctx", mode="QUICK", language="zh-CN",
          model="deepseek-v4-flash", prompt_version=AI_REPORT_PROMPT_VERSION, source_versions={}),
        _compiled_with_every_namespace(),
    )
    assert schema["reference_contract_summary"] == {
        "unconstrained_reference_fields": 0,
        "cross_namespace_provider_paths": 0,
        "citations_must_equal_section_macro_refs": True,
    }


def test_exact_cross_namespace_citations_reproduce_failure_but_contract_forbids_them():
    historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))["next_attempt_exact_citation_failure"]
    request, registry = setup("QUICK")
    report = json.loads(FakeAIReportProvider().generate(request).raw_text)
    report["sections"][0]["macro_refs"] = []
    report["citations"] = [{"evidence_id": value} for value in historical["citation_evidence_ids"]]
    with pytest.raises(ReportValidationError, match="UNKNOWN_MACRO_REF"):
        validate_report(report, request, registry)
    schema = provider_json_schema(
        response_metadata_contract(context_id=request["context_id"], mode="QUICK", language="zh-CN",
          model=request["model"], prompt_version=request["prompt_version"], source_versions={}),
        request["compiled_context"],
    )
    assert schema["allowed_reference_ids"]["macro_refs"] == []
    assert schema["reference_namespace_matrix"]["citations[].evidence_id"]["namespace"] == "macro_refs"
    report["citations"] = []
    assert validate_report(report, request, registry)["status"] == "VALID"


def test_citation_ids_are_included_in_exact_persisted_reference_diagnostics():
    request, registry = setup("QUICK")
    report = json.loads(FakeAIReportProvider().generate(request).raw_text)
    report["citations"] = [{"evidence_id": "TF15_SUMMARY"}]
    value = reference_diagnostics(report, request, registry)
    assert value["unknown_refs"]["macro_refs"] == ["TF15_SUMMARY"]


@pytest.mark.parametrize("field,bad,code", [
    ("macro_refs", "MACRO_UNAVAILABLE", "UNKNOWN_MACRO_REF"),
    ("level_refs", "FACT_1", "UNKNOWN_LEVEL_REF"),
    ("scenario_refs", "LEVEL_01", "UNKNOWN_SCENARIO_REF"),
    ("position_refs", "TF15_SUMMARY", "UNKNOWN_POSITION_REF"),
])
def test_wrong_namespace_and_invented_ids_still_fail_closed(field, bad, code):
    request, registry = setup("QUICK")
    report = json.loads(FakeAIReportProvider().generate(request).raw_text)
    report["sections"][0][field] = [bad]
    with pytest.raises(ReportValidationError, match=code):
        validate_report(report, request, registry)


def test_real_provider_validation_failure_persists_sanitized_raw_normalized_and_exact_ref_diagnostics(tmp_path, monkeypatch):
    path = tmp_path / "reports.db"
    migrate_database(path)
    repository = ReportRepository(path)
    monkeypatch.setenv("AI_REPORT_COST_STATUS", "B3_CONTROL_LEDGER")
    monkeypatch.setenv("AI_REPORT_INPUT_USD_PER_MILLION", "0.14")
    monkeypatch.setenv("AI_REPORT_OUTPUT_USD_PER_MILLION", "0.28")
    submitted = ReportService(repository).submit(
        base_context(), mode="QUICK", position_source="NONE",
        provider="deepseek", model="deepseek-v4-flash",
    )

    class UnknownMacroProvider:
        def generate(self, request):
            generated = FakeAIReportProvider("deepseek-v4-flash").generate(request)
            value = json.loads(generated.raw_text)
            value["sections"][0]["macro_refs"] = ["MACRO_UNAVAILABLE"]
            credential_shape = "sk" + "-testcredentialvalue"
            value["sections"][0]["body"] += " " + credential_shape
            raw = canonical_json(value)
            return ProviderResult(raw, "fixture-provider-id", "deepseek-v4-flash", generated.usage,
                                  "stop", 200, 1, "fixture-response-sha256")

    assert ReportWorker(repository, lambda _request: UnknownMacroProvider()).run_once() is True
    status = repository.status(submitted["request_id"])
    assert status["status"] == "VALIDATION_FAILED"
    with repository.connect() as connection:
        attempt = connection.execute(
            "SELECT attempt_id,failure_code FROM ai_report_attempts WHERE request_id=?",
            (submitted["request_id"],),
        ).fetchone()
    assert attempt["failure_code"] == "UNKNOWN_MACRO_REF"
    diagnostic = repository.attempt_diagnostic(attempt["attempt_id"])
    assert diagnostic is not None
    assert ("sk" + "-testcredentialvalue") not in diagnostic["sanitized_raw_response"]
    assert diagnostic["normalized_response"]["sections"][0]["macro_refs"] == ["MACRO_UNAVAILABLE"]
    assert diagnostic["parse_diagnostic"]["status"] == "VALID"
    assert diagnostic["validation_diagnostic"]["unknown_refs"]["macro_refs"] == ["MACRO_UNAVAILABLE"]


def test_response_sanitizer_redacts_key_bearer_and_private_key_shapes():
    raw = json.dumps({"api_key": "sk" + "-examplecredential", "text": "Bearer " + "abcdefghijklmnop"})
    value = sanitize_provider_response(raw)
    assert ("sk" + "-examplecredential") not in value
    assert "abcdefghijklmnop" not in value
    assert "[REDACTED]" in value
