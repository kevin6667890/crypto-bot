from __future__ import annotations

import copy
import json

import pytest

from dashboard.ai_market_analysis.provider_claim_pack import _narrative_text, build_provider_claim_pack, ground_provider_report, provider_claim_pack_contract
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_prompt_templates import compile_prompt
from dashboard.ai_market_analysis.report_response_contract import provider_reference_allowlists
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

    grounded = ground_provider_report(corrupted, compile_report_context(registry, "QUICK")["provider_claim_pack"])
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


def test_quick_claim_pack_uses_complete_frozen_registry_after_narrative_pruning():
    _request, registry = setup("QUICK")
    compiled = compile_report_context(registry, "QUICK")
    pack = compiled["provider_claim_pack"]
    categories = pack["fact_ids_by_category"]
    assert categories.get("LEVEL") == [item["fact_id"] for item in registry["facts"] if item["category"] == "LEVEL"]
    assert categories.get("ORDER_FLOW") == [item["fact_id"] for item in registry["facts"] if item["category"] == "ORDER_FLOW"]
    refs = provider_reference_allowlists(compiled)
    assert set(categories["LEVEL"]).issubset(refs["fact_refs"])
    assert set(categories["ORDER_FLOW"]).issubset(refs["fact_refs"])


def test_grounding_canonicalizes_timeframe_and_no_macro_status_without_accepting_macro_claims():
    request, _registry, report = _real_style_report("QUICK")
    report["sections"][0]["body"] = "4H 数据过期，宏观证据未加入。"
    grounded = ground_provider_report(report, compile_report_context(_registry, "QUICK")["provider_claim_pack"])
    assert grounded["sections"][0]["body"] == "中周期 数据过期，本次未加入已验证宏观证据。"


def test_provider_claim_pack_contract_is_compact_view_of_host_source_of_truth():
    _request, registry = setup("QUICK")
    host = compile_report_context(registry, "QUICK")["provider_claim_pack"]
    provider = provider_claim_pack_contract(host)
    assert [item["level_id"] for item in provider["level_claim_slots"]] == [item["level_id"] for item in host["levels"]]
    assert [item["scenario_id"] for item in provider["scenario_claim_slots"]] == [item["scenario_id"] for item in host["scenarios"]]
    assert {item[0] for item in provider["allowed_numeric_values"]} <= {
        item["source_fact_id"] for item in host["allowed_numeric_values"]
    }


def test_provider_prompt_contains_one_claim_pack_copy():
    request, registry = setup("QUICK")
    compiled = compile_report_context(registry, "QUICK")
    prompt = compile_prompt(compiled, "QUICK")
    assert prompt["messages"][1]["content"].count('"claim_pack_version"') == 1


def test_empty_scenario_grounding_adds_canonical_invalidation_limitation():
    request, registry, report = _real_style_report("QUICK")
    request = copy.deepcopy(request)
    registry = copy.deepcopy(registry)
    registry["facts"] = [item for item in registry["facts"] if item["category"] != "SCENARIO"]
    request["compiled_context"]["facts"] = [
        item for item in request["compiled_context"]["facts"] if item["category"] != "SCENARIO"
    ]
    report["scenarios"] = []
    report["sections"][0]["body"] = "当前结论仅使用已冻结证据。"
    pack = build_provider_claim_pack(request["compiled_context"], "QUICK")

    grounded = ground_provider_report(report, pack)

    assert grounded["sections"][0]["body"].endswith("证据不足，当前没有可审计的情景失效路径。")
    assert validate_report(grounded, request, registry)["status"] == "VALID"
    limitation = [
        claim for claim in extract_claims("report_empty_scenario", grounded)
        if "当前没有可审计的情景失效路径" in claim["original_text"]
    ]
    assert len(limitation) == 1
    assert limitation[0]["claim_type"] == "LIMITATION"


def test_empty_scenario_grounding_does_not_duplicate_existing_invalidation():
    _request, registry, report = _real_style_report("QUICK")
    compiled = copy.deepcopy(_request["compiled_context"])
    compiled["facts"] = [item for item in compiled["facts"] if item["category"] != "SCENARIO"]
    report["scenarios"] = []
    report["sections"][0]["body"] = "当前没有可审计的情景失效路径。"

    grounded = ground_provider_report(report, build_provider_claim_pack(compiled, "QUICK"))

    assert grounded["sections"][0]["body"].count("当前没有可审计的情景失效路径") == 1


def test_grounding_removes_provider_level_counts_not_present_in_numeric_registry():
    request, registry, report = _real_style_report("QUICK")
    report["sections"][0]["body"] = "两个支撑保持有效，两个压力仍需关注。"

    grounded = ground_provider_report(report, compile_report_context(registry, "QUICK")["provider_claim_pack"])

    assert grounded["sections"][0]["body"] == "支撑保持有效，压力仍需关注。"
    assert validate_report(grounded, request, registry)["status"] == "VALID"


def test_grounding_splits_mixed_direction_flow_values_into_independent_claims():
    body = _narrative_text(
        "订单流事实：FLOW_PHASE_01 显示净负值 -0.0129，"
        "FLOW_PHASE_02 显示正值 0.0430，"
        "FLOW_PHASE_03 显示净负值 -0.0203。"
    )
    report = {"sections": [{"section_id": "QUICK_SUMMARY", "body": body,
                            "fact_refs": [], "level_refs": [], "scenario_refs": [],
                            "macro_refs": [], "position_refs": []}]}
    claims = extract_claims("report_mixed_flow", report)
    registry = [
        {"source_fact_id": "FLOW_PHASE_01", "canonical_value": -0.0129, "absolute_tolerance": 0.00001},
        {"source_fact_id": "FLOW_PHASE_02", "canonical_value": 0.0430, "absolute_tolerance": 0.00001},
        {"source_fact_id": "FLOW_PHASE_03", "canonical_value": -0.0203, "absolute_tolerance": 0.00001},
    ]

    result = audit_numeric_claims(claims, registry)

    assert len(claims) == 3
    assert result["numeric_grounding_ratio"] == 1.0
    assert result["failure_codes"] == []


def test_grounding_formats_unix_evidence_timestamps_as_utc_dates():
    assert _narrative_text("该区域在 1784131200 时间戳被翻转。") == "该区域在 2026-07-15 时间戳被翻转。"
