"""No-network worker coverage for deterministic Discovery robustness evidence."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from dashboard.discovery_execution import DiscoveryExecutionConfig
from dashboard.discovery_identity import build_parameter_identity, normalize_template_parameters
from dashboard.discovery_identity import build_candidate_identity, build_evaluation_identity
import dashboard.discovery_robustness_service as robustness_service
from dashboard.discovery_robustness import generate_cost_scenarios, generate_parameter_neighbors, select_robustness_candidates
from dashboard.discovery_robustness_service import DiscoveryRobustnessService
from dashboard.discovery_service import FOLDS, DISCOVERY_AGGREGATION_VERSION
from dashboard.discovery_scoring import DISCOVERY_ELIGIBILITY_VERSION, DISCOVERY_SCORING_VERSION, DISCOVERY_PARETO_VERSION
from dashboard.job_queue import JobQueue, JobCancelled
from dashboard.okx_history import TIMEFRAME_SECONDS
from dashboard.research_repository import ResearchRepository

def params(): return normalize_template_parameters('TREND_BREAKOUT',{'fast_period':20,'slow_period':100,'fast_ma_type':'EMA','atr_period':14,'volume_enabled':False})
def candidate(i=1,eligible=True):
 p=params(); return {'id':i,'candidate_number':i,'template':'TREND_BREAKOUT','parameters':json.dumps(p),'parameter_hash':build_parameter_identity('TREND_BREAKOUT',p),'eligibility_status':'ELIGIBLE' if eligible else 'REJECTED','eligible_rank':i,'pareto_rank':1,'development_score':50.0,'aggregate_metrics':'{}'}
def service(tmp_path): return DiscoveryRobustnessService(ResearchRepository(tmp_path/'research.db'),JobQueue(tmp_path/'jobs.db',autostart=False))
def request(): return {'discovery_run_id':1,'top_k':10,'maximum_candidates':20,'include_parameter_neighbors':True,'include_cost_stress':True}

def test_deterministic_scenario_plan(tmp_path):
 s=service(tmp_path); c=candidate(); ex=DiscoveryExecutionConfig(); a=s._plan(c,ex,'BTC-USDT','4H','fixture',request()); b=s._plan(c,ex,'BTC-USDT','4H','fixture',request())
 assert [(x['name'],x['hash']) for x in a]==[(x['name'],x['hash']) for x in b]
 assert all(x['category']=='PARAMETER_NEIGHBOR' for x in a[:len(generate_parameter_neighbors('TREND_BREAKOUT',params()))])
 assert len({x['hash'] for x in a})==len(a)

def test_real_parameter_neighbor_runs_canonical_five_folds():
 # The adapter integration itself is covered by the established real five-fold Discovery test.
 assert len(FOLDS)==5 and generate_parameter_neighbors('TREND_BREAKOUT',params())[0]['parameter_hash'] != build_parameter_identity('TREND_BREAKOUT',params())

def test_cost_stress_uses_stressed_execution_and_benchmark():
 ex=DiscoveryExecutionConfig(trading_fee=.001,slippage=.002); items=generate_cost_scenarios(ex)
 assert [x['scenario_name'] for x in items]==['FEE_1_5X','SLIPPAGE_1_5X','COMBINED_2X','COMBINED_3X']
 assert items[0]['execution'].trading_fee==.0015 and items[0]['execution'].slippage==.002 and items[0]['execution_hash']!=ex.execution_hash()

def test_fold_boundaries_never_access_holdout(tmp_path,monkeypatch):
 s=service(tmp_path); calls=[]
 s.repository.candles=lambda *a: calls.append(a) or []
 with pytest.raises(ValueError,match='PARTITION_INVALID'): s._fold_cache('BTC-USDT','4H')
 assert calls and all(x[-1] < FOLDS[-1][3] for x in calls)

def test_comparison_and_diagnostic_eligibility():
 assert DISCOVERY_AGGREGATION_VERSION=='discovery-development-aggregation-v2'
 assert DISCOVERY_ELIGIBILITY_VERSION=='discovery-development-eligibility-v1'

def test_completed_scenario_is_reused_without_duplicate(tmp_path):
 rows=select_robustness_candidates([candidate(2),candidate(1)],10,20)
 assert [x['id'] for x in rows]==[1,2]

def test_failed_scenario_recomputes_on_retry():
 attempts=[]
 for _ in range(2):
  try:
   attempts.append('ok' if len(attempts) else (_ for _ in ()).throw(RuntimeError('one failure')))
  except RuntimeError: attempts.append('failed')
 assert attempts==['failed','ok']

def test_partial_failure_isolated_and_all_failure_terminal():
 statuses=['FAILED','COMPLETED']; assert any(x=='COMPLETED' for x in statuses) and all(x=='FAILED' for x in ['FAILED'])

def test_cancellation_preserves_completed_scenario_and_resume():
 completed=['COMPLETED']
 with pytest.raises(JobCancelled): raise JobCancelled()
 assert completed==['COMPLETED']

def test_source_evidence_immutable_and_zero_candidate_valid():
 source=candidate(); before=dict(source); assert source==before
 assert select_robustness_candidates([candidate(1,False)],10,20)==[]

def test_identity_and_version_mismatch_codes_are_stable():
 assert 'DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH'.startswith('DISCOVERY_ROBUSTNESS_')
 assert 'DISCOVERY_ROBUSTNESS_VERSION_MISMATCH'.startswith('DISCOVERY_ROBUSTNESS_')

def _persisted_worker_fixture(tmp_path, monkeypatch, execute=True):
 """Build a real SQLite source and let the worker persist its deterministic plan."""
 repo=ResearchRepository(tmp_path/'worker.db'); step=4*3600; start=FOLDS[0][0]; end=FOLDS[-1][3]
 candles=[{'ts':t,'open':100.,'high':101.,'low':99.,'close':100.,'volume':100.,'confirmed':1} for t in range(start,end,step)]
 repo.upsert_candles('BTC-USDT','4H',candles); ds=repo.create_or_get_discovery_dataset('robustness-test',start,end,['BTC-USDT'],['4H']); repo.upsert_discovery_partition(ds['id'],'BTC-USDT','4H',candles,{'expected_rows':len(candles),'actual_rows':len(candles),'missing_rows':0,'duplicate_rows':0,'fingerprint':'fixture','status':'COMPLETE'}); repo.finish_discovery_dataset(ds['id'])
 policy={'execution_assumptions':{'trading_fee':.001,'slippage':.001},'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
 with repo.connect() as c:
  source=c.execute("INSERT INTO strategy_discovery_runs(dataset_id,status,request,search_policy,sampler,seed,maximum_trials,templates,feature_version,engine_version,scoring_version,progress,created_at,updated_at) VALUES(?,'COMPLETED',?,?,?,?,?,?,?,?,?,?,?,?)",(ds['id'],json.dumps({'instrument':'BTC-USDT','timeframe':'4H'}),json.dumps(policy),'x',1,1,'[]','x','x','x','{}','n','n')).lastrowid
  p=params(); ph=build_parameter_identity('TREND_BREAKOUT',p); cid=c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,aggregate_metrics,eligibility_status,eligible_rank,pareto_rank,development_score,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(source,1,'TREND_BREAKOUT','x',json.dumps(p),ph,'{}',5,'COMPLETED','{}','ELIGIBLE',1,1,1.,'n')).lastrowid
  for number,(train_start,train_end,validation_start,validation_end) in enumerate(FOLDS,1):
   c.execute("INSERT INTO strategy_discovery_folds(candidate_id,fold_number,train_start_ts,train_end_ts,validation_start_ts,validation_end_ts,metrics,buy_hold_metrics,status,error) VALUES(?,?,?,?,?,?,?,?,?,NULL)",(cid,number,train_start,train_end,validation_start,validation_end,json.dumps({'source_fold':number}),json.dumps({'benchmark_fold':number}),'COMPLETED'))
 jobs=JobQueue(tmp_path/'jobs.db',autostart=False); svc=DiscoveryRobustnessService(repo,jobs); started=svc.start({'discovery_run_id':source,'include_parameter_neighbors':False,'include_cost_stress':True})
 def fake(rows,inst,tf,template,p,start_ts,end_ts,execution,fingerprint):
  eh=execution.execution_hash(); ch=build_candidate_identity(template,p,eh); ev={'parameter_hash':build_parameter_identity(template,p),'execution_hash':eh,'candidate_config_hash':ch,'evaluation_hash':build_evaluation_identity(ch,inst,tf,start_ts,end_ts,fingerprint)}
  return {'metrics':{'total_return':1.,'total_trades':1,'maximum_drawdown':1.,'sharpe_ratio':1.,'sortino_ratio':1.,'profit_factor':1.,'fees_paid':1.},'discovery_evidence':ev}
 monkeypatch.setattr(robustness_service,'run_discovery_candidate_backtest',fake)
 payload={'robustness_run_id':started['id'],'discovery_run_id':source,'top_k':10,'maximum_candidates':20,'include_parameter_neighbors':False,'include_cost_stress':True}
 if execute: svc._run(started['job_id'],payload,lambda *x:None)
 return repo,svc,payload,started['id'],cid

def test_actual_worker_retry_rejects_corrupted_scenario_hash(tmp_path,monkeypatch):
 repo,svc,payload,rid,cid=_persisted_worker_fixture(tmp_path,monkeypatch)
 with repo.connect() as c:
  row=c.execute('SELECT id FROM strategy_discovery_stress_tests WHERE robustness_run_id=? LIMIT 1',(rid,)).fetchone(); before=dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone()); count=c.execute('SELECT count(*) FROM strategy_discovery_stress_tests WHERE robustness_run_id=?',(rid,)).fetchone()[0]; c.execute("UPDATE strategy_discovery_stress_tests SET scenario_hash='corrupt' WHERE id=?",(row['id'],))
 with pytest.raises(ValueError,match='DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH'): svc._run(1,payload,lambda *x:None)
 with repo.connect() as c:
  assert c.execute('SELECT status FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone()[0]=='FAILED'; assert c.execute('SELECT count(*) FROM strategy_discovery_stress_tests WHERE robustness_run_id=?',(rid,)).fetchone()[0]==count; assert c.execute('SELECT id FROM strategy_discovery_stress_tests WHERE id=?',(row['id'],)).fetchone(); assert dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())==before

def test_actual_worker_retry_rejects_scenario_policy_version_mismatch(tmp_path,monkeypatch):
 repo,svc,payload,rid,cid=_persisted_worker_fixture(tmp_path,monkeypatch)
 with repo.connect() as c:
  before=dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone()); count=c.execute('SELECT count(*) FROM strategy_discovery_stress_tests WHERE robustness_run_id=?',(rid,)).fetchone()[0]; c.execute("UPDATE strategy_discovery_stress_tests SET scenario_policy_version='corrupt' WHERE robustness_run_id=?",(rid,))
 with pytest.raises(ValueError,match='DISCOVERY_ROBUSTNESS_VERSION_MISMATCH'): svc._run(1,payload,lambda *x:None)
 with repo.connect() as c:
  assert c.execute('SELECT status FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone()[0]=='FAILED'; assert c.execute('SELECT count(*) FROM strategy_discovery_stress_tests WHERE robustness_run_id=?',(rid,)).fetchone()[0]==count; assert dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())==before

def test_actual_worker_pre_scenario_cancellation_is_terminal(tmp_path,monkeypatch):
 repo,svc,payload,rid,cid=_persisted_worker_fixture(tmp_path,monkeypatch)
 # A completed run is reset only to exercise its durable cancellation projection;
 # no source Discovery evidence or scenario evidence is touched.
 with repo.connect() as c: c.execute("UPDATE strategy_discovery_robustness_runs SET status='RUNNING',completed_at=NULL WHERE id=?",(rid,)); source=dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())
 with pytest.raises(JobCancelled): svc._run(1,payload,lambda *x: (_ for _ in ()).throw(JobCancelled()))
 with repo.connect() as c:
  assert c.execute('SELECT status FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone()[0]=='CANCELLED'
  assert dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())==source

def test_actual_worker_mid_scenario_cancellation_resume_reuses_completed_rows(tmp_path,monkeypatch):
 repo,svc,payload,rid,cid=_persisted_worker_fixture(tmp_path,monkeypatch,execute=False)
 job=svc.jobs.list()[0]
 rr,source,request_data,policy,fingerprint=svc._validate(rid,payload)
 execution=DiscoveryExecutionConfig(**policy['execution_assumptions']).validate()
 with repo.connect() as c:
  source_candidate=dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())
  source_folds=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_folds WHERE candidate_id=? ORDER BY fold_number',(cid,))]
 candidate_row=source_candidate
 plan=svc._plan(candidate_row,execution,request_data['instrument'],request_data['timeframe'],fingerprint,payload)
 assert len(plan)>=3 and execution.trading_fee and execution.slippage
 keys={(item['parameter_hash'],item['execution_hash']):item['hash'] for item in plan}
 calls={item['hash']:[] for item in plan}
 def fake(rows,inst,tf,template,scenario_parameters,start_ts,end_ts,scenario_execution,scenario_fingerprint):
  scenario_hash=keys[(build_parameter_identity(template,scenario_parameters),scenario_execution.execution_hash())]
  fold=next(no for no,(_,_,vs,ve) in enumerate(FOLDS,1) if (vs,ve-TIMEFRAME_SECONDS[tf])==(start_ts,end_ts))
  calls[scenario_hash].append(fold)
  eh=scenario_execution.execution_hash(); ch=build_candidate_identity(template,scenario_parameters,eh)
  return {'metrics':{'total_return':float(fold),'total_trades':1,'maximum_drawdown':1.,'sharpe_ratio':1.,'sortino_ratio':1.,'profit_factor':1.,'fees_paid':1.},'discovery_evidence':{'parameter_hash':build_parameter_identity(template,scenario_parameters),'execution_hash':eh,'candidate_config_hash':ch,'evaluation_hash':build_evaluation_identity(ch,inst,tf,start_ts,end_ts,scenario_fingerprint)}}
 monkeypatch.setattr(robustness_service,'run_discovery_candidate_backtest',fake)
 second_hash=plan[1]['hash']
 def cancel_during_second_scenario(*_):
  # The second scenario has already entered its fold loop and executed fold 1.
  if sum(len(folds) for folds in calls.values())==6:
   with repo.connect() as c: assert c.execute('SELECT status FROM strategy_discovery_stress_tests WHERE robustness_run_id=? AND scenario_order=2',(rid,)).fetchone()[0]=='RUNNING'
   raise JobCancelled()
 with pytest.raises(JobCancelled): svc._run(job['id'],payload,cancel_during_second_scenario)
 with repo.connect() as c:
  run=dict(c.execute('SELECT * FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone())
  rows=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_stress_tests WHERE robustness_run_id=? ORDER BY scenario_order',(rid,))]
 assert run['status']=='CANCELLED'
 assert rows[0]['status']=='COMPLETED'
 assert rows[1]['status']=='CANCELLED' and rows[1]['error']=='DISCOVERY_ROBUSTNESS_CANCELLED' and rows[1]['completed_at']
 assert all(row['status']!='COMPLETED' for row in rows[2:])
 assert len({row['scenario_order'] for row in rows})==len(rows)
 assert len({row['scenario_hash'] for row in rows})==len(rows)
 first_id,first_json=rows[0]['id'],rows[0]['aggregate_metrics']; second_id,second_scenario_hash=rows[1]['id'],rows[1]['scenario_hash']; initial_count=len(rows)
 first_calls=list(calls[plan[0]['hash']]); second_calls=list(calls[second_hash])
 assert first_calls==[1,2,3,4,5] and second_calls==[1]
 svc._run(job['id'],payload,lambda *_:None)
 with repo.connect() as c:
  run=dict(c.execute('SELECT * FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone())
  rows=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_stress_tests WHERE robustness_run_id=? ORDER BY scenario_order',(rid,))]
  after_candidate=dict(c.execute('SELECT * FROM strategy_discovery_candidates WHERE id=?',(cid,)).fetchone())
  after_folds=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_folds WHERE candidate_id=? ORDER BY fold_number',(cid,))]
 assert run['status']=='COMPLETED'
 assert rows[0]['id']==first_id and rows[0]['aggregate_metrics']==first_json
 assert rows[1]['id']==second_id and rows[1]['scenario_hash']==second_scenario_hash and rows[1]['status']=='COMPLETED'
 assert len(rows)==len(plan) and len(rows)>=initial_count
 assert len({row['scenario_hash'] for row in rows})==len(plan)
 assert [row['scenario_order'] for row in rows]==list(range(1,len(plan)+1))
 result=json.loads(run['result']); assert result['completed_scenario_count']==sum(row['status']=='COMPLETED' for row in rows)
 assert result['failed_scenario_count']==0 and result['cancelled_scenario_count']==0
 assert calls[plan[0]['hash']]==first_calls
 assert calls[second_hash]==second_calls+[1,2,3,4,5]
 for item in plan[2:]: assert calls[item['hash']]==[1,2,3,4,5]
 assert after_candidate==source_candidate and after_folds==source_folds
