from dashboard.discovery_diagnostics import fixed_path_cost_attribution, lifecycle_summary
from dashboard.discovery_execution import run_discovery_candidate_backtest
from dashboard.discovery_identity import build_parameter_identity
from dashboard.strategy_v2_1 import TEMPLATES, StrategyV21Evaluator
from tests.test_strategy_v2 import trend_rows, range_rows

def run(template,rows):
    return run_discovery_candidate_backtest(rows,"BTC-USDT","15m",template,{},rows[200]["ts"],rows[-1]["ts"],dataset_fingerprint="fixture")

def test_v2_is_unchanged_and_v21_identities_are_distinct():
    v2=build_parameter_identity("TREND_PULLBACK_V2",{})
    assert v2==build_parameter_identity("TREND_PULLBACK_V2",{})
    assert v2!=build_parameter_identity("TREND_PULLBACK_V2_1",{})

def test_real_feature_lifecycle_execution_is_deterministic_and_one_trigger_per_setup():
    fixtures={"TREND_PULLBACK_V2_1":trend_rows(),"TREND_BREAKOUT_V2_1":trend_rows(),
              "RANGE_MEAN_REVERSION_V2_1":range_rows()}
    for template,rows in fixtures.items():
        first=run(template,rows); second=run(template,rows)
        assert first["discovery_evidence"]["candidate_config_hash"]==second["discovery_evidence"]["candidate_config_hash"]
        diagnostics=lifecycle_summary(first,900)
        assert diagnostics["maximum_triggers_from_one_setup"]<=1
        assert all(x["setup_activation_context"] for x in first["v2_evaluations"].values() if x.get("trigger_timestamp"))
        assert first["discovery_evidence"]["flow_history_requested"] is False
        assert all(x["strategy_version"]=="discovery-strategy-v2.1" for x in first["trades"])

def test_future_candles_cannot_change_past_lifecycle_evidence():
    rows=trend_rows(); a=run("TREND_BREAKOUT_V2_1",rows)
    extended=rows+[{"ts":rows[-1]["ts"]+900,"open":1,"high":2,"low":.5,"close":1,"volume":1}]
    b=run_discovery_candidate_backtest(extended,"BTC-USDT","15m","TREND_BREAKOUT_V2_1",{},rows[200]["ts"],rows[-1]["ts"],dataset_fingerprint="fixture")
    common=set(a["v2_evaluations"])&set(b["v2_evaluations"])
    assert all(a["v2_evaluations"][ts]==b["v2_evaluations"][ts] for ts in common)

def test_state_isolated_by_evaluator_candidate_instrument_and_fold():
    one=StrategyV21Evaluator("TREND_BREAKOUT_V2_1",{},"BTC-USDT","15m","d",{"fold":1})
    two=StrategyV21Evaluator("TREND_BREAKOUT_V2_1",{},"ETH-USDT","15m","d",{"fold":1})
    three=StrategyV21Evaluator("TREND_BREAKOUT_V2_1",{},"BTC-USDT","15m","d",{"fold":2})
    one.state="TRIGGERED"
    assert two.state==three.state=="IDLE" and len({id(one),id(two),id(three)})==3

def test_corrected_rules_record_crossing_reentry_rsi_reversal_and_causal_regime():
    breakout=run("TREND_BREAKOUT_V2_1",trend_rows())
    bt=[x for x in breakout["v2_evaluations"].values() if x.get("trigger_timestamp")]
    assert all(x["gates"]["actual_crossing"] and x["frozen_level"] is not None for x in bt)
    mean=run("RANGE_MEAN_REVERSION_V2_1",range_rows())
    mt=[x for x in mean["v2_evaluations"].values() if x.get("trigger_timestamp")]
    assert all(x["gates"]["band_reentry"] and x["gates"]["rsi_directional_reversal"] for x in mt)
    assert all(x["regime_stability"]["confirmation_count"]>=1 for x in mean["v2_evaluations"].values())

def test_each_corrected_template_is_independently_reachable_in_fixed_registry():
    from dashboard.discovery_service import DiscoveryService
    service=object.__new__(DiscoveryService)
    import random
    for template in TEMPLATES:
        definitions,_=service._candidate_definitions(random.Random(1),[template],1)
        assert len(definitions)==1 and definitions[0][0]==template

def test_fixed_path_zero_cost_diagnostic_does_not_change_signals_or_trades():
    result=run("TREND_PULLBACK_V2_1",trend_rows())
    before_signals=result["signal_count"]; before_trades=[(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]]
    attribution=fixed_path_cost_attribution(result,10000.)
    assert result["signal_count"]==before_signals
    assert [(x["signal_id"],x["entry_ts"],x["exit_ts"]) for x in result["trades"]]==before_trades
    assert abs(attribution["gross_profit_before_costs"]-attribution["total_cost_drag"]-attribution["net_profit"])<1e-6
