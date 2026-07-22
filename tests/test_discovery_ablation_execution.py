"""Real adapter-to-shared-engine coverage for Discovery component ablation."""
from __future__ import annotations

import pytest

from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_identity import build_candidate_identity, build_evaluation_identity

T=1_700_000_000
P={"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":True,"minimum_volume_ratio":1.0}


def breakout_candles():
    rows=[]
    for index in range(205):
        rows.append({"ts":T+index*900,"open":100.,"high":101.,"low":99.,"close":100.,"volume":100.,"confirmed":1})
    # This is the sole breakout.  Its deliberately low volume is the observable gate.
    rows.append({"ts":T+205*900,"open":100.,"high":111.,"low":99.,"close":110.,"volume":1.,"confirmed":1})
    rows.extend({"ts":T+index*900,"open":100.,"high":101.,"low":99.,"close":100.,"volume":100.,"confirmed":1} for index in range(206,209))
    return rows


def run(flags=None):
    rows=breakout_candles()
    return run_discovery_candidate_backtest(rows,"BTC-USDT","15m","TREND_BREAKOUT",P,rows[200]["ts"],rows[-1]["ts"],DiscoveryExecutionConfig(trading_fee=.001,slippage=.002,stop_loss_atr_multiplier=1.5,risk_reward_ratio=2.5),"ablation-fixture",flags)


def test_default_and_explicit_empty_ablation_are_fully_backward_compatible():
    omitted,empty=run(),run({})
    assert omitted["decisions"]==empty["decisions"] and omitted["trades"]==empty["trades"] and omitted["metrics"]==empty["metrics"]
    assert omitted["discovery_evidence"]==empty["discovery_evidence"]
    evidence=omitted["discovery_evidence"]
    assert evidence["removed_component"] is None and evidence["ablation_identity"] is None
    assert evidence["candidate_config_hash"]==build_candidate_identity("TREND_BREAKOUT",P,evidence["execution_hash"])
    assert evidence["evaluation_hash"]==build_evaluation_identity(evidence["candidate_config_hash"],"BTC-USDT","15m",breakout_candles()[200]["ts"],breakout_candles()[-1]["ts"],"ablation-fixture")


def test_real_volume_ablation_changes_signals_uses_canonical_execution_and_preserves_safety():
    base,ablated=run(),run({"removed_component":"VOLUME_CONFIRMATION"})
    source,changed=base["discovery_evidence"],ablated["discovery_evidence"]
    assert base["signal_count"]==0 and ablated["signal_count"]>base["signal_count"]
    assert ablated["trades"] and ablated["trades"][0]["entry_price"]==pytest.approx(100.2)
    assert source["parameter_hash"]==changed["parameter_hash"]
    assert source["execution_hash"]==changed["execution_hash"]
    assert source["candidate_config_hash"]!=changed["candidate_config_hash"]
    assert source["evaluation_hash"]!=changed["evaluation_hash"]
    assert changed["removed_component"]=="VOLUME_CONFIRMATION" and changed["ablation_identity"]
    assert changed["normalized_ablation_flags"]=={"removed_component":"VOLUME_CONFIRMATION"}
    # The common execution core still applies adverse costs and stop/target setup.
    trade=ablated["trades"][0]
    assert trade["fees"]>0 and trade["stop_loss"]<trade["entry_price"]<trade["take_profit"]
    assert base["trades"] is not ablated["trades"] and base["discovery_evidence"] is not ablated["discovery_evidence"]


def test_unknown_or_inapplicable_ablation_fails_before_execution():
    with pytest.raises(ValueError,match="UNKNOWN"):
        run({"removed_component":"UNKNOWN"})
    with pytest.raises(ValueError,match="INAPPLICABLE"):
        run({"removed_component":"RSI_ENTRY_GATE"})
