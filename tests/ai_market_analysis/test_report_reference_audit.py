from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_reference_audit import audit_references
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


def _timeframe_claim(mention, fact_id):
    return {"claim_id":"claim","claim_type":"INFERENCE","modality":"FACT",
            "original_text":f"{mention} structure","fact_refs":[fact_id],
            "level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[],
            "timeframe_mentions":[mention],"instrument_mentions":[]}


def test_lowercase_timeframe_mentions_match_canonical_referenced_facts():
    registry={"instrument":"SOL-USDT-SWAP","facts":[
        {"fact_id":fact_id,"category":"TIMEFRAME","value":{"timeframe":canonical}}
        for fact_id,canonical in (("TF15_SUMMARY","15m"),("TF1H_SUMMARY","1H"),
                                  ("TF4H_SUMMARY","4H"),("TF1D_SUMMARY","1D"),
                                  ("TF1W_SUMMARY","1W"))]}
    for mention,fact_id in (("15M","TF15_SUMMARY"),("15m","TF15_SUMMARY"),
                            ("1h","TF1H_SUMMARY"),("4h","TF4H_SUMMARY"),
                            ("1d","TF1D_SUMMARY"),("1w","TF1W_SUMMARY")):
        assert audit_references([_timeframe_claim(mention,fact_id)],registry)["failure_codes"]==[]


def test_lowercase_timeframe_still_requires_matching_referenced_fact():
    registry={"instrument":"SOL-USDT-SWAP","facts":[
        {"fact_id":"TF15_SUMMARY","category":"TIMEFRAME","value":{"timeframe":"15m"}},
        {"fact_id":"TF1H_SUMMARY","category":"TIMEFRAME","value":{"timeframe":"1H"}}]}
    result=audit_references([_timeframe_claim("1d","TF15_SUMMARY")],registry)
    assert result["failure_codes"]==["TIMEFRAME_MISMATCH"]
