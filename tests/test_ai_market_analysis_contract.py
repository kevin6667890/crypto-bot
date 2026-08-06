"""Static contract tests for Phase AI-1; no production path or LLM is invoked."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas" / "ai_market_analysis"
FIXTURES = ROOT / "fixtures" / "ai_market_analysis"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = load(SCHEMAS / name)
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        item = load(path)
        resource = Resource.from_contents(item)
        resources.extend(((item["$id"], resource), (path.resolve().as_uri(), resource)))
    return Draft202012Validator(
        schema, registry=Registry().with_resources(resources), format_checker=FormatChecker()
    )


def golden():
    return load(FIXTURES / "golden_eth_breakout_context_v1.json")


def pointer(document, path: str):
    value = document
    for token in path.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def semantic_errors(context: dict) -> list[str]:
    errors = []
    decision = context["decision_time"]
    if context["latest_confirmed_market_time"] > decision:
        errors.append("confirmed market watermark exceeds decision time")
    for event in context["structure_events"]:
        if event["event_type"] == "BREAKOUT_CONFIRMED" and event["confirmation_status"] == "CONFIRMED":
            if any("current_incomplete_candle" in item["evidence_paths"][0] for item in event["evidence"]):
                errors.append("incomplete candle confirms breakout")
            if any(ts > decision for ts in event["source_bar_timestamps"]):
                errors.append("future candle confirms breakout")
    for phase in context["order_flow_phases"]:
        flags = set(phase["gap_flags"])
        if flags and phase["data_quality"]["overall"] == "VALID":
            errors.append("flow gap marked VALID")
        if "CVD_GAP" in flags and phase["cvd_delta"]["quality"] == "VALID":
            errors.append("CVD gap delta marked VALID")
        if "OI_GAP" in flags and phase["oi_change"]["value"] is not None:
            errors.append("OI change crosses gap")
        if flags and phase["cross_gap_change"]:
            errors.append("cross-gap change enabled")
        if not phase["counterevidence"]:
            errors.append("attribution lacks counterevidence")
    return errors


def report_errors(request: dict, response: dict) -> list[str]:
    errors = []
    context = request["context"]
    if request["context_id"] != context["context_id"]:
        errors.append("request context_id mismatch")
    if response["context_id"] != request["context_id"]:
        errors.append("response context_id mismatch")
    for citation in response["citations"]:
        for path in citation["context_paths"]:
            try:
                pointer(context, path)
            except (KeyError, IndexError, ValueError, TypeError):
                errors.append(f"citation does not resolve: {path}")
    if context["position_context"]["source"] == "NONE" and response.get("position_guidance"):
        if response["position_guidance"].get("specific_reduction_quantity") is not None:
            errors.append("position NONE has specific reduction quantity")
    return errors


def request_response():
    context = golden()
    request = {
        "schema_version": "ai-market-report-request-v1", "request_id": "req_fixture",
        "context_id": context["context_id"], "mode": "FULL", "requested_at": context["generated_at"],
        "language": "zh-CN", "context": context,
        "generation_policy": {"may_create_numbers": False, "must_preserve_unknown": True,
                              "max_input_tokens": 12000, "max_output_tokens": 4000},
    }
    response = {
        "schema_version": "ai-market-report-response-v1", "report_id": "report_fixture",
        "request_id": request["request_id"], "context_id": context["context_id"],
        "headline": "ETH 突破后回踩验证", "market_phase": "POST_BREAKOUT_PULLBACK",
        "directional_bias": "BULLISH", "confidence": "MEDIUM",
        "sections": [{"section_id": "summary", "title": "综合结论", "text": "突破已发生，回踩仍待验证。", "claim_ids": ["c1"]}],
        "key_levels": ["/key_levels/0", "/key_levels/1"],
        "scenarios": ["/scenario_tree/scenarios/0", "/scenario_tree/scenarios/1", "/scenario_tree/scenarios/2"],
        "position_guidance": None, "unsupported_claims": [], "data_warnings": [],
        "citations": [{"claim_id": "c1", "context_paths": ["/market_timeline/current_phase", "/key_levels/0/price/value"]}],
        "generated_text": "突破已发生，当前是回踩验证。", "model": "fixture-model",
        "prompt_version": "fixture-prompt-v1", "audit_status": "PENDING",
    }
    return request, response


def test_all_json_schemas_are_legal():
    for path in SCHEMAS.glob("*.schema.json"):
        try:
            Draft202012Validator.check_schema(load(path))
        except SchemaError as exc:
            pytest.fail(f"{path.name}: {exc}")


def test_golden_fixture_passes_schema_and_semantics():
    context = golden()
    validator("market_analysis_context_v1.schema.json").validate(context)
    assert semantic_errors(context) == []


@pytest.mark.parametrize("name", [
    "order_flow_new_longs_v1.json", "order_flow_short_cover_weak_cvd_v1.json",
    "order_flow_new_shorts_v1.json", "order_flow_long_liquidation_v1.json",
    "order_flow_gap_insufficient_v1.json",
])
def test_counterexample_fixtures_pass_schema(name):
    value = load(FIXTURES / name)
    validator("order_flow_phase_v1.schema.json").validate(value)


def test_missing_required_field_fails():
    value = golden(); value.pop("provenance")
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)


def test_invalid_timestamp_fails():
    value = golden(); value["decision_time"] = "15 January sometime"
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)


def test_invalid_enum_fails():
    value = golden(); value["requested_analysis_mode"] = "VERBOSE"
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)


def test_source_and_provenance_are_mandatory():
    value = golden(); value["market_timeline"]["range_high"].pop("source")
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)


def test_cvd_gap_cannot_be_valid():
    value = golden(); phase = value["order_flow_phases"][0]
    phase["gap_flags"] = ["CVD_GAP"]; phase["data_quality"]["overall"] = "VALID"
    phase["cvd_delta"]["quality"] = "VALID"
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)
    assert "flow gap marked VALID" in semantic_errors(value)


def test_oi_change_cannot_cross_gap():
    value = golden(); phase = value["order_flow_phases"][0]
    phase["gap_flags"] = ["OI_GAP"]; phase["oi_change"]["value"] = 2.0
    with pytest.raises(ValidationError):
        validator("market_analysis_context_v1.schema.json").validate(value)
    assert "OI change crosses gap" in semantic_errors(value)


def test_incomplete_candle_cannot_confirm_breakout():
    value = golden()
    value["structure_events"][0]["evidence"] = [ev := {"claim": "bad", "evidence_paths": ["/timeframe_structures/0/current_incomplete_candle"]}]
    assert ev and "incomplete candle confirms breakout" in semantic_errors(value)


def test_report_context_id_must_match_request():
    request, response = request_response(); response["context_id"] = "ctx_wrong"
    assert "response context_id mismatch" in report_errors(request, response)


def test_report_number_citation_must_resolve_to_context():
    request, response = request_response(); response["citations"][0]["context_paths"] = ["/not/a/context/value"]
    assert report_errors(request, response)[0].startswith("citation does not resolve")


def test_scenario_requires_invalidation():
    value = deepcopy(golden()["scenario_tree"]); value["scenarios"][0].pop("invalidation")
    with pytest.raises(ValidationError):
        validator("scenario_tree_v1.schema.json").validate(value)


def test_order_flow_attribution_requires_counterevidence():
    value = load(FIXTURES / "order_flow_new_longs_v1.json"); value["counterevidence"] = []
    with pytest.raises(ValidationError):
        validator("order_flow_phase_v1.schema.json").validate(value)


def test_position_none_forbids_specific_reduction_quantity():
    request, response = request_response()
    response["position_guidance"] = {"summary": "reduce", "specific_reduction_quantity": 1.25}
    assert "position NONE has specific reduction quantity" in report_errors(request, response)


def test_request_and_response_schemas_pass():
    request, response = request_response()
    validator("ai_market_report_request_v1.schema.json").validate(request)
    validator("ai_market_report_response_v1.schema.json").validate(response)
    assert report_errors(request, response) == []


def test_phase_context_may_explicitly_defer_later_phase_sections():
    value = golden()
    value["order_flow_phases"] = []
    value["key_levels"] = []
    value["scenario_tree"] = {"status": "NOT_IMPLEMENTED", "scenarios": []}
    value["unsupported_claims"] = ["order_flow_phases:NOT_IMPLEMENTED", "key_levels:NOT_IMPLEMENTED", "scenario_tree:NOT_IMPLEMENTED"]
    validator("market_analysis_context_v1.schema.json").validate(value)


def test_available_scenario_tree_still_requires_three_scenarios():
    value = {"status": "AVAILABLE", "scenarios": golden()["scenario_tree"]["scenarios"][:2]}
    with pytest.raises(ValidationError):
        validator("scenario_tree_v1.schema.json").validate(value)


def test_phase_two_enums_and_empty_missing_timeframe_evidence_are_legal():
    value = golden()["timeframe_structures"][0]
    value["volume_regime"] = "CONTRACTING"
    value["trend_classification"] = "STRONG_BULL"
    value["source_bar_timestamps"] = []
    validator("timeframe_structure_v1.schema.json").validate(value)
