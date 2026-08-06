from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_macro_frozen_evidence_and_unsupported_news():
    assert audit_report(golden_bundle("FULL",False,True))["macro_audit"]["evidence_count"]==3
    for case in ("macro_without_evidence","macro_fake_url"):assert "UNSUPPORTED_MACRO" in audit_report(adversarial_bundle(case))["hard_failures"]
