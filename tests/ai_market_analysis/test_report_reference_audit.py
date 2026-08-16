from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_supported_golden_and_category_timeframe_unknown_reference_failures():
    assert audit_report(golden_bundle())["scorecard"]["ratios"]["reference_semantic_support"]==1
    for case,code in (("tf_reference_mismatch","TIMEFRAME_MISMATCH"),("price_ref_for_oi","REFERENCE_NOT_SUPPORTING_CLAIM"),("scenario_unknown_target","UNKNOWN_REFERENCE")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]

def test_event_timeline_fact_can_support_its_canonical_invalidation():
    registry={"instrument":"BTC-USDT-SWAP","facts":[{"fact_id":"EVENT_01","category":"TIMELINE","value":{
        "event_type":"REVERSAL","confirmation_status":"CONFIRMED","invalidation":"opposite boundary"}}]}
    claim={"claim_id":"claim","claim_type":"INVALIDATION","modality":"CONFIRMED",
           "original_text":"\u5931\u6548\u6761\u4ef6\u4e3a\u786e\u8ba4\u6536\u590d\u76f8\u53cd\u8fb9\u754c","fact_refs":["EVENT_01"],
           "level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[],
           "timeframe_mentions":[],"instrument_mentions":[]}
    result=__import__("dashboard.ai_market_analysis.report_reference_audit",fromlist=["audit_references"]).audit_references([claim],registry)
    assert result["failure_codes"]==[]
