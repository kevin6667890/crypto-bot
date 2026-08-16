from __future__ import annotations

import copy
import json

import pytest

from dashboard.ai_market_analysis.provider_claim_pack import _narrative_text, _scenario_narrative_text, build_provider_claim_pack, ground_provider_report, provider_claim_pack_contract
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
from dashboard.ai_market_analysis.report_repetition_audit import audit_repetition
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


def test_provider_prompt_forbids_derived_numbers_numeric_lists_and_unregistered_indicator_periods():
    _request, registry = setup("FULL")
    prompt = compile_prompt(compile_report_context(registry, "FULL"), "FULL")
    system = prompt["messages"][0]["content"]
    contract = prompt["messages"][1]["content"]
    assert "不得计算、舍入、插值、换算涨跌幅或价差" in system
    assert "不得使用阿拉伯数字给段落或限制事项编号" in system
    assert "不得提及没有对应允许值的指标周期" in system
    assert "never derive percentages, differences, averages, ratios" in contract
    assert "never use ASCII digits as list or paragraph numbering" in contract
    assert "never mention an indicator period unless its exact numeric value" in contract


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


def test_full_empty_scenario_grounding_puts_invalidation_in_canonical_limitations_section():
    request, registry, report = _real_style_report("FULL")
    request = copy.deepcopy(request)
    registry = copy.deepcopy(registry)
    registry["facts"] = [item for item in registry["facts"] if item["category"] != "SCENARIO"]
    request["compiled_context"]["facts"] = [
        item for item in request["compiled_context"]["facts"] if item["category"] != "SCENARIO"
    ]
    report["scenarios"] = []
    scenarios = next(item for item in report["sections"] if item["section_id"] == "SCENARIOS")
    limitations = next(item for item in report["sections"] if item["section_id"] == "LIMITATIONS")
    scenarios["body"] = "场景数据不可用，当前没有可审计的情景路径。"
    limitations["body"] = "订单流数据部分缺失。"

    grounded = ground_provider_report(report, build_provider_claim_pack(request["compiled_context"], "FULL"))

    grounded_scenarios = next(item for item in grounded["sections"] if item["section_id"] == "SCENARIOS")
    grounded_limitations = next(item for item in grounded["sections"] if item["section_id"] == "LIMITATIONS")
    assert grounded_scenarios["body"] == scenarios["body"]
    assert grounded_limitations["body"].endswith("证据不足，当前没有可审计的情景失效路径。")
    assert validate_report(grounded, request, registry)["status"] == "VALID"


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


def test_grounding_splits_real_provider_mixed_flow_clause_before_numeric_audit():
    body = _narrative_text(
        "订单流显示资金费率转负（-0.005012677565483098），"
        "但成交量放大（2182680.532165999），"
        "CVD 为正（0.002984441853589368），表明空头回补可能推动反弹。"
    )
    report = {"sections": [{"section_id": "QUICK_SUMMARY", "body": body,
                            "fact_refs": [], "level_refs": [], "scenario_refs": [],
                            "macro_refs": [], "position_refs": []}]}
    claims = extract_claims("report_b4_mixed_flow", report)
    registry = [
        {"source_fact_id": "FLOW_PHASE_04", "canonical_value": -0.005012677565483098, "absolute_tolerance": 0.51},
        {"source_fact_id": "FLOW_PHASE_02", "canonical_value": 2182680.532165999, "absolute_tolerance": 0.51},
        {"source_fact_id": "FLOW_PHASE_02", "canonical_value": 0.002984441853589368, "absolute_tolerance": 0.51},
    ]

    result = audit_numeric_claims(claims, registry)

    assert [claim["original_text"] for claim in claims] == [
        "订单流显示资金费率转负（-0.005012677565483098）",
        "成交量放大（2182680.532165999）",
        "CVD 为正（0.002984441853589368），表明空头回补可能推动反弹",
    ]
    assert result["numeric_grounding_ratio"] == 1.0
    assert result["failure_codes"] == []


def test_grounding_formats_unix_evidence_timestamps_as_utc_dates():
    assert _narrative_text("该区域在 1784131200 时间戳被翻转。") == "该区域在 2026-07-15 时间戳被翻转。"
    assert _narrative_text("该区域动态有效至 1786763700。") == "该区域动态有效至 2026-08-15。"


def test_grounding_removes_presentation_only_scenario_counts_and_numbering():
    value = "基于当前结构，存在三个情景：1) 看涨延续，2) 正常回测，3) 失败突破。"
    assert _scenario_narrative_text(value) == "基于当前结构，存在情景：看涨延续，正常回测，失败突破。"


