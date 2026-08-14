from __future__ import annotations

import copy
import json

import pytest

from dashboard.ai_market_analysis.provider_claim_pack import build_provider_claim_pack, ground_provider_report
from dashboard.ai_market_analysis.report_basic_validation import validate_report
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_level_audit import audit_report_levels
from dashboard.ai_market_analysis.report_macro_audit import audit_macro
from dashboard.ai_market_analysis.report_numeric_audit import audit_numeric_claims
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_reference_audit import audit_references
from dashboard.ai_market_analysis.report_scenario_audit import audit_report_scenarios
from tests.ai_market_analysis.test_report_provider_validation import setup


def _real_style_report(mode: str = "QUICK"):
    request, registry = setup(mode)
    report = json.loads(FakeAIReportProvider("deepseek-v4-flash").generate(request).raw_text)
    request = {**request, "model": "deepseek-v4-flash"}
    report["model"] = "deepseek-v4-flash"
    return request, registry, report


def test_current_failure_classes_are_closed_by_deterministic_claim_pack():
    request, registry, report = _real_style_report("QUICK")
    corrupted = copy.deepcopy(report)
    corrupted["sections"][0]["fact_refs"] = [
        item for item in corrupted["sections"][0]["fact_refs"]
        if not item.startswith(("LEVEL_", "FLOW_"))
    ]
    corrupted["sections"][0]["body"] += " 四小时结构仅作冻结证据解释。"
    for level in corrupted["key_levels"]:
        level.update(asserted_role="WRONG", asserted_state="WRONG", asserted_timeframe="WRONG",
                     asserted_dynamic="WRONG", valid_until="WRONG", level_refs=[])
    for scenario in corrupted["scenarios"]:
        scenario.update(trigger_text="WRONG", confirmation_text="WRONG", expected_path_level_refs=[],
                        volume_confirmation_text="WRONG", cvd_confirmation_text="WRONG",
                        oi_confirmation_text="WRONG", funding_basis_confirmation_text="WRONG")
    assert audit_report_levels(corrupted, registry)["failure_codes"]
    assert audit_report_scenarios(corrupted, registry)["failure_codes"]

    grounded = ground_provider_report(corrupted, build_provider_claim_pack(request["compiled_context"], "QUICK"))
    assert validate_report(grounded, request, registry)["status"] == "VALID"
    claims = extract_claims("report_fixture", grounded)
    assert audit_numeric_claims(claims, registry["numeric_registry"])["numeric_grounding_ratio"] == 1.0
    assert audit_references(claims, registry)["reference_support_ratio"] == 1.0
    assert audit_report_levels(grounded, registry)["field_coverage"] == 1.0
    assert audit_report_scenarios(grounded, registry)["field_coverage"] == 1.0
    assert audit_macro(claims, {"items": []}, "2099-01-01T00:00:00Z")["failure_codes"] == []


@pytest.mark.parametrize(
    ("remove", "status_key", "expected"),
    [
        (set(), "flow_available", True),
        ({"MACRO"}, "macro_available", False),
        ({"ORDER_FLOW"}, "flow_available", False),
        ({"SCENARIO"}, "scenarios_available", False),
        ({"LEVEL"}, "levels_available", False),
    ],
)
def test_claim_pack_multi_context_evidence_status(remove, status_key, expected):
    request, _registry = setup("FULL")
    compiled = copy.deepcopy(request["compiled_context"])
    compiled["facts"] = [item for item in compiled["facts"] if item["category"] not in remove]
    pack = build_provider_claim_pack(compiled, "FULL")
    assert pack["evidence_status"][status_key] is expected
    if not pack["evidence_status"]["macro_available"]:
        assert pack["macro_evidence_ids"] == []
        assert pack["macro_unavailable_statement"]


def test_claim_pack_preserves_exact_numeric_and_multiple_level_timeframe_facts():
    request, _registry = setup("FULL")
    pack = build_provider_claim_pack(request["compiled_context"], "FULL")
    assert pack["allowed_numeric_values"] == [
        {"source_fact_id": item["source_fact_id"], "canonical_value": item["canonical_value"],
         "exact_display": item.get("exact_display", str(item["canonical_value"])), "unit": item.get("unit")}
        for item in request["compiled_context"]["numeric_registry"]
    ]
    assert len(pack["levels"]) >= 2
    assert all(item["asserted_timeframe"] for item in pack["levels"])
