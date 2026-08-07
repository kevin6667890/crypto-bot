import pytest
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_evaluation_models import ADVERSARIAL_CASES
from .ai5_helpers import adversarial_bundle

@pytest.mark.parametrize("case_id,description,expected",ADVERSARIAL_CASES,ids=[x[0] for x in ADVERSARIAL_CASES])
def test_each_adversarial_report_hits_expected_gate(case_id,description,expected):
    audit=audit_report(adversarial_bundle(case_id));assert audit["status"]=="FAILED",description;assert expected in audit["hard_failures"],(description,audit["hard_failures"])
