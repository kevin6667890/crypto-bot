from __future__ import annotations

import base64
import copy
import gzip
import json
from pathlib import Path

import pytest

from dashboard.ai_market_analysis.provider_claim_pack import build_provider_claim_pack, ground_provider_report
from dashboard.ai_market_analysis.report_basic_validation import validate_report
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_level_audit import audit_report_levels
from dashboard.ai_market_analysis.report_macro_audit import audit_macro
from dashboard.ai_market_analysis.report_numeric_audit import audit_numeric_claims
from dashboard.ai_market_analysis.report_reference_audit import audit_references
from dashboard.ai_market_analysis.report_repetition_audit import audit_repetition
from dashboard.ai_market_analysis.report_scenario_audit import audit_report_scenarios
from dashboard.ai_market_analysis.report_semantic_audit import audit_semantics


FIXTURE = Path(__file__).parent / "fixtures" / "production_narrative_regression_corpus_v1.json.gz.b64"


def _cases():
    payload = gzip.decompress(base64.b64decode(FIXTURE.read_text(encoding="ascii")))
    return json.loads(payload.decode("utf-8"))["cases"]


def _replay(case):
    registry = case["fact_registry"]
    pack = build_provider_claim_pack(registry, "QUICK")
    report = copy.deepcopy(case["raw_response"])
    report["request_id"] = case["request"]["request_id"]
    report = ground_provider_report(report, pack)
    claims = extract_claims(f"replay_{case['case_id']}", report)
    return {
        "report": report,
        "claims": claims,
        "numeric": audit_numeric_claims(claims, pack["allowed_numeric_values"]),
        "reference": audit_references(claims, registry),
        "semantic": audit_semantics(claims, registry["facts"]),
        "duplicate": audit_repetition(claims),
        "level": audit_report_levels(report, registry),
        "scenario": audit_report_scenarios(report, registry),
        "macro": audit_macro(claims, {"items": []}, registry["decision_time"]),
        "registry": registry,
        "pack": pack,
        "case": case,
    }


@pytest.mark.parametrize("case", _cases(), ids=lambda item: item["case_id"])
def test_frozen_production_narrative_component_replay(case):
    replay = _replay(case)
    assert replay["numeric"]["failure_codes"] == []
    assert replay["reference"]["failure_codes"] == []
    assert replay["semantic"]["failure_codes"] == []
    assert replay["duplicate"]["duplicate_pair_count"] == 0
    assert replay["level"]["failure_codes"] == []
    assert replay["scenario"]["failure_codes"] == []
    assert replay["macro"]["failure_codes"] == []


def test_1748_frozen_response_passes_current_eleven_section_contract():
    case = next(item for item in _cases() if item["case_id"] == "NEAR_DUPLICATE_1748")
    replay = _replay(case)
    report = replay["report"]
    request = {
        **case["request"],
        "compiled_context": {**replay["registry"], "provider_claim_pack": replay["pack"]},
        "source_versions": report["source_versions"],
        "macro_items": [],
        "position_source": "NONE",
    }
    assert len(report["sections"]) == 11
    assert validate_report(report, request, replay["registry"])["status"] == "VALID"
    assert replay["reference"]["unsupported_claims"] == []
    assert replay["duplicate"]["duplicate_pair_count"] == 0
    assert replay["duplicate"]["typed_false_positive_count"] >= 1


def test_corpus_is_exactly_the_seven_recent_failure_classes():
    assert {item["case_id"] for item in _cases()} == {
        "EMPTY_INVALIDATION", "LEVEL_REFERENCE", "MACRO_BOUNDARY",
        "DUPLICATE_LIMITATION", "INDICATOR_PERIOD", "UNKNOWN_LEVEL_REF",
        "NEAR_DUPLICATE_1748",
    }
