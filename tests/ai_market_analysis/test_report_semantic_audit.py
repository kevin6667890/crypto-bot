from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_semantic_audit import audit_semantics
from dashboard.ai_market_analysis.report_semantic_registry import SEMANTIC_REGISTRY
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from .ai5_helpers import adversarial_bundle
def test_phase_orderflow_likely_unknown_funding_liquidation_semantics():
    for case,code in (("phase_unbroken","CRITICAL_CONTRADICTION"),("new_longs_primary","ORDER_FLOW_CONTRADICTION"),("likely_confirmed","LIKELY_PROMOTED_TO_CONFIRMED"),("unknown_as_fact","UNKNOWN_PROMOTED_TO_FACT"),("predicted_as_settled","UNSUPPORTED_CLAIM"),("no_liquidation_claim","UNSUPPORTED_CLAIM")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]

def test_irrelevant_unavailable_ref_does_not_poison_supported_timeframe_claim():
    certainty=SEMANTIC_REGISTRY["UNKNOWN"]["forbidden_certainty"][0]
    facts=[{"fact_id":"TF","category":"TIMEFRAME","value":"UP"},
           {"fact_id":"FLOW","category":"ORDER_FLOW","value":{"status":"UNAVAILABLE"}}]
    claim={"claim_id":"c","claim_type":"TIMEFRAME_TREND","original_text":certainty,"fact_refs":["TF","FLOW"]}
    assert audit_semantics([claim],facts)["failure_codes"]==[]
    claim={**claim,"claim_type":"ORDER_FLOW_ATTRIBUTION"}
    assert "UNKNOWN_PROMOTED_TO_FACT" in audit_semantics([claim],facts)["failure_codes"]

def test_move_nature_timeframe_sentence_is_not_classified_as_orderflow():
    report={"sections":[{"section_id":"MOVE_NATURE","body":"超短周期级别结构为 RANGE，价格位于均线混合区域。",
                         "fact_refs":[],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    assert extract_claims("report",report)[0]["claim_type"]=="TIMEFRAME_TREND"

def test_deferred_orderflow_confirmation_does_not_promote_partial_evidence():
    facts=[{"fact_id":"FLOW","category":"ORDER_FLOW","quality":"PARTIAL","value":{"status":"PARTIAL"}}]
    report={"sections":[{"section_id":"CONCLUSION",
        "body":"\u5efa\u8bae\u7b49\u5f85\u66f4\u660e\u786e\u7684\u8ba2\u5355\u6d41\u786e\u8ba4",
        "fact_refs":["FLOW"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claim=extract_claims("report",report)[0]
    assert claim["modality"]=="UNCERTAIN"
    assert audit_semantics([claim],facts)["failure_codes"]==[]
