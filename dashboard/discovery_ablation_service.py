"""Persistent, development-only component-ablation worker for Discovery."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .discovery_ablation import (DISCOVERY_ABLATION_IDENTITY_VERSION,
    DISCOVERY_ABLATION_VERSION, generate_ablation_scenarios)
from .discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from .discovery_identity import (build_candidate_identity, build_evaluation_identity,
    build_parameter_identity, canonical_json_hash, normalize_template_parameters)
from .discovery_scoring import (DISCOVERY_ELIGIBILITY_VERSION, DISCOVERY_PARETO_VERSION,
    DISCOVERY_SCORING_VERSION, evaluate_eligibility)
from .discovery_service import DISCOVERY_AGGREGATION_VERSION, FOLDS, aggregate, buy_and_hold
from .job_queue import JobCancelled
from .okx_history import INSTRUMENTS, TIMEFRAME_SECONDS

DISCOVERY_ABLATION_RUN_VERSION = "discovery-development-ablation-run-v1"
WARNINGS = ["Ablation uses development folds only.", "Primary holdout was not accessed.",
    "Final OOT was not accessed.", "Ablation results do not prove future profitability.",
    "Source development ranking was not changed.", "No strategy was activated."]

def _now(): return datetime.now(timezone.utc).isoformat()
def _loads(value, default):
 try: return json.loads(value) if value else default
 except (TypeError, json.JSONDecodeError): return default

def select_ablation_candidates(candidates, top_k=10, maximum_candidates=20):
 """Front one first, rank fill, then a hard cap; result always rank ordered."""
 if not 1 <= int(top_k) <= 20 or not 1 <= int(maximum_candidates) <= 20: raise ValueError('top_k and maximum_candidates must be 1..20.')
 eligible=[dict(x) for x in candidates if x.get('eligibility_status')=='ELIGIBLE']
 order=lambda x:(int(x.get('eligible_rank') or 10**9),int(x.get('id') or 10**9))
 chosen={}
 for x in sorted((x for x in eligible if x.get('pareto_rank')==1),key=order): chosen[x['id']]=x
 for x in sorted(eligible,key=order):
  if len(chosen)>=int(top_k): break
  chosen.setdefault(x['id'],x)
 return sorted(chosen.values(),key=order)[:int(maximum_candidates)]

class DiscoveryAblationService:
 def __init__(self, repository, jobs):
  self.repository,self.jobs=repository,jobs
  jobs.register('DISCOVERY_ABLATION',self._run)
  jobs.register_terminal_handler('DISCOVERY_ABLATION',self._terminal)
 def _request(self,p):
  if not isinstance(p,dict) or 'discovery_run_id' not in p: raise ValueError('discovery_run_id is required.')
  r={'discovery_run_id':p['discovery_run_id'],'top_k':p.get('top_k',10),'maximum_candidates':p.get('maximum_candidates',20)}
  if any(type(v) is not int for v in r.values()) or r['discovery_run_id']<1: raise ValueError('discovery_run_id, top_k and maximum_candidates must be integers.')
  if not 1<=r['top_k']<=20 or not 1<=r['maximum_candidates']<=20: raise ValueError('top_k and maximum_candidates must be 1..20.')
  return r
 def _policy(self): return {'ablation_run_version':DISCOVERY_ABLATION_RUN_VERSION,'ablation_version':DISCOVERY_ABLATION_VERSION,'ablation_identity_version':DISCOVERY_ABLATION_IDENTITY_VERSION,'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION}
 def start(self,p,client='public'):
  p=self._request(p)
  with self.repository.connect() as c: source=c.execute('SELECT status FROM strategy_discovery_runs WHERE id=?',(p['discovery_run_id'],)).fetchone()
  if not source or source['status']!='COMPLETED': raise ValueError('DISCOVERY_ABLATION_SOURCE_RUN_INVALID')
  now=_now()
  with self.repository.connect() as c: rid=c.execute('INSERT INTO strategy_discovery_ablation_runs(discovery_run_id,status,request,policy,created_at,updated_at) VALUES(?,?,?,?,?,?)',(p['discovery_run_id'],'QUEUED',json.dumps(p,sort_keys=True),json.dumps(self._policy(),sort_keys=True),now,now)).lastrowid
  try: job=self.jobs.enqueue('DISCOVERY_ABLATION',{**p,'ablation_run_id':rid},client,priority=117)
  except Exception:
   self._fail(rid,'DISCOVERY_ABLATION_QUEUE_ERROR'); raise
  with self.repository.connect() as c:c.execute('UPDATE strategy_discovery_ablation_runs SET job_id=? WHERE id=?',(job['id'],rid))
  return self.run_detail(rid)
 def retry(self,rid,client='public'):
  with self.repository.connect() as c: row=c.execute('SELECT * FROM strategy_discovery_ablation_runs WHERE id=?',(rid,)).fetchone()
  if not row or row['status'] not in ('FAILED','CANCELLED'): raise ValueError('Only failed or cancelled ablation runs can be retried.')
  p=_loads(row['request'],{}); job=self.jobs.enqueue('DISCOVERY_ABLATION',{**p,'ablation_run_id':rid},client,priority=117,retry_of=row['job_id'])
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_ablation_runs SET status='QUEUED',error=NULL,completed_at=NULL,job_id=?,updated_at=? WHERE id=?",(job['id'],_now(),rid))
  return {'id':rid,'job_id':job['id'],'status':'QUEUED'}
 def list_runs(self):
  with self.repository.connect() as c: rows=c.execute('SELECT * FROM strategy_discovery_ablation_runs ORDER BY id DESC').fetchall()
  return [self._run_row(x) for x in rows]
 def _run_row(self,row):
  x=dict(row)
  for k,d in (('request',{}),('policy',{}),('selected_candidates',[]),('progress',{}),('result',None)):x[k]=_loads(x.get(k),d)
  return x
 def run_detail(self,rid):
  with self.repository.connect() as c:
   row=c.execute('SELECT * FROM strategy_discovery_ablation_runs WHERE id=?',(rid,)).fetchone()
   scenarios=c.execute('SELECT * FROM strategy_discovery_ablation_scenarios WHERE ablation_run_id=? ORDER BY scenario_order',(rid,)).fetchall() if row else []
  if not row:return None
  out=self._run_row(row); out['scenarios']=[]
  for row in scenarios:
   x=dict(row)
   for k,d in (('normalized_ablation_flags',{}),('aggregate_metrics',{}),('comparison_to_base',{}),('scenario_elimination_reasons',[])):x[k]=_loads(x.get(k),d)
   out['scenarios'].append(x)
  return out
 def cancel(self,rid):
  with self.repository.connect() as c: row=c.execute('SELECT job_id,status FROM strategy_discovery_ablation_runs WHERE id=?',(rid,)).fetchone()
  if not row:raise ValueError('Ablation run not found.')
  if row['status'] in ('COMPLETED','CANCELLED','FAILED'):return self.run_detail(rid)
  if row['job_id']:
   self.jobs.cancel(row['job_id'])
   return self.run_detail(rid)
  raise ValueError('Active job not found.')
 def _terminal(self,job):
  rid=job.get('request_payload',{}).get('ablation_run_id')
  if rid and job['status'] in {'FAILED','CANCELLED'}:
   self._fail(rid,'DISCOVERY_ABLATION_CANCELLED' if job['status']=='CANCELLED' else 'DISCOVERY_ABLATION_WORKER_ERROR',job['status'])
 def _fail(self,rid,code,status='FAILED'):
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_ablation_runs SET status=?,error=?,completed_at=?,updated_at=? WHERE id=? AND status!='COMPLETED'",(status,code,_now(),_now(),rid))
 def _validate(self,rid,p):
  with self.repository.connect() as c:
   run=c.execute('SELECT * FROM strategy_discovery_ablation_runs WHERE id=?',(rid,)).fetchone(); source=c.execute('SELECT * FROM strategy_discovery_runs WHERE id=?',(p['discovery_run_id'],)).fetchone()
  if not run or not source or source['status']!='COMPLETED':raise ValueError('DISCOVERY_ABLATION_SOURCE_RUN_INVALID')
  policy=_loads(run['policy'],{}); expected=self._policy()
  if any(policy.get(k)!=v for k,v in expected.items()):raise ValueError('DISCOVERY_ABLATION_VERSION_MISMATCH')
  source_policy=_loads(source['search_policy'],{}); source_request=_loads(source['request'],{})
  need={'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
  if any(source_policy.get(k)!=v for k,v in need.items()):raise ValueError('DISCOVERY_ABLATION_VERSION_MISMATCH')
  if source_request.get('instrument') not in INSTRUMENTS or source_request.get('timeframe') not in TIMEFRAME_SECONDS:raise ValueError('DISCOVERY_ABLATION_SOURCE_RUN_INVALID')
  try: execution=DiscoveryExecutionConfig(**source_policy['execution_assumptions']).validate()
  except Exception:raise ValueError('DISCOVERY_ABLATION_SOURCE_RUN_INVALID')
  dataset=self.repository.discovery_dataset(source['dataset_id']); part=self.repository.discovery_partition(source['dataset_id'],source_request['instrument'],source_request['timeframe'])
  if not dataset or dataset['status']!='COMPLETE':raise ValueError('DISCOVERY_ABLATION_DATASET_INVALID')
  if not part or part['status']!='COMPLETE' or not part.get('fingerprint'):raise ValueError('DISCOVERY_ABLATION_PARTITION_INVALID')
  return dict(run),dict(source),source_request,execution,part['fingerprint']
 def _cache(self,inst,timeframe):
  cache={}; step=TIMEFRAME_SECONDS[timeframe]; boundary=int(datetime(2025,5,1,tzinfo=timezone.utc).timestamp())
  for no,(start,_,vs,ve) in enumerate(FOLDS,1):
   if ve>boundary:raise ValueError('DISCOVERY_ABLATION_PARTITION_INVALID')
   rows=sorted({int(x['ts']):dict(x) for x in self.repository.candles(inst,timeframe,start,ve-1) if x.get('confirmed',1)}.values(),key=lambda x:int(x['ts']))
   if not rows or any(int(x['ts'])>=boundary for x in rows) or not any(int(x['ts'])==vs for x in rows) or not any(int(x['ts'])==ve-step for x in rows):raise ValueError('DISCOVERY_ABLATION_PARTITION_INVALID')
   cache[no]=tuple(rows)
  return cache
 def _plan(self,candidates,execution,fingerprint):
  out=[]; order=0; eh=execution.execution_hash()
  for candidate in candidates:
   try: base=normalize_template_parameters(candidate['template'],_loads(candidate['parameters'],{}))
   except Exception:raise ValueError('DISCOVERY_ABLATION_SOURCE_CANDIDATE_INVALID')
   if candidate.get('parameter_hash') != build_parameter_identity(candidate['template'],base) or not candidate.get('aggregate_metrics') or candidate.get('eligible_rank') is None or candidate.get('pareto_rank') is None:raise ValueError('DISCOVERY_ABLATION_SOURCE_CANDIDATE_INVALID')
   base_hash=build_candidate_identity(candidate['template'],base,eh)
   for s in generate_ablation_scenarios(candidate['template'],base):
    order+=1; ablated=canonical_json_hash({'ablation_candidate_identity_version':DISCOVERY_ABLATION_IDENTITY_VERSION,'base_candidate_config_hash':base_hash,'execution_hash':eh,'ablation_identity':s['ablation_identity']})
    out.append({'candidate':candidate,'order':order,'base':base,'base_hash':base_hash,'ablated_hash':ablated,'execution_hash':eh,**s})
  return out
 def _verify_plan(self,plan,rows,fingerprint):
  expected={(x['candidate']['id'],x['ablation_identity']):x for x in plan}; seen=set(); result={}
  for r in rows:
   key=(r['candidate_id'],r['ablation_identity'])
   if key not in expected or key in seen:raise ValueError('DISCOVERY_ABLATION_IDENTITY_MISMATCH')
   x=expected[key]; fields={'scenario_order':x['order'],'removed_component':x['component_code'],'ablation_version':DISCOVERY_ABLATION_VERSION,'ablation_identity_version':DISCOVERY_ABLATION_IDENTITY_VERSION,'normalized_ablation_flags':json.dumps(x['normalized_ablation_flags'],sort_keys=True),'source_parameter_hash':x['source_parameter_hash'],'source_execution_hash':x['execution_hash'],'source_candidate_config_hash':x['base_hash'],'ablated_candidate_config_hash':x['ablated_hash'],'dataset_fingerprint':fingerprint}
   if r['ablation_version']!=DISCOVERY_ABLATION_VERSION or r['ablation_identity_version']!=DISCOVERY_ABLATION_IDENTITY_VERSION:raise ValueError('DISCOVERY_ABLATION_VERSION_MISMATCH')
   if any(r[k]!=v for k,v in fields.items() if k not in ('ablation_version','ablation_identity_version')):raise ValueError('DISCOVERY_ABLATION_IDENTITY_MISMATCH')
   seen.add(key);result[key]=dict(r)
  return result
 def _persist(self,rid,x,status,aggregate=None,comparison=None,error=None,diag=None):
  vals=(rid,x['candidate']['id'],x['order'],x['component_code'],x['ablation_identity'],DISCOVERY_ABLATION_VERSION,DISCOVERY_ABLATION_IDENTITY_VERSION,status,json.dumps(x['normalized_ablation_flags'],sort_keys=True),x['source_parameter_hash'],x['execution_hash'],x['base_hash'],x['ablated_hash'],x['_fingerprint'],(aggregate or {}).get('completed_fold_count',0),(aggregate or {}).get('failed_fold_count',0),json.dumps(aggregate or {},sort_keys=True),json.dumps(comparison or {},sort_keys=True),diag[0] if diag else None,json.dumps(diag[1],sort_keys=True) if diag else None,_now(),_now() if status in ('COMPLETED','FAILED','CANCELLED') else None,error)
  sql='INSERT INTO strategy_discovery_ablation_scenarios(ablation_run_id,candidate_id,scenario_order,removed_component,ablation_identity,ablation_version,ablation_identity_version,status,normalized_ablation_flags,source_parameter_hash,source_execution_hash,source_candidate_config_hash,ablated_candidate_config_hash,dataset_fingerprint,completed_fold_count,failed_fold_count,aggregate_metrics,comparison_to_base,scenario_eligibility_status,scenario_elimination_reasons,created_at,completed_at,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ablation_run_id,candidate_id,ablation_identity) DO UPDATE SET status=excluded.status,completed_fold_count=excluded.completed_fold_count,failed_fold_count=excluded.failed_fold_count,aggregate_metrics=excluded.aggregate_metrics,comparison_to_base=excluded.comparison_to_base,scenario_eligibility_status=excluded.scenario_eligibility_status,scenario_elimination_reasons=excluded.scenario_elimination_reasons,completed_at=excluded.completed_at,error=excluded.error'
  with self.repository.connect() as c:c.execute(sql,vals)
 def _summary(self,candidate,rows):
  rows=[dict(x) for x in rows]; done=[x for x in rows if x['status']=='COMPLETED']; comp=[]
  for x in rows:comp.append({'removed_component':x['removed_component'],'status':x['status'],'aggregate_metrics_subset':{k:_loads(x.get('aggregate_metrics'),{}).get(k) for k in ('median_excess_return','worst_excess_return','total_trades')},'comparison_deltas':_loads(x.get('comparison_to_base'),{}),'diagnostic_eligibility':x.get('scenario_eligibility_status'),'elimination_reasons':_loads(x.get('scenario_elimination_reasons'),[])})
  eligible=sum(x.get('scenario_eligibility_status')=='ELIGIBLE' for x in done)
  return {'candidate_id':candidate['id'],'candidate_number':candidate['candidate_number'],'eligible_rank':candidate['eligible_rank'],'pareto_rank':candidate['pareto_rank'],'development_score':candidate['development_score'],'planned_ablation_count':len(rows),'completed_ablation_count':len(done),'failed_ablation_count':sum(x['status']=='FAILED' for x in rows),'cancelled_ablation_count':sum(x['status']=='CANCELLED' for x in rows),'ablations_remaining_eligible':eligible,'ablation_eligibility_ratio':eligible/len(done) if done else None,'component_results':comp,'warnings':['NO_SUPPORTED_ABLATION_COMPONENTS'] if not rows else []}
 def _run(self,jid,p,checkpoint):
  rid=int(p['ablation_run_id'])
  try:
   checkpoint(jid,1,'ablation.validating_source_run',{}); rr,source,request,execution,fingerprint=self._validate(rid,p)
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_ablation_runs SET status='RUNNING',error=NULL,updated_at=? WHERE id=?",(_now(),rid))
   checkpoint(jid,8,'ablation.selecting_candidates',{})
   with self.repository.connect() as c:allc=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=?',(source['id'],))]
   ids=_loads(rr['selected_candidates'],[]); selected=[next(x for x in allc if x['id']==i) for i in ids] if ids else select_ablation_candidates(allc,p['top_k'],p['maximum_candidates'])
   if not ids:
    with self.repository.connect() as c:c.execute('UPDATE strategy_discovery_ablation_runs SET selected_candidates=?,updated_at=? WHERE id=?',(json.dumps([x['id'] for x in selected]),_now(),rid))
   warnings=list(WARNINGS)
   if not selected:
    warnings.append('NO_ELIGIBLE_CANDIDATES_FOR_ABLATION'); result={'source_discovery_run_id':source['id'],**self._policy(),'selected_candidate_count':0,'total_scenario_count':0,'completed_scenario_count':0,'failed_scenario_count':0,'cancelled_scenario_count':0,'candidate_summaries':[],'warnings':warnings}
    with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_ablation_runs SET status='COMPLETED',result=?,completed_at=?,updated_at=? WHERE id=?",(json.dumps(result),_now(),_now(),rid))
    return {'ablation_run_id':rid}
   cache=self._cache(request['instrument'],request['timeframe'])
   checkpoint(jid,12,'ablation.generating_scenarios',{}); plan=self._plan(selected,execution,fingerprint)
   for x in plan:x['_fingerprint']=fingerprint
   with self.repository.connect() as c: persisted=c.execute('SELECT * FROM strategy_discovery_ablation_scenarios WHERE ablation_run_id=?',(rid,)).fetchall()
   existing=self._verify_plan(plan,persisted,fingerprint)
   # Materialize the complete identity plan before evaluating any scenario.  This
   # makes retries validate a closed semantic plan rather than silently inventing
   # rows after an interruption.
   if not persisted:
    for x in plan:self._persist(rid,x,'PENDING')
    with self.repository.connect() as c: existing=self._verify_plan(plan,c.execute('SELECT * FROM strategy_discovery_ablation_scenarios WHERE ablation_run_id=?',(rid,)).fetchall(),fingerprint)
   records=[]; failed=0
   for ix,x in enumerate(plan,1):
    checkpoint(jid,None,'ablation.evaluating_scenarios',{'current_scenario':ix,'total_scenario_count':len(plan)})
    old=existing.get((x['candidate']['id'],x['ablation_identity']))
    if old and old['status']=='COMPLETED' and old['aggregate_metrics']:records.append(old);continue
    self._persist(rid,x,'RUNNING')
    try:
     folds=[]
     for no,(_,_,vs,ve) in enumerate(FOLDS,1):
      checkpoint(jid,None,'ablation.evaluating_fold',{'fold':no,'total_folds':5}); end=ve-TIMEFRAME_SECONDS[request['timeframe']]
      out=run_discovery_candidate_backtest(list(cache[no]),request['instrument'],request['timeframe'],x['candidate']['template'],x['base'],vs,end,execution,fingerprint,x['normalized_ablation_flags']); ev=out['discovery_evidence']; expected_eval=build_evaluation_identity(x['ablated_hash'],request['instrument'],request['timeframe'],vs,end,fingerprint)
      if ev['parameter_hash']!=x['source_parameter_hash'] or ev['execution_hash']!=x['execution_hash'] or ev['removed_component']!=x['component_code'] or ev['ablation_identity']!=x['ablation_identity'] or ev['normalized_ablation_flags']!=x['normalized_ablation_flags'] or ev['candidate_config_hash']!=x['ablated_hash'] or ev['evaluation_hash']!=expected_eval or ev['candidate_config_hash']==x['base_hash']:raise ValueError('DISCOVERY_ABLATION_IDENTITY_MISMATCH')
      folds.append({'status':'COMPLETED','metrics':out['metrics'],'buy_hold_metrics':buy_and_hold(list(cache[no]),vs,end,execution)}); checkpoint(jid,None,'ablation.evaluating_fold',{'fold':no,'total_folds':5})
     ag=aggregate(folds); base=_loads(x['candidate']['aggregate_metrics'],{}); keys=('median_excess_return','worst_excess_return','mean_excess_return','worst_validation_return','worst_maximum_drawdown','profitable_fold_ratio','benchmark_beating_fold_ratio','total_trades','signal_count'); comparison={k+'_delta':ag.get(k)-base.get(k) if ag.get(k) is not None and base.get(k) is not None else None for k in keys};comparison['delta_convention']='ablated value - source value'; verdict=evaluate_eligibility(ag,request['timeframe'],'DEVELOPMENT_CANDIDATE'); diag=('ELIGIBLE' if verdict['eligible'] else 'REJECTED',verdict['reasons']);self._persist(rid,x,'COMPLETED',ag,comparison,None,diag)
    except JobCancelled:self._persist(rid,x,'CANCELLED',error='DISCOVERY_ABLATION_CANCELLED');raise
    except Exception as e:failed+=1;self._persist(rid,x,'FAILED',error=str(e) if str(e).startswith('DISCOVERY_ABLATION_') else 'DISCOVERY_ABLATION_WORKER_ERROR')
    with self.repository.connect() as c:records.append(dict(c.execute('SELECT * FROM strategy_discovery_ablation_scenarios WHERE ablation_run_id=? AND candidate_id=? AND ablation_identity=?',(rid,x['candidate']['id'],x['ablation_identity'])).fetchone()))
   with self.repository.connect() as c:records=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_ablation_scenarios WHERE ablation_run_id=? ORDER BY scenario_order',(rid,))]
   summaries=[self._summary(x,[r for r in records if r['candidate_id']==x['id']]) for x in selected]
   if failed:warnings.append('PARTIAL_ABLATION_SCENARIO_FAILURE')
   result={'source_discovery_run_id':source['id'],**self._policy(),'selected_candidate_count':len(selected),'total_scenario_count':len(plan),'completed_scenario_count':sum(r['status']=='COMPLETED' for r in records),'failed_scenario_count':sum(r['status']=='FAILED' for r in records),'cancelled_scenario_count':sum(r['status']=='CANCELLED' for r in records),'candidate_summaries':summaries,'warnings':warnings}
   status='FAILED' if plan and failed==len(plan) else 'COMPLETED'
   with self.repository.connect() as c:c.execute('UPDATE strategy_discovery_ablation_runs SET status=?,result=?,error=?,completed_at=?,updated_at=? WHERE id=?',(status,json.dumps(result), 'ALL_ABLATION_SCENARIOS_FAILED' if status=='FAILED' else None,_now(),_now(),rid))
   return {'ablation_run_id':rid}
  except JobCancelled:
   self._fail(rid,'DISCOVERY_ABLATION_CANCELLED','CANCELLED');raise
  except Exception as e:
   self._fail(rid,str(e) if str(e).startswith('DISCOVERY_ABLATION_') else 'DISCOVERY_ABLATION_WORKER_ERROR');raise
