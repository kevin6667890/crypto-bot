from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_mode_scenario_invalidation_warning_coverage():
    for mode in ("QUICK","FULL"):assert audit_report(golden_bundle(mode))["status"]=="PASSED"
    for case,code in (("scenario_no_trigger","SCENARIO_INCOMPLETE"),("scenario_no_invalidation","INVALIDATION_MISSING"),("warning_omitted","CRITICAL_WARNING_OMITTED")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]