def test_unavailable_flow_facts_render_only_auditable_limitation():
    request, registry, report = _real_style_report("FULL")
    compiled = copy.deepcopy(request["compiled_context"])
    for fact in compiled["facts"]:
        if fact["category"] == "ORDER_FLOW":
            fact["quality"] = "UNAVAILABLE"
            if isinstance(fact.get("value"), dict):
                fact["value"]["quality"] = "UNAVAILABLE"
    pack = build_provider_claim_pack(compiled, "FULL")
    grounded = ground_provider_report(report, pack)

    assert pack["evidence_status"]["flow_available"] is False
    assert pack["fact_ids_by_category"]["ORDER_FLOW"] == []
    for section_id in ("MOVE_NATURE", "ORDER_FLOW"):
        section = next(item for item in grounded["sections"] if item["section_id"] == section_id)
        assert section["body"] == "当前无可审计订单流证据，无法判定驱动性质。"
        assert all(not ref.startswith("FLOW_") for ref in section["fact_refs"])
        claims = extract_claims("report_unavailable_flow", {"sections": [section]})
        assert len(claims) == 1
        assert claims[0]["claim_type"] == "LIMITATION"


def test_real_full_mixed_volume_and_price_clause_is_split_before_numeric_audit():
    body = _narrative_text(
        "\u8ba2\u5355\u6d41\u6570\u636e\u663e\u793a\u5f53\u524d\u9636\u6bb5\u6210\u4ea4\u91cf\u6536\u7f29\uff0c"
        "\u4ef7\u683c\u53d8\u52a8 434.59999999999854\uff0c"
        "\u4ef7\u683c\u53d8\u52a8\u767e\u5206\u6bd4 0.0066715380458810015\uff0c\u4f46 CVD \u4e0e OI \u4e0d\u53ef\u7528"
    )
    report={"sections":[{"section_id":"ORDER_FLOW","body":body,"fact_refs":["FLOW_PHASE_03"],
                         "level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claims=extract_claims("real_full",report)
    registry=[
        {"source_fact_id":"FLOW_PHASE_03","canonical_value":434.59999999999854,"absolute_tolerance":0.51},
        {"source_fact_id":"FLOW_PHASE_03","canonical_value":0.0066715380458810015,"absolute_tolerance":0.00001},
    ]
    assert len(claims)==4
    assert audit_numeric_claims(claims,registry)["failure_codes"]==[]


def test_partial_flow_and_missing_macro_are_rendered_as_grounded_limitations():
    request, registry, report = _real_style_report("FULL")
    compiled=copy.deepcopy(request["compiled_context"])
    for fact in compiled["facts"]:
        if fact["category"]=="ORDER_FLOW":
            fact["quality"]="PARTIAL"
            if isinstance(fact.get("value"),dict):fact["value"]["quality"]="PARTIAL"
    report["sections"][0]["body"]="\u5b8f\u89c2\u8bc1\u636e\u7f3a\u5931\uff0c\u6574\u4f53\u5224\u65ad\u57fa\u4e8e\u6709\u9650\u7684\u6280\u672f\u7ed3\u6784"
    flow=next(item for item in report["sections"] if item["section_id"]=="ORDER_FLOW")
    flow["body"]="\u8ba2\u5355\u6d41\u8f6c\u53d8\u663e\u793a\u89e3\u91ca\u4e3a\u6df7\u5408\u6301\u4ed3"
    grounded=ground_provider_report(report,build_provider_claim_pack(compiled,"FULL"))
    claims=extract_claims("grounded",grounded)
    assert "\u672c\u6b21\u672a\u52a0\u5165\u5df2\u9a8c\u8bc1\u5b8f\u89c2\u8bc1\u636e" in grounded["sections"][0]["body"]
    flow_claim=next(item for item in claims if item["section_id"]=="ORDER_FLOW")
    assert flow_claim["claim_type"]=="ORDER_FLOW_ATTRIBUTION"
    assert flow_claim["modality"]=="UNCERTAIN"
    assert audit_macro(claims,{"items":[]},"2099-01-01T00:00:00Z")["failure_codes"]==[]


def test_grounding_deduplicates_repeated_numeric_templates_across_full_sections():
    request, registry, report = _real_style_report("FULL")
    repeated="\u6210\u4ea4\u91cf 4096.768287579999\u3002\u6210\u4ea4\u91cf\u4f53\u5236\u6536\u7f29\u3002"
    next(item for item in report["sections"] if item["section_id"]=="RECENT_PROCESS")["body"]=repeated
    next(item for item in report["sections"] if item["section_id"]=="ORDER_FLOW")["body"]=(
        "\u6210\u4ea4\u91cf 1025.74710114\u3002\u6210\u4ea4\u91cf\u4f53\u5236\u6536\u7f29\u3002"
    )
    grounded=ground_provider_report(report,compile_report_context(registry,"FULL")["provider_claim_pack"])
    repetition=audit_repetition(extract_claims("deduped",grounded))
    assert repetition["exact_duplicate_count"]==0
    assert repetition["repeated_claim_ratio"]==0
