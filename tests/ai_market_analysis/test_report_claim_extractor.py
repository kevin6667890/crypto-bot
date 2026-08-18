from dashboard.ai_market_analysis.report_claim_extractor import split_sentences,extract_claims

def test_sentence_boundaries_preserve_decimal_range_url_parentheses():
    text="价格1,928.5美元，区间1885-1892（有效）。来源https://example.com/a.b；下一句!"
    parts=split_sentences(text);assert len(parts)==3 and "1,928.5" in parts[0] and "1885-1892" in parts[0] and "a.b" in parts[1]

def test_claim_identity_type_modality_timeframe_refs_stable():
    report={"sections":[{"section_id":"TF_1W","body":"周线尚未确认反转。","fact_refs":["TF1W_SUMMARY"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    a=extract_claims("r",report);b=extract_claims("r",report);assert a==b and a[0]["claim_type"]=="TIMEFRAME_TREND" and a[0]["modality"]=="UNCERTAIN" and a[0]["timeframe_mentions"]==["周线"]

def test_macro_unavailable_status_is_a_limitation_not_a_macro_claim():
    report={"sections":[{"section_id":"QUICK_SUMMARY","body":"宏观证据未纳入。","fact_refs":["MACRO_UNAVAILABLE"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claim=extract_claims("r",report)[0]
    assert claim["claim_type"]=="LIMITATION"

def test_orderflow_phase_is_not_misclassified_as_market_timeline():
    report={"sections":[{"section_id":"ORDER_FLOW",
        "body":"\u5f53\u524d\u8ba2\u5355\u6d41\u9636\u6bb5\u4e3a CURRENT\uff0c\u8d28\u91cf\u90e8\u5206\u53ef\u7528",
        "fact_refs":["FLOW_PHASE_03","DATA_QUALITY"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claim=extract_claims("report",report)[0]
    assert claim["claim_type"]=="ORDER_FLOW_ATTRIBUTION"

def test_waiting_for_clearer_confirmation_is_uncertain_not_confirmed():
    report={"sections":[{"section_id":"CONCLUSION",
        "body":"\u5efa\u8bae\u7b49\u5f85\u66f4\u660e\u786e\u7684\u8ba2\u5355\u6d41\u786e\u8ba4",
        "fact_refs":["FLOW"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claim=extract_claims("report",report)[0]
    assert claim["modality"]=="UNCERTAIN"

def test_partial_orderflow_transition_is_scoped_and_uncertain():
    report={"sections":[{"section_id":"ORDER_FLOW",
        "body":"\u90e8\u5206\u53ef\u7528\u7684\u8ba2\u5355\u6d41\u8f6c\u53d8\u8bc1\u636e\u663e\u793a\u89e3\u91ca\u4e3a\u6df7\u5408\u6301\u4ed3",
        "fact_refs":["FLOW_TRANSITION_01"],"level_refs":[],"scenario_refs":[],"macro_refs":[],"position_refs":[]}]}
    claim=extract_claims("report",report)[0]
    assert claim["claim_type"]=="ORDER_FLOW_ATTRIBUTION"
    assert claim["modality"]=="UNCERTAIN"
