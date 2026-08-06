from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle
def test_order_guarantee_probability_and_secret_fail_closed():
    for case,code in (("order_instruction","ORDER_INSTRUCTION"),("guaranteed_return","GUARANTEE_OR_CERTAINTY"),("invent_probability","EXACT_PROBABILITY"),("local_path","SECRET_OR_INTERNAL_DATA_EXPOSURE")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]
