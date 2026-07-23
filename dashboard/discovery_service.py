"""Bounded development-only Strategy Discovery fold evaluation; it never activates or trades a candidate."""
from __future__ import annotations

import json, math, random, statistics
from datetime import datetime, timezone
from typing import Any

from .dataset_service import DiscoveryDatasetService
from .discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from .discovery_features import FEATURE_VERSION
from .discovery_features import build_features
from .discovery_templates import TEMPLATES, TEMPLATE_VERSION
from .strategy_v2 import TEMPLATES as V2_TEMPLATES, TEMPLATE_VERSION as V2_TEMPLATE_VERSION, DISCOVERY_STRATEGY_VERSION, STRATEGY_RULES_VERSION
from .discovery_v2_registry import V2_DISCOVERY_REGISTRY_VERSION, V2_SAMPLING_POLICY_VERSION, plan as v2_plan
from .strategy_v2_1 import TEMPLATES as V21_TEMPLATES, TEMPLATE_VERSION as V21_TEMPLATE_VERSION, DISCOVERY_STRATEGY_VERSION as V21_STRATEGY_VERSION, STRATEGY_RULES_VERSION as V21_RULES_VERSION, normalize_parameters as normalize_v21_parameters
from .discovery_v2_1_registry import REGISTRY_VERSION as V21_REGISTRY_VERSION, SAMPLING_POLICY_VERSION as V21_SAMPLING_POLICY_VERSION, plan as v21_plan
from .discovery_identity import (normalize_template_parameters, build_parameter_identity, build_candidate_identity, build_evaluation_identity, build_v2_evaluation_identity, build_v21_evaluation_identity,
    DISCOVERY_PARAMETER_IDENTITY_VERSION, DISCOVERY_CANDIDATE_IDENTITY_VERSION, DISCOVERY_EVALUATION_IDENTITY_VERSION, V2_PARAMETER_IDENTITY_VERSION, V2_CANDIDATE_IDENTITY_VERSION, V2_EVALUATION_IDENTITY_VERSION, V21_PARAMETER_IDENTITY_VERSION, V21_CANDIDATE_IDENTITY_VERSION, V21_EVALUATION_IDENTITY_VERSION)
from .job_queue import JobCancelled
from .okx_history import INSTRUMENTS, TIMEFRAME_SECONDS
from .discovery_scoring import (DISCOVERY_ELIGIBILITY_VERSION,DISCOVERY_SCORING_VERSION,DISCOVERY_PARETO_VERSION,candidate_complexity,evaluate_eligibility,calculate_score,assign_pareto_fronts,rank_eligible_candidates)
from .discovery_diagnostics import (fixed_path_cost_attribution, lifecycle_summary,
    mean_reversion_diagnostics, signal_event_study)

ENGINE_VERSION = "canonical-next-bar-open/discovery-adapter-v1"
POLICY_VERSION = "discovery-policy-v1"
DISCOVERY_AGGREGATION_VERSION = "discovery-development-aggregation-v2"
DISCOVERY_SAMPLER_VERSION = "discovery-template-semantic-sampler-v1"
SAMPLING_ATTEMPT_MULTIPLIER = 100

def ts(y, m, d): return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
FOLDS = [(ts(2024,1,1),ts(2024,7,1),ts(2024,7,1),ts(2024,9,1)),(ts(2024,3,1),ts(2024,9,1),ts(2024,9,1),ts(2024,11,1)),(ts(2024,5,1),ts(2024,11,1),ts(2024,11,1),ts(2025,1,1)),(ts(2024,7,1),ts(2025,1,1),ts(2025,1,1),ts(2025,3,1)),(ts(2024,9,1),ts(2025,3,1),ts(2025,3,1),ts(2025,5,1))]

def _median(values): return statistics.median(values) if values else None
def _finite(values): return [float(x) for x in values if x is not None and math.isfinite(float(x))]

