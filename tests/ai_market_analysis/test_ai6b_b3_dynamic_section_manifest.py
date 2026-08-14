from __future__ import annotations

import json

import pytest

from dashboard.ai_market_analysis.report_basic_validation import ReportValidationError, validate_report
from dashboard.ai_market_analysis.report_prompt_templates import compile_prompt
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_response_contract import (
    expected_section_manifest, provider_json_schema, response_metadata_contract,
)
from dashboard.ai_market_analysis.versions import AI_REPORT_PROMPT_VERSION
from tests.ai_market_analysis.test_report_provider_validation import setup


@pytest.mark.parametrize(("mode", "macro", "required", "forbidden"), [
    ("QUICK", False, ["QUICK_SUMMARY"], "MACRO_BACKGROUND"),
    ("FULL", False, ["CONCLUSION", "RECENT_PROCESS", "MOVE_NATURE", "TF_15M", "TF_1H",
                     "TF_4H", "TF_1D", "TF_1W", "ORDER_FLOW", "KEY_LEVELS", "SCENARIOS",
                     "LIMITATIONS"], "MACRO_BACKGROUND"),
    ("FULL", True, ["CONCLUSION", "MACRO_BACKGROUND", "RECENT_PROCESS", "MOVE_NATURE", "TF_15M",
                    "TF_1H", "TF_4H", "TF_1D", "TF_1W", "ORDER_FLOW", "KEY_LEVELS", "SCENARIOS",
                    "LIMITATIONS"], "POSITION_PLAN"),
    ("POSITION_AWARE", False, ["CONCLUSION", "RECENT_PROCESS", "MOVE_NATURE", "TF_15M", "TF_1H",
                               "TF_4H", "TF_1D", "TF_1W", "ORDER_FLOW", "KEY_LEVELS", "SCENARIOS",
                               "LIMITATIONS", "POSITION_PLAN"], "MACRO_BACKGROUND"),
])
def test_dynamic_section_matrix(mode, macro, required, forbidden):
    manifest = expected_section_manifest(mode, macro)
    assert manifest["required_section_ids_in_exact_order"] == required
    assert forbidden in manifest["forbidden_section_ids"]
    assert manifest["unconstrained_conditional_sections"] == 0


def test_provider_and_validator_share_manifest_and_forbid_unavailable_macro():
    request, registry = setup("FULL", macro=False)
    metadata = response_metadata_contract(
        context_id=request["context_id"], mode="FULL", language="zh-CN", model=request["model"],
        prompt_version=AI_REPORT_PROMPT_VERSION, source_versions={},
    )
    contract = provider_json_schema(metadata, request["compiled_context"])
    manifest = contract["expected_section_manifest"]
    assert contract["section_order"] == manifest["required_section_ids_in_exact_order"]
    assert "MACRO_BACKGROUND" in manifest["forbidden_section_ids"]
    prompt = compile_prompt(request["compiled_context"], "FULL", metadata)["messages"][1]["content"]
    assert "EXPECTED_SECTION_MANIFEST" in prompt
    assert '"unconstrained_conditional_sections":0' in prompt
    report = json.loads(FakeAIReportProvider().generate(request).raw_text)
    report["sections"].insert(1, {
        "section_id": "MACRO_BACKGROUND", "title": "macro", "body": "unavailable",
        "fact_refs": [], "level_refs": [], "scenario_refs": [], "macro_refs": [],
        "position_refs": [], "uncertainties": [],
    })
    with pytest.raises(ReportValidationError, match="SECTION_ORDER_OR_COMPLETENESS"):
        validate_report(report, request, registry)


def test_full_empty_flow_levels_scenarios_keep_required_sections_as_limitations():
    manifest = expected_section_manifest("FULL", False)
    required = manifest["required_section_ids_in_exact_order"]
    assert all(section in required for section in ("ORDER_FLOW", "KEY_LEVELS", "SCENARIOS"))
    rules = manifest["conditional_section_rules"]
    assert all("limitation" in rules[section] for section in ("ORDER_FLOW", "KEY_LEVELS", "SCENARIOS"))

