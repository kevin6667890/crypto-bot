from dashboard.ai_market_analysis.provider_claim_pack import build_provider_claim_pack, ground_provider_report
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_numeric_audit import audit_numeric_claims
from dashboard.ai_market_analysis.report_semantic_audit import audit_semantics


def _claim(text, fact_refs=None):
    report = {"sections": [{"section_id": "QUICK_SUMMARY", "body": text,
                            "fact_refs": fact_refs or [], "level_refs": [], "scenario_refs": [],
                            "macro_refs": [], "position_refs": []}]}
    return extract_claims("semantic-role-report", report)


def _numeric(value, *, fact="FACT", role="SIGNED_DELTA", namespace="PRICE_CHANGE", unit=None, field="value"):
    return {"source_fact_id": fact, "canonical_value": value, "absolute_tolerance": 0.000001,
            "unit": unit, "semantic_role": role, "semantic_namespace": namespace,
            "semantic_field": field}


def test_signed_positive_price_change_uses_local_direction():
    result = audit_numeric_claims(_claim("价格上涨 0.043%"), [
        _numeric(0.043, unit="percent", field="price_change_pct")
    ])
    assert result["failure_codes"] == []


def test_volume_comparative_pair_validates_values_not_positive_signs():
    claims = _claim("成交量由 329475 降至 37279，显著收缩")
    registry = [
        _numeric(329475, fact="FLOW_PREVIOUS", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
        _numeric(37279, fact="FLOW_CURRENT", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
    ]
    assert audit_numeric_claims(claims, registry)["failure_codes"] == []


def test_absolute_boundary_allows_downward_relation():
    claims = _claim("若价格跌破 1780 USDT")
    registry = [_numeric(1780, fact="STRUCT_BREAKOUT_BOUNDARY", role="THRESHOLD_RELATION",
                         namespace="PRICE_LEVEL", unit="USDT", field="range_high")]
    assert audit_numeric_claims(claims, registry)["failure_codes"] == []


def test_positive_absolute_value_with_decrease_relation_is_not_direction_mismatch():
    claims = _claim("成交量由 329475 降至 37279")
    registry = [
        _numeric(329475, fact="FLOW_PREVIOUS", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
        _numeric(37279, fact="FLOW_CURRENT", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
    ]
    result = audit_numeric_claims(claims, registry)
    assert "NUMERIC_DIRECTION_MISMATCH" not in result["failure_codes"]


def test_comparative_contraction_with_increasing_values_fails():
    claims = _claim("成交量由 37279 升至 329475，却显著收缩")
    registry = [
        _numeric(37279, fact="FLOW_PREVIOUS", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
        _numeric(329475, fact="FLOW_CURRENT", role="ABSOLUTE_VALUE", namespace="VOLUME", field="volume"),
    ]
    assert "NUMERIC_DIRECTION_MISMATCH" in audit_numeric_claims(claims, registry)["failure_codes"]


def test_negative_signed_delta_described_as_increase_fails():
    claims = _claim("价格上涨 0.043%")
    registry = [_numeric(-0.043, unit="percent", field="price_change_pct")]
    assert "NUMERIC_DIRECTION_MISMATCH" in audit_numeric_claims(claims, registry)["failure_codes"]


def test_wrong_boundary_value_still_fails_grounding():
    claims = _claim("若价格跌破 1850 USDT")
    registry = [_numeric(1780, fact="STRUCT_BREAKOUT_BOUNDARY", role="THRESHOLD_RELATION",
                         namespace="PRICE_LEVEL", unit="USDT", field="range_high")]
    assert "NUMERIC_HALLUCINATION" in audit_numeric_claims(claims, registry)["failure_codes"]


def test_price_change_cannot_be_claimed_as_net_flow():
    facts = [{"fact_id": "FLOW_PHASE_03", "category": "ORDER_FLOW", "quality": "VALID",
              "value": {"price_change_pct": 0.043, "cvd_delta": None, "cvd_status": "UNAVAILABLE"}}]
    claims = _claim("FLOW_PHASE_03 显示资金净流入 0.043", ["FLOW_PHASE_03"])
    assert "ORDER_FLOW_SEMANTIC_NAMESPACE_MISMATCH" in audit_semantics(claims, facts)["failure_codes"]


def test_unavailable_cvd_cannot_support_certain_net_flow():
    facts = [{"fact_id": "FLOW_PHASE_03", "category": "ORDER_FLOW", "quality": "UNAVAILABLE",
              "value": {"price_change_pct": 0.043, "cvd_delta": None, "cvd_status": "UNAVAILABLE",
                        "quality": "UNAVAILABLE"}}]
    claims = _claim("资金净流入已经确认", ["FLOW_PHASE_03"])
    assert "ORDER_FLOW_SEMANTIC_NAMESPACE_MISMATCH" in audit_semantics(claims, facts)["failure_codes"]


def test_frozen_failure_replay_removes_wrong_flow_claim_and_four_false_positives():
    flow_3 = {"fact_id": "FLOW_PHASE_03", "category": "ORDER_FLOW", "quality": "UNAVAILABLE",
              "value": {"price_change_pct": 0.043265219857840445, "volume": 329475.245672,
                        "cvd_delta": None, "cvd_status": "UNAVAILABLE", "quality": "UNAVAILABLE"}}
    flow_4 = {"fact_id": "FLOW_PHASE_04", "category": "ORDER_FLOW", "quality": "UNAVAILABLE",
              "value": {"price_change_pct": -0.005012677565483098, "volume": 37279.738633,
                        "cvd_delta": None, "cvd_status": "UNAVAILABLE", "quality": "UNAVAILABLE"}}
    boundary = {"fact_id": "STRUCT_BREAKOUT_BOUNDARY", "category": "TIMELINE", "quality": "VALID",
                "value": 1780.0, "unit": "USDT"}
    registry = [
        {"source_fact_id": "FLOW_PHASE_03", "canonical_value": 0.043265219857840445, "absolute_tolerance": .51},
        {"source_fact_id": "FLOW_PHASE_03", "canonical_value": 329475.245672, "absolute_tolerance": .51},
        {"source_fact_id": "FLOW_PHASE_04", "canonical_value": -0.005012677565483098, "absolute_tolerance": .51},
        {"source_fact_id": "FLOW_PHASE_04", "canonical_value": 37279.738633, "absolute_tolerance": .51},
        {"source_fact_id": "STRUCT_BREAKOUT_BOUNDARY", "canonical_value": 1780.0,
         "absolute_tolerance": .51, "unit": "USDT"},
    ]
    compiled = {"facts": [flow_3, flow_4, boundary], "numeric_registry": registry}
    pack = build_provider_claim_pack(compiled, "QUICK")
    report = {"headline": "冻结回放", "sections": [{"section_id": "QUICK_SUMMARY", "title": "摘要",
              "body": ("FLOW_PHASE_03 显示净流入 0.043265219857840445，但 FLOW_PHASE_04 显示净流出 "
                       "-0.005012677565483098，且 FLOW_PHASE_04 的成交量 37279.738633 较 FLOW_PHASE_03 的 "
                       "329475.245672 显著收缩，表明近期资金流入减弱。若价格连续两根超短周期 K 线收盘"
                       "跌破突破边界 1780.0 USDT，则失败突破情景确认，回踩转为下跌。"),
              "fact_refs": [], "level_refs": [], "scenario_refs": [], "macro_refs": [], "position_refs": []}],
              "key_levels": [], "scenarios": [], "citations": []}
    grounded = ground_provider_report(report, pack)
    text = grounded["sections"][0]["body"]
    assert "净流入" not in text and "净流出" not in text
    claims = extract_claims("frozen-replay", grounded)
    result = audit_numeric_claims(claims, registry)
    assert "NUMERIC_DIRECTION_MISMATCH" not in result["failure_codes"]
