from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_supported_golden_and_category_timeframe_unknown_reference_failures():
    assert audit_report(golden_bundle())["scorecard"]["ratios"]["reference_semantic_support"]==1
    for case,code in (("tf_reference_mismatch","TIMEFRAME_MISMATCH"),("price_ref_for_oi","REFERENCE_NOT_SUPPORTING_CLAIM"),("scenario_unknown_target","UNKNOWN_REFERENCE")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]
