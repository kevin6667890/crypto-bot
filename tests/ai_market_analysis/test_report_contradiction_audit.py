from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle
def test_context_internal_timeframe_level_and_orderflow_contradictions():
    for case,code in (("weekly_bull","CRITICAL_CONTRADICTION"),("internal_breakout_conflict","CRITICAL_CONTRADICTION"),("support_as_resistance","KEY_LEVEL_CONTRADICTION"),("cvd_direction","ORDER_FLOW_CONTRADICTION")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]