def buy_and_hold(candles: list[dict[str, Any]], start_ts: int, end_ts: int, execution: DiscoveryExecutionConfig) -> dict[str, Any]:
    """Long-only benchmark: adverse fills and both fees, sized so entry cost never exceeds capital."""
    visible = [x for x in candles if start_ts <= int(x["ts"]) <= end_ts]
    if not visible: raise ValueError("VALIDATION_CANDLES_MISSING")
    first, last = visible[0], visible[-1]
    raw_entry, raw_exit = float(first["open"]), float(last["close"])
    entry = raw_entry * (1 + execution.slippage); exit = raw_exit * (1 - execution.slippage)
    size = execution.initial_capital / (entry * (1 + execution.trading_fee))
    entry_fee = entry * size * execution.trading_fee; exit_fee = exit * size * execution.trading_fee
    final_equity = execution.initial_capital - entry * size - entry_fee + exit * size - exit_fee
    # Mark the same no-leverage position at each validation close using an immediate
    # adverse, fee-bearing liquidation value, so benchmark drawdown is cost-aware too.
    marked = [size * float(row["close"]) * (1-execution.slippage) * (1-execution.trading_fee) for row in visible]
    peak = execution.initial_capital; maximum_drawdown = 0.0
    for value in marked:
        peak=max(peak,value); maximum_drawdown=max(maximum_drawdown,(peak-value)/peak*100 if peak else 0.0)
    result = {"initial_capital": execution.initial_capital, "entry_ts": int(first["ts"]), "exit_ts": int(last["ts"]),
        "raw_entry_price": raw_entry, "effective_entry_price": entry, "raw_exit_price": raw_exit, "effective_exit_price": exit,
        "position_size": size, "entry_fee": entry_fee, "exit_fee": exit_fee, "final_equity": final_equity,
        "net_profit": final_equity-execution.initial_capital, "total_return": (final_equity/execution.initial_capital-1)*100,
        "fees_paid": entry_fee+exit_fee, "maximum_drawdown": maximum_drawdown, "annualized_return": None,
        "sample_note": "Cost-aware long-only buy-and-hold benchmark; drawdown uses close-marked liquidation equity."}
    elapsed = int(last["ts"]) - int(first["ts"])
    if elapsed >= 86400 and final_equity > 0:
        result["annualized_return"] = ((final_equity/execution.initial_capital) ** (365.25*86400/elapsed)-1)*100
    return result

def aggregate(folds: list[dict[str, Any]]) -> dict[str, Any]:
    done = [x for x in folds if x["status"] == "COMPLETED"]; metrics = [x["metrics"] for x in done]
    returns = [float(x["total_return"]) for x in metrics]; trades = [int(x["total_trades"]) for x in metrics]
    dd = _finite([x.get("maximum_drawdown") for x in metrics]); sharpe = _finite([x.get("sharpe_ratio") for x in metrics]); sortino = _finite([x.get("sortino_ratio") for x in metrics]); pf = _finite([x.get("profit_factor") for x in metrics])
    benchmarks = [x["buy_hold_metrics"] for x in done]; benchmark_returns = [float(x["total_return"]) for x in benchmarks]
    excess = [a-b for a,b in zip(returns, benchmark_returns)]
    return {"completed_fold_count": len(done), "failed_fold_count": len(folds)-len(done), "folds_with_trades": sum(x > 0 for x in trades), "total_trades": sum(trades),
      "median_trades_per_fold": _median(trades), "minimum_trades_in_one_fold": min(trades) if trades else None, "maximum_trades_in_one_fold": max(trades) if trades else None,
      "mean_validation_return": statistics.mean(returns) if returns else None, "median_validation_return": _median(returns), "worst_validation_return": min(returns) if returns else None, "best_validation_return": max(returns) if returns else None,
      "validation_return_standard_deviation": statistics.stdev(returns) if len(returns) > 1 else None, "profitable_fold_count": sum(x > 0 for x in returns), "profitable_fold_ratio": sum(x > 0 for x in returns)/len(returns) if returns else None,
      "benchmark_beating_fold_count":sum(a>b for a,b in zip(returns,benchmark_returns)),"benchmark_beating_fold_ratio":sum(a>b for a,b in zip(returns,benchmark_returns))/len(returns) if returns else None,
      "median_maximum_drawdown": _median(dd), "worst_maximum_drawdown": max(dd) if dd else None, "median_sharpe_ratio": _median(sharpe), "median_sortino_ratio": _median(sortino), "median_profit_factor": _median(pf), "finite_sharpe_fold_count":len(sharpe),"finite_sortino_fold_count":len(sortino),"finite_profit_factor_fold_count":len(pf),"folds_with_undefined_profit_factor": sum(x.get("profit_factor") is None or not math.isfinite(float(x["profit_factor"])) for x in metrics),
      "total_fees_paid": sum(float(x["fees_paid"]) for x in metrics), "mean_benchmark_return": statistics.mean(benchmark_returns) if benchmark_returns else None, "median_benchmark_return": _median(benchmark_returns), "mean_excess_return": statistics.mean(excess) if excess else None, "median_excess_return": _median(excess), "worst_excess_return": min(excess) if excess else None,
      "aggregate_policy_version": DISCOVERY_AGGREGATION_VERSION}

