from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_audit_identity import AUDIT_SOURCE_VERSIONS
from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import golden_bundle

def test_missing_frozen_input_is_error_not_current_data_fallback():
    bundle=golden_bundle();del bundle["numeric_registry"];audit=audit_report(bundle);assert audit["status"]=="ERROR" and audit["hard_failures"]==["AUDIT_INPUT_INCOMPLETE"] and not audit["promotion_eligible"]

def test_hash_and_source_version_mismatches_fail_closed():
    bundle=golden_bundle();bundle["report_hash"]="bad";assert "REPORT_HASH_MISMATCH" in audit_report(bundle)["hard_failures"]
    bundle=golden_bundle();bundle["context_hash"]="bad";assert "CONTEXT_HASH_MISMATCH" in audit_report(bundle)["hard_failures"]
    bundle=golden_bundle();bundle["source_versions"]={};assert "SOURCE_VERSION_MISMATCH" in audit_report(bundle)["hard_failures"]

def test_promotion_only_passed_and_versions_are_complete():
    audit=audit_report(golden_bundle());assert audit["promotion_eligible"] is True and audit["source_versions"]==AUDIT_SOURCE_VERSIONS
