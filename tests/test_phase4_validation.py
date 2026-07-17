from copy import deepcopy
import pytest

from dashboard.gate_analysis import GATE_ORDER, aggregate_gate_funnel
from dashboard.market_regime import REGIME_VERSION, classify_regime
from dashboard.near_miss import counterfactual_outcome, identify_near_miss


def decision(asset="BTC-USDT", bias="LONG", failures=(), score=80):
    gates=[]
    for key in GATE_ORDER:
        passed=key not in failures and (key!="final_entry_allowed" or not failures)
        gates.append({"key":key,"label":key,"passed":passed,"applicable":key not in {"cvd_alignment","oi_context","flow_combined"},"blocking":key!="oi_context"})
    return {"signal_id":f"{asset}-{bias}-{','.join(failures)}","instrument":asset,"candle_close_ts":1700000000,"strategy_version":"v1","config_hash":"hash","bias":bias,"score":score,"warmed":True,"entry_allowed":not failures,"gate_results":gates,"failed_gates":list(failures),"indicator_values":{"fast_ma":110,"slow_ma":100,"ema":105,"rsi":50,"atr":2,"volume_ratio":1.1},"decision_input_summary":{"close":105},"timeframe_context":{"required_frames":["1H","4H"],"frame_biases":{"1H":bias,"4H":bias}},"flow_context":{"available":False},"risk_context":{"allowed":True},"regime":"Bull Trend","rejection_reason":", ".join(failures)}


def test_independent_and_sequential_rates():
    result=aggregate_gate_funnel([decision(),decision(failures=("ema_pullback",)),decision(failures=("minimum_score",))])
    pullback=next(x for x in result["gates"] if x["gate"]=="ema_pullback")
    score=next(x for x in result["gates"] if x["gate"]=="minimum_score")
    assert pullback["pass_rate"]==pytest.approx(200/3)
    assert pullback["conditional_pass_rate"]==pytest.approx(200/3)
    assert score["sequential_evaluated_count"]==2
    assert score["conditional_pass_rate"]==50


def test_exclusive_and_multiple_failures():
    result=aggregate_gate_funnel([decision(failures=("ema_pullback",)),decision(failures=("ema_pullback","minimum_score"))])
    gate=next(x for x in result["gates"] if x["gate"]=="ema_pullback")
    assert gate["exclusive_failure_count"]==1 and gate["combined_failure_count"]==1


def test_asset_and_long_short_grouping():
    result=aggregate_gate_funnel([decision(),decision("ETH-USDT","SHORT",("volume_ratio",))])
    gate=next(x for x in result["gates"] if x["gate"]=="volume_ratio")
    assert gate["long_pass_rate"]==100 and gate["short_pass_rate"]==0
    assert gate["per_asset_pass_rate"]=={"BTC-USDT":100,"ETH-USDT":0}


def test_empty_gate_data():
    result=aggregate_gate_funnel([])
    assert result["decision_count"]==0 and all(x["pass_rate"] is None for x in result["gates"])


def test_near_miss_single_gate_and_score_gap():
    item=identify_near_miss(decision(failures=("minimum_score",),score=72),{"minimum_score":75,"ema_pullback_distance":.0045,"rsi_min":35,"rsi_max":68,"minimum_volume_ratio":1})
    assert item and item["failed_gates"]==["minimum_score"] and item["score_gap"]==3


def test_near_miss_rejects_too_many_failures():
    item=identify_near_miss(decision(failures=("ema_pullback","volume_ratio","minimum_score"),score=72),{"minimum_score":75},max_failed_gates=2)
    assert item is None


def test_counterfactual_stop_first_tie_and_not_paper():
    near=identify_near_miss(decision(failures=("minimum_score",),score=72),{"minimum_score":75})
    outcome=counterfactual_outcome(near,[{"ts":1,"open":100,"high":105,"low":95,"close":101}],{"stop_loss_atr_multiplier":1,"risk_reward_ratio":2})
    assert outcome["first_trigger"]=="STOP" and outcome["paper_pnl_included"] is False
    assert outcome["mfe_r"]==2.5 and outcome["mae_r"]==2.5


def test_counterfactual_target_and_stop_paths():
    near=decision(failures=("minimum_score",),score=72); near.update({"bias":"LONG","indicator_values":{"atr":2}})
    target=counterfactual_outcome(near,[{"ts":1,"open":100,"high":104.1,"low":99,"close":104}],{"stop_loss_atr_multiplier":1,"risk_reward_ratio":2})
    stop=counterfactual_outcome(near,[{"ts":1,"open":100,"high":101,"low":97.9,"close":98}],{"stop_loss_atr_multiplier":1,"risk_reward_ratio":2})
    assert target["first_trigger"]=="2R" and stop["first_trigger"]=="STOP"


def test_regime_deterministic_unknown_and_versioned():
    a=classify_regime(120,{"fast_ma":110,"slow_ma":100,"atr":2})
    b=classify_regime(120,{"fast_ma":110,"slow_ma":100,"atr":2})
    assert a==b and a["name"]=="Bull Trend" and a["version"]==REGIME_VERSION
    assert classify_regime(100,{})["name"]=="Unknown"


def test_regime_uses_only_supplied_history():
    early=classify_regime(100,{"fast_ma":100,"slow_ma":100,"atr":.2},[{"close":99,"atr":.2}])
    future=deepcopy(early)
    assert early==future
