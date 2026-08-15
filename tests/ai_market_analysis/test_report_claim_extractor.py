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
