from dashboard.decision_engine import FlowContext, MarketContext, RiskContext, TimeframeContext, asof_timeframe_context, evaluate_decision
from dashboard.signal_identity import config_hash, signal_id
from dashboard.strategy_rules import StrategyParameters


def context(instrument="BTC-USDT", ts=1000):
    return MarketContext(instrument,"15m",ts,110,{"fast_ma":105,"slow_ma":100,"ema":109.8,"rsi":55,"atr":2,"volume_ratio":1.2})


def test_canonical_decision_is_identical_for_paper_and_backtest_callers():
    params=StrategyParameters(fast_ma=2,slow_ma=3)
    frames=TimeframeContext({"1H":{"trend":"Bullish","candle_close_ts":900},"4H":{"trend":"Bullish","candle_close_ts":800}},("1H","4H"),False,"multi-timeframe")
    a=evaluate_decision(params,context(),frames,FlowContext(False),RiskContext(),"historical-mtf-no-flow-v1")
    b=evaluate_decision(params,context(),frames,FlowContext(False),RiskContext(),"historical-mtf-no-flow-v1")
    assert a==b and a.signal_id==b.signal_id


def test_hash_and_signal_identity_are_stable_and_sensitive():
    assert config_hash({"b":2,"a":1})==config_hash({"a":1,"b":2})
    base=signal_id("v1",config_hash({"a":1}),"BTC-USDT","15m",100)
    assert base==signal_id("v1",config_hash({"a":1}),"BTC-USDT","15m",100)
    assert len({base,signal_id("v1",config_hash({"a":2}),"BTC-USDT","15m",100),signal_id("v1",config_hash({"a":1}),"ETH-USDT","15m",100),signal_id("v1",config_hash({"a":1}),"BTC-USDT","15m",101),signal_id("v2",config_hash({"a":1}),"BTC-USDT","15m",100)})==5


def test_asof_join_excludes_unclosed_and_future_higher_timeframes():
    rows={"1H":[{"candle_close_ts":900,"trend":"Bullish","confirmed":1},{"candle_close_ts":1100,"trend":"Bearish","confirmed":1},{"candle_close_ts":800,"trend":"Bearish","confirmed":0}]}
    selected=asof_timeframe_context(1000,rows,("1H",))
    assert selected.frames["1H"]["candle_close_ts"]==900
    changed={"1H":[rows["1H"][0],{**rows["1H"][1],"trend":"Bullish"}]}
    assert asof_timeframe_context(1000,changed,("1H",)).frames==selected.frames


def test_no_flow_mode_is_explicit_and_not_fabricated():
    decision=evaluate_decision(StrategyParameters(fast_ma=2,slow_ma=3),context(),flow=FlowContext(False))
    assert decision.flow_context["available"] is False
    assert decision.flow_context["score_mode"]=="historical-no-flow-normalized-100"
    assert next(x for x in decision.contributions if x["key"]=="flow")["status"]=="unavailable"

