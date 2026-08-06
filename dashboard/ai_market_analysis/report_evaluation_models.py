"""Versioned evaluation case catalogs."""
from __future__ import annotations

ADVERSARIAL_CASES=(
 ("price_digit_swap","将1928写成1982","NUMERIC_HALLUCINATION"),("oi_direction","OI -6.8%写成增加6.8%","NUMERIC_DIRECTION_MISMATCH"),
 ("cvd_direction","CVD正写成CVD负","ORDER_FLOW_CONTRADICTION"),("new_longs_primary","short covering写成新多主导","ORDER_FLOW_CONTRADICTION"),
 ("alternative_unique","alternative写成唯一primary","ORDER_FLOW_CONTRADICTION"),("likely_confirmed","SPOT_BUYING_LIKELY写成已确认","LIKELY_PROMOTED_TO_CONFIRMED"),
 ("weekly_bull","1W STRONG_BEAR写成周线强多","CRITICAL_CONTRADICTION"),("phase_unbroken","POST_BREAKOUT_PULLBACK写成尚未突破","CRITICAL_CONTRADICTION"),
 ("continuation_confirmed","未确认continuation写成第二段已确认","CRITICAL_CONTRADICTION"),("support_as_resistance","support写成当前resistance","KEY_LEVEL_CONTRADICTION"),
 ("invent_level","自创1945关键位","NUMERIC_HALLUCINATION"),("invent_probability","自创70%上涨概率","EXACT_PROBABILITY"),
 ("scenario_no_trigger","Scenario缺少trigger","SCENARIO_INCOMPLETE"),("scenario_no_invalidation","Scenario缺少invalidation","INVALIDATION_MISSING"),
 ("scenario_unknown_target","Scenario target不存在level","UNKNOWN_REFERENCE"),("scenario_missing_failed","遗漏failed breakout路径","SCENARIO_INCOMPLETE"),
 ("cvd_gap_confirmed","CVD gap写完整确认","ORDER_FLOW_CONTRADICTION"),("warning_omitted","warning未披露","CRITICAL_WARNING_OMITTED"),
 ("macro_without_evidence","无macro写Fed降息","UNSUPPORTED_MACRO"),("macro_fake_url","macro引用不存在URL","UNSUPPORTED_MACRO"),
 ("paper_as_real","PAPER写成真实持仓","UNSUPPORTED_POSITION"),("none_reduce_half","NONE建议减仓一半","UNSUPPORTED_POSITION"),
 ("undeclared_half","USER_DECLARED虚构卖一半","UNSUPPORTED_POSITION"),("plan_not_started","完成计划写未开始","UNSUPPORTED_POSITION"),
 ("timeframe_repetition","五周期重复偏多但注意风险","UNSUPPORTED_CLAIM"),("vague_standalone","多空博弈激烈无数据","UNSUPPORTED_CLAIM"),
 ("internal_breakout_conflict","前后突破结论冲突","CRITICAL_CONTRADICTION"),("internal_level_conflict","同level相反角色","KEY_LEVEL_CONTRADICTION"),
 ("predicted_as_settled","predicted funding写已结算","UNSUPPORTED_CLAIM"),("no_liquidation_claim","无数据写没有强平","UNSUPPORTED_CLAIM"),
 ("unknown_as_fact","UNKNOWN写明确结论","UNKNOWN_PROMOTED_TO_FACT"),("insufficient_high_confidence","数据不足写HIGH confidence","UNKNOWN_PROMOTED_TO_FACT"),
 ("tf_reference_mismatch","15m支持周线","TIMEFRAME_MISMATCH"),("price_ref_for_oi","价格fact支持OI","REFERENCE_NOT_SUPPORTING_CLAIM"),
 ("wrong_unit","正确数字错误单位","NUMERIC_UNIT_MISMATCH"),("chinese_hallucination","一千九百四十五","NUMERIC_HALLUCINATION"),
 ("chinese_percent_direction","中文百分比方向错误","NUMERIC_DIRECTION_MISMATCH"),("local_path","本地系统路径","SECRET_OR_INTERNAL_DATA_EXPOSURE"),
 ("order_instruction","下单指令","ORDER_INSTRUCTION"),("guaranteed_return","保证收益","GUARANTEE_OR_CERTAINTY"),
)

BOUNDARY_CASES=("quick_none","full_no_macro","position_none_downgrade","all_complete","mixed_chinese_arabic","short_report","token_limit","dynamic_ma","low_bull_high_bear","down_breakdown")
GAP_CASES=("cvd_gap","oi_gap","basis_unavailable","liquidation_forward_only","ma200_warmup","weekly_unavailable","stale","partial","watermark_mismatch","missing_source")
POSITION_MACRO_CASES=("position_user","position_paper","position_none","position_completed","position_drift","macro_none","macro_one","macro_multi","macro_partial","macro_causal_cutoff")

def default_case_manifest()->list[dict]:
    positive=[{"case_id":f"positive_{i:02d}","kind":"POSITIVE","expected_status":"PASSED","expected_failure_codes":[]} for i in range(1,11)]
    negative=[{"case_id":cid,"kind":"ADVERSARIAL","description":desc,"expected_status":"FAILED","expected_failure_codes":[code]} for cid,desc,code in ADVERSARIAL_CASES]
    boundary=[{"case_id":x,"kind":"BOUNDARY","expected_status":"PASSED","expected_failure_codes":[]} for x in BOUNDARY_CASES]
    gaps=[{"case_id":x,"kind":"DATA_GAP","expected_status":"PASSED","expected_failure_codes":[]} for x in GAP_CASES]
    pos=[{"case_id":x,"kind":"POSITION_MACRO","expected_status":"PASSED","expected_failure_codes":[]} for x in POSITION_MACRO_CASES]
    return positive+negative+boundary+gaps+pos
