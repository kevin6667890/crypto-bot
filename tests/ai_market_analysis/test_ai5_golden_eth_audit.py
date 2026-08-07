from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import golden_bundle

def test_golden_eth_full_passes_semantic_gates():
    audit=audit_report(golden_bundle());assert audit["status"]=="PASSED" and audit["hard_failures"]==[]
    assert audit["scorecard"]["ratios"]["numeric_grounding"]==1 and audit["scorecard"]["ratios"]["reference_semantic_support"]==1
    assert audit["scorecard"]["ratios"]["level_field_coverage"]==audit["scorecard"]["ratios"]["scenario_field_coverage"]==audit["scorecard"]["ratios"]["invalidation_coverage"]==1
    assert audit["scorecard"]["ratios"]["registry_identity_valid"] is True

def test_golden_position_passes_and_grounds_1835():
    audit=audit_report(golden_bundle("POSITION_AWARE",True));assert audit["status"]=="PASSED" and audit["position_audit"]["source"]=="USER_DECLARED"
    assert any(x["normalized_value"]==1835 and x["source_fact_id"]=="POSITION_AVERAGE_COST" for x in audit["numeric_audits"])
    assert "TIMEFRAME_DRIFT_RISK" in audit["position_audit"]["discipline_warnings"]
    assert audit["scorecard"]["ratios"]["level_field_coverage"]==audit["scorecard"]["ratios"]["scenario_field_coverage"]==audit["scorecard"]["ratios"]["invalidation_coverage"]==1

def test_quick_passes_mode_specific_coverage():assert audit_report(golden_bundle("QUICK"))["status"]=="PASSED"
