from __future__ import annotations

from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_identity import build_parameter_identity
from dashboard.strategy_v2 import (LIVE_STRATEGY_VERSION, PARAMETER_SCHEMA, TEMPLATES,
    classify_regime_v2, evaluate_v2, normalize_parameters)

def trend_rows():
    rows=[]
    for i in range(220):
        close=100+i*.05; rows.append({"ts":i*900,"open":close-.02,"high":close+1,"low":close-1,"close":close,"volume":100})
    for o,c in [(110.9,110.5),(110.4,110.7),(110.6,110.9),(110.8,111.1),(111,114)]:
        rows.append({"ts":len(rows)*900,"open":o,"high":max(o,c)+1,"low":min(o,c)-1,"close":c,"volume":200})
    return rows

def range_rows():
    import math
    rows=[]
    for i in range(230):
        close=100+math.sin(i*.1)*.5; rows.append({"ts":i*900,"open":close-.05,"high":close+.2,"low":close-.2,"close":close,"volume":100})
    previous=rows[-1]["close"]
    rows += [{"ts":230*900,"open":previous-.2,"high":previous+.2,"low":previous-1,"close":previous+.1,"volume":100}, {"ts":231*900,"open":previous+.1,"high":previous+3,"low":previous,"close":previous+2,"volume":100}]
    return rows

def test_v1_identity_is_unchanged_and_v2_is_distinct():
    p={"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":False}
    assert build_parameter_identity("TREND_BREAKOUT",p)==build_parameter_identity("TREND_BREAKOUT",dict(reversed(list(p.items()))))
    ev=evaluate_v2("TREND_BREAKOUT_V2",{},trend_rows(),220,"BTC-USDT","15m","dataset",{"fold":1})
    assert ev["evidence"]["parameter_hash"] != build_parameter_identity("TREND_BREAKOUT",p)

def test_regime_is_deterministic_and_causal():
    rows=trend_rows(); a=evaluate_v2("TREND_PULLBACK_V2",{},rows,221)["evidence"]
    changed=rows+[{"ts":999999,"open":1,"high":2,"low":.5,"close":1,"volume":1}]
    b=evaluate_v2("TREND_PULLBACK_V2",{},changed,221)["evidence"]
    assert a["regime"]==b["regime"] and a["evaluation_id"]==b["evaluation_id"]
    assert a["regime"]["code"]=="BULL_TREND"

def test_trend_templates_have_causal_prior_levels_and_all_layers():
    rows=trend_rows(); pull=evaluate_v2("TREND_PULLBACK_V2",{},rows,221)
    assert pull["action"]=="LONG" and pull["evidence"]["stop_distance"]>0
    breakout=evaluate_v2("TREND_BREAKOUT_V2",{},rows,224)
    assert breakout["evidence"]["causal_feature_timestamps"]["breakout_level_end"]==rows[223]["ts"]
    assert {"regime","setup_gates","trigger_gates","stop_mode","exit_mode","position_sizing"} <= set(pull["evidence"])

def test_range_reversion_is_blocked_in_trend_and_requires_reversal():
    rows=trend_rows(); value=evaluate_v2("RANGE_MEAN_REVERSION_V2",{},rows,221)
    assert value["action"]=="WAIT" and not value["evidence"]["setup_gates"]["regime"]
    assert "trigger" in value["evidence"]["setup_gates"]

def test_parameter_contract_is_bounded_and_canonical():
    assert all("type" in x and "templates" in x and "group" in x for x in PARAMETER_SCHEMA.values())
    a=normalize_parameters("TREND_PULLBACK_V2",{"risk_per_trade":.01})
    b=normalize_parameters("TREND_PULLBACK_V2",{"risk_per_trade":.01})
    assert a==b

def test_pullback_real_features_to_canonical_execution_evidence():
    rows=trend_rows()
    result=run_discovery_candidate_backtest(rows,"BTC-USDT","15m","TREND_PULLBACK_V2",{},rows[200]["ts"],rows[-1]["ts"],DiscoveryExecutionConfig(),"fixture")
    assert result["v2_evaluations"] and result["discovery_evidence"]["historical_input_policy"]=="PRICE_ONLY_OHLCV"
    assert all(x.get("strategy_version")==LIVE_STRATEGY_VERSION for x in result["trades"])

def test_breakout_and_range_real_features_to_canonical_execution_evidence():
    trend=trend_rows(); breakout=run_discovery_candidate_backtest(trend,"BTC-USDT","15m","TREND_BREAKOUT_V2",{},trend[200]["ts"],trend[-1]["ts"],dataset_fingerprint="fixture")
    ranged=range_rows(); mean=run_discovery_candidate_backtest(ranged,"BTC-USDT","15m","RANGE_MEAN_REVERSION_V2",{},ranged[200]["ts"],ranged[-1]["ts"],dataset_fingerprint="fixture")
    assert breakout["v2_evaluations"] and mean["v2_evaluations"]
    assert evaluate_v2("RANGE_MEAN_REVERSION_V2",{},ranged,230)["action"]=="LONG"

def test_v2_never_requests_flow_and_caps_notional_in_execution():
    rows=trend_rows(); result=run_discovery_candidate_backtest(rows,"BTC-USDT","15m","TREND_PULLBACK_V2",{"max_notional_fraction":.1},rows[200]["ts"],rows[-1]["ts"],dataset_fingerprint="fixture")
    assert not result["discovery_evidence"]["flow_history_requested"]
    for trade in result["trades"]: assert trade["position_size"]*trade["entry_price"] <= 1000.1