class DiscoveryService:
 def __init__(self, repo, jobs):
  self.repository=repo; self.jobs=jobs; self.datasets=DiscoveryDatasetService(repo)
  jobs.register('DISCOVERY_DATASET',self._dataset_job); jobs.register('STRATEGY_DISCOVERY',self._run_job); jobs.register_terminal_handler('STRATEGY_DISCOVERY',self._job_terminal)
 def _dataset_job(self,jid,p,checkpoint): return self.datasets.prepare(p,lambda _,pct,msg,args:checkpoint(jid,pct,msg,args),lambda:self.jobs.checkpoint(jid))
 def prepare_dataset(self,p,client='public'):
  job=self.jobs.enqueue('DISCOVERY_DATASET',p,client,priority=105); return {'job_id':job['id'],'status':job['status']}
 def _request(self,p):
  required=('instrument','timeframe','execution_assumptions','templates','trial_budget','seed')
  if any(key not in p for key in required): raise ValueError('Discovery runs require instrument, timeframe, execution assumptions, templates, trial budget, and seed.')
  inst=p.get('instrument'); timeframe=p.get('timeframe')
  if inst not in INSTRUMENTS or timeframe not in TIMEFRAME_SECONDS: raise ValueError('Unsupported Discovery instrument or timeframe.')
  budget=int(p['trial_budget']);
  if not 1<=budget<=500: raise ValueError('Trial budget must be 1..500.')
  templates=p['templates'];
  all_templates=set(TEMPLATES)|set(V2_TEMPLATES)|set(V21_TEMPLATES)
  if not templates or not set(templates) <= all_templates: raise ValueError('Unsupported Discovery template.')
  families=[set(TEMPLATES),set(V2_TEMPLATES),set(V21_TEMPLATES)]
  if sum(bool(set(templates)&family) for family in families)!=1: raise ValueError('V1, V2, and V2.1 templates must use separate Discovery runs.')
  if (set(templates)&(set(V2_TEMPLATES)|set(V21_TEMPLATES))) and budget>36: raise ValueError('V2 Discovery pilot budget must be at most 36.')
  if p.get('mode','PRICE_ONLY')!='PRICE_ONLY': raise ValueError('FLOW_OVERLAY is unavailable until verified public coverage is persisted.')
  execution=DiscoveryExecutionConfig(**p['execution_assumptions']).validate()
  return inst,timeframe,budget,templates,execution
 def start(self,p,client='public'):
  did=int(p['dataset_id']); ds=self.repository.discovery_dataset(did)
  if not ds or ds['status']!='COMPLETE': raise ValueError('A complete fixed dataset is required.')
  if json.loads(ds.get('manifest') or '{}').get('is_smoke_test'): raise ValueError('Smoke-test datasets are not eligible for formal Discovery runs.')
  inst,timeframe,budget,templates,execution=self._request(p)
  part=self.repository.discovery_partition(did,inst,timeframe)
  if not part: raise ValueError('Selected dataset partition is missing.')
  if part['status']!='COMPLETE': raise ValueError('Selected dataset partition must be COMPLETE.')
  if not part.get('fingerprint'): raise ValueError('Selected dataset partition fingerprint is required.')
  if int(part.get('first_ts') or 0)>FOLDS[0][0] or int(part.get('last_ts') or 0)<FOLDS[-1][3]-TIMEFRAME_SECONDS[timeframe]: raise ValueError('Selected partition does not cover development folds.')
  normalized={**p,'instrument':inst,'timeframe':timeframe,'trial_budget':budget,'templates':templates,'execution_assumptions':execution.__dict__,'mode':'PRICE_ONLY'}; now=datetime.now(timezone.utc).isoformat(); seed=int(p.get('seed',20260721))
  with self.repository.connect() as c:
   is_v2=bool(set(templates)&set(V2_TEMPLATES)); is_v21=bool(set(templates)&set(V21_TEMPLATES))
   policy={'execution_assumptions':execution.__dict__,'parameter_identity_version':V21_PARAMETER_IDENTITY_VERSION if is_v21 else V2_PARAMETER_IDENTITY_VERSION if is_v2 else DISCOVERY_PARAMETER_IDENTITY_VERSION,'candidate_identity_version':V21_CANDIDATE_IDENTITY_VERSION if is_v21 else V2_CANDIDATE_IDENTITY_VERSION if is_v2 else DISCOVERY_CANDIDATE_IDENTITY_VERSION,'evaluation_identity_version':V21_EVALUATION_IDENTITY_VERSION if is_v21 else V2_EVALUATION_IDENTITY_VERSION if is_v2 else DISCOVERY_EVALUATION_IDENTITY_VERSION,'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
   if is_v2: policy.update(registry_version=V2_DISCOVERY_REGISTRY_VERSION,strategy_rules_version=STRATEGY_RULES_VERSION,strategy_version=DISCOVERY_STRATEGY_VERSION)
   if is_v21: policy.update(registry_version=V21_REGISTRY_VERSION,strategy_rules_version=V21_RULES_VERSION,strategy_version=V21_STRATEGY_VERSION)
   sampler=V21_SAMPLING_POLICY_VERSION if is_v21 else V2_SAMPLING_POLICY_VERSION if is_v2 else DISCOVERY_SAMPLER_VERSION
   cur=c.execute("INSERT INTO strategy_discovery_runs(dataset_id,status,request,search_policy,sampler,seed,maximum_trials,templates,feature_version,engine_version,scoring_version,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(did,'QUEUED',json.dumps(normalized),json.dumps(policy),sampler,seed,budget,json.dumps(templates),FEATURE_VERSION,ENGINE_VERSION,POLICY_VERSION,'{}',now,now)); rid=cur.lastrowid
  try: j=self.jobs.enqueue('STRATEGY_DISCOVERY',{**normalized,'discovery_run_id':rid},client,priority=115)
  except Exception:
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='FAILED',error=? WHERE id=?",('Queue enqueue failed',rid))
   raise
  return {'id':rid,'job_id':j['id'],'status':'QUEUED'}
 def _sample(self,rng,t):
  fast=rng.choice([6,10,20,30,60]); slow=rng.choice([60,100,150,200])
  while fast>=slow: fast=rng.choice([6,10,20,30,60])
  p={'fast_period':fast,'slow_period':slow,'fast_ma_type':rng.choice(['SMA','EMA']),'atr_period':rng.choice([7,10,14,20,28]),'volume_enabled':rng.choice([True,False])}
  if p['volume_enabled']: p['minimum_volume_ratio']=rng.randint(70,200)/100
  if t=='TREND_PULLBACK': p['maximum_distance']=rng.choice([.002,.003,.004,.005,.006,.008])
  if t=='MEAN_REVERSION': p.update(rsi_lower=rng.randint(20,49),rsi_upper=rng.randint(51,80))
  return normalize_template_parameters(t,p)
 def _candidate_definitions(self, rng, templates, budget):
  if set(templates) <= set(V21_TEMPLATES):
   planned,rejected,sampling=v21_plan(32); selected=[(t,p) for t,p in planned if t in templates][:budget]; definitions=[(t,p,build_parameter_identity(t,p)) for t,p in selected]; sampling={**sampling,'sampled_candidate_count':len(definitions)}
   if not definitions: raise ValueError('DISCOVERY_SEARCH_SPACE_EXHAUSTED')
   return definitions,{**sampling,'structurally_rejected':[{"template":t,"parameters":p,"reason":r} for t,p,r in rejected]}
  if set(templates) <= set(V2_TEMPLATES):
   planned,rejected,sampling=v2_plan(budget); definitions=[(t,p,build_parameter_identity(t,p)) for t,p in planned if t in templates]
   if not definitions: raise ValueError('DISCOVERY_SEARCH_SPACE_EXHAUSTED')
   return definitions,{**sampling,'structurally_rejected':[{"template":t,"parameters":p,"reason":r} for t,p,r in rejected]}
  result=[]; seen=set(); attempts=duplicates=0; limit=max(100,budget*SAMPLING_ATTEMPT_MULTIPLIER)
  while len(result)<budget and attempts<limit:
   template=templates[len(result)%len(templates)]; attempts+=1; params=self._sample(rng,template); ph=build_parameter_identity(template,params); key=(template,ph)
   if key in seen: duplicates+=1; continue
   seen.add(key); result.append((template,params,ph))
  if len(result)!=budget: raise ValueError('DISCOVERY_SEARCH_SPACE_EXHAUSTED')
  return result,{'sampler_version':DISCOVERY_SAMPLER_VERSION,'sampling_attempts':attempts,'duplicate_samples_rejected':duplicates,'unique_candidates_generated':len(result),'sampling_attempt_limit':limit}
 def _job_terminal(self, job):
  rid=job.get('request_payload',{}).get('discovery_run_id')
  if not rid or job['status'] not in {'CANCELLED','FAILED'}: return
  code=self._error_code(job.get('error')) if job['status']=='FAILED' else 'DISCOVERY_CANCELLED'
  with self.repository.connect() as c:
   if job['status']=='CANCELLED': c.execute("UPDATE strategy_discovery_candidates SET status='CANCELLED',completed_at=? WHERE discovery_run_id=? AND status='EVALUATING'",(datetime.now(timezone.utc).isoformat(),rid))
   # A queue can be cancelled before its worker claims it, while failures can
   # occur before Discovery has completed validation.  Never overwrite evidence
   # from a completed run.
   c.execute("UPDATE strategy_discovery_runs SET status=?,error=?,completed_at=? WHERE id=? AND status IN ('QUEUED','RUNNING')",(job['status'],code,datetime.now(timezone.utc).isoformat(),rid))
 def _error_code(self, error):
  text=str(error or '')
  for code in ('DISCOVERY_IDENTITY_VERSION_MISMATCH','DISCOVERY_SEARCH_SPACE_EXHAUSTED','VALIDATION_CANDLES_MISSING','DISCOVERY_PARTITION_MISSING','ALL_CANDIDATES_FAILED'):
   if code in text: return code
  return 'DISCOVERY_WORKER_ERROR'
 def _fold_rows(self, inst, timeframe, train_start, validation_end):
  # End exclusive query: never load holdout/OOT or the validation end candle.
  rows=self.repository.candles(inst,timeframe,train_start,validation_end-1)
  rows=sorted({int(x['ts']):x for x in rows if x.get('confirmed',1)}.values(),key=lambda x:int(x['ts']))
  return rows
 def _evaluation_hash(self, template, params, execution, inst, timeframe, start, end, fingerprint):
  build=build_v21_evaluation_identity if template in V21_TEMPLATES else build_v2_evaluation_identity if template in V2_TEMPLATES else build_evaluation_identity
  return build(build_candidate_identity(template,params,execution.execution_hash()),inst,timeframe,start,end,fingerprint)
 def _run_job(self,jid,p,checkpoint):
  rid=int(p['discovery_run_id'])
  # This must precede all worker validation so every terminal worker outcome
  # projects to a terminal Discovery run state.
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='RUNNING',error=NULL WHERE id=? AND status!='COMPLETED'",(rid,))
  try:
   inst,timeframe,budget,templates,execution=self._request(p); step=TIMEFRAME_SECONDS[timeframe]; rng=random.Random(int(p['seed'])); part=self.repository.discovery_partition(int(p['dataset_id']),inst,timeframe)
   if not part or not part.get('fingerprint'): raise ValueError('DISCOVERY_PARTITION_MISSING')
   dataset=self.repository.discovery_dataset(int(p['dataset_id']))
   fingerprint=(dataset or {}).get('dataset_fingerprint') if set(templates)<=(set(V2_TEMPLATES)|set(V21_TEMPLATES)) else part['fingerprint']
   if not fingerprint: raise ValueError('DISCOVERY_PARTITION_MISSING')
   fold_cache={}
   with self.repository.connect() as c:
    stored=c.execute('SELECT sampler,search_policy FROM strategy_discovery_runs WHERE id=?',(rid,)).fetchone()
   if not stored: raise ValueError('DISCOVERY_PARTITION_MISSING')
   run=dict(stored); policy=json.loads(run['search_policy'] or '{}')
   is_v21=set(templates)<=set(V21_TEMPLATES); is_v2=set(templates)<=set(V2_TEMPLATES)
   expected_sampler=V21_SAMPLING_POLICY_VERSION if is_v21 else V2_SAMPLING_POLICY_VERSION if is_v2 else DISCOVERY_SAMPLER_VERSION
   expected_versions={'parameter_identity_version':V21_PARAMETER_IDENTITY_VERSION if is_v21 else V2_PARAMETER_IDENTITY_VERSION if is_v2 else DISCOVERY_PARAMETER_IDENTITY_VERSION,'candidate_identity_version':V21_CANDIDATE_IDENTITY_VERSION if is_v21 else V2_CANDIDATE_IDENTITY_VERSION if is_v2 else DISCOVERY_CANDIDATE_IDENTITY_VERSION,'evaluation_identity_version':V21_EVALUATION_IDENTITY_VERSION if is_v21 else V2_EVALUATION_IDENTITY_VERSION if is_v2 else DISCOVERY_EVALUATION_IDENTITY_VERSION,'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION}
   if run['sampler'] != expected_sampler or any(policy.get(k)!=v for k,v in expected_versions.items()): raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
   checkpoint(jid,5,'discovery.validating_dataset',{}); checkpoint(jid,10,'discovery.loading_folds',{})
   for no,(a,b,vs,ve) in enumerate(FOLDS,1):
    rows=self._fold_rows(inst,timeframe,a,ve)
    if not rows or not any(int(x['ts'])==vs for x in rows) or not any(int(x['ts'])==ve-step for x in rows): raise ValueError('VALIDATION_CANDLES_MISSING')
    fold_cache[no]=(rows, build_features(rows,{"ma_periods":[20,60,200],"atr_period":14,"bb_period":20,"rsi_period":14,"volume_period":20}) if set(templates)<=(set(V2_TEMPLATES)|set(V21_TEMPLATES)) else None)
   checkpoint(jid,15,'discovery.generating_candidates',{'total_candidates':budget})
   completed=failed=fold_done=fold_failed=0
   with self.repository.connect() as c: existing=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number',(rid,))]
   # Rebuild the complete sequence from the original seed.  Persisted rows are
   # valid only when they are its exact contiguous prefix; this supports retry
   # without changing candidate IDs or semantic identities.
   definitions,sampling=self._candidate_definitions(rng,templates,budget)
   if len(existing)>budget: raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
   seen=set()
   for expected_number,old in enumerate(existing,1):
    if old['candidate_number'] != expected_number or old['template'] not in templates or old['template'] not in (set(TEMPLATE_VERSION)|set(V2_TEMPLATE_VERSION)|set(V21_TEMPLATE_VERSION)): raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
    try: persisted=json.loads(old['parameters'])
    except (TypeError,json.JSONDecodeError): raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
    try: params=(normalize_v21_parameters(old['template'],persisted) if old['template'] in V21_TEMPLATES else __import__('dashboard.strategy_v2',fromlist=['normalize_parameters']).normalize_parameters(old['template'],persisted) if old['template'] in V2_TEMPLATES else normalize_template_parameters(old['template'],persisted))
    except ValueError: raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
    ph=build_parameter_identity(old['template'],params); semantic=(old['template'],ph)
    if persisted != params or ph != old['parameter_hash'] or int(old['complexity']) != candidate_complexity(old['template'],params) or semantic in seen: raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
    seen.add(semantic)
    if (old['template'],params,ph) != definitions[expected_number-1]: raise ValueError('DISCOVERY_IDENTITY_VERSION_MISMATCH')
   sampling={**sampling,'reused_persisted_candidates':bool(existing),'persisted_candidate_count':len(existing)}
   for n,(template,params,ph) in enumerate(definitions,1):
    checkpoint(jid,15+int((n-1)*75/budget),'discovery.evaluating_folds',{'current_candidate':n,'total_candidates':budget,'candidates_completed':completed,'candidates_failed':failed,'folds_completed':fold_done,'folds_failed':fold_failed})
    now=datetime.now(timezone.utc).isoformat()
    with self.repository.connect() as c:
     old=c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? AND candidate_number=?',(rid,n)).fetchone()
     if old: cid=old['id']; c.execute("UPDATE strategy_discovery_candidates SET status='EVALUATING',error=NULL WHERE id=?",(cid,))
     else: cid=c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(rid,n,template,(V21_TEMPLATE_VERSION[template] if template in V21_TEMPLATES else V2_TEMPLATE_VERSION[template] if template in V2_TEMPLATES else TEMPLATE_VERSION[template]),json.dumps(params),ph,'{}',candidate_complexity(template,params),'EVALUATING',now)).lastrowid
    candidate_failed=False
    for no,(a,b,vs,ve) in enumerate(FOLDS,1):
     checkpoint(jid,None,'discovery.evaluating_folds',{'current_candidate':n,'total_candidates':budget,'current_fold':no,'total_folds':len(FOLDS),'candidates_completed':completed,'candidates_failed':failed,'folds_completed':fold_done,'folds_failed':fold_failed})
     effective=ve-step; rows,shared_v2_features=fold_cache[no]
     try:
      expected_hash=self._evaluation_hash(template,params,execution,inst,timeframe,vs,effective,fingerprint)
      with self.repository.connect() as c: prior=c.execute('SELECT metrics,status FROM strategy_discovery_folds WHERE candidate_id=? AND fold_number=?',(cid,no)).fetchone()
      if prior and prior['status']=='COMPLETED' and prior['metrics'] and json.loads(prior['metrics']).get('fold_evidence',{}).get('evaluation_hash')==expected_hash:
       fold_done+=1; continue
      outcome=run_discovery_candidate_backtest(rows,inst,timeframe,template,params,vs,effective,execution,fingerprint, v2_features=shared_v2_features) if template in (set(V2_TEMPLATES)|set(V21_TEMPLATES)) else run_discovery_candidate_backtest(rows,inst,timeframe,template,params,vs,effective,execution,fingerprint); evidence=outcome['discovery_evidence']; metrics=outcome['metrics']; benchmark=buy_and_hold(rows,vs,effective,execution)
      if evidence['parameter_hash'] != ph or evidence['evaluation_hash'] != expected_hash: raise ValueError('DISCOVERY_IDENTITY_MISMATCH')
      phase5c={'event_study':signal_event_study(outcome,rows,vs,effective)} if template in V21_TEMPLATES else {}
      if template=='RANGE_MEAN_REVERSION_V2_1':
       phase5c['mean_reversion_diagnostics']=mean_reversion_diagnostics(outcome,rows,shared_v2_features,step,vs,effective)
      fold_metrics={**metrics,'cost_attribution':fixed_path_cost_attribution(outcome,execution.initial_capital),'lifecycle_diagnostics':lifecycle_summary(outcome,step),**phase5c,'fold_evidence':{'effective_engine_end_ts':effective,'maximum_candle_timestamp_loaded':max(int(x['ts']) for x in rows),'instrument':inst,'timeframe':timeframe,'candle_count':len(rows),'warmup_candle_count':sum(int(x['ts'])<vs for x in rows),'validation_candle_count':sum(vs<=int(x['ts'])<=effective for x in rows),'first_validation_candle_ts':vs,'last_validation_candle_ts':effective,'dataset_fingerprint':fingerprint,'parameter_hash':evidence['parameter_hash'],'execution_hash':evidence['execution_hash'],'candidate_config_hash':evidence['candidate_config_hash'],'evaluation_hash':evidence['evaluation_hash'],'template_version':evidence['template_version'],'feature_version':evidence['feature_version'],'execution_policy_version':evidence['execution_policy_version'],'execution_engine_version':evidence['execution_engine_version'],'execution_assumptions':evidence['execution'],'signal_count':outcome['signal_count']}}
      benchmark.update({'strategy_minus_benchmark_return':metrics['total_return']-benchmark['total_return'],'strategy_drawdown_minus_benchmark_drawdown':None if metrics.get('maximum_drawdown') is None or benchmark.get('maximum_drawdown') is None else metrics['maximum_drawdown']-benchmark['maximum_drawdown']})
      with self.repository.connect() as c:c.execute("INSERT INTO strategy_discovery_folds(candidate_id,fold_number,train_start_ts,train_end_ts,validation_start_ts,validation_end_ts,metrics,buy_hold_metrics,status,error) VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(candidate_id,fold_number) DO UPDATE SET metrics=excluded.metrics,buy_hold_metrics=excluded.buy_hold_metrics,status=excluded.status,error=NULL",(cid,no,a,b,vs,ve,json.dumps(fold_metrics),json.dumps(benchmark),'COMPLETED'))
      fold_done+=1
     except JobCancelled: raise
     except Exception as error:
      candidate_failed=True; fold_failed+=1
      with self.repository.connect() as c:c.execute("INSERT INTO strategy_discovery_folds(candidate_id,fold_number,train_start_ts,train_end_ts,validation_start_ts,validation_end_ts,metrics,buy_hold_metrics,status,error) VALUES(?,?,?,?,?,?,NULL,NULL,'FAILED',?) ON CONFLICT(candidate_id,fold_number) DO UPDATE SET status='FAILED',error=excluded.error",(cid,no,a,b,vs,ve,type(error).__name__[:80]))
     checkpoint(jid,None,'discovery.evaluating_folds',{'current_candidate':n,'total_candidates':budget,'current_fold':no,'total_folds':len(FOLDS),'candidates_completed':completed,'candidates_failed':failed,'folds_completed':fold_done,'folds_failed':fold_failed})
    checkpoint(jid,None,'discovery.aggregating',{'current_candidate':n,'total_candidates':budget})
    with self.repository.connect() as c: records=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_folds WHERE candidate_id=? ORDER BY fold_number',(cid,))]
    normalized=[]
    for x in records:
     normalized.append({**x,'metrics':json.loads(x['metrics']) if x['metrics'] else {},'buy_hold_metrics':json.loads(x['buy_hold_metrics']) if x['buy_hold_metrics'] else {}})
    data=aggregate(normalized); data.update({'parameter_hash':ph,'execution_hash':execution.execution_hash(),'candidate_config_hash':next((x['metrics'].get('fold_evidence',{}).get('candidate_config_hash') for x in normalized if x['status']=='COMPLETED'),None),'dataset_fingerprint':fingerprint})
    if any(x['metrics'].get('fold_evidence',{}).get('parameter_hash') != ph for x in normalized if x['status']=='COMPLETED'): raise ValueError('DISCOVERY_IDENTITY_MISMATCH')
    status='FAILED' if candidate_failed else 'DEVELOPMENT_CANDIDATE'
    with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_candidates SET status=?,aggregate_metrics=?,score_components=NULL,pareto_rank=NULL,elimination_reasons=NULL,eligibility_status='NOT_SCORED',development_score=NULL,eligible_rank=NULL,scoring_policy_version=NULL,completed_at=?,error=? WHERE id=?",(status,json.dumps(data),datetime.now(timezone.utc).isoformat(), 'FOLD_EVALUATION_FAILED' if candidate_failed else None,cid))
    if candidate_failed: failed+=1
    else: completed+=1
   if not completed: raise RuntimeError('ALL_CANDIDATES_FAILED')
   checkpoint(jid,91,'discovery.evaluating_eligibility',{})
   with self.repository.connect() as c: candidates=[dict(x) for x in c.execute('SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY candidate_number',(rid,))]
   eligible=[]
   for item in candidates:
    item['aggregate']=json.loads(item['aggregate_metrics']) if item['aggregate_metrics'] else {}
    verdict=evaluate_eligibility(item['aggregate'],timeframe,item['status']); item['reasons']=verdict['reasons']; item['eligibility_status']='ELIGIBLE' if verdict['eligible'] else ('NOT_SCORED' if item['status']!='DEVELOPMENT_CANDIDATE' else 'REJECTED')
    if verdict['eligible']: eligible.append(item)
   checkpoint(jid,93,'discovery.scoring_candidates',{})
   for item in eligible:
    item['development_score'],item['score_components']=calculate_score(item['aggregate'],item['complexity'],timeframe)
   checkpoint(jid,95,'discovery.assigning_pareto_fronts',{})
   pareto_ranks=assign_pareto_fronts(eligible); checkpoint(jid,97,'discovery.ranking_candidates',{}); eligible_ranks=rank_eligible_candidates(eligible,pareto_ranks)
   for item in eligible:
    identity=(item['parameter_hash'],item['candidate_number'])
    item['pareto_rank']=pareto_ranks[identity]
    item['eligible_rank']=eligible_ranks[identity]
   ranked=sorted(eligible,key=lambda item:item['eligible_rank'])
   # This checkpoint is deliberately outside the one persistence transaction.
   checkpoint(jid,98,'discovery.persisting_development_ranking',{})
   # One transaction clears stale values and replaces every interpretation only
   # after cancellation checkpoints have completed.
   with self.repository.connect() as c:
    for item in candidates:
     if item['eligibility_status']=='ELIGIBLE':
      components={**item['score_components'],'pareto':{'version':DISCOVERY_PARETO_VERSION,'objectives':{k:item['aggregate'][k] for k in ('median_excess_return','worst_excess_return','worst_maximum_drawdown','validation_return_standard_deviation')}}}
      c.execute("UPDATE strategy_discovery_candidates SET eligibility_status='ELIGIBLE',development_score=?,eligible_rank=?,pareto_rank=?,score_components=?,elimination_reasons='[]',scoring_policy_version=? WHERE id=?",(item['development_score'],item['eligible_rank'],item['pareto_rank'],json.dumps(components),DISCOVERY_SCORING_VERSION,item['id']))
     else:
      c.execute("UPDATE strategy_discovery_candidates SET eligibility_status=?,development_score=NULL,eligible_rank=NULL,pareto_rank=NULL,score_components=NULL,elimination_reasons=?,scoring_policy_version=? WHERE id=?",(item['eligibility_status'],json.dumps(item['reasons']),DISCOVERY_SCORING_VERSION,item['id']))
   top=ranked[0] if ranked else None; warnings=['Ranking uses development folds only.','Primary holdout was not accessed.','Final OOT was not accessed.','A high score is not proof of future profitability.','No strategy was activated.']
   if not top:warnings.append('NO_DEVELOPMENT_CANDIDATE_PASSED_ELIGIBILITY')
   result={'requested_candidates':budget,'generated_candidates':budget,'evaluated_candidates':completed,'failed_candidates':failed,'completed_folds':fold_done,'failed_folds':fold_failed,'instrument':inst,'timeframe':timeframe,'dataset_fingerprint':fingerprint,'execution_assumptions':execution.__dict__,'aggregation_version':DISCOVERY_AGGREGATION_VERSION,'eligibility_version':DISCOVERY_ELIGIBILITY_VERSION,'scoring_version':DISCOVERY_SCORING_VERSION,'pareto_version':DISCOVERY_PARETO_VERSION,'development_candidate_count':completed,'eligible_candidate_count':len(eligible),'rejected_candidate_count':sum(x['eligibility_status']=='REJECTED' for x in candidates),'failed_candidate_count':sum(x['status']=='FAILED' for x in candidates),'pareto_front_one_count':sum(x.get('pareto_rank')==1 for x in eligible),'top_development_candidate_id':top['id'] if top else None,'top_development_candidate_number':top['candidate_number'] if top else None,'top_development_score':top['development_score'] if top else None,'top_development_parameter_hash':top['parameter_hash'] if top else None,'warnings':warnings,**sampling}
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='COMPLETED',progress=?,result=?,completed_at=? WHERE id=?",(json.dumps(result),json.dumps(result),datetime.now(timezone.utc).isoformat(),rid))
   checkpoint(jid,99,'discovery.completed',result); return {'discovery_run_id':rid}
  except JobCancelled:
   now=datetime.now(timezone.utc).isoformat()
   with self.repository.connect() as c:
    c.execute("UPDATE strategy_discovery_candidates SET status='CANCELLED',completed_at=? WHERE discovery_run_id=? AND status='EVALUATING'",(now,rid))
    c.execute("UPDATE strategy_discovery_runs SET status='CANCELLED',error='DISCOVERY_CANCELLED',completed_at=? WHERE id=? AND status!='COMPLETED'",(now,rid))
   raise
  except Exception as error:
   now=datetime.now(timezone.utc).isoformat()
   with self.repository.connect() as c:
    c.execute("UPDATE strategy_discovery_runs SET status='FAILED',error=?,completed_at=? WHERE id=? AND status!='COMPLETED'",(self._error_code(error),now,rid))
   raise
