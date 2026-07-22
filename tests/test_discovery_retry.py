"""No-network persistence contracts for deterministic Discovery retry/resume."""
from __future__ import annotations
import json
import random
import pytest

import dashboard.discovery_service as discovery
from dashboard.discovery_identity import build_candidate_identity, build_evaluation_identity, build_parameter_identity
from dashboard.discovery_service import DISCOVERY_SAMPLER_VERSION, DiscoveryService, FOLDS
from dashboard.job_queue import JobCancelled, JobQueue
from dashboard.research_repository import ResearchRepository
from dashboard.discovery_scoring import candidate_complexity

STEP=4*3600

def setup(tmp_path,budget=10):
    start,end=FOLDS[0][0],FOLDS[-1][3]
    candles=[{'ts':t,'open':100+i*.1,'high':101+i*.1,'low':99+i*.1,'close':100.5+i*.1,'volume':100+i,'confirmed':1} for i,t in enumerate(range(start,end,STEP))]
    repo=ResearchRepository(tmp_path/'research.db'); repo.upsert_candles('BTC-USDT','4H',candles)
    ds=repo.create_or_get_discovery_dataset('retry-data',start,end,['BTC-USDT'],['4H'])
    repo.upsert_discovery_partition(ds['id'],'BTC-USDT','4H',candles,{'expected_rows':len(candles),'actual_rows':len(candles),'missing_rows':0,'duplicate_rows':0,'fingerprint':'retry-fingerprint','status':'COMPLETE'})
    ds=repo.finish_discovery_dataset(ds['id']); service=DiscoveryService(repo,JobQueue(tmp_path/'jobs.db',autostart=False))
    request={'dataset_id':ds['id'],'instrument':'BTC-USDT','timeframe':'4H','templates':['TREND_BREAKOUT'],'trial_budget':budget,'seed':17,'execution_assumptions':{}}
    rid=service.start(request)['id']
    return repo,service,rid,request

def fake_engine(calls):
    def run(candles,instrument,timeframe,template,parameters,start,end,execution,dataset_fingerprint):
        calls.append((template,parameters,start,end)); ph=build_parameter_identity(template,parameters); eh=execution.execution_hash(); candidate=build_candidate_identity(template,parameters,eh)
        return {'metrics':{'initial_capital':10000,'final_equity':10010,'net_profit':10,'total_return':.1,'annualized_return':None,'total_trades':1,'win_rate':100,'profit_factor':None,'expectancy':10,'average_win':10,'average_loss':None,'realized_risk_reward':None,'maximum_drawdown':1,'sharpe_ratio':None,'sortino_ratio':None,'consecutive_wins':1,'consecutive_losses':0,'fees_paid':1,'long_trades':1,'short_trades':0,'average_holding_seconds':1,'sample_note':'test'},'signal_count':1,'discovery_evidence':{'parameter_hash':ph,'execution_hash':eh,'candidate_config_hash':candidate,'evaluation_hash':build_evaluation_identity(candidate,instrument,timeframe,start,end,dataset_fingerprint),'template_version':'test','feature_version':'test','execution_policy_version':'test','execution_engine_version':'test','execution':execution.__dict__}}
    return run

def run(service,rid,request): return service._run_job(1,{**request,'discovery_run_id':rid},lambda *_:None)

def persisted_definitions(repo,rid):
    with repo.connect() as c:return [(x['candidate_number'],x['id'],x['template'],json.loads(x['parameters']),x['parameter_hash']) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number',(rid,))]

def test_zero_existing_creates_full_sequence_and_matches_clean_sampler(tmp_path,monkeypatch):
    repo,service,rid,request=setup(tmp_path); monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine([])); run(service,rid,request)
    stored=persisted_definitions(repo,rid); expected=service._candidate_definitions(random.Random(17),request['templates'],10)[0]
    assert [(n,t,p,h) for n,_,t,p,h in stored] == [(n,t,p,h) for n,(t,p,h) in enumerate(expected,1)]

