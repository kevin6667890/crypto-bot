"""Exact signal-lineage reconciliation between paper and backtest executions."""
from __future__ import annotations
from statistics import median
from typing import Any

def _pct(observed:float|None,expected:float|None)->float|None:
    return (float(observed)-float(expected))/float(expected)*100 if observed is not None and expected not in (None,0) else None

def reconcile(paper:list[dict[str,Any]], backtest:list[dict[str,Any]], collector_fresh:bool|None=True)->dict[str,Any]:
    bt_by_id={x.get('signal_id'):x for x in backtest if x.get('signal_id')}; used:set[int]=set(); rows=[]
    for p in paper:
        b=bt_by_id.get(p.get('signal_id')) if p.get('signal_id') else None
        legacy=False
        if b is None and not p.get('signal_id'):
            candidates=[x for x in backtest if not x.get('signal_id') and x.get('instrument')==p.get('instrument') and x.get('strategy_version') and x.get('strategy_version')==p.get('strategy_version') and int(x.get('signal_ts',x.get('candle_close_ts',-1)))==int(p.get('candle_close_ts',-2))]
            if len(candidates)==1: b=candidates[0]; legacy=True
        if b is not None: used.add(id(b))
        rows.append(_row(p,b,legacy,collector_fresh))
    for b in backtest:
        if id(b) not in used: rows.append(_row(None,b,False,collector_fresh))
    matched=[r for r in rows if r['match_status'] not in {'Paper Missing','Backtest Missing','Legacy Unmatched','Service Gap'}]
    entry_diffs=[abs(r['entry_difference_pct']) for r in matched if r['entry_difference_pct'] is not None]
    action_mismatch=sum(r['paper_action']!=r['backtest_action'] for r in matched)
    unmatched=sum(r['match_status'] in {'Paper Missing','Backtest Missing','Legacy Unmatched','Service Gap'} for r in rows)
    if not rows or not matched: status='Insufficient Data'
    else:
        match_rate=len(matched)/len(rows); mismatch_rate=action_mismatch/len(matched); med=median(entry_diffs) if entry_diffs else 0
        status='Normal' if match_rate>=.9 and mismatch_rate==0 and med<=.1 else 'Watch' if match_rate>=.7 and mismatch_rate<=.1 and med<=.5 else 'Diverging'
    return {'items':rows,'drift_status':status,'signal_match_rate':len(matched)/len(rows)*100 if rows else None,'action_mismatch_rate':action_mismatch/len(matched)*100 if matched else None,
            'median_entry_difference_pct':median(entry_diffs) if entry_diffs else None,'unmatched_signal_ratio':unmatched/len(rows)*100 if rows else None,
            'paper_signal_count':len(paper),'backtest_signal_count':len(backtest),'matched_count':len(matched),'unmatched_count':unmatched}

def _row(p:dict[str,Any]|None,b:dict[str,Any]|None,legacy:bool,collector_fresh:bool|None)->dict[str,Any]:
    expected=(b or {}).get('expected_entry_price',(b or {}).get('entry_price')); observed=(p or {}).get('observed_entry_price',(p or {}).get('entry'))
    action_p=(p or {}).get('action',(p or {}).get('side')); action_b=(b or {}).get('action',(b or {}).get('side'))
    entry_diff=_pct(observed,expected); configured=(b or {}).get('configured_slippage'); observed_slip=(p or {}).get('observed_slippage_pct')
    if p is None: status='Paper Missing' if collector_fresh is not False else 'Service Gap'
    elif b is None: status='Legacy Unmatched' if not p.get('signal_id') else 'Backtest Missing'
    elif legacy: status='Exact Match' if action_p==action_b else 'Decision Mismatch'
    elif action_p!=action_b: status='Risk Blocked' if not (p.get('entry_allowed',True)) else 'Decision Mismatch'
    elif entry_diff is not None and abs(entry_diff)>max(.1,abs(float(configured or 0))*200): status='Entry Divergence'
    elif p.get('reason') and b.get('exit_reason') and p.get('reason')!=b.get('exit_reason'): status='Exit Divergence'
    else: status='Exact Match'
    return {'signal_id':(p or b or {}).get('signal_id'),'instrument':(p or b or {}).get('instrument'),'candle_close_ts':(p or {}).get('candle_close_ts',(b or {}).get('signal_ts')),
            'paper_action':action_p,'backtest_action':action_b,'paper_score':(p or {}).get('score',(p or {}).get('signal_score')),'backtest_score':(b or {}).get('score',(b or {}).get('signal_score')),
            'expected_entry_time':(b or {}).get('expected_entry_ts',(b or {}).get('entry_ts')),'observed_entry_time':(p or {}).get('created_at'),'execution_delay':(p or {}).get('execution_delay_ms'),
            'expected_entry_price':expected,'observed_entry_price':observed,'entry_difference_pct':entry_diff,'configured_slippage':configured,'observed_slippage':observed_slip,
            'paper_exit_reason':(p or {}).get('reason'),'backtest_exit_reason':(b or {}).get('exit_reason'),'paper_result_r':(p or {}).get('pnl_r'),'backtest_result_r':(b or {}).get('result_r'),
            'match_status':status,'divergence_reason':None if status=='Exact Match' else status}
