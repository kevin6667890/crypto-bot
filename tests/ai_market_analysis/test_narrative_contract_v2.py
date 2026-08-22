import copy

from dashboard.ai_market_analysis.provider_claim_pack import ground_provider_report
from dashboard.ai_market_analysis.report_claim_extractor import extract_claims
from dashboard.ai_market_analysis.report_context_compiler import compile_report_context
from dashboard.ai_market_analysis.report_narrative_contract import (
    NARRATIVE_CONTRACT_VERSION, provider_section_claim_plan,
)
from dashboard.ai_market_analysis.report_repetition_audit import audit_repetition
from dashboard.ai_market_analysis.report_response_contract import QUICK_SECTION_IDS
from .test_ai6b_b3_claim_pack import _real_style_report


def _claim(claim_id, section, text, scope, role, semantic_key, facts, claim_type="TIMEFRAME_STRUCTURE"):
    return {"claim_id":claim_id,"section_id":section,"normalized_text":text,"original_text":text,
            "claim_type":claim_type,"scope":scope,"role":role,"semantic_key":semantic_key,
            "source_fact_ids":facts,"fact_refs":facts,"quantities":[]}


def test_section_claim_plan_is_host_owned_and_complete_for_quick():
    plan=provider_section_claim_plan(QUICK_SECTION_IDS)
    assert plan["version"]==NARRATIVE_CONTRACT_VERSION
    assert plan["sections"]["QUICK_SUMMARY"]=={
        "scope":"GLOBAL","role":"SUMMARY",
        "allowed_claim_types":["MARKET_STATE","CROSS_TIMEFRAME_SYNTHESIS","LIMITATION","COVERAGE_METADATA"],
    }
    assert plan["sections"]["TF_4H"]["scope"]=="4H"
    assert plan["sections"]["SCENARIOS"]["allowed_claim_types"]==["SCENARIO","TRIGGER","INVALIDATION","LIMITATION"]


def test_same_4h_claim_in_4h_and_1h_sections_is_rejected():
    claims=[
        _claim("a","TF_4H","中周期保持明显伸展","4H","DETAIL","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION"),
        _claim("b","TF_1H","中周期保持明显伸展","4H","DETAIL","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION"),
    ]
    result=audit_repetition(claims)
    assert result["duplicate_pair_count"]==1
    assert result["duplicate_pairs"][0]["duplicate_type"]=="SAME_CLAIM_SAME_SCOPE"


def test_same_limitation_in_three_sections_is_rejected():
    claims=[_claim(str(i),section,"订单流不可用","FLOW","LIMITATION","LIMITATION:FLOW",[],"LIMITATION")
            for i,section in enumerate(("QUICK_SUMMARY","ORDER_FLOW","LIMITATIONS"))]
    result=audit_repetition(claims)
    assert result["duplicate_pair_count"]==2
    assert all(item["duplicate_type"]=="LIMITATION_REPETITION" for item in result["duplicate_pairs"])


def test_summary_detail_similarity_with_distinct_scope_and_lineage_is_allowed():
    summary=_claim("s","QUICK_SUMMARY","高周期仍明显伸展","GLOBAL","SUMMARY",
                   "CROSS_TIMEFRAME_SYNTHESIS:4H+1D",["TF4H_STRUCTURE","TF1D_STRUCTURE"],"CROSS_TIMEFRAME_SYNTHESIS")
    summary["derived_from_claim_ids"]=["d"]
    detail=_claim("d","TF_4H","中周期仍明显伸展","4H","DETAIL","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION")
    result=audit_repetition([summary,detail])
    assert result["duplicate_pair_count"]==0


def test_distinct_timeframe_extension_facts_are_not_semantic_duplicates():
    claims=[
        _claim("a","TF_4H","中周期位置明显伸展","4H","DETAIL","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION"),
        _claim("b","TF_1D","日线位置保持明显伸展","1D","DETAIL","EXTENSION:1D",["TF1D_STRUCTURE"],"EXTENSION"),
    ]
    assert audit_repetition(claims)["duplicate_pair_count"]==0


def test_same_words_with_distinct_timeframe_prices_and_facts_are_a_signal_not_a_failure():
    claims=[
        _claim("a","TF_4H","当前价格2432.75，显示中期趋势仍偏多","4H","DETAIL","TIMEFRAME_STRUCTURE:4H",["TF4H_SUMMARY"]),
        _claim("b","TF_1D","当前价格2515.91，显示中期趋势仍偏多","1D","DETAIL","TIMEFRAME_STRUCTURE:1D",["TF1D_SUMMARY"]),
    ]
    result=audit_repetition(claims)
    assert result["duplicate_pair_count"]==0
    assert result["allowed_structured_repetitions"][0]["duplicate_type"]=="CROSS_TIMEFRAME_FALSE_POSITIVE"


def test_verbatim_cross_timeframe_copy_paste_is_still_rejected():
    claims=[
        _claim("a","TF_4H","趋势偏多但需要注意风险","4H","DETAIL","TIMEFRAME_STRUCTURE:4H",["TF4H_SUMMARY"]),
        _claim("b","TF_1D","趋势偏多但需要注意风险","1D","DETAIL","TIMEFRAME_STRUCTURE:1D",["TF1D_SUMMARY"]),
    ]
    result=audit_repetition(claims)
    assert result["duplicate_pair_count"]==1
    assert result["duplicate_pairs"][0]["duplicate_type"]=="CROSS_TIMEFRAME_COPY_PASTE"


def test_grounding_assigns_only_owned_timeframe_facts_and_keeps_eleven_sections():
    request,registry,report=_real_style_report("QUICK")
    pack=compile_report_context(registry,"QUICK")["provider_claim_pack"]
    grounded=ground_provider_report(copy.deepcopy(report),pack)
    assert len(grounded["sections"])==11
    for section in grounded["sections"]:
        if section["section_id"].startswith("TF_"):
            prefix={"TF_15M":"TF15_","TF_1H":"TF1H_","TF_4H":"TF4H_","TF_1D":"TF1D_","TF_1W":"TF1W_"}[section["section_id"]]
            assert section["fact_refs"] and all(ref.startswith(prefix) for ref in section["fact_refs"])
    claims=extract_claims("owned",grounded)
    assert all(claim["section_owner"]==claim["section_id"] for claim in claims)


def test_exact_summary_detail_without_new_evidence_is_rejected():
    summary=_claim("s","QUICK_SUMMARY","中周期明显伸展","4H","SUMMARY","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION")
    detail=_claim("d","TF_4H","中周期明显伸展","4H","DETAIL","EXTENSION:4H",["TF4H_STRUCTURE"],"EXTENSION")
    result=audit_repetition([summary,detail])
    assert result["duplicate_pairs"][0]["duplicate_type"]=="SUMMARY_DETAIL_WITHOUT_NEW_EVIDENCE"
