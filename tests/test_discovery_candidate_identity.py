"""Candidate semantic identity and deterministic sampling contracts."""
from __future__ import annotations
import random
import pytest
import dashboard.discovery_features as discovery_features
import dashboard.discovery_identity as discovery_identity
from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from dashboard.discovery_identity import (build_parameter_identity, build_candidate_identity, build_evaluation_identity,
    normalize_template_parameters, DISCOVERY_PARAMETER_IDENTITY_VERSION, DISCOVERY_CANDIDATE_IDENTITY_VERSION, DISCOVERY_EVALUATION_IDENTITY_VERSION)
from dashboard.discovery_service import DiscoveryService, DISCOVERY_SAMPLER_VERSION
from dashboard.discovery_service import FOLDS
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository

P={"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":False}

def test_canonical_parameter_identity_ignores_key_order_and_rejects_dead_fields():
    assert build_parameter_identity("TREND_BREAKOUT",P) == build_parameter_identity("TREND_BREAKOUT",dict(reversed(list(P.items()))))
    with pytest.raises(ValueError, match="Unknown or inactive"):
        normalize_template_parameters("TREND_BREAKOUT",{**P,"stop_atr":1})
    with pytest.raises(ValueError, match="Unknown or inactive"):
        normalize_template_parameters("TREND_BREAKOUT",{**P,"minimum_volume_ratio":1.0})

@pytest.mark.parametrize("value",["false",0,1,None])
def test_volume_enabled_requires_a_json_boolean(value):
    with pytest.raises(ValueError,match="volume_enabled"):
        normalize_template_parameters("TREND_BREAKOUT",{**P,"volume_enabled":value})

@pytest.mark.parametrize("key,value",[("fast_period",True),("slow_period","200"),("atr_period",14.0),("rsi_lower","35"),("rsi_upper",False)])
def test_integer_parameters_reject_coercible_non_integers(key,value):
    template="MEAN_REVERSION" if key.startswith("rsi_") else "TREND_BREAKOUT"
    with pytest.raises(ValueError,match=key): normalize_template_parameters(template,{**P,key:value})

@pytest.mark.parametrize("key,value",[("minimum_volume_ratio","1.0"),("minimum_volume_ratio",float("nan")),("maximum_distance",True),("maximum_distance",float("inf"))])
def test_float_parameters_require_finite_json_numbers(key,value):
    template="TREND_PULLBACK" if key=="maximum_distance" else "TREND_BREAKOUT"
    parameters={**P,"volume_enabled":True,key:value}
    with pytest.raises(ValueError,match=key): normalize_template_parameters(template,parameters)

def test_identity_uses_exported_feature_version_source(monkeypatch):
    before=build_parameter_identity("TREND_BREAKOUT",P)
    assert discovery_identity.FEATURE_VERSION == discovery_features.FEATURE_VERSION
    monkeypatch.setattr(discovery_identity,"FEATURE_VERSION","test-feature-version")
    assert build_parameter_identity("TREND_BREAKOUT",P) != before

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

def test_real_worker_adapter_engine_runs_all_five_folds_without_holdout(tmp_path):
    """A real 4H candidate crosses worker, adapter, features and shared execution."""
    step=4*3600; start=FOLDS[0][0]; end=FOLDS[-1][3]
    candles=[{"ts":t,"open":100+i*.2,"high":100.6+i*.2,"low":99.0+i*.2,"close":100.5+i*.2,"volume":100+i,"confirmed":1} for i,t in enumerate(range(start,end,step))]
    repo=ResearchRepository(tmp_path/"research.db"); repo.upsert_candles("BTC-USDT","4H",candles)
    dataset=repo.create_or_get_discovery_dataset("five-fold-real",start,end,["BTC-USDT"],["4H"])
    repo.upsert_discovery_partition(dataset["id"],"BTC-USDT","4H",candles,{"expected_rows":len(candles),"actual_rows":len(candles),"missing_rows":0,"duplicate_rows":0,"fingerprint":"real-five-fold","status":"COMPLETE"})
    dataset=repo.finish_discovery_dataset(dataset["id"]); service=DiscoveryService(repo,JobQueue(tmp_path/"jobs.db",autostart=False))
    started=service.start({"dataset_id":dataset["id"],"instrument":"BTC-USDT","timeframe":"4H","templates":["TREND_BREAKOUT"],"trial_budget":1,"seed":7,"execution_assumptions":{}})
    fixed=normalize_template_parameters("TREND_BREAKOUT",P); ph=build_parameter_identity("TREND_BREAKOUT",fixed)
    service._candidate_definitions=lambda *_: ([("TREND_BREAKOUT",fixed,ph)],{"sampler_version":DISCOVERY_SAMPLER_VERSION,"sampling_attempts":1,"duplicate_samples_rejected":0,"unique_candidates_generated":1,"sampling_attempt_limit":1})
    payload={"discovery_run_id":started["id"],"dataset_id":dataset["id"],"instrument":"BTC-USDT","timeframe":"4H","templates":["TREND_BREAKOUT"],"trial_budget":1,"seed":7,"execution_assumptions":{}}
    service._run_job(1,payload,lambda *_:None)
    with repo.connect() as c:
        candidate=dict(c.execute("SELECT * FROM strategy_discovery_candidates").fetchone()); folds=[dict(x) for x in c.execute("SELECT * FROM strategy_discovery_folds ORDER BY fold_number")]
    evidence=[__import__("json").loads(x["metrics"])["fold_evidence"] for x in folds]
    assert len(folds)==5 and all(x["status"]=="COMPLETED" for x in folds)
    assert sum(__import__("json").loads(x["metrics"])["total_trades"] for x in folds)>0
    assert all(x["validation_end_ts"]==FOLDS[i][3] for i,x in enumerate(folds))
    assert all(x["effective_engine_end_ts"]==FOLDS[i][3]-step for i,x in enumerate(evidence))
    assert all(x["parameter_hash"]==candidate["parameter_hash"] for x in evidence)
    aggregate=__import__("json").loads(candidate["aggregate_metrics"])
    assert aggregate["parameter_hash"]==candidate["parameter_hash"] and aggregate["total_trades"]>0
