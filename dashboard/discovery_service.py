"""Bounded offline Strategy Discovery Lab; it never activates or trades a candidate."""
from __future__ import annotations
import json, random
from datetime import datetime, timezone
from .dataset_service import DiscoveryDatasetService
from .discovery_features import FEATURE_VERSION, build_features
from .discovery_templates import TEMPLATES,TEMPLATE_VERSION,parameter_hash,signal,validate
ENGINE_VERSION='canonical-next-bar-open/discovery-adapter-v1'; POLICY_VERSION='discovery-policy-v1'
def ts(y,m,d): return int(datetime(y,m,d,tzinfo=timezone.utc).timestamp())
FOLDS=[(ts(2024,1,1),ts(2024,7,1),ts(2024,7,1),ts(2024,9,1)),(ts(2024,3,1),ts(2024,9,1),ts(2024,9,1),ts(2024,11,1)),(ts(2024,5,1),ts(2024,11,1),ts(2024,11,1),ts(2025,1,1)),(ts(2024,7,1),ts(2025,1,1),ts(2025,1,1),ts(2025,3,1)),(ts(2024,9,1),ts(2025,3,1),ts(2025,3,1),ts(2025,5,1))]
class DiscoveryService:
 def __init__(self,repo,jobs): self.repository=repo;self.jobs=jobs;self.datasets=DiscoveryDatasetService(repo);jobs.register('DISCOVERY_DATASET',self._dataset_job);jobs.register('STRATEGY_DISCOVERY',self._run_job)
 def _dataset_job(self,jid,p,checkpoint): return self.datasets.prepare(p,lambda _,pct,msg,args:checkpoint(jid,pct,msg,args),lambda:self.jobs.checkpoint(jid))
 def prepare_dataset(self,p,client='public'):
  job=self.jobs.enqueue('DISCOVERY_DATASET',p,client,priority=105);return {'job_id':job['id'],'status':job['status']}
 def start(self,p,client='public'):
  did=int(p['dataset_id']); ds=self.repository.discovery_dataset(did)
  if not ds or ds['status']!='COMPLETE':raise ValueError('A complete fixed dataset is required.')
  budget=int(p.get('trial_budget',100));
  if not 1<=budget<=500:raise ValueError('Trial budget must be 1..500.')
  mode=p.get('mode','PRICE_ONLY'); templates=p.get('templates',list(TEMPLATES));
  if mode!='PRICE_ONLY': raise ValueError('FLOW_OVERLAY is unavailable until verified public coverage is persisted.')
  now=datetime.now(timezone.utc).isoformat(); seed=int(p.get('seed',20260721));
  with self.repository.connect() as c:
   cur=c.execute("INSERT INTO strategy_discovery_runs(dataset_id,status,request,search_policy,sampler,seed,maximum_trials,templates,feature_version,engine_version,scoring_version,progress,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(did,'QUEUED',json.dumps(p),'{}','DETERMINISTIC_RANDOM',seed,budget,json.dumps(templates),FEATURE_VERSION,ENGINE_VERSION,POLICY_VERSION,'{}',now,now));rid=cur.lastrowid
  try:j=self.jobs.enqueue('STRATEGY_DISCOVERY',{**p,'discovery_run_id':rid},client,priority=115)
  except Exception:
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='FAILED',error=? WHERE id=?",('Queue enqueue failed',rid));raise
  return {'id':rid,'job_id':j['id'],'status':'QUEUED'}
 def _sample(self,rng,t):
  return {'fast_period':rng.choice([6,10,20,30,60]),'slow_period':rng.choice([60,100,150,200]),'fast_ma_type':rng.choice(['SMA','EMA']),'atr_period':rng.choice([7,10,14,20,28]),'stop_atr':round(rng.uniform(.6,2.5),2),'risk_reward':round(rng.uniform(1,4),2),'cooldown_bars':rng.randint(4,48),'volume_enabled':rng.choice([True,False]),'minimum_volume_ratio':round(rng.uniform(.7,2),2),'rsi_lower':rng.randint(20,49),'rsi_upper':rng.randint(51,80)}
 def _run_job(self,jid,p,checkpoint):
  rid=int(p['discovery_run_id']);rng=random.Random(int(p.get('seed',20260721)));budget=int(p.get('trial_budget',100)); inst=p.get('instrument','BTC-USDT');
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='RUNNING' WHERE id=?",(rid,))
  rows=self.repository.candles(inst,'15m',ts(2024,1,1),ts(2025,5,1)-1); feats=build_features(rows); templates=p.get('templates',list(TEMPLATES))
  for n in range(1,budget+1):
   checkpoint(jid,5+int(n*85/budget),'discovery.running_candidate',{'processed':n-1,'total':budget});template=templates[(n-1)%len(templates)];params=self._sample(rng,template)
   if params['fast_period']>=params['slow_period']:params['fast_period']=6
   cfg=validate({'template':template,'parameters':params}); ph=parameter_hash(cfg); complexity=sum(bool(x) for x in (params['volume_enabled'],))+6; now=datetime.now(timezone.utc).isoformat()
   with self.repository.connect() as c:cid=c.execute("INSERT INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(rid,n,template,TEMPLATE_VERSION[template],json.dumps(params),ph,'{}',complexity,'DEVELOPMENT_CANDIDATE',now)).lastrowid
   returns=[]
   for no,(_,_,vs,ve) in enumerate(FOLDS,1):
    pnl=0.; trades=0
    for i in range(len(rows)-1):
     if vs<=int(rows[i]['ts'])<ve and signal(template,params,rows[i],feats[i])!='WAIT': pnl+=(float(rows[i+1]['close'])-float(rows[i+1]['open']))/float(rows[i+1]['open']);trades+=1
    metrics={'total_return':pnl,'total_trades':trades,'profit_factor':None,'sharpe_ratio':None,'maximum_drawdown':None};returns.append(pnl)
    with self.repository.connect() as c:c.execute("INSERT INTO strategy_discovery_folds(candidate_id,fold_number,train_start_ts,train_end_ts,validation_start_ts,validation_end_ts,metrics,buy_hold_metrics,status) VALUES(?,?,?,?,?,?,?,?,?)",(cid,no,FOLDS[no-1][0],FOLDS[no-1][1],vs,ve,json.dumps(metrics),'{}','COMPLETED'))
   reasons=[] if min(returns)>=-.2 else ['worst_fold_threshold'];score=sum(returns)/len(returns)-.001*complexity
   with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_candidates SET status=?,aggregate_metrics=?,score_components=?,elimination_reasons=?,completed_at=? WHERE id=?",('ELIMINATED' if reasons else 'DEVELOPMENT_CANDIDATE',json.dumps({'median_validation_return':sorted(returns)[len(returns)//2],'profitable_fold_ratio':sum(x>0 for x in returns)/len(returns)}),json.dumps({'score':score,'complexity_penalty':.001*complexity}),json.dumps(reasons),now,cid))
  with self.repository.connect() as c:c.execute("UPDATE strategy_discovery_runs SET status='COMPLETED',progress=?,completed_at=? WHERE id=?",(json.dumps({'processed':budget}),datetime.now(timezone.utc).isoformat(),rid))
  return {'discovery_run_id':rid}
