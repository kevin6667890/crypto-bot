"""Bounded cross-asset information cycle; no single-asset indicator search."""
from __future__ import annotations
import json, sqlite3, statistics, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from dashboard.backtest_engine import run_execution_backtest
from dashboard.strategy_rules import StrategyParameters
from research_engine.budget import ResearchBudget, ResearchBudgetManager
from research_engine.cross_asset import align_confirmed, audit_confirmed_alignment
from research_engine.ledger import ExperimentLedger
from research_engine.scheduler import ResearchEngine
from research_engine.schema import Candidate, Hypothesis, candidate_id

def stamp(value): return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
def mean(values): return sum(values)/len(values) if values else 0.0

def main():
    out=Path('.research/research_cycle_v3_new_information'); out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect('file:'+str((ROOT/'data/production-export/2026-09-29/paper_trades.db').resolve())+'?mode=ro',uri=True); con.row_factory=sqlite3.Row
    start,end=stamp('2024-01-01'),stamp('2025-01-01')
    def candles(instrument): return [dict(r) for r in con.execute("select ts,open,high,low,close,volume,confirmed from historical_candles where instrument=? and timeframe='15m' and confirmed=1 and ts>=? and ts<? order by ts",(instrument,start,end))]
    series={name:candles(name) for name in ('BTC-USDT','ETH-USDT','SOL-USDT')}
    audits={asset:audit_confirmed_alignment(rows,series['BTC-USDT']).serialize() for asset,rows in series.items() if asset!='BTC-USDT'}
    if not all(a['status']=='COMPLETE' for a in audits.values()): raise RuntimeError('cross-asset coverage audit failed; no cycle permitted')
    ledger=ExperimentLedger(out/'experiment_ledger.sqlite'); engine=ResearchEngine(ledger); budget=ResearchBudgetManager(ledger,ResearchBudget(max_hypotheses=5,max_candidates=5,max_parameter_trials=5,max_holdout_access=1))
    recipes=(
      ('RELATIVE_STRENGTH','asset return minus BTC return predicts continuation'),
      ('ASSET_BTC_MOMENTUM','alt momentum confirmed by BTC direction'),
      ('LEADER_LAGGARD','cross-sectional leadership versus peer altcoin'),
      ('BTC_REGIME_CONTEXT','alt momentum only under BTC trend regime'),
      ('CROSS_SECTIONAL_RANK','trade an alt only when its causal return rank is extreme'),
    )
    thresholds=(.001,.002,.003,.004,.005); results=[]
    for asset in ('ETH-USDT','SOL-USDT'):
      primary=series[asset]; btc=series['BTC-USDT']; peer=series['SOL-USDT' if asset=='ETH-USDT' else 'ETH-USDT']
      assert len(align_confirmed(primary,btc))==len(primary)==len(align_confirmed(primary,peer))
      for family,intuition in recipes:
       budget.consume('research_cycle_v3_new_information',family+':'+asset,'hypotheses')
       h=Hypothesis('hyp:v3:'+family+':'+asset,'research_cycle_v3_new_information',family,family+' '+asset,intuition,('OHLCV','RELATIVE_STRENGTH'),(asset,'BTC-USDT'),('15m',),{'thresholds':thresholds},family,'canonical-next-open','next_open_fee_slippage',('OHLCV',),'confirmed-ohlcv-2024-v1','research-engine-v3')
       for threshold in thresholds:
        if not budget.consume(h.research_cycle_id,family+':'+asset,'candidates').allowed: continue
        params={'threshold':threshold,'lookback_bars':16}; features=('RELATIVE_STRENGTH','BTC_REGIME')
        c=Candidate(candidate_id(hypothesis=h,parameters=params,feature_set=features,asset=asset,timeframe='15m',execution_assumptions={'model':'next_open','fee':.0005,'slippage':.0003}),h.hypothesis_id,family,params,features,asset,'15m',h.dataset_version,h.code_version,{'model':'next_open','fee':.0005,'slippage':.0003})
        def provider(candle,i):
          if i<200: return {'action':'WAIT','warmed':False,'atr':None,'signal_ts':int(candle['ts'])}
          own=float(primary[i]['close'])/float(primary[i-16]['close'])-1; br=float(btc[i]['close'])/float(btc[i-16]['close'])-1; pr=float(peer[i]['close'])/float(peer[i-16]['close'])-1
          btc_fast=mean([float(x['close']) for x in btc[i-20:i]]); btc_slow=mean([float(x['close']) for x in btc[i-80:i]]); action='WAIT'
          if family=='RELATIVE_STRENGTH': action='LONG' if own-br>threshold else 'SHORT' if own-br<-threshold else 'WAIT'
          elif family=='ASSET_BTC_MOMENTUM': action='LONG' if own>threshold and br>0 else 'SHORT' if own<-threshold and br<0 else 'WAIT'
          elif family=='LEADER_LAGGARD': action='LONG' if own-pr>threshold else 'SHORT' if own-pr<-threshold else 'WAIT'
          elif family=='BTC_REGIME_CONTEXT': action='LONG' if own>threshold and btc_fast>btc_slow else 'SHORT' if own<-threshold and btc_fast<btc_slow else 'WAIT'
          elif family=='CROSS_SECTIONAL_RANK': action='LONG' if own>max(br,pr)+threshold else 'SHORT' if own<min(br,pr)-threshold else 'WAIT'
          return {'action':action,'warmed':True,'atr':abs(float(candle['close'])-float(primary[i-1]['close'])) or 1e-9,'score':0.0,'signal_ts':int(candle['ts']),'signal_id':f'{family}:{asset}:{threshold}:{candle["ts"]}','strategy_version':family,'config_hash':str(threshold),'target_r':2.0}
        run=run_execution_backtest(primary,asset,'15m',StrategyParameters(initial_capital=10000,risk_per_trade=.01,trading_fee=.0005,slippage=.0003,cooldown_bars=16),start,end-900,signal_provider=provider,include_details=True)
        m,tr=run['metrics'],run['trades']; gross=sum(float(t['pnl'])+float(t['fees']) for t in tr); reasons=[]
        if gross<=0: reasons.append('NO_RAW_EDGE')
        if m['total_return']<0: reasons.append('NEGATIVE_RETURN')
        if (m.get('sharpe_ratio') or 0)<0: reasons.append('NEGATIVE_SHARPE')
        if m['maximum_drawdown']>25: reasons.append('MAXIMUM_DRAWDOWN')
        if len(tr)/366>2: reasons.append('EXCESSIVE_TURNOVER')
        if gross>0 and m['net_profit']<0: reasons.append('EXECUTION_FRAGILE')
        metrics={**m,'gross_pnl':gross,'gross_return':gross/10000*100,'net_return':m['total_return'],'fee_drag':m['fees_paid'],'slippage_drag':None,'trades_per_day':len(tr)/366,'median_holding_seconds':statistics.median([t['holding_seconds'] for t in tr]) if tr else None,'turnover':sum(float(t['entry_price'])*float(t['position_size']) for t in tr),'cost_ratio':m['fees_paid']/abs(gross) if gross else None}
        zero=engine.evaluate(c,h,0,lambda _c,_s:{}); one=engine.evaluate(c,h,1,lambda _c,_s:metrics)
        results.append({'family':family,'asset':asset,'candidate_id':c.candidate_id,'status':'PASS' if one.status=='PASS' and not reasons else 'FAIL','reasons':list(dict.fromkeys(reasons+([one.reason] if one.reason else []))),'metrics':metrics,'stage0':zero.status,'stage1':one.status})
    summary={'cycle':'research_cycle_v3_new_information','coverage_audit':audits,'hypotheses_tested':20,'total_candidates':len(results),'stage_pass_counts':{'stage0':sum(x['stage0']=='PASS' for x in results),'stage1':sum(x['stage1']=='PASS' for x in results),'stage2':0,'stage3':0,'stage4':0,'stage5':0},'holdout_access_count':0,'survivors':[],'results':results}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8'); print(json.dumps({k:v for k,v in summary.items() if k!='results'},indent=2))
if __name__=='__main__': main()
