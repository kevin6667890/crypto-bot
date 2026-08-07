from dashboard.ai_market_analysis.report_audit_service import audit_report
from .ai5_helpers import adversarial_bundle,golden_bundle
def test_exact_chinese_direction_unit_hallucination_and_probability_gates():
    assert audit_report(golden_bundle())["scorecard"]["ratios"]["numeric_grounding"]==1
    for case,code in (("chinese_hallucination","NUMERIC_HALLUCINATION"),("chinese_percent_direction","NUMERIC_DIRECTION_MISMATCH"),("wrong_unit","NUMERIC_UNIT_MISMATCH"),("invent_probability","EXACT_PROBABILITY")):
        assert code in audit_report(adversarial_bundle(case))["hard_failures"]

def test_unparsed_market_number_is_not_ignored():
    b=golden_bundle();next(s for s in b["report"]["sections"] if s["section_id"]=="KEY_LEVELS")["body"]+="。关键位一千九百廿五美元"
    from dashboard.ai_market_analysis.canonical import stable_hash
    b["report_hash"]=stable_hash(b["report"]);assert "UNPARSED_NUMERIC_CLAIM" in audit_report(b)["hard_failures"]
