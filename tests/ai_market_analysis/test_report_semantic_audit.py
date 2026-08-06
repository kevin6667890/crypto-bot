from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle
def test_phase_orderflow_likely_unknown_funding_liquidation_semantics():
    for case,code in (("phase_unbroken","CRITICAL_CONTRADICTION"),("new_longs_primary","ORDER_FLOW_CONTRADICTION"),("likely_confirmed","LIKELY_PROMOTED_TO_CONFIRMED"),("unknown_as_fact","UNKNOWN_PROMOTED_TO_FACT"),("predicted_as_settled","UNSUPPORTED_CLAIM"),("no_liquidation_claim","UNSUPPORTED_CLAIM")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]
