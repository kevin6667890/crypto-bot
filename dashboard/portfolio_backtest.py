"""Event-driven multi-asset backtest with shared cash and portfolio risk."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any
import math
try:
    from metrics import calculate_metrics, maximum_drawdown, monthly_returns
    from strategy_rules import StrategyParameters, calculate_indicators, evaluate_signal
except ImportError:
    from .metrics import calculate_metrics, maximum_drawdown, monthly_returns
    from .strategy_rules import StrategyParameters, calculate_indicators, evaluate_signal

@dataclass(frozen=True)
class PortfolioParameters:
    initial_capital: float=10000.0
    max_positions: int=3
    max_asset_weight: float=0.5
    max_asset_risk: float=0.015
    max_portfolio_risk: float=0.03
    max_long_exposure: float=1.0
    max_short_exposure: float=1.0
    asset_weights: dict[str,float]|None=None
    risk_parity: bool=False
    portfolio_cooldown_bars: int=0

def run_portfolio_backtest(datasets:dict[str,list[dict[str,Any]]], strategy:StrategyParameters, config:PortfolioParameters,
                           start_ts:int, end_ts:int, progress=None, cancelled=None)->dict[str,Any]:
    if not datasets: raise ValueError("At least one portfolio asset is required.")
    normalized={asset:sorted({int(r['ts']):dict(r) for r in rows}.values(),key=lambda r:int(r['ts'])) for asset,rows in sorted(datasets.items())}
    indicators={asset:calculate_indicators(rows,strategy) for asset,rows in normalized.items()}
    index_by_ts={asset:{int(r['ts']):i for i,r in enumerate(rows)} for asset,rows in normalized.items()}
    timeline=sorted({int(r['ts']) for rows in normalized.values() for r in rows if start_ts<=int(r['ts'])<=end_ts})
    cash=config.initial_capital; realized=config.initial_capital; positions:dict[str,dict[str,Any]]={}; pending:dict[int,list[dict[str,Any]]]={}
    trades=[]; equity=[]; exposure_timeline=[]; contributions={a:0.0 for a in normalized}; fees_by_asset={a:0.0 for a in normalized}; peak_by_asset={a:0.0 for a in normalized}; dd_by_asset={a:0.0 for a in normalized}; concurrent_max=0
    weights=config.asset_weights or {a:1/len(normalized) for a in normalized}
    if config.risk_parity:
        inverse={}
        for asset,rows in normalized.items():
            values=[float(y['close'])/float(x['close'])-1 for x,y in zip(rows,rows[1:])]
            mean=sum(values)/len(values) if values else 0; variance=sum((x-mean)**2 for x in values)/max(1,len(values)-1); inverse[asset]=1/max(math.sqrt(variance),1e-9)
        weights=inverse
    total_weight=sum(max(0,float(weights.get(a,0))) for a in normalized) or 1
    weights={a:max(0,float(weights.get(a,0)))/total_weight for a in normalized}
    last_prices:dict[str,float]={}; portfolio_cooldown_until=-1; asset_cooldown_until={a:-1 for a in normalized}; risk_utilization_peak=0.0
    for ti,ts in enumerate(timeline):
        if cancelled and cancelled(): raise RuntimeError("cancelled")
        if progress and ti%max(1,len(timeline)//20)==0: progress(int(ti/max(1,len(timeline))*90),"Simulating shared portfolio events")
        for asset,rows in normalized.items():
            idx=index_by_ts[asset].get(ts)
            if idx is None: continue
            candle=rows[idx]; last_prices[asset]=float(candle['close'])
            pos=positions.get(asset)
            if pos:
                hit_stop=float(candle['low'])<=pos['stop'] if pos['side']=='LONG' else float(candle['high'])>=pos['stop']
                hit_target=float(candle['high'])>=pos['target'] if pos['side']=='LONG' else float(candle['low'])<=pos['target']
                if hit_stop or hit_target:
                    reason='STOP_LOSS' if hit_stop else 'TAKE_PROFIT'; raw=pos['stop'] if hit_stop else pos['target']
                    exit_price=raw*(1-strategy.slippage if pos['side']=='LONG' else 1+strategy.slippage)
                    gross=(exit_price-pos['entry'])*pos['size']*(1 if pos['side']=='LONG' else -1); fee=exit_price*pos['size']*strategy.trading_fee
                    pnl=gross-pos['entry_fee']-fee; cash+=pos['reserved']+gross-fee; realized+=pnl; contributions[asset]+=pnl; fees_by_asset[asset]+=fee
                    trades.append({"trade_id":len(trades)+1,"instrument":asset,"side":pos['side'],"entry_ts":pos['entry_ts'],"exit_ts":ts,"entry_price":pos['entry'],"exit_price":exit_price,"position_size":pos['size'],"pnl":pnl,"result_r":pnl/max(pos['risk_amount'],1e-12),"fees":pos['entry_fee']+fee,"exit_reason":reason,"signal_id":pos['signal']['signal_id'],"strategy_version":pos['signal']['strategy_version'],"config_hash":pos['signal']['config_hash'],"expected_entry_ts":pos['entry_ts'],"expected_entry_price":pos['raw_entry']})
                    del positions[asset]; asset_cooldown_until[asset]=ti+strategy.cooldown_bars; portfolio_cooldown_until=max(portfolio_cooldown_until,ti+config.portfolio_cooldown_bars)
        candidates=sorted(pending.pop(ts,[]),key=lambda x:(-x['signal']['score'],x['asset']))
        for candidate in candidates:
            asset=candidate['asset']
            if asset in positions or len(positions)>=config.max_positions or ti<portfolio_cooldown_until or ti<asset_cooldown_until[asset]: continue
            idx=index_by_ts[asset].get(ts)
            if idx is None: continue
            candle=normalized[asset][idx]; side=candidate['signal']['action']; raw=float(candle['open']); entry=raw*(1+strategy.slippage if side=='LONG' else 1-strategy.slippage)
            stop_distance=float(candidate['signal']['atr'])*strategy.stop_loss_atr_multiplier
            current_equity=realized+sum((last_prices.get(a,p['entry'])-p['entry'])*p['size']*(1 if p['side']=='LONG' else -1) for a,p in positions.items())
            used_risk=sum(p['risk_amount'] for p in positions.values()); risk_cap=min(current_equity*config.max_asset_risk,current_equity*config.max_portfolio_risk-used_risk)
            notional_cap=min(current_equity*config.max_asset_weight,current_equity*weights[asset],cash)
            long_notional=sum(p['reserved'] for p in positions.values() if p['side']=='LONG'); short_notional=sum(p['reserved'] for p in positions.values() if p['side']=='SHORT')
            exposure_room=current_equity*(config.max_long_exposure if side=='LONG' else config.max_short_exposure)-(long_notional if side=='LONG' else short_notional)
            size=min(risk_cap/stop_distance if stop_distance>0 else 0,notional_cap/entry if entry else 0,max(0,exposure_room)/entry if entry else 0)
            if size<=0: continue
            reserved=entry*size; entry_fee=reserved*strategy.trading_fee
            if reserved+entry_fee>cash: size=cash/(entry*(1+strategy.trading_fee)); reserved=entry*size; entry_fee=reserved*strategy.trading_fee
            cash-=reserved+entry_fee; fees_by_asset[asset]+=entry_fee
            stop=entry-stop_distance if side=='LONG' else entry+stop_distance; target=entry+stop_distance*strategy.risk_reward_ratio if side=='LONG' else entry-stop_distance*strategy.risk_reward_ratio
            positions[asset]={"side":side,"entry":entry,"raw_entry":raw,"entry_ts":ts,"stop":stop,"target":target,"size":size,"reserved":reserved,"entry_fee":entry_fee,"risk_amount":stop_distance*size,"signal":candidate['signal']}
        for asset,rows in normalized.items():
            idx=index_by_ts[asset].get(ts)
            if idx is None or idx+1>=len(rows): continue
            signal=evaluate_signal(rows[idx],indicators[asset][idx],strategy,instrument=asset,timeframe='15m')
            next_ts=int(rows[idx+1]['ts'])
            if signal['action']!='WAIT' and next_ts<=end_ts: pending.setdefault(next_ts,[]).append({"asset":asset,"signal":signal})
        unrealized=sum((last_prices.get(a,p['entry'])-p['entry'])*p['size']*(1 if p['side']=='LONG' else -1) for a,p in positions.items())
        value=realized+unrealized; gross=sum(p['reserved'] for p in positions.values()); long=sum(p['reserved'] for p in positions.values() if p['side']=='LONG'); short=sum(p['reserved'] for p in positions.values() if p['side']=='SHORT')
        risk_used=sum(p['risk_amount'] for p in positions.values()); risk_util=risk_used/max(value*config.max_portfolio_risk,1e-12)*100; risk_utilization_peak=max(risk_utilization_peak,risk_util)
        equity.append({"ts":ts,"equity":value,"cash":cash}); exposure_timeline.append({"ts":ts,"gross":gross/max(value,1e-12)*100,"long":long/max(value,1e-12)*100,"short":short/max(value,1e-12)*100,"positions":len(positions),"risk_utilization":risk_util}); concurrent_max=max(concurrent_max,len(positions))
    if positions and timeline:
        ts=timeline[-1]
        for asset,pos in list(positions.items()):
            raw=last_prices.get(asset,pos['entry']); exit_price=raw*(1-strategy.slippage if pos['side']=='LONG' else 1+strategy.slippage); gross=(exit_price-pos['entry'])*pos['size']*(1 if pos['side']=='LONG' else -1); fee=exit_price*pos['size']*strategy.trading_fee; pnl=gross-pos['entry_fee']-fee
            cash+=pos['reserved']+gross-fee; realized+=pnl; contributions[asset]+=pnl; fees_by_asset[asset]+=fee
            trades.append({"trade_id":len(trades)+1,"instrument":asset,"side":pos['side'],"entry_ts":pos['entry_ts'],"exit_ts":ts,"entry_price":pos['entry'],"exit_price":exit_price,"position_size":pos['size'],"pnl":pnl,"result_r":pnl/max(pos['risk_amount'],1e-12),"fees":pos['entry_fee']+fee,"exit_reason":"END_OF_DATA","signal_id":pos['signal']['signal_id'],"strategy_version":pos['signal']['strategy_version'],"config_hash":pos['signal']['config_hash']})
        equity[-1]['equity']=realized; equity[-1]['cash']=cash
    def correlation(left:list[float],right:list[float])->float|None:
        n=min(len(left),len(right)); left,right=left[-n:],right[-n:]
        if n<3:return None
        lm,rm=sum(left)/n,sum(right)/n; numerator=sum((a-lm)*(b-rm) for a,b in zip(left,right)); denominator=math.sqrt(sum((a-lm)**2 for a in left)*sum((b-rm)**2 for b in right)); return numerator/denominator if denominator else None
    returns={a:[float(y['close'])/float(x['close'])-1 for x,y in zip(rows,rows[1:])] for a,rows in normalized.items()}
    correlation_matrix={a:{b:(1.0 if a==b else correlation(returns[a],returns[b])) for b in normalized} for a in normalized}
    assets=list(normalized); rolling_correlation=[]
    if len(assets)>1:
        common=min(len(returns[a]) for a in assets)
        for end in range(30,common+1,max(1,common//120)):
            rolling_correlation.append({"index":end,**{f"{a}/{b}":correlation(returns[a][end-30:end],returns[b][end-30:end]) for i,a in enumerate(assets) for b in assets[i+1:]}})
    per_asset_equity=[]; per_asset_drawdown={}
    for asset in normalized:
        value=config.initial_capital*weights[asset]; peak=value; worst=0.0
        per_asset_equity.append({"instrument":asset,"points":[]})
        by_exit={}
        for trade in trades:
            if trade['instrument']==asset:by_exit[trade['exit_ts']]=by_exit.get(trade['exit_ts'],0)+trade['pnl']
        for ts in timeline:
            value+=by_exit.get(ts,0); peak=max(peak,value); worst=min(worst,(value-peak)/peak if peak else 0); per_asset_equity[-1]['points'].append({"ts":ts,"equity":value})
        per_asset_drawdown[asset]=abs(worst)*100
    metrics=calculate_metrics(config.initial_capital,equity,trades,900); metrics.update({"exposure":sum(p['gross'] for p in exposure_timeline)/len(exposure_timeline) if exposure_timeline else 0,"cash_utilization":sum(100-e['cash']/max(e['equity'],1e-12)*100 for e in equity)/len(equity) if equity else 0,"long_exposure":max((p['long'] for p in exposure_timeline),default=0),"short_exposure":max((p['short'] for p in exposure_timeline),default=0),"concurrent_positions":concurrent_max,"portfolio_risk_utilization":risk_utilization_peak,"per_asset_pnl":contributions,"per_asset_contribution":{a:v/config.initial_capital*100 for a,v in contributions.items()},"per_asset_drawdown":per_asset_drawdown,"fees_by_asset":fees_by_asset})
    return {"metrics":metrics,"trades":trades,"equity":equity,"drawdown":maximum_drawdown(equity)[1],"monthly_returns":monthly_returns(equity),"exposure_timeline":exposure_timeline,"per_asset_equity":per_asset_equity,"correlation_matrix":correlation_matrix,"rolling_correlation":rolling_correlation,"config":asdict(config),"execution_model":"Unified chronological event stream with shared cash, risk budget, asset and portfolio cooldowns, and adverse next-open slippage."}
