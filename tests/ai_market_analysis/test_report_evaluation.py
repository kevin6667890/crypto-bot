from dashboard.ai_market_analysis.canonical import stable_hash
from dashboard.ai_market_analysis.report_evaluation import default_manifest,evaluate,evaluation_identity,baseline_diff
from dashboard.ai_market_analysis.report_evaluation_models import ADVERSARIAL_CASES
from .ai5_helpers import adversarial_bundle,golden_bundle,refreeze_bundle

def case_bundle(case):
    cid=case["case_id"]
    if case["kind"]=="ADVERSARIAL":return adversarial_bundle(cid)
    if case["kind"]=="POSITION_MACRO":
        return golden_bundle("POSITION_AWARE",True) if cid.startswith("position") else golden_bundle("FULL",False,cid!="macro_none")
    if case["kind"]=="DATA_GAP":
        bundle=golden_bundle();fact={"fact_id":"DATA_WARNING_CASE","category":"WARNING","label":"case warning","value":cid.upper(),"unit":None,"timestamp":bundle["context"]["decision_time"],"source":"FIXTURE","quality":"PARTIAL","context_pointer":"/fixture","display_value":cid,"allowed_rounding":.51,"claim_scope":"MARKET","provenance":"fixture","priority":100}
        bundle["fact_registry"]["facts"].append(fact);section=next(s for s in bundle["report"]["sections"] if s["section_id"]=="LIMITATIONS");section["fact_refs"].append("DATA_WARNING_CASE");section["body"]+="。已披露该项严重数据限制"
        bundle["report_hash"]=stable_hash(bundle["report"]);return refreeze_bundle(bundle,True)
    if cid in {"quick_none","positive_02","positive_05","positive_08"}:return golden_bundle("QUICK")
    if cid in {"position_none_downgrade","positive_03","positive_06","positive_09"}:return golden_bundle("POSITION_AWARE",False)
    if cid in {"full_no_macro","positive_01","positive_04","positive_07","positive_10"}:return golden_bundle()
    return golden_bundle("FULL",False,cid=="all_complete")

def test_manifest_has_required_80_distinct_cases():
    manifest=default_manifest("test");cases=manifest["cases"];assert len(cases)==80 and len({x["case_id"] for x in cases})==80
    assert sum(x["kind"]=="POSITIVE" for x in cases)==10 and sum(x["kind"]=="ADVERSARIAL" for x in cases)==40
    assert sum(x["kind"]=="BOUNDARY" for x in cases)==10 and sum(x["kind"]=="DATA_GAP" for x in cases)==10 and sum(x["kind"]=="POSITION_MACRO" for x in cases)==10

def test_all_80_cases_match_status_and_expected_failure_codes():
    result=evaluate(default_manifest("test"),case_bundle);assert result["case_count"]==80 and result["pass_count"]==80 and result["fail_count"]==0

def test_evaluation_identity_excludes_run_timestamps_and_baseline_diff_is_explicit():
    a=default_manifest("abc");b={**a,"started_at":"later","completed_at":"later"};assert evaluation_identity(a)==evaluation_identity(b)
    assert "pass_count" in baseline_diff({"pass_count":2},{"pass_count":1})
