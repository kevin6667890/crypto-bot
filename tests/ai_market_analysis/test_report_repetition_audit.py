from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_exact_near_vague_and_reasonable_summary():
    assert audit_report(golden_bundle())["repetition_audit"]["standalone_vague_sentence_count"]==0
    repeated=audit_report(adversarial_bundle("timeframe_repetition"));assert repeated["repetition_audit"]["exact_duplicate_count"]>=4
    vague=audit_report(adversarial_bundle("vague_standalone"));assert vague["repetition_audit"]["standalone_vague_sentence_count"]==1
