"""Breadth-first local cycle: fixed ideas, no holdout and no score ranking."""
from __future__ import annotations
import csv,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dashboard.backtest_engine import run_execution_backtest
from dashboard.discovery_features import build_features
from dashboard.strategy_rules import StrategyParameters
from research_engine.budget import ResearchBudget,ResearchBudgetManager
from research_engine.ledger import ExperimentLedger
from research_engine.scheduler import ResearchEngine
from research_engine.schema import Candidate,Hypothesis,candidate_id
def ts(x):return int(datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp())
def main():
 cycle='research_cycle_v2_breadth_final';out=Path('.research')/cycle;out.mkdir(parents=True,exist_ok=True);ledger=ExperimentLedger(out/'ledger.db');engine=ResearchEngine(ledger);budget=ResearchBudgetManager(ledger,ResearchBudget(max_hypotheses=6,max_candidates=6,max_parameter_trials=6,max_holdout_access=1))
 db=sqlite3.connect('file:'+str((ROOT/'data/production-export/2026-09-29/paper_trades.db').resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;start,end=ts('2024-01-01'),ts('2025-01-01');rows=[dict(x) for x in db.execute('select ts,open,high,low,close,volume,confirmed from historical_candles where instrument=? and timeframe=? and confirmed=1 and ts>=? and ts<? order by ts',('BTC-USDT','15m',start,end))];features=build_features(rows,{'ma_periods':[20,60,200],'vpva_lookback':100,'vpva_bins':24})
 families={'VOLATILITY_EXPANSION':('Volatility',('OHLCV','VOLATILITY','BOLLINGER')),'VOLUME_CONFIRMED_TREND':('Volume Trend',('OHLCV','TREND','VOLUME')),'TIME_SERIES_MOMENTUM':('Momentum',('OHLCV','MOMENTUM')),'BREAKOUT':('Breakout',('OHLCV','VOLATILITY')),'REGIME_FILTER':('Regime Filter',('OHLCV','TREND','BOLLINGER')),'CROSS_ASSET_RELATIVE_STRENGTH':('Relative Strength',('OHLCV','RELATIVE_STRENGTH'))};dataset='local-confirmed-ohlcv-2026-09-29';execution={'trading_fee':.0005,'slippage':.0003,'risk_per_trade':.01,'initial_capital':10000.,'cooldown_bars':16};summary={'cycle':cycle,'families':{},'holdout_access_count':0}
 for template,(family,req) in families.items():
  h=Hypothesis('hyp:'+template,cycle,family,template,'fixed breadth hypothesis',req,('BTC-USDT',),('15m',),{'variants':6},template,'fixed-r','next-open',req,dataset,'v2');budget.consume(cycle,family,'hypotheses');s0=engine.stage0(h)
  if template=='CROSS_ASSET_RELATIVE_STRENGTH':ledger.append_experiment({'research_run_id':cycle,'research_cycle_id':cycle,'hypothesis_id':h.hypothesis_id,'family':family,'stage':'STAGE_0','status':'UNAVAILABLE','rejection_reason':'CAUSAL_ALIGNMENT_ADAPTER_UNAVAILABLE','dataset_version':dataset});summary['families'][family]={'candidates':0,'stage1_passed':0};continue
  passed=count=0
  for n,threshold in enumerate((.8,.9,1.,1.1,1.2,1.3),1):
   budget.consume(cycle,family,'candidates');p={'threshold':threshold};cid=candidate_id(hypothesis=h,parameters=p,feature_set=req,asset='BTC-USDT',timeframe='15m',execution_assumptions=execution);cand=Candidate(cid,h.hypothesis_id,family,p,req,'BTC-USDT','15m',dataset,'v2',execution)
   def provider(candle,i,template=template,threshold=threshold):
    f=features[i];close=float(candle['close']);warm=bool(f.get('warm'));vr=f.get('volume_ratio') or 0; atr=f.get('atr');side='WAIT'; prior=rows[i-20] if i>=20 else None
    bull=f.get('sma_60') and f.get('sma_200') and f['sma_60']>f['sma_200'];bear=f.get('sma_60') and f.get('sma_200') and f['sma_60']<f['sma_200']
    if template=='VOLATILITY_EXPANSION' and f.get('bb_upper'): side='LONG' if f.get('bb_width',0)>=.01*threshold and close>f['bb_upper'] else 'SHORT' if close<f['bb_lower'] else 'WAIT'
    elif template=='VOLUME_CONFIRMED_TREND': side='LONG' if bull and vr>=threshold else 'SHORT' if bear and vr>=threshold else 'WAIT'
    elif template=='TIME_SERIES_MOMENTUM' and prior: side='LONG' if close/float(prior['close'])-1>=.002*threshold else 'SHORT' if close/float(prior['close'])-1<=-.002*threshold else 'WAIT'
    elif template=='BREAKOUT': side='LONG' if f.get('recent_high') and close>f['recent_high'] else 'SHORT' if f.get('recent_low') and close<f['recent_low'] else 'WAIT'
    elif template=='REGIME_FILTER': side='LONG' if bull and f.get('bb_pct',.5)<.5 else 'SHORT' if bear and f.get('bb_pct',.5)>.5 else 'WAIT'
    if side!='WAIT' and (not atr or float(atr)<=0): side='WAIT'
    return {'action':side,'warmed':warm,'atr':atr,'score':0.,'signal_ts':int(candle['ts']),'signal_id':f'{template}:{threshold}:{candle["ts"]}','strategy_version':template,'config_hash':str(threshold),'target_r':2.0}
   def runner(_c,stage):
    if stage!=1:return {}
    r=run_execution_backtest(rows,'BTC-USDT','15m',StrategyParameters(**execution),start,end-900,signal_provider=provider,include_details=True);m=r['metrics'];gross=sum(abs(float(t['pnl'])) for t in r['trades']);m['cost_ratio']=m['fees_paid']/gross if gross else None;return m
   res=engine.run_candidate(cand,h,runner);count+=1;passed+=res[-1].stage>1
  summary['families'][family]={'candidates':count,'stage1_passed':passed,'budget':budget.usage(cycle,family)}
 summary.update(ledger.summary());events=ledger.events();summary['rejection_reasons']={};
 for e in events:
  if e['rejection_reason']:summary['rejection_reasons'][e['rejection_reason']]=summary['rejection_reasons'].get(e['rejection_reason'],0)+1
 (out/'research_run_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
 with (out/'experiment_ledger.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['candidate_id','family','stage','status','rejection_reason','created_at']);w.writeheader();w.writerows([{k:e.get(k) for k in w.fieldnames} for e in events])
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
