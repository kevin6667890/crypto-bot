"""Pure Phase 5C diagnostics over an already executed canonical trade path.

Forward outcomes are labels attached after signal generation.  They never enter
candidate identity, feature generation, setup state, or execution.
"""
from __future__ import annotations
import math, statistics
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

def _median(values):
    finite=[float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.median(finite) if finite else None

def signal_event_study(result: dict[str,Any], candles: list[dict[str,Any]],
                       validation_start_ts: int, validation_end_ts: int,
                       horizons: tuple[int,...]=(1,2,4,8,16)) -> dict[str,Any]:
    """Label each trigger using only forward bars inside the validation fold."""
    rows=sorted((x for x in candles if validation_start_ts<=int(x["ts"])<=validation_end_ts),
                key=lambda x:int(x["ts"]))
    positions={int(row["ts"]):index for index,row in enumerate(rows)}
    events=[]
    for evidence in sorted(result.get("v2_evaluations",{}).values(),
                           key=lambda x:int(x.get("source_candle_timestamp") or 0)):
        trigger=evidence.get("trigger_timestamp")
        if trigger is None or int(trigger) not in positions:
            continue
        index=positions[int(trigger)]; origin=rows[index]; close=float(origin["close"])
        stop=evidence.get("stop_price"); target=evidence.get("target_price")
        if stop is None or target is None:
            continue
        side="LONG" if float(stop)<close<float(target) else "SHORT"
        sign=1. if side=="LONG" else -1.
        labels={}
        for horizon in horizons:
            end=index+int(horizon)
            if end>=len(rows):
                continue
            path=rows[index+1:end+1]; last=path[-1]
            favorable=max((float(x["high"])-close if side=="LONG" else close-float(x["low"])) for x in path)
            adverse=max((close-float(x["low"]) if side=="LONG" else float(x["high"])-close) for x in path)
            outcome="NEITHER_HIT"
            for candle in path:
                stop_hit=float(candle["low"])<=float(stop) if side=="LONG" else float(candle["high"])>=float(stop)
                target_hit=float(candle["high"])>=float(target) if side=="LONG" else float(candle["low"])<=float(target)
                if stop_hit or target_hit:
                    outcome="STOP_HIT_FIRST" if stop_hit else "TARGET_HIT_FIRST"
                    break
            labels[str(horizon)]={"outcome_timestamp":int(last["ts"]),
              "direction_adjusted_forward_return":sign*(float(last["close"])/close-1)*100,
              "mfe":favorable/close*100,"mae":adverse/close*100,
              "stop_hit_first":outcome=="STOP_HIT_FIRST",
              "target_hit_first":outcome=="TARGET_HIT_FIRST",
              "neither_hit":outcome=="NEITHER_HIT"}
        events.append({"setup_id":evidence.get("setup_id"),"trigger_timestamp":int(trigger),
          "side":side,"fold_identity":evidence.get("fold_identity",{}),"labels":labels})
    aggregate={}
    for horizon in horizons:
        labels=[event["labels"][str(horizon)] for event in events if str(horizon) in event["labels"]]
        returns=[x["direction_adjusted_forward_return"] for x in labels]
        aggregate[str(horizon)]={"event_count":len(labels),"median_forward_return":_median(returns),
          "profitable_event_ratio":sum(x>0 for x in returns)/len(returns) if returns else None,
          "median_mfe":_median([x["mfe"] for x in labels]),"median_mae":_median([x["mae"] for x in labels]),
          "stop_hit_first_ratio":sum(x["stop_hit_first"] for x in labels)/len(labels) if labels else None,
          "target_hit_first_ratio":sum(x["target_hit_first"] for x in labels)/len(labels) if labels else None,
          "neither_hit_ratio":sum(x["neither_hit"] for x in labels)/len(labels) if labels else None}
    return {"event_study_version":"phase5c-causal-forward-labels-v1","horizons":list(horizons),
      "validation_start_ts":int(validation_start_ts),"validation_end_ts":int(validation_end_ts),
      "event_count":len(events),"events":events,"aggregate":aggregate,
      "diagnostic_labels_only":True,"candidate_identity_included":False}

def mean_reversion_diagnostics(result: dict[str,Any], candles: list[dict[str,Any]],
                               features: list[dict[str,Any]], timeframe_seconds: int,
                               validation_start_ts: int, validation_end_ts: int) -> dict[str,Any]:
    """Detailed v2.1 mean-reversion lifecycle and geometry attribution."""
    visible={int(row["ts"]):(row,features[index]) for index,row in enumerate(candles)
             if validation_start_ts<=int(row["ts"])<=validation_end_ts}
    evaluations=sorted((x for x in result.get("v2_evaluations",{}).values()
                        if validation_start_ts<=int(x["source_candle_timestamp"])<=validation_end_ts),
                       key=lambda x:int(x["source_candle_timestamp"]))
    activations=[]; triggers=[]; seen=set(); neutral=[]; neutral_count=0
    for evidence in evaluations:
        setup=evidence.get("setup_id")
        if setup and setup not in seen:
            seen.add(setup); activations.append(evidence)
        if evidence.get("trigger_timestamp") is not None:
            triggers.append(evidence)
        if evidence.get("prior_state") in {"REARM_REQUIRED","INVALIDATED"}:
            neutral_count+=1
            if evidence.get("resulting_state")=="IDLE":
                neutral.append(neutral_count); neutral_count=0
        else:
            neutral_count=0
    excursion_depth=[]; excursion_rsi=[]; setup_regimes={}
    for evidence in activations:
        ts=int(evidence["setup_activation_timestamp"])
        if ts not in visible: continue
        candle,feature=visible[ts]; side=evidence.get("setup_activation_context",{}).get("side")
        if side not in {"LONG","SHORT"}:
            lower,upper=feature.get("bb_lower"),feature.get("bb_upper")
            side="LONG" if lower is not None and float(candle["low"])<=float(lower) else "SHORT"
        atr=float(feature["atr"])
        depth=(float(feature["bb_lower"])-float(candle["low"]))/atr if side=="LONG" else (float(candle["high"])-float(feature["bb_upper"]))/atr
        excursion_depth.append(depth); excursion_rsi.append(feature.get("rsi"))
        regime=evidence.get("regime_stability",{}).get("confirmed_regime")
        setup_regimes[regime]=setup_regimes.get(regime,0)+1
    reentry_rsi=[]; mid_distance=[]; stop_distance=[]; target_distance=[]; expected_r=[]; trigger_regimes={}
    for evidence in triggers:
        ts=int(evidence["trigger_timestamp"])
        if ts not in visible: continue
        candle,feature=visible[ts]; close=float(candle["close"])
        reentry_rsi.append(feature.get("rsi")); mid_distance.append(abs(float(feature["bb_mid"])-close))
        if evidence.get("geometry_valid") is not False and evidence.get("target_price") is not None:
            stop_distance.append(evidence.get("stop_distance"))
            target_distance.append(abs(float(evidence["target_price"])-close))
            expected_r.append(evidence.get("expected_r"))
        regime=evidence.get("regime_stability",{}).get("confirmed_regime")
        trigger_regimes[regime]=trigger_regimes.get(regime,0)+1
    trades=result.get("trades",[]); entries=sorted(int(x["entry_ts"]) for x in trades)
    gaps=[(b-a)//timeframe_seconds for a,b in zip(entries,entries[1:])]
    mfe=[]; mae=[]
    ordered=sorted(candles,key=lambda x:int(x["ts"]))
    for trade in trades:
        path=[x for x in ordered if int(trade["entry_ts"])<=int(x["ts"])<=int(trade["exit_ts"])]
        if not path: continue
        entry=float(trade["entry_price"]); risk=abs(entry-float(trade["stop_loss"]))
        favorable=max((float(x["high"])-entry if trade["side"]=="LONG" else entry-float(x["low"])) for x in path)
        adverse=max((entry-float(x["low"]) if trade["side"]=="LONG" else float(x["high"])-entry) for x in path)
        mfe.append(favorable/risk if risk else None); mae.append(adverse/risk if risk else None)
    cost=fixed_path_cost_attribution(result,float(result["metrics"]["initial_capital"]))
    return {"setup_count":len(activations),"trigger_count":len(triggers),"trade_count":len(trades),
      "long_trades":sum(x["side"]=="LONG" for x in trades),"short_trades":sum(x["side"]=="SHORT" for x in trades),
      "setup_regime_distribution":setup_regimes,"trigger_regime_distribution":trigger_regimes,
      "median_bollinger_excursion_depth_atr":_median(excursion_depth),
      "median_rsi_at_excursion":_median(excursion_rsi),"median_rsi_at_reentry":_median(reentry_rsi),
      "median_neutral_state_duration_bars":_median(neutral),
      "median_candles_between_repeated_entries":_median(gaps),
      "median_entry_distance_to_bollinger_midline":_median(mid_distance),
      "median_stop_distance":_median(stop_distance),"median_target_distance":_median(target_distance),
      "median_expected_reward_risk_at_entry":_median(expected_r),
      "stop_hit_ratio":sum(x["exit_reason"]=="STOP_LOSS" for x in trades)/len(trades) if trades else None,
      "target_hit_ratio":sum(x["exit_reason"]=="TAKE_PROFIT" for x in trades)/len(trades) if trades else None,
      "median_mfe_r":_median(mfe),"median_mae_r":_median(mae),
      "gross_return":cost["gross_return_before_costs"],"fee_drag":cost["fee_drag_return"],
      "slippage_drag":cost["slippage_drag_return"],"net_return":cost["net_return"]}
