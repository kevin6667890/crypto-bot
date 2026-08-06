from __future__ import annotations

from copy import deepcopy

import pytest

from dashboard.ai_market_analysis.orderflow_attribution import classify_orderflow
from dashboard.ai_market_analysis.orderflow_metrics import compute_phase_metrics, price_oi_quadrant


def metric(price=10.0, oi=.02, cvd=100.0, volume="EXPANDING", *, long=0, short=0,
           cvd_gap=False, oi_gap=False, mismatch=False, observations=3, unit="USD"):
    q=price_oi_quadrant(price,price/1000,oi,1,1000)
    return {"price_change":price,"price_change_pct":price/1000,"volume_regime":volume,"quadrant":q,
            "cvd":{"signed_delta":None if cvd_gap else cvd,"trade_count":100,"status":"PARTIAL" if cvd_gap else "VALID"},
            "oi":{"absolute_change":None if oi_gap or observations<2 else oi*1000,"percentage_change":None if oi_gap or observations<2 else oi,
                  "observation_count":observations,"unit":unit,"status":"PARTIAL" if oi_gap else "VALID"},
            "basis":{"status":"VALID"},"funding":{},
            "liquidation":{"long_notional":long,"short_notional":short,"feed_complete":False},
            "quality":{"cvd_gap":cvd_gap,"oi_gap":oi_gap,"basis_gap":False,"watermark_mismatch":mismatch,"overall":"PARTIAL" if cvd_gap or oi_gap else "VALID"}}


@pytest.mark.parametrize(("kwargs","expected"),[
    ({"price":10,"oi":.02,"cvd":100},"NEW_LONGS_DOMINANT"),
    ({"price":10,"oi":-.02,"cvd":100},"SHORT_COVERING_DOMINANT"),
    ({"price":10,"oi":-.02,"cvd":-10},"SHORT_COVERING_DOMINANT"),
    ({"price":10,"oi":0,"cvd":100},"SPOT_BUYING_LIKELY"),
    ({"price":-10,"oi":.02,"cvd":-100},"NEW_SHORTS_DOMINANT"),
    ({"price":-10,"oi":-.02,"cvd":-100},"LONG_UNWINDING_DOMINANT"),
    ({"price":0.01,"oi":-.03,"cvd":0,"long":100,"short":100},"TWO_SIDED_DELEVERAGING"),
    ({"price":0.01,"oi":.03,"cvd":0},"MIXED_POSITIONING"),
    ({"price":10,"oi":-.02,"cvd":100,"short":100},"SHORT_COVERING_DOMINANT"),
    ({"price":-10,"oi":-.02,"cvd":-100,"long":100},"LONG_UNWINDING_DOMINANT"),
])
def test_attribution_quadrants_and_assists(kwargs,expected):
    assert classify_orderflow(metric(**kwargs))["primary"]==expected


def test_short_covering_preserves_active_buying_alternative():
    out=classify_orderflow(metric(price=10,oi=-.03,cvd=300))
    assert out["alternatives"][0]=="ACTIVE_BUYING_CONTRIBUTED"


def test_weak_cvd_reduces_short_covering_confidence():
    strong=classify_orderflow(metric(price=10,oi=-.03,cvd=300))
    weak=classify_orderflow(metric(price=10,oi=-.03,cvd=-1))
    assert strong["confidence"] in {"HIGH","MEDIUM"} and weak["confidence"]!="HIGH"


@pytest.mark.parametrize("field",["cvd_gap","oi_gap"])
def test_critical_gap_never_high(field):
    out=classify_orderflow(metric(**{field:True}))
    assert out["confidence"]!="HIGH"


def test_watermark_mismatch_is_insufficient():
    assert classify_orderflow(metric(mismatch=True))["primary"]=="INSUFFICIENT_EVIDENCE"


def test_single_oi_observation_only_reports_absolute_observation():
    rows={"oi":[{"timestamp":0,"value":100,"unit":"USD"}],"cvd":[{"timestamp":0,"delta":1,"trade_count":20}]}
    out=compute_phase_metrics({"start":0,"end":900},[{"ts":0,"open":100,"close":101,"volume":10}],rows)
    assert out["oi"]["start"]==100 and out["oi"]["absolute_change"] is None


def test_predicted_funding_is_not_settled():
    rows={"funding":[{"timestamp":0,"state":"PREDICTED","rate":.001}]}
    out=compute_phase_metrics({"start":0,"end":900},[{"ts":0,"open":100,"close":101,"volume":10}],rows)
    assert out["funding"]["last_settled"] is None and out["funding"]["predicted"]==.001
    assert out["funding"]["settled_count"]==0 and out["funding"]["predicted_count"]==1


def test_basis_gap_prevents_change():
    rows={"basis":[{"timestamp":0,"basis_pct":.01},{"timestamp":600,"basis_pct":.02,"gap":True}]}
    out=compute_phase_metrics({"start":0,"end":900},[{"ts":0,"open":100,"close":101,"volume":10}],rows,bucket_seconds=300)
    assert out["basis"]["change"] is None and out["basis"]["status"]=="PARTIAL"


def test_absent_liquidation_keeps_feed_warning():
    out=compute_phase_metrics({"start":0,"end":900},[{"ts":0,"open":100,"close":101,"volume":10}],{})
    assert out["liquidation"]["event_count"]==0 and "absence" in out["liquidation"]["warning"]


def test_funding_alone_never_decides_classification():
    m=metric(price=.01,oi=0,cvd=0); m["funding"]={"last_settled":.1}
    assert classify_orderflow(m)["primary"] not in {"NEW_LONGS_DOMINANT","NEW_SHORTS_DOMINANT"}


def test_pagination_order_does_not_change_metrics():
    source={"cvd":[{"timestamp":0,"delta":2,"trade_count":10},{"timestamp":300,"delta":3,"trade_count":10}],
            "oi":[{"timestamp":0,"value":100,"unit":"USD"},{"timestamp":300,"value":99,"unit":"USD"}]}
    args=({"start":0,"end":600},[{"ts":0,"open":100,"close":101,"volume":10}])
    a=compute_phase_metrics(*args,source,bucket_seconds=300)
    b=compute_phase_metrics(*args,{k:list(reversed(v)) for k,v in source.items()},bucket_seconds=300)
    assert a==b


def test_future_rows_do_not_rewrite_old_window():
    source={"cvd":[{"timestamp":0,"delta":2,"trade_count":10}],"oi":[{"timestamp":0,"value":100,"unit":"USD"}]}
    args=({"start":0,"end":300},[{"ts":0,"open":100,"close":101,"volume":10}])
    before=compute_phase_metrics(*args,source,bucket_seconds=300)
    future=deepcopy(source); future["cvd"].append({"timestamp":600,"delta":999}); future["oi"].append({"timestamp":600,"value":999,"unit":"USD"})
    assert before==compute_phase_metrics(*args,future,bucket_seconds=300)
