"""One bounded (100-candidate) structured-combination cycle; development only."""
from __future__ import annotations
import json,sqlite3,sys,statistics
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dashboard.backtest_engine import run_execution_backtest
from dashboard.discovery_features import build_features
from dashboard.strategy_rules import StrategyParameters
from research_engine.indicators import combinations
from research_engine.ledger import ExperimentLedger
from research_engine.scheduler import ResearchEngine
from research_engine.schema import Candidate, Hypothesis, candidate_id
def ts(x):return int(datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp())
def main():
 out=Path('.research/research_cycle_v2_combinations_final');out.mkdir(parents=True,exist_ok=True);db=sqlite3.connect('file:'+str((ROOT/'data/production-export/2026-09-29/paper_trades.db').resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;s,e=ts('2024-01-01'),ts('2025-01-01');rows=[dict(x) for x in db.execute('select ts,open,high,low,close,volume,confirmed from historical_candles where instrument=? and timeframe=? and confirmed=1 and ts>=? and ts<? order by ts',('BTC-USDT','15m',s,e))];fs=build_features(rows,{'ma_periods':[20,60,200],'vpva_lookback':100});variants=(.8,.9,1.,1.1,1.2);results=[]
 for recipe in combinations():
  if recipe['confirmation']=='BTC_REGIME':
   for v in variants:results.append({'family':recipe['family'],'recipe':recipe,'variant':{'mode':recipe['variant'],'threshold':v},'status':'UNAVAILABLE','reasons':['CAUSAL_ALIGNMENT_COVERAGE_UNAVAILABLE']})
   continue
  for threshold in variants:
   v=threshold*(.9 if recipe['variant']=='FAST' else 1.1)
   def provider(candle,i,r=recipe,v=v):
    f=fs[i];close=float(candle['close']);atr=f.get('atr');vr=f.get('volume_ratio') or 0;bull=bool(f.get('sma_60') and f.get('sma_200') and f['sma_60']>f['sma_200']);bear=bool(f.get('sma_60') and f.get('sma_200') and f['sma_60']<f['sma_200']);prior=rows[i-20] if i>=20 else None;side='WAIT';name=r['family']
    momentum=bool(prior and abs(close/float(prior['close'])-1)>=.002*v);breakout=bool(f.get('recent_high') and (close>f['recent_high'] or close<f.get('recent_low',-1)))
    if 'MEANREV' in name:side='LONG' if f.get('bb_lower') and float(candle['low'])<=f['bb_lower'] and f.get('bb_pct',.5)<.5 else 'SHORT' if f.get('bb_upper') and float(candle['high'])>=f['bb_upper'] and f.get('bb_pct',.5)>.5 else 'WAIT'
    elif 'BREAKOUT' in name:side='LONG' if breakout and close>f.get('recent_high',close+1) and vr>=v else 'SHORT' if breakout and close<f.get('recent_low',close-1) and vr>=v else 'WAIT'
    elif 'MOMENTUM' in name:side='LONG' if momentum and close>float(prior['close']) and (bull or vr>=v) else 'SHORT' if momentum and close<float(prior['close']) and (bear or vr>=v) else 'WAIT'
    else:side='LONG' if bull and ((vr>=v) or (f.get('bb_width',0)>=.01*v)) else 'SHORT' if bear and ((vr>=v) or (f.get('bb_width',0)>=.01*v)) else 'WAIT'
    if side!='WAIT' and not atr:side='WAIT'
    return {'action':side,'warmed':bool(f.get('warm')),'atr':atr,'score':0.,'signal_ts':int(candle['ts']),'signal_id':f'{name}:{v}:{candle["ts"]}','strategy_version':name,'config_hash':str(v),'target_r':2.0}
   r=run_execution_backtest(rows,'BTC-USDT','15m',StrategyParameters(initial_capital=10000,risk_per_trade=.01,trading_fee=.0005,slippage=.0003,cooldown_bars=16),s,e-900,signal_provider=provider,include_details=True);m=r['metrics'];tr=r['trades'];gross=sum(float(x['pnl'])+float(x['fees']) for x in tr);fees=m['fees_paid'];days=(e-s)/86400;reasons=[]
   if gross<=0:reasons.append('NO_RAW_EDGE')
   if m['total_return']<0:reasons.append('NEGATIVE_RETURN')
   if (m.get('sharpe_ratio') or 0)<0:reasons.append('NEGATIVE_SHARPE')
   if m['maximum_drawdown']>25:reasons.append('MAXIMUM_DRAWDOWN')
   if len(tr)/days>2:reasons.append('EXCESSIVE_TURNOVER')
   if gross>0 and m['net_profit']<0:reasons.append('EXECUTION_FRAGILE')
   results.append({'family':recipe['family'],'recipe':recipe,'variant':{'mode':recipe['variant'],'threshold':threshold},'status':'FAIL' if reasons else 'PASS','reasons':reasons,'metrics':{**m,'gross_pnl':gross,'gross_return':gross/10000*100,'net_return':m['total_return'],'fee_drag':fees,'slippage_drag':None,'trades_per_day':len(tr)/days,'median_holding_seconds':statistics.median([x['holding_seconds'] for x in tr]) if tr else None,'turnover':sum(float(x['entry_price'])*float(x['position_size']) for x in tr),'cost_ratio':fees/abs(gross) if gross else None}})
 # Route every eligible recipe through the thin staged scheduler.  The direct
 # execution call above is deliberately only a Stage-1 runner; no holdout or
 # legacy optimizer is reachable from this script.
 ledger=ExperimentLedger(out/'experiment_ledger.sqlite');engine=ResearchEngine(ledger)
 for item in results:
  recipe=item['recipe']; required=('OHLCV','TREND','MOMENTUM','VOLUME','VOLATILITY')
  h=Hypothesis('hyp:'+recipe['family']+':'+recipe['variant'],'research_cycle_v2_combinations_final',recipe['family'],recipe['family']+' '+recipe['variant']+' structured combination','Explicit base signal plus confirmation/regime recipe',required,('BTC-USDT',),('15m',),{'threshold':[.8,.9,1.,1.1,1.2],'mode':recipe['variant']},recipe['family']+':'+recipe['variant'],'canonical-next-open','next_open_fee_slippage',('OHLCV',),'confirmed-ohlcv-2024-v1','research-engine-v2')
  c=Candidate(candidate_id(hypothesis=h,parameters={'variant':item['variant']},feature_set=tuple(x for x in (recipe['base_signal'],recipe['confirmation'],recipe['regime_filter']) if x),asset='BTC-USDT',timeframe='15m',execution_assumptions={'model':'next_open','fee':.0005,'slippage':.0003}),h.hypothesis_id,h.family,{'variant':item['variant']},tuple(x for x in (recipe['base_signal'],recipe['confirmation'],recipe['regime_filter']) if x),'BTC-USDT','15m',h.dataset_version,h.code_version,{'model':'next_open','fee':.0005,'slippage':.0003})
  item['hypothesis_id'],item['candidate_id']=h.hypothesis_id,c.candidate_id
  if item['status']=='UNAVAILABLE':
   ledger.append_experiment({'research_run_id':h.research_cycle_id,'research_cycle_id':h.research_cycle_id,'hypothesis_id':h.hypothesis_id,'candidate_id':c.candidate_id,'family':c.family,'asset':c.asset,'timeframe':c.timeframe,'dataset_version':c.dataset_version,'code_version':c.code_version,'stage':'STAGE_0','status':'UNAVAILABLE','rejection_reason':item['reasons'][0],'parameters':c.parameters,'feature_set':c.feature_set,'execution_assumptions':c.execution_assumptions})
   continue
  zero=engine.evaluate(c,h,0,lambda _c,_s:{})
  one=engine.evaluate(c,h,1,lambda _c,_s:item['metrics'])
  item['scheduler_stage0'],item['scheduler_stage1']=zero.status,one.status
  if one.status!='PASS': item['status']='FAIL'; item['reasons']=list(dict.fromkeys(item['reasons']+([one.reason] if one.reason else [])))
 stage1=sum(x.get('scheduler_stage1')=='PASS' for x in results)
 summary={'cycle':'research_cycle_v2_combinations_final','hypotheses_tested':len(combinations()),'total_candidates':len(results),'stage_pass_counts':{'stage0':sum(x.get('scheduler_stage0')=='PASS' for x in results),'stage1':stage1,'stage2':0,'stage3':0,'stage4':0,'stage5':0},'holdout_access_count':0,'survivors':[],'results':results};(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in summary.items() if k!='results'},indent=2))
if __name__=='__main__':main()
