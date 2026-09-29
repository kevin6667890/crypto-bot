from research_engine.cross_asset import align_confirmed, audit_confirmed_alignment
from research_engine.derivatives import evaluate_coverage

def test_alignment_audit_rejects_gaps_and_duplicates_without_fill():
    primary=[{"ts":1,"confirmed":1},{"ts":2,"confirmed":1},{"ts":3,"confirmed":1}]
    context=[{"ts":1,"confirmed":1},{"ts":3,"confirmed":1},{"ts":3,"confirmed":1}]
    audit=audit_confirmed_alignment(primary,context)
    assert audit.status == "UNAVAILABLE" and audit.primary_missing_context == 1
    assert [row[0]["ts"] for row in align_confirmed(primary,context)] == [1,3]

def test_derivatives_coverage_is_fail_closed():
    bad=evaluate_coverage(dataset="OI",source="OKX",symbol="BTC-USDT-SWAP",exchange="OKX",first_ts=1,last_ts=2,resolution="1m",expected_rows=100,observed_rows=95,missing_ratio=.05)
    good=evaluate_coverage(dataset="FUNDING",source="OKX",symbol="BTC-USDT-SWAP",exchange="OKX",first_ts=1,last_ts=2,resolution="8h",expected_rows=10,observed_rows=10,missing_ratio=0)
    assert bad.validation_status == "UNAVAILABLE" and good.validation_status == "COMPLETE"
