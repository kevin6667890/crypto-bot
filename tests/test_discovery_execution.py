"""Discovery adapter integration and reproducibility checks."""
from __future__ import annotations
from dashboard.discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest

T=1_700_000_000
def rows():
    return [{"ts":T+i*900,"open":100+i,"high":101+i,"low":99+i,"close":100+i,"volume":100+i} for i in range(230)]
def run(**kwargs):
    return run_discovery_candidate_backtest(rows(),"BTC-USDT","15m","TREND_BREAKOUT",
      {"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14},T+200*900,T+229*900,
      DiscoveryExecutionConfig(**kwargs),dataset_fingerprint="dataset")

def test_real_template_runs_through_canonical_engine_and_is_reproducible():
    first,second=run(),run()
    assert first["trades"] and first["trades"] == second["trades"]
    evidence=first["discovery_evidence"]
    assert all(t["config_hash"] == evidence["candidate_config_hash"] for t in first["trades"])
    assert evidence == second["discovery_evidence"]

def test_execution_changes_do_not_change_parameter_hash_but_change_candidate_hash():
    base,changed=run(),run(trading_fee=.001)
    a,b=base["discovery_evidence"],changed["discovery_evidence"]
    assert a["parameter_hash"] == b["parameter_hash"]
    assert a["execution_hash"] != b["execution_hash"] != ""
    assert a["candidate_config_hash"] != b["candidate_config_hash"]

def test_evaluation_context_only_changes_evaluation_hash():
    base=run()["discovery_evidence"]
    other=run_discovery_candidate_backtest(rows(),"ETH-USDT","15m","TREND_BREAKOUT",{"slow_period":200,"atr_period":14,"fast_ma_type":"EMA","fast_period":20},T+200*900,T+229*900,dataset_fingerprint="dataset")["discovery_evidence"]
    assert base["parameter_hash"] == other["parameter_hash"]
    assert base["candidate_config_hash"] == other["candidate_config_hash"]
    assert base["evaluation_hash"] != other["evaluation_hash"]
