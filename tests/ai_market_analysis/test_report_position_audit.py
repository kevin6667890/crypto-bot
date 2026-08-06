from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_position_sources_and_discipline():
    assert audit_report(golden_bundle("POSITION_AWARE",True))["position_audit"]["result"]=="PASSED"
    for case in ("paper_as_real","none_reduce_half","undeclared_half","plan_not_started"):assert "UNSUPPORTED_POSITION" in audit_report(adversarial_bundle(case))["hard_failures"]