def test_partial_prefix_resumes_without_replacing_candidates_or_folds(tmp_path,monkeypatch):
    repo,service,rid,request=setup(tmp_path); definitions,_=service._candidate_definitions(random.Random(17),request['templates'],10)
    with repo.connect() as c:
        for number,(template,parameters,ph) in enumerate(definitions[:3],1):
            cid=c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(rid,number,template,'v',json.dumps(parameters),ph,'{}',candidate_complexity(template,parameters),'CANCELLED','now')).lastrowid
            if number==1:
                execution=discovery.DiscoveryExecutionConfig(); a,b,vs,ve=FOLDS[0]; end=ve-STEP; evaluation=service._evaluation_hash(template,parameters,execution,'BTC-USDT','4H',vs,end,'retry-fingerprint')
                outcome=fake_engine([])([],'BTC-USDT','4H',template,parameters,vs,end,execution,'retry-fingerprint'); saved=outcome['metrics']; saved['fold_evidence']=outcome['discovery_evidence']
                saved['fold_evidence']['evaluation_hash']=evaluation
                c.execute("INSERT INTO strategy_discovery_folds(candidate_id,fold_number,train_start_ts,train_end_ts,validation_start_ts,validation_end_ts,metrics,buy_hold_metrics,status,error) VALUES(?,?,?,?,?,?,?,?,?,NULL)",(cid,1,a,b,vs,ve,json.dumps(saved),json.dumps({'total_return':0}),'COMPLETED'))
    before=persisted_definitions(repo,rid); calls=[]; monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine(calls)); run(service,rid,request)
    after=persisted_definitions(repo,rid)
    assert [(x[0],x[1]) for x in after[:3]] == [(x[0],x[1]) for x in before]
    assert [x[0] for x in after] == list(range(1,11)) and len(after)==10
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_discovery_folds WHERE candidate_id=?',(before[0][1],)).fetchone()[0] == 5
        assert c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()[0] == 'COMPLETED'
    assert len(calls)==49  # existing completed fold is reused exactly once.

@pytest.mark.parametrize('corrupt',('hash','number','duplicate','seed'))
def test_corrupt_prefix_or_version_fails_deterministically(tmp_path,monkeypatch,corrupt):
    repo,service,rid,request=setup(tmp_path,1); definitions,_=service._candidate_definitions(random.Random(17),request['templates'],1); t,p,h=definitions[0]
    with repo.connect() as c:
        c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(rid,2 if corrupt=='number' else 1,t,'v',json.dumps(p),'bad' if corrupt=='hash' else h,'{}',7,'CANCELLED','now'))
        if corrupt=='duplicate': c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(rid,2,t,'v',json.dumps(p),h,'{}',7,'CANCELLED','now'))
        if corrupt=='seed': c.execute("UPDATE strategy_discovery_runs SET sampler='changed' WHERE id=?",(rid,))
    monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine([]))
    with pytest.raises(ValueError,match='DISCOVERY_IDENTITY_VERSION_MISMATCH'): run(service,rid,request)
    with repo.connect() as c: assert c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()[0] == 'FAILED'

def test_exhaustion_unexpected_error_and_cancellation_are_terminal(tmp_path,monkeypatch):
    repo,service,rid,request=setup(tmp_path,1)
    monkeypatch.setattr(service,'_candidate_definitions',lambda *_: (_ for _ in ()).throw(ValueError('DISCOVERY_SEARCH_SPACE_EXHAUSTED')))
    with pytest.raises(ValueError): run(service,rid,request)
    with repo.connect() as c: assert tuple(c.execute('SELECT status,error FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()) == ('FAILED','DISCOVERY_SEARCH_SPACE_EXHAUSTED')
    repo,service,rid,request=setup(tmp_path/'unexpected',1); monkeypatch.setattr(service,'_candidate_definitions',lambda *_: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError): run(service,rid,request)
    with repo.connect() as c: assert tuple(c.execute('SELECT status,error FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()) == ('FAILED','DISCOVERY_WORKER_ERROR')
    repo,service,rid,request=setup(tmp_path/'cancel',1); monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine([]))
    with pytest.raises(JobCancelled): service._run_job(1,{**request,'discovery_run_id':rid},lambda *_: (_ for _ in ()).throw(JobCancelled()))
    with repo.connect() as c: assert c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()[0] == 'CANCELLED'

def test_cancellation_after_completed_fold_resumes_without_duplicate_fold_rows(tmp_path,monkeypatch):
    repo,service,rid,request=setup(tmp_path,1); calls=[]; monkeypatch.setattr(discovery,'run_discovery_candidate_backtest',fake_engine(calls)); checkpoints=0
    def cancel_after_first_fold(*_):
        nonlocal checkpoints; checkpoints+=1
        if checkpoints > 6: raise JobCancelled()
    with pytest.raises(JobCancelled): service._run_job(1,{**request,'discovery_run_id':rid},cancel_after_first_fold)
    with repo.connect() as c: assert c.execute('SELECT COUNT(*) FROM strategy_discovery_folds').fetchone()[0] == 1
    run(service,rid,request)
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM strategy_discovery_folds').fetchone()[0] == 5
        assert c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()[0] == 'COMPLETED'
    assert len(calls)==5
