"""Queue-backed, development-only robustness evidence for Strategy Discovery."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from .discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from .discovery_identity import build_candidate_identity, build_evaluation_identity, normalize_template_parameters
from .discovery_robustness import (DISCOVERY_ROBUSTNESS_VERSION, DISCOVERY_NEIGHBOR_VERSION, DISCOVERY_COST_STRESS_VERSION,
    build_robustness_scenario_identity, generate_cost_scenarios, generate_parameter_neighbors, select_robustness_candidates,
    summarize_candidate_robustness, summarize_robustness_run)
from .discovery_scoring import DISCOVERY_ELIGIBILITY_VERSION, DISCOVERY_PARETO_VERSION, DISCOVERY_SCORING_VERSION, evaluate_eligibility
from .discovery_service import DISCOVERY_AGGREGATION_VERSION, FOLDS, POLICY_VERSION, aggregate, buy_and_hold
from .job_queue import JobCancelled
from .okx_history import TIMEFRAME_SECONDS

def _now(): return datetime.now(timezone.utc).isoformat()
WARNINGS=['Robustness uses development data only.','Primary holdout was not accessed.','Final OOT was not accessed.','Robustness survival is not proof of future profitability.','Original development ranking was not changed.','No strategy was activated.']

class DiscoveryRobustnessService:
 def __init__(self, repository, jobs):
  self.repository,self.jobs=repository,jobs; jobs.register('DISCOVERY_ROBUSTNESS',self._run); jobs.register_terminal_handler('DISCOVERY_ROBUSTNESS',self._terminal)
 def _request(self,p):
  if not isinstance(p,dict) or 'discovery_run_id' not in p: raise ValueError('discovery_run_id is required.')
  source=p['discovery_run_id']; top=p.get('top_k',10); maxc=p.get('maximum_candidates',20)
  if any(type(value) is not int for value in (source,top,maxc)): raise ValueError('discovery_run_id, top_k and maximum_candidates must be integers.')
  if source<1: raise ValueError('discovery_run_id must be a positive integer.')
  neighbours=p.get('include_parameter_neighbors',True); costs=p.get('include_cost_stress',True)
  if type(neighbours) is not bool or type(costs) is not bool: raise ValueError('Robustness scenario categories must be booleans.')
  if not 1<=top<=20 or not 1<=maxc<=20: raise ValueError('top_k and maximum_candidates must be 1..20.')
  if not(neighbours or costs): raise ValueError('At least one robustness scenario category must be enabled.')
  return {'discovery_run_id':source,'top_k':top,'maximum_candidates':maxc,'include_parameter_neighbors':neighbours,'include_cost_stress':costs}
 def _json(self,value,default=None):
  try:return json.loads(value) if value else default
  except (TypeError,json.JSONDecodeError):return default
 def _run_payload(self,row):
  item=dict(row)
  for name,default in (('request',{}),('selected_candidates',[]),('progress',{}),('result',None)): item[name]=self._json(item.get(name),default)
  return item
 def list_runs(self):
  with self.repository.connect() as c: rows=c.execute('SELECT * FROM strategy_discovery_robustness_runs ORDER BY id DESC').fetchall()
  return [self._run_payload(row) for row in rows]
 def run_detail(self,rid):
  with self.repository.connect() as c:
   run=c.execute('SELECT * FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone()
   if not run:return None
   scenarios=c.execute('SELECT * FROM strategy_discovery_stress_tests WHERE robustness_run_id=? ORDER BY candidate_id,scenario_order',(rid,)).fetchall()
  item=self._run_payload(run); item['scenarios']=[]
  for row in scenarios:
   scenario=dict(row)
   for name,default in (('assumptions',{}),('aggregate_metrics',{}),('comparison_to_base',{}),('scenario',None),('metrics',None)): scenario[name]=self._json(scenario.get(name),default)
   item['scenarios'].append(scenario)
  return item
 def cancel(self,rid):
  with self.repository.connect() as c: exists=c.execute('SELECT id FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone()
  if not exists: raise ValueError('Robustness run not found.')
  for job in self.jobs.list(200):
   if job['job_type']=='DISCOVERY_ROBUSTNESS' and job['request_payload'].get('robustness_run_id')==rid:return self.jobs.cancel(job['id'])
  raise ValueError('Active job not found.')
 def start(self,p,client='public'):
  p=self._request(p)
  with self.repository.connect() as c: source=c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(p['discovery_run_id'],)).fetchone()
  if not source or source['status']!='COMPLETED': raise ValueError('DISCOVERY_ROBUSTNESS_SOURCE_RUN_INVALID')
  now=_now()
  with self.repository.connect() as c: rid=c.execute("INSERT INTO strategy_discovery_robustness_runs(discovery_run_id,status,request,robustness_version,neighbor_version,cost_stress_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(p['discovery_run_id'],'QUEUED',json.dumps(p,sort_keys=True),DISCOVERY_ROBUSTNESS_VERSION,DISCOVERY_NEIGHBOR_VERSION,DISCOVERY_COST_STRESS_VERSION,now,now)).lastrowid
  try: job=self.jobs.enqueue('DISCOVERY_ROBUSTNESS',{**p,'robustness_run_id':rid},client,priority=116)
  except Exception:
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status='FAILED',error=?,completed_at=? WHERE id=?",('DISCOVERY_ROBUSTNESS_QUEUE_ERROR',_now(),rid))
   raise
  return {'id':rid,'job_id':job['id'],'status':'QUEUED'}
 def _terminal(self,job):
  rid=job.get('request_payload',{}).get('robustness_run_id')
  if rid and job['status'] in {'FAILED','CANCELLED'}:
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status=?,error=?,completed_at=?,updated_at=? WHERE id=? AND status IN ('QUEUED','RUNNING')",(job['status'],'DISCOVERY_ROBUSTNESS_CANCELLED' if job['status']=='CANCELLED' else 'DISCOVERY_ROBUSTNESS_WORKER_ERROR',_now(),_now(),rid))
 def _fail(self,rid,code):
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status='FAILED',error=?,completed_at=?,updated_at=? WHERE id=? AND status!='COMPLETED'",(code,_now(),_now(),rid))
 def _validate(self,rid,p):
  with self.repository.connect() as c:
   rr=c.execute('SELECT * FROM strategy_discovery_robustness_runs WHERE id=?',(rid,)).fetchone(); source=c.execute('SELECT * FROM strategy_discovery_runs WHERE id=?',(p['discovery_run_id'],)).fetchone()
  if not rr or not source or source['status']!='COMPLETED': raise ValueError('DISCOVERY_ROBUSTNESS_SOURCE_RUN_INVALID')
  policy=json.loads(source['search_policy'] or '{}'); request=json.loads(source['request'] or '{}')
  expected={'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
  if rr['robustness_version']!=DISCOVERY_ROBUSTNESS_VERSION or rr['neighbor_version']!=DISCOVERY_NEIGHBOR_VERSION or rr['cost_stress_version']!=DISCOVERY_COST_STRESS_VERSION or any(policy.get(k)!=v for k,v in expected.items()): raise ValueError('DISCOVERY_ROBUSTNESS_POLICY_VERSION_MISMATCH')
  if 'execution_assumptions' not in policy or request.get('timeframe') not in TIMEFRAME_SECONDS: raise ValueError('DISCOVERY_ROBUSTNESS_SOURCE_RUN_INVALID')
  dataset=self.repository.discovery_dataset(source['dataset_id']); part=self.repository.discovery_partition(source['dataset_id'],request['instrument'],request['timeframe'])
  if not dataset or dataset['status']!='COMPLETE': raise ValueError('DISCOVERY_ROBUSTNESS_DATASET_INVALID')
  if not part or part['status']!='COMPLETE' or not part.get('fingerprint'): raise ValueError('DISCOVERY_ROBUSTNESS_PARTITION_INVALID')
  return dict(rr),dict(source),request,policy,part['fingerprint']
 def _fold_cache(self,inst,timeframe):
  step=TIMEFRAME_SECONDS[timeframe]; cache={}
  for number,(start,_,vs,ve) in enumerate(FOLDS,1):
   if ve>int(datetime(2025,5,1,tzinfo=timezone.utc).timestamp()): raise ValueError('DISCOVERY_ROBUSTNESS_PARTITION_INVALID')
   rows=self.repository.candles(inst,timeframe,start,ve-1); rows=sorted({int(x['ts']):dict(x) for x in rows if x.get('confirmed',1)}.values(),key=lambda x:int(x['ts']))
   if not rows or not any(int(x['ts'])==vs for x in rows) or not any(int(x['ts'])==ve-step for x in rows): raise ValueError('DISCOVERY_ROBUSTNESS_PARTITION_INVALID')
   cache[number]=tuple(rows)
  return cache
 def _plan(self,candidate,execution,inst,timeframe,fingerprint,p):
  template=candidate['template']; base=normalize_template_parameters(template,json.loads(candidate['parameters'])); sph=candidate['parameter_hash']; seh=execution.execution_hash(); output=[]
  if p['include_parameter_neighbors']:
   for n in generate_parameter_neighbors(template,base):
    assumptions={k:n[k] for k in ('changed_parameter','direction','original_value','neighbor_value')}; name=n['changed_parameter']+'_'+n['direction']; h=build_robustness_scenario_identity(category='PARAMETER_NEIGHBOR',scenario_name=name,source_parameter_hash=sph,scenario_parameter_hash=n['parameter_hash'],source_execution_hash=seh,scenario_execution_hash=seh,instrument=inst,timeframe=timeframe,dataset_fingerprint=fingerprint,assumptions=assumptions,five_fold_policy_version=POLICY_VERSION,scenario_policy_version=DISCOVERY_NEIGHBOR_VERSION)
    output.append({'category':'PARAMETER_NEIGHBOR','name':name,'parameters':n['parameters'],'execution':execution,'parameter_hash':n['parameter_hash'],'execution_hash':seh,'assumptions':assumptions,'policy':DISCOVERY_NEIGHBOR_VERSION,'hash':h})
  if p['include_cost_stress']:
   for n in generate_cost_scenarios(execution):
    h=build_robustness_scenario_identity(category='COST_STRESS',scenario_name=n['scenario_name'],source_parameter_hash=sph,scenario_parameter_hash=sph,source_execution_hash=seh,scenario_execution_hash=n['execution_hash'],instrument=inst,timeframe=timeframe,dataset_fingerprint=fingerprint,assumptions=n['assumptions'],five_fold_policy_version=POLICY_VERSION,scenario_policy_version=DISCOVERY_COST_STRESS_VERSION)
    output.append({'category':'COST_STRESS','name':n['scenario_name'],'parameters':base,'execution':n['execution'],'parameter_hash':sph,'execution_hash':n['execution_hash'],'assumptions':n['assumptions'],'policy':DISCOVERY_COST_STRESS_VERSION,'hash':h})
  return output
 def _persist(self,rid,candidate,scenario,order,fingerprint,status,aggregate_metrics,comparison,error=None):
  base=normalize_template_parameters(candidate['template'],json.loads(candidate['parameters'])); source_execution=aggregate_metrics.pop('_source_execution',None) or scenario['source_execution']; sch=build_candidate_identity(candidate['template'],scenario['parameters'],scenario['execution_hash']); bch=build_candidate_identity(candidate['template'],base,source_execution)
  values=(rid,candidate['id'],order,scenario['category'],scenario['name'],scenario['hash'],DISCOVERY_ROBUSTNESS_VERSION,scenario['policy'],status,json.dumps(scenario['assumptions'],sort_keys=True),candidate['parameter_hash'],scenario['parameter_hash'],source_execution,scenario['execution_hash'],bch,sch,fingerprint,5,aggregate_metrics.get('completed_fold_count',0),aggregate_metrics.get('failed_fold_count',5),json.dumps(aggregate_metrics,sort_keys=True),json.dumps(comparison,sort_keys=True),_now(),_now(),error,scenario['name'],json.dumps(aggregate_metrics,sort_keys=True))
  with self.repository.connect() as c:c.execute("INSERT INTO strategy_discovery_stress_tests(robustness_run_id,candidate_id,scenario_order,scenario_category,scenario_name,scenario_hash,robustness_version,scenario_policy_version,status,assumptions,source_parameter_hash,scenario_parameter_hash,source_execution_hash,scenario_execution_hash,source_candidate_config_hash,scenario_candidate_config_hash,dataset_fingerprint,fold_count,completed_fold_count,failed_fold_count,aggregate_metrics,comparison_to_base,created_at,completed_at,error,scenario,metrics) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(robustness_run_id,candidate_id,scenario_hash) DO UPDATE SET status=excluded.status,aggregate_metrics=excluded.aggregate_metrics,comparison_to_base=excluded.comparison_to_base,completed_at=excluded.completed_at,error=excluded.error",values)
 def _validate_persisted_scenario_plan(self, planned, persisted, fingerprint):
  """Return exact planned identity -> row mapping, rejecting durable plan drift."""
  expected={}; orders=set()
  for candidate, scenarios in planned:
   for order, scenario in enumerate(scenarios,1):
    key=(candidate['id'],scenario['hash']); expected[key]=(candidate,order,scenario)
  result={}
  for row in persisted:
   key=(row['candidate_id'],row['scenario_hash'])
   if key not in expected or key in result: raise ValueError('DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH')
   candidate,order,scenario=expected[key]
   if row['robustness_version']!=DISCOVERY_ROBUSTNESS_VERSION or row['scenario_policy_version']!=scenario['policy']:
    raise ValueError('DISCOVERY_ROBUSTNESS_VERSION_MISMATCH')
   fields={'scenario_order':order,'scenario_category':scenario['category'],'scenario_name':scenario['name'],'source_parameter_hash':candidate['parameter_hash'],'scenario_parameter_hash':scenario['parameter_hash'],'source_execution_hash':scenario['source_execution'],'scenario_execution_hash':scenario['execution_hash'],'dataset_fingerprint':fingerprint}
   if any(row[name]!=value for name,value in fields.items()): raise ValueError('DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH')
   result[key]=row; orders.add((candidate['id'],order))
  if len(orders)!=len(result): raise ValueError('DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH')
  return result
 def _run(self,jid,p,checkpoint):
  rid=int(p['robustness_run_id'])
  try:
   checkpoint(jid,1,'robustness.validating_source_run',{}); rr,source,request,policy,fingerprint=self._validate(rid,p)
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status='RUNNING',error=NULL,updated_at=? WHERE id=?",(_now(),rid))
   execution=DiscoveryExecutionConfig(**policy['execution_assumptions']).validate(); inst,timeframe=request['instrument'],request['timeframe']; cache=self._fold_cache(inst,timeframe)
   checkpoint(jid,8,'robustness.selecting_candidates',{})
   with self.repository.connect() as c: all_candidates=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number',(source['id'],))]
   ids=json.loads(rr['selected_candidates'] or '[]'); selected=[next(x for x in all_candidates if x['id']==cid) for cid in ids] if ids else select_robustness_candidates(all_candidates,p['top_k'],p['maximum_candidates'])
   if not ids:
    with self.repository.connect() as c:c.execute('UPDATE strategy_discovery_robustness_runs SET selected_candidates=?,updated_at=? WHERE id=?',(json.dumps([x['id'] for x in selected]),_now(),rid))
   warnings=list(WARNINGS)
   if not selected:
    warnings.append('NO_ELIGIBLE_CANDIDATES_FOR_ROBUSTNESS'); result=summarize_robustness_run(source['id'],[],[],warnings); result.update(neighbor_version=DISCOVERY_NEIGHBOR_VERSION,cost_stress_version=DISCOVERY_COST_STRESS_VERSION,cancelled_scenario_count=0)
    with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status='COMPLETED',result=?,completed_at=? WHERE id=?",(json.dumps(result),_now(),rid)); return {'robustness_run_id':rid}
   checkpoint(jid,12,'robustness.generating_scenarios',{}); plans=[(c,self._plan(c,execution,inst,timeframe,fingerprint,p)) for c in selected]
   for _,scenarios in plans:
    for scenario in scenarios: scenario['source_execution']=execution.execution_hash()
   with self.repository.connect() as c: persisted=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_stress_tests WHERE robustness_run_id=?',(rid,))]
   existing=self._validate_persisted_scenario_plan(plans,persisted,fingerprint)
   total=sum(len(x[1]) for x in plans); records=[]; completed=failed=0
   for cnum,(candidate,scenarios) in enumerate(plans,1):
    checkpoint(jid,None,'robustness.evaluating_scenarios',{'selected_candidate_count':len(selected),'current_candidate_number':cnum,'total_scenario_count':total,'completed_scenarios':completed,'failed_scenarios':failed})
    base=json.loads(candidate['aggregate_metrics'] or '{}')
    for order,scenario in enumerate(scenarios,1):
     checkpoint(jid,None,'robustness.evaluating_scenarios',{'current_candidate_number':cnum,'current_scenario_number':order,'total_scenario_count':total})
     old=existing.get((candidate['id'],scenario['hash']))
     if old and old['status']=='COMPLETED' and old['dataset_fingerprint']==fingerprint and old['scenario_parameter_hash']==scenario['parameter_hash'] and old['scenario_execution_hash']==scenario['execution_hash'] and old['aggregate_metrics']:
      records.append(dict(old)); completed+=1; continue
     # Persist the durable in-progress identity before any fold checkpoint can
     # raise JobCancelled.  Retries reuse this row and recompute it.
     self._persist(rid,candidate,scenario,order,fingerprint,'RUNNING',{}, {},None)
     folds=[]; error=None
     try:
      for no,(_,_,vs,ve) in enumerate(FOLDS,1):
       checkpoint(jid,None,'robustness.evaluating_scenarios',{'current_fold':no,'total_folds':5}); effective=ve-TIMEFRAME_SECONDS[timeframe]
       outcome=run_discovery_candidate_backtest(list(cache[no]),inst,timeframe,candidate['template'],scenario['parameters'],vs,effective,scenario['execution'],fingerprint); evidence=outcome['discovery_evidence']; expected=build_candidate_identity(candidate['template'],scenario['parameters'],scenario['execution_hash']); expected_eval=build_evaluation_identity(expected,inst,timeframe,vs,effective,fingerprint)
       if evidence['parameter_hash']!=scenario['parameter_hash'] or evidence['execution_hash']!=scenario['execution_hash'] or evidence['candidate_config_hash']!=expected or evidence['evaluation_hash']!=expected_eval: raise ValueError('DISCOVERY_ROBUSTNESS_IDENTITY_MISMATCH')
       folds.append({'status':'COMPLETED','metrics':outcome['metrics'],'buy_hold_metrics':buy_and_hold(list(cache[no]),vs,effective,scenario['execution'])}); checkpoint(jid,None,'robustness.evaluating_scenarios',{'current_fold':no,'total_folds':5})
      ag=aggregate(folds); diag=evaluate_eligibility(ag,timeframe,'DEVELOPMENT_CANDIDATE'); comparison={key+'_delta':(ag.get(key)-base.get(key) if ag.get(key) is not None and base.get(key) is not None else None) for key in ('median_excess_return','worst_excess_return','worst_maximum_drawdown','profitable_fold_ratio','benchmark_beating_fold_ratio','total_trades')}; comparison.update(scenario_eligibility_status='ELIGIBLE' if diag['eligible'] else 'REJECTED',scenario_elimination_reasons=diag['reasons']); status='COMPLETED'; completed+=1
     except JobCancelled:
      self._persist(rid,candidate,scenario,order,fingerprint,'CANCELLED',{}, {},'DISCOVERY_ROBUSTNESS_CANCELLED')
      raise
     except Exception as exc: ag={}; comparison={}; error=str(exc); status='FAILED'; failed+=1
     self._persist(rid,candidate,scenario,order,fingerprint,status,ag,comparison,error)
     with self.repository.connect() as c:
      records.append(dict(c.execute('SELECT * FROM strategy_discovery_stress_tests WHERE robustness_run_id=? AND candidate_id=? AND scenario_hash=?',(rid,candidate['id'],scenario['hash'])).fetchone()))
     checkpoint(jid,None,'robustness.evaluating_scenarios',{'completed_scenarios':completed,'failed_scenarios':failed})
   checkpoint(jid,98,'robustness.summarizing_candidates',{}); summaries=[summarize_candidate_robustness(c,[r for r in records if r['candidate_id']==c['id']]) for c in selected]
   if failed:warnings.append('PARTIAL_ROBUSTNESS_SCENARIO_FAILURE')
   result=summarize_robustness_run(source['id'],summaries,records,warnings); result.update(neighbor_version=DISCOVERY_NEIGHBOR_VERSION,cost_stress_version=DISCOVERY_COST_STRESS_VERSION,cancelled_scenario_count=0)
   status='FAILED' if failed==total else 'COMPLETED'; checkpoint(jid,99,'robustness.completed',{})
   with self.repository.connect() as c:c.execute('UPDATE strategy_discovery_robustness_runs SET status=?,progress=?,result=?,error=?,completed_at=?,updated_at=? WHERE id=?',(status,json.dumps({'stage':'COMPLETED','completed_scenarios':completed,'failed_scenarios':failed}),json.dumps(result), 'ALL_ROBUSTNESS_SCENARIOS_FAILED' if status=='FAILED' else None,_now(),_now(),rid))
   return {'robustness_run_id':rid}
  except JobCancelled:
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_robustness_runs SET status='CANCELLED',error='DISCOVERY_ROBUSTNESS_CANCELLED',completed_at=?,updated_at=? WHERE id=? AND status!='COMPLETED'",(_now(),_now(),rid))
   raise
  except Exception as error:
   code=str(error) if str(error).startswith('DISCOVERY_ROBUSTNESS_') else 'DISCOVERY_ROBUSTNESS_WORKER_ERROR'; self._fail(rid,code); raise
