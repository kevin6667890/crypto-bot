"""Pure diagnostic attribution over an already executed canonical trade path."""
from __future__ import annotations
import statistics
from typing import Any

def fixed_path_cost_attribution(result: dict[str,Any], initial_capital: float) -> dict[str,Any]:
    """Remove fees/slippage from the same trades; signals and exits are not rerun."""
    gross=net=fees=slippage=0.0
    for trade in result.get("trades",[]):
        side=1 if trade["side"]=="LONG" else -1; size=float(trade["position_size"])
        raw_entry=float(trade["expected_entry_price"]); effective_entry=float(trade["entry_price"])
        effective_exit=float(trade["exit_price"]); fee=float(trade["fees"])
        # Exit slippage uses the same adverse rate as entry. Infer the raw
        # collision/close price from the persisted effective fill and side.
        entry_rate=abs(effective_entry/raw_entry-1) if raw_entry else 0.
        raw_exit=effective_exit/(1-entry_rate if side==1 else 1+entry_rate)
        raw_pnl=(raw_exit-raw_entry)*size*side
        effective_before_fees=(effective_exit-effective_entry)*size*side
        gross+=raw_pnl; slippage+=raw_pnl-effective_before_fees; fees+=fee; net+=float(trade["pnl"])
    return {"fixed_trade_path":True,"trade_count":len(result.get("trades",[])),
      "gross_profit_before_costs":gross,"gross_return_before_costs":gross/initial_capital*100,
      "fee_drag":fees,"fee_drag_return":fees/initial_capital*100,
      "slippage_drag":slippage,"slippage_drag_return":slippage/initial_capital*100,
      "total_cost_drag":fees+slippage,"net_profit":net,"net_return":net/initial_capital*100,
      "average_gross_edge_per_trade":gross/len(result["trades"]) if result.get("trades") else None,
      "break_even_cost_per_trade":gross/len(result["trades"]) if result.get("trades") else None,
      "zero_cost_diagnostic_return":gross/initial_capital*100}

def lifecycle_summary(result: dict[str,Any], timeframe_seconds: int) -> dict[str,Any]:
    evaluations=list(result.get("v2_evaluations",{}).values()); trades=result.get("trades",[])
    setup_ids=[x.get("setup_id") for x in evaluations if x.get("setup_id")]
    triggers=[x for x in evaluations if x.get("trigger_timestamp")]
    entries=sorted(int(x["entry_ts"]) for x in trades); gaps=[(b-a)//timeframe_seconds for a,b in zip(entries,entries[1:])]
    regimes={}
    for evidence in evaluations:
        code=evidence.get("regime_stability",{}).get("confirmed_regime") or evidence.get("regime",{}).get("code")
        if code: regimes[code]=regimes.get(code,0)+1
    exits={}
    for trade in trades: exits[trade["exit_reason"]]=exits.get(trade["exit_reason"],0)+1
    setup_trigger_counts={}
    for evidence in triggers:
        setup_trigger_counts[evidence.get("setup_id")]=setup_trigger_counts.get(evidence.get("setup_id"),0)+1
    return {"regime_candle_counts":regimes,"evaluation_count":len(evaluations),
      "setup_count":len(set(setup_ids)),"trigger_count":len(triggers),"signal_count":result.get("signal_count",0),
      "executed_trades":len(trades),"skipped_signals":max(0,result.get("signal_count",0)-len(trades)),
      "long_trades":sum(x["side"]=="LONG" for x in trades),"short_trades":sum(x["side"]=="SHORT" for x in trades),
      "average_holding_seconds":statistics.mean([x["holding_seconds"] for x in trades]) if trades else None,
      "median_holding_seconds":statistics.median([x["holding_seconds"] for x in trades]) if trades else None,
      "exit_counts":exits,"median_candles_between_entries":statistics.median(gaps) if gaps else None,
      "repeated_setup_trigger_count":sum(max(0,n-1) for n in setup_trigger_counts.values()),
      "maximum_triggers_from_one_setup":max(setup_trigger_counts.values(),default=0)}
