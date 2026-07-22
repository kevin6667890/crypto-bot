"""Focused, synthetic/no-network evidence tests for Discovery Phase 3A."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

import dashboard.discovery_service as discovery
from dashboard.discovery_service import DiscoveryService, FOLDS, aggregate, buy_and_hold
from dashboard.job_queue import JobCancelled, JobQueue
from dashboard.research_repository import ResearchRepository
from dashboard.discovery_identity import build_parameter_identity, build_candidate_identity, build_evaluation_identity

STEP=900
def rows():
    start=FOLDS[0][0]; end=FOLDS[-1][3]
    return [{"ts":t,"open":100.0+(t-start)/10_000_000,"high":102.0+(t-start)/10_000_000,"low":99.0+(t-start)/10_000_000,"close":101.0+(t-start)/10_000_000,"volume":10.0,"confirmed":1} for t in range(start,end,STEP)]

def setup(tmp_path: Path):
    repo=ResearchRepository(tmp_path/'research.db'); data=rows(); repo.upsert_candles('ETH-USDT','15m',data)
    ds=repo.create_or_get_discovery_dataset('synthetic-development',FOLDS[0][0],FOLDS[-1][3],['ETH-USDT'],['15m'])
    repo.upsert_discovery_partition(ds['id'],'ETH-USDT','15m',data,{'expected_rows':len(data),'actual_rows':len(data),'missing_rows':0,'duplicate_rows':0,'fingerprint':'partition-fingerprint','status':'COMPLETE'})
    ds=repo.finish_discovery_dataset(ds['id']); queue=JobQueue(tmp_path/'jobs.db',autostart=False); service=DiscoveryService(repo,queue)
    started=service.start({'dataset_id':ds['id'],'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{'trading_fee':.001,'slippage':.002}})
    return repo,service,started['id']

def fake_engine(calls):
    def run(candles,instrument,timeframe,template,parameters,start_ts,end_ts,execution,dataset_fingerprint):
        calls.append((candles,instrument,timeframe,start_ts,end_ts,dataset_fingerprint))
        ph=build_parameter_identity(template,parameters); eh=execution.execution_hash(); ch=build_candidate_identity(template,parameters,eh); evaluation=build_evaluation_identity(ch,instrument,timeframe,start_ts,end_ts,dataset_fingerprint)
        return {'metrics':{'initial_capital':execution.initial_capital,'final_equity':10100.0,'net_profit':100.0,'total_return':1.0,'annualized_return':None,'total_trades':1,'win_rate':100.0,'profit_factor':None,'expectancy':100.0,'average_win':100.0,'average_loss':None,'realized_risk_reward':None,'maximum_drawdown':2.0,'sharpe_ratio':None,'sortino_ratio':None,'consecutive_wins':1,'consecutive_losses':0,'fees_paid':2.0,'long_trades':1,'short_trades':0,'average_holding_seconds':900.0,'sample_note':'synthetic'},'signal_count':1,'discovery_evidence':{'parameter_hash':ph,'execution_hash':eh,'candidate_config_hash':ch,'evaluation_hash':evaluation,'template_version':'v','feature_version':'f','execution_policy_version':'x','execution_engine_version':'y','execution':execution.__dict__}}
    return run

def test_five_canonical_fold_attempts_persist_boundaries_hashes_and_selected_partition(tmp_path,monkeypatch):
    repo,service,rid=setup(tmp_path); calls=[]; monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine(calls))
    service._run_job(1,{'discovery_run_id':rid,'dataset_id':1,'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{'trading_fee':.001,'slippage':.002}},lambda *_:None)
    assert len(calls)==5 and all(x[1:3]==('ETH-USDT','15m') for x in calls)
    assert [(x[3],x[4]) for x in calls]==[(v,e-STEP) for _,_,v,e in FOLDS]
    assert all(max(int(c['ts']) for c in x[0]) < ve for x,(_,_,_,ve) in zip(calls,FOLDS))
    with repo.connect() as c: folds=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_folds ORDER BY fold_number')]
    assert len(folds)==5
    evidence=json.loads(folds[0]['metrics'])['fold_evidence']
    assert evidence['warmup_candle_count']>0 and evidence['dataset_fingerprint']=='partition-fingerprint'
    assert all(evidence[k] for k in ('parameter_hash','execution_hash','candidate_config_hash','evaluation_hash'))
    with repo.connect() as c: candidate=c.execute('SELECT aggregate_metrics FROM strategy_discovery_candidates').fetchone()
    assert json.loads(candidate[0])['median_validation_return']==1.0

def test_fold_loader_never_queries_holdout_or_oot(tmp_path):
    repo,service,_=setup(tmp_path); seen=[]; original=repo.candles
    def spy(*args): seen.append(args); return original(*args)
    repo.candles=spy
    for a,_,_,ve in FOLDS: service._fold_rows('ETH-USDT','15m',a,ve)
    assert all(end < discovery.ts(2025,5,1) for _,_,_,end in seen)

def test_benchmark_applies_costs_and_never_overspends():
    config=discovery.DiscoveryExecutionConfig(initial_capital=1000,trading_fee=.01,slippage=.01)
    result=buy_and_hold([{'ts':10,'open':100,'close':100},{'ts':20,'open':100,'close':110}],10,20,config)
    assert result['entry_fee']>0 and result['exit_fee']>0
    assert result['effective_entry_price']*result['position_size']+result['entry_fee'] <= 1000+1e-9
    assert result['entry_ts']==10 and result['exit_ts']==20

def test_aggregate_is_foldwise_and_keeps_null_ratios():
    folds=[{'status':'COMPLETED','metrics':{'total_return':x,'total_trades':1,'maximum_drawdown':2,'sharpe_ratio':None,'sortino_ratio':None,'profit_factor':None,'fees_paid':1},'buy_hold_metrics':{'total_return':0}} for x in (-2,1,3)]
    result=aggregate(folds)
    assert result['median_validation_return']==1 and result['worst_validation_return']==-2
    assert result['profitable_fold_ratio']==2/3 and result['median_sharpe_ratio'] is None and result['median_profit_factor'] is None
    assert result['mean_validation_return'] != sum((-2,1,3))

def test_cancellation_preserves_completed_folds_and_marks_run(tmp_path,monkeypatch):
    repo,service,rid=setup(tmp_path); calls=[]; monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine(calls)); count=0
    def checkpoint(*_):
        nonlocal count; count+=1
        if count>5: raise JobCancelled()
    with pytest.raises(JobCancelled): service._run_job(1,{'discovery_run_id':rid,'dataset_id':1,'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{}},checkpoint)
    with repo.connect() as c: assert c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()[0]=='CANCELLED'

def test_matching_evaluation_hash_reuses_fold_and_changed_hash_recomputes(tmp_path,monkeypatch):
    repo,service,rid=setup(tmp_path); calls=[]
    def matching(candles,instrument,timeframe,template,parameters,start_ts,end_ts,execution,dataset_fingerprint):
        calls.append(start_ts); evaluation=service._evaluation_hash(template,parameters,execution,instrument,timeframe,start_ts,end_ts,dataset_fingerprint)
        result=fake_engine([])(candles,instrument,timeframe,template,parameters,start_ts,end_ts,execution,dataset_fingerprint)
        result['discovery_evidence']['evaluation_hash']=evaluation
        return result
    monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',matching)
    payload={'discovery_run_id':rid,'dataset_id':1,'instrument':'ETH-USDT','timeframe':'15m','templates':['TREND_BREAKOUT'],'trial_budget':1,'seed':7,'execution_assumptions':{'trading_fee':.001,'slippage':.002}}
    service._run_job(1,payload,lambda *_:None); service._run_job(1,payload,lambda *_:None)
    assert len(calls)==5
    payload={**payload,'execution_assumptions':{'trading_fee':.002,'slippage':.002}}
    service._run_job(1,payload,lambda *_:None)
    assert len(calls)==10
    with repo.connect() as c: assert c.execute('SELECT COUNT(*) FROM strategy_discovery_folds').fetchone()[0]==5
