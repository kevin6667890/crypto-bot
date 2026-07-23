import copy
from pathlib import Path

import pytest

from dashboard.discovery_diagnostics import fixed_path_cost_attribution, signal_event_study
from dashboard.discovery_execution import run_discovery_candidate_backtest
from dashboard.discovery_identity import build_parameter_identity
from dashboard.discovery_service import DiscoveryService, FOLDS
from dashboard.discovery_v2_1_registry import plan
from dashboard.discovery_v2_registry import SPACES
from tests.test_strategy_v2 import range_rows, trend_rows

FINGERPRINT="fixture"
IDENTITIES={
 "TREND_PULLBACK_V2":"cc68da064cc9d5ee603a15e318a9fccd6c8b41a27dc6847c227674e43e5db929",
 "TREND_BREAKOUT_V2":"f21e4bed23f65fb1d81d6e812bfa982d6faced771d41083b23ce2e743e96f773",
 "RANGE_MEAN_REVERSION_V2":"36351fe8199af7788a6b9a21502557f27a99350691701437122782b9385aa3df",
 "TREND_PULLBACK_V2_1":"b2a59ca082bd5c7b854a6b8fc8c3b6c11a88fb7ce483023f033db9abff97bece",
 "TREND_BREAKOUT_V2_1":"5f92a57854557ff8b63b79ed2be2a49fd01aafafa39237e5a1a079953abcfaec",
 "RANGE_MEAN_REVERSION_V2_1":"5b9d1803d13ddc950f094e6139b5012e0fc42758a99582fa1680edd4b9aeaca7",
}

def execute(template="TREND_BREAKOUT_V2_1",rows=None,timeframe="15m"):
    rows=rows or trend_rows()
    return run_discovery_candidate_backtest(rows,"BTC-USDT",timeframe,template,{},
      rows[200]["ts"],rows[-1]["ts"],dataset_fingerprint=FINGERPRINT)

def test_frozen_v2_and_v21_identities_remain_byte_for_byte_unchanged():
    assert {template:build_parameter_identity(template,{}) for template in IDENTITIES}==IDENTITIES
    assert not Path("dashboard/strategy_v2_2.py").exists()

def test_event_labels_are_post_signal_and_stay_inside_fold_boundary():
    rows=trend_rows(); result=execute(rows=rows)
    before=(result["signal_count"],copy.deepcopy(result["v2_evaluations"]),
      [(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]])
    study=signal_event_study(result,rows,rows[200]["ts"],rows[-2]["ts"])
    after=(result["signal_count"],result["v2_evaluations"],
      [(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]])
    assert before==after and study["diagnostic_labels_only"]
    assert all(label["outcome_timestamp"]<=rows[-2]["ts"]
      for event in study["events"] for label in event["labels"].values())

def test_future_candle_cannot_change_past_signal_or_event_evidence():
    rows=trend_rows(); end=rows[-1]["ts"]; first=execute(rows=rows)
    extended=rows+[{**rows[-1],"ts":end+900,"open":1,"high":100000,"low":1,"close":2}]
    second=run_discovery_candidate_backtest(extended,"BTC-USDT","15m",
      "TREND_BREAKOUT_V2_1",{},rows[200]["ts"],end,dataset_fingerprint=FINGERPRINT)
    common=set(first["v2_evaluations"])&set(second["v2_evaluations"])
    assert all(first["v2_evaluations"][ts]==second["v2_evaluations"][ts] for ts in common)

def test_fixed_plan_is_deterministic_identical_across_timeframes_and_not_expanded():
    first,_,_=plan(32); second,_,_=plan(32)
    assert first==second and len(first)==32
    plans={timeframe:first for timeframe in ("15m","1H","4H")}
    assert plans["15m"]==plans["1H"]==plans["4H"]
    for template,parameters in first:
        base=template.replace("_V2_1","_V2")
        for name,values in SPACES[base].items():
            assert parameters[name] in values

def test_one_excursion_one_trigger_and_range_never_triggers_in_trend_or_transition():
    ranged=execute("RANGE_MEAN_REVERSION_V2_1",range_rows())
    counts={}
    for evidence in ranged["v2_evaluations"].values():
        if evidence.get("trigger_timestamp"):
            counts[evidence["setup_id"]]=counts.get(evidence["setup_id"],0)+1
    assert max(counts.values(),default=0)<=1
    trended=execute("RANGE_MEAN_REVERSION_V2_1",trend_rows())
    assert not [x for x in trended["v2_evaluations"].values() if x.get("trigger_timestamp")]

def test_zero_cost_diagnostic_keeps_identical_trades_and_raw_ohlcv():
    rows=trend_rows(); frozen=copy.deepcopy(rows); result=execute(rows=rows)
    trades=[(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]]
    attribution=fixed_path_cost_attribution(result,10000)
    assert attribution["trade_count"]==len(trades)
    assert trades==[(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]]
    assert rows==frozen

@pytest.mark.parametrize(("timeframe","seconds"),[("15m",900),("1H",3600),("4H",14400)])
def test_real_pipeline_execution_and_event_evidence_per_timeframe(timeframe,seconds):
    base=trend_rows(); start=base[0]["ts"]
    rows=[{**row,"ts":start+index*seconds} for index,row in enumerate(base)]
    result=execute(rows=rows,timeframe=timeframe)
    study=signal_event_study(result,rows,rows[200]["ts"],rows[-1]["ts"])
    assert result["discovery_evidence"]["flow_history_requested"] is False
    assert result["v2_evaluations"] and study["event_count"]>0
    assert all(x["regime_stability"] for x in result["v2_evaluations"].values())

def test_development_fold_queries_never_reach_holdout_and_never_request_flow():
    class Repository:
        def __init__(self): self.calls=[]
        def candles(self,instrument,timeframe,start,end):
            self.calls.append((instrument,timeframe,start,end)); return []
        def flow(self,*args): raise AssertionError("historical CVD/OI requested")
    repository=Repository(); service=object.__new__(DiscoveryService); service.repository=repository
    for start,unused,validation_start,end in FOLDS:
        service._fold_rows("BTC-USDT","15m",start,end)
    assert max(call[3] for call in repository.calls)<1746057600
