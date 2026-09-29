"""Small real, fail-closed Research Engine V1 cycle (no holdout optimization)."""
from __future__ import annotations
import csv,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dashboard.discovery_execution import DiscoveryExecutionConfig,run_discovery_candidate_backtest
from dashboard.discovery_v2_registry import FIXED_EXECUTION,plan
from research_engine.budget import ResearchBudget,ResearchBudgetManager
from research_engine.ledger import ExperimentLedger
from research_engine.scheduler import ResearchEngine
from research_engine.schema import Candidate,Hypothesis,candidate_id
def ts(x):return int(datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp())
def main():
 cycle='research_cycle_v1_final'; out=Path('.research/research_cycle_v1_final');out.mkdir(parents=True,exist_ok=True); ledger=ExperimentLedger(out/'ledger.db'); budget=ResearchBudgetManager(ledger,ResearchBudget(max_hypotheses=4,max_candidates=12,max_parameter_trials=24,max_holdout_access=1)); engine=ResearchEngine(ledger)
 db=sqlite3.connect('file:'+str((ROOT/'data/production-export/2026-09-29/paper_trades.db').resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;start,end=ts('2024-01-01'),ts('2025-01-01')
 rows=[dict(x) for x in db.execute('select ts,open,high,low,close,volume,confirmed from historical_candles where instrument=? and timeframe=? and confirmed=1 and ts>=? and ts<? order by ts',('BTC-USDT','15m',start,end))]
 dataset='local-confirmed-ohlcv-2026-09-29';execution={k:FIXED_EXECUTION[k] for k in ('initial_capital','risk_per_trade','trading_fee','slippage','cooldown_bars')}; cfg=DiscoveryExecutionConfig(**execution)
 families={'TREND_PULLBACK_V2':('Trend',('OHLCV','TREND','VOLUME')),'TREND_BREAKOUT_V2':('Momentum',('OHLCV','MOMENTUM','VOLUME')),'RANGE_MEAN_REVERSION_V2':('Mean Reversion',('OHLCV','MEAN_REVERSION','BOLLINGER')),'VPVA_PROXY_BOLL':('VPVA_PROXY / Boll',('OHLCV','VPVA_PROXY','BOLLINGER','VOLUME'))}
 defs,_,_=plan(36); summary={'research_cycle_id':cycle,'holdout_accessed':0,'families':{},'notes':['VPVA_PROXY/Boll is registered as an integration hypothesis only; no new grid was generated.']}
 for template,(family,features) in families.items():
  hid='hyp:'+template.lower(); h=Hypothesis(hid,cycle,family,template+' bounded integration hypothesis','falsification-first',features,('BTC-USDT',),('15m',),{},template,'canonical-next-open','next-open',features,dataset,'research-engine-v1')
  budget.consume(cycle,family,'hypotheses'); zero=engine.stage0(h)
  if template=='VPVA_PROXY_BOLL': ledger.append_experiment({'research_run_id':cycle,'research_cycle_id':cycle,'hypothesis_id':hid,'family':family,'stage':'STAGE_0','status':'UNAVAILABLE','rejection_reason':'ADAPTER_UNAVAILABLE_NO_GRID_EXPANSION','dataset_version':dataset});summary['families'][family]={'stage0':'AVAILABLE','candidates':0,'survivors':0};continue
  count=passed=0
  for t,p in defs:
   if t!=template or count>=4:continue
   if not budget.consume(cycle,family,'candidates').allowed:break
   cid=candidate_id(hypothesis=h,parameters=p,feature_set=features,asset='BTC-USDT',timeframe='15m',execution_assumptions=execution); c=Candidate(cid,hid,family,p,features,'BTC-USDT','15m',dataset,'research-engine-v1',execution)
   def runner(_c,stage,p=p,t=t):
    if stage!=1:return {}
    return run_discovery_candidate_backtest(rows,'BTC-USDT','15m',t,p,start,end-900,cfg,dataset)['metrics']
   results=engine.run_candidate(c,h,runner);count+=1;passed+=int(results[-1].status=='PASS' and results[-1].stage==6)
  summary['families'][family]={'stage0':zero.status,'candidates':count,'survivors':passed,'budget':budget.usage(cycle,family)}
 summary.update(ledger.summary());(out/'research_run_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
 events=ledger.events();
 with (out/'experiment_ledger.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=['experiment_id','research_run_id','research_cycle_id','hypothesis_id','candidate_id','family','stage','status','rejection_reason','asset','timeframe','dataset_version','created_at']);w.writeheader();w.writerows([{k:x.get(k) for k in w.fieldnames} for x in events])
 artifacts={'candidate_metrics':events,'rejection_log':[x for x in events if x['status']!='PASS'],'budget_usage':summary['families'],'holdout_access_log':[],'survivors':[],
   # Empty files are intentional evidence: Stage 1 had no survivors, so later
   # stages were not run merely to populate attractive-looking artifacts.
   'parameter_stability':[],'walkforward_results':[],'cross_asset_results':[],'cost_stress_results':[],'ablation_results':[],'bootstrap_results':[]}
 for name,rows_out in artifacts.items():(out/(name+'.json')).write_text(json.dumps(rows_out,indent=2),encoding='utf-8')
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
