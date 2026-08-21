from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_macro_audit import audit_macro
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_macro_frozen_evidence_and_unsupported_news():
    assert audit_report(golden_bundle("FULL",False,True))["macro_audit"]["evidence_count"]==3
    for case in ("macro_without_evidence","macro_fake_url"):assert "UNSUPPORTED_MACRO" in audit_report(adversarial_bundle(case))["hard_failures"]


def test_neutral_not_included_macro_statement_is_not_a_macro_claim():
    claim={"claim_id":"neutral","claim_type":"MACRO","original_text":"本轮未纳入宏观背景。",
           "fact_refs":["MACRO_UNAVAILABLE"],"macro_refs":[]}
    result=audit_macro([claim],{"items":[]},"2026-08-21T00:00:00Z")
    assert result["failure_codes"]==[]
