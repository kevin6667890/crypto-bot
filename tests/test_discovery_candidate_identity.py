"""Candidate semantic identity and deterministic sampling contracts."""
from __future__ import annotations
import random
import pytest
from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_identity import (build_parameter_identity, build_candidate_identity, build_evaluation_identity,
    normalize_template_parameters, DISCOVERY_PARAMETER_IDENTITY_VERSION, DISCOVERY_CANDIDATE_IDENTITY_VERSION, DISCOVERY_EVALUATION_IDENTITY_VERSION)
from dashboard.discovery_service import DiscoveryService, DISCOVERY_SAMPLER_VERSION

P={"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":False}

def test_canonical_parameter_identity_ignores_key_order_and_rejects_dead_fields():
    assert build_parameter_identity("TREND_BREAKOUT",P) == build_parameter_identity("TREND_BREAKOUT",dict(reversed(list(P.items()))))
    with pytest.raises(ValueError, match="Unknown or inactive"):
        normalize_template_parameters("TREND_BREAKOUT",{**P,"stop_atr":1})

def test_execution_only_changes_candidate_not_parameter_identity():
    one,two=DiscoveryExecutionConfig(),DiscoveryExecutionConfig(trading_fee=.001)
    assert build_parameter_identity("TREND_BREAKOUT",P)==build_parameter_identity("TREND_BREAKOUT",P)
    assert build_candidate_identity("TREND_BREAKOUT",P,one.execution_hash()) != build_candidate_identity("TREND_BREAKOUT",P,two.execution_hash())

def test_evaluation_context_only_changes_evaluation_identity():
    candidate=build_candidate_identity("TREND_BREAKOUT",P,DiscoveryExecutionConfig().execution_hash())
    a=build_evaluation_identity(candidate,"BTC-USDT","15m",1,2,"x")
    assert a != build_evaluation_identity(candidate,"ETH-USDT","15m",1,2,"x")
    assert a != build_evaluation_identity(candidate,"BTC-USDT","1H",1,2,"x")
    assert a != build_evaluation_identity(candidate,"BTC-USDT","15m",1,3,"x")
    assert a != build_evaluation_identity(candidate,"BTC-USDT","15m",1,2,"y")

def test_sampler_is_template_semantic_deterministic_and_unique():
    service=DiscoveryService.__new__(DiscoveryService)
    first,meta=service._candidate_definitions(random.Random(7),["TREND_PULLBACK","MEAN_REVERSION","VOLATILITY_BREAKOUT","TREND_BREAKOUT"],12)
    second,_=service._candidate_definitions(random.Random(7),["TREND_PULLBACK","MEAN_REVERSION","VOLATILITY_BREAKOUT","TREND_BREAKOUT"],12)
    assert first==second and meta["sampler_version"]==DISCOVERY_SAMPLER_VERSION
    assert len({(t,h) for t,_,h in first})==12
    for template,params,_ in first:
        assert not {"stop_atr","risk_reward","cooldown_bars"}&set(params)
        assert ("rsi_lower" in params)==(template=="MEAN_REVERSION")
        assert ("maximum_distance" in params)==(template=="TREND_PULLBACK")
        if not params["volume_enabled"]: assert "minimum_volume_ratio" not in params

def test_adapter_returns_persistable_versions_and_normalized_parameters():
    rows=[{"ts":1_700_000_000+i*900,"open":100+i,"high":101+i,"low":99+i,"close":100+i,"volume":100+i} for i in range(230)]
    evidence=run_discovery_candidate_backtest(rows,"BTC-USDT","15m","TREND_BREAKOUT",P,rows[200]["ts"],rows[-1]["ts"],dataset_fingerprint="fixture")["discovery_evidence"]
    assert evidence["parameter_hash"]==build_parameter_identity("TREND_BREAKOUT",evidence["parameters"])
    assert (evidence["parameter_identity_version"],evidence["candidate_identity_version"],evidence["evaluation_identity_version"]) == (DISCOVERY_PARAMETER_IDENTITY_VERSION,DISCOVERY_CANDIDATE_IDENTITY_VERSION,DISCOVERY_EVALUATION_IDENTITY_VERSION)
