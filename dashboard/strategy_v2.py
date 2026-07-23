"""Causal, versioned OHLCV-only strategy architecture v2.

This is intentionally a small research core: it has no database, network, CVD,
or OI dependency.  A caller supplies closed candles and receives immutable,
auditable evaluation evidence.
"""
from __future__ import annotations

import math
from typing import Any

from .discovery_features import FEATURE_VERSION, build_features
from .discovery_identity import canonical_json_hash

LIVE_STRATEGY_VERSION = "live-mtf-flow-v2"
DISCOVERY_STRATEGY_VERSION = "discovery-strategy-v2"
STRATEGY_RULES_VERSION = "strategy-rules-v2"
V2_FEATURE_VERSION = FEATURE_VERSION
TEMPLATE_VERSION = {"TREND_PULLBACK_V2": "trend-pullback-v2", "TREND_BREAKOUT_V2": "trend-breakout-v2", "RANGE_MEAN_REVERSION_V2": "range-mean-reversion-v2"}
TEMPLATES = tuple(TEMPLATE_VERSION)
REGIME_VERSION = "causal-regime-v2"

# Each item is deliberately bounded and is included in semantic identity.
PARAMETER_SCHEMA = {
 "pullback_max_atr": {"type":"number","minimum":0.25,"maximum":1.5,"step":0.25,"templates":["TREND_PULLBACK_V2"],"group":"signal","description":"Maximum EMA20 pullback distance in ATR."},
 "breakout_lookback": {"type":"integer","allowed_values":[10,20,30,40],"templates":["TREND_BREAKOUT_V2"],"group":"signal","description":"Completed candles used for breakout level."},
 "rsi_lower": {"type":"integer","allowed_values":[25,30,35,40],"templates":["RANGE_MEAN_REVERSION_V2"],"group":"signal","description":"Long range RSI extreme."},
 "rsi_upper": {"type":"integer","allowed_values":[60,65,70,75],"templates":["RANGE_MEAN_REVERSION_V2"],"group":"signal","description":"Short range RSI extreme."},
 "volume_enabled": {"type":"boolean","allowed_values":[False,True],"templates":list(TEMPLATES),"group":"signal","description":"Optional removable volume confirmation."},
 "minimum_volume_ratio": {"type":"number","minimum":0.8,"maximum":1.5,"step":0.1,"templates":list(TEMPLATES),"group":"signal","description":"Minimum rolling volume ratio when enabled."},
 "stop_mode": {"type":"string","allowed_values":["ATR","RECENT_SWING"],"templates":["TREND_PULLBACK_V2"],"group":"stop","description":"Explicit pullback stop anchor."},
 "stop_atr_multiple": {"type":"number","minimum":0.75,"maximum":2.5,"step":0.25,"templates":list(TEMPLATES),"group":"stop","description":"ATR stop distance or level buffer."},
 "target_r": {"type":"number","minimum":1.0,"maximum":3.0,"step":0.25,"templates":["TREND_PULLBACK_V2","TREND_BREAKOUT_V2"],"group":"exit","description":"Fixed bounded R target."},
 "risk_per_trade": {"type":"number","minimum":0.0025,"maximum":0.02,"step":0.0025,"templates":list(TEMPLATES),"group":"risk","description":"Requested equity risk fraction."},
 "max_notional_fraction": {"type":"number","minimum":0.1,"maximum":1.0,"step":0.1,"templates":list(TEMPLATES),"group":"risk","description":"Explicit maximum notional fraction."},
 "trading_fee": {"type":"number","minimum":0.0,"maximum":0.002,"step":0.0001,"templates":list(TEMPLATES),"group":"execution","description":"Per-side fee assumption."},
 "slippage": {"type":"number","minimum":0.0,"maximum":0.002,"step":0.0001,"templates":list(TEMPLATES),"group":"execution","description":"Adverse fill slippage assumption."},
}
DEFAULTS={"pullback_max_atr":1.0,"breakout_lookback":20,"rsi_lower":35,"rsi_upper":65,"volume_enabled":False,"minimum_volume_ratio":1.0,"stop_mode":"ATR","stop_atr_multiple":1.25,"target_r":2.0,"risk_per_trade":0.01,"max_notional_fraction":1.0,"trading_fee":0.0005,"slippage":0.0003}

def live_policy_status(core_evaluation: dict[str,Any], flow_diagnostic: dict[str,Any] | None = None) -> dict[str,Any]:
    """Keep optional forward flow evidence out of the historical core identity.

    The default policy is core-only.  A future forward-test policy can compare
    this unchanged core status to ``CORE_PLUS_FLOW_CONFIRMATION`` explicitly.
    """
    flow=dict(flow_diagnostic or {})
    core="PASS" if core_evaluation.get("action") in {"LONG","SHORT"} else "WAIT"
    diagnostic="UNAVAILABLE" if not flow.get("available") else "ALIGNED" if flow.get("aligned") else "NOT_ALIGNED"
    return {"core_price_signal_status":core,"live_flow_diagnostic_status":diagnostic,
            "final_paper_policy_status":core,"policy":"CORE_ONLY_DEFAULT",
            "future_policy_seam":["CORE_ONLY","CORE_PLUS_FLOW_CONFIRMATION"],
            "evaluation_id":core_evaluation.get("evidence",{}).get("evaluation_id")}

def normalize_parameters(template: str, raw: dict[str, Any]) -> dict[str, Any]:
    if template not in TEMPLATES or not isinstance(raw, dict): raise ValueError("Unknown v2 template or parameters.")
    allowed={k for k,v in PARAMETER_SCHEMA.items() if template in v["templates"]}
    if set(raw)-allowed: raise ValueError("Unknown or inapplicable v2 parameter.")
    p={k:v for k,v in DEFAULTS.items() if k in allowed}; p.update(raw)
    for key in allowed:
        spec=PARAMETER_SCHEMA[key]; value=p[key]
        if spec["type"] == "boolean":
            if not isinstance(value,bool): raise ValueError(f"{key} must be boolean")
        elif "allowed_values" in spec:
            if value not in spec["allowed_values"]: raise ValueError(f"Unsupported {key}")
        else:
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(float(value)): raise ValueError(f"Invalid {key}")
            value=int(value) if spec["type"] == "integer" else round(float(value), 4)
            if not spec["minimum"] <= value <= spec["maximum"] or abs((value-spec["minimum"])/spec["step"]-round((value-spec["minimum"])/spec["step"]))>1e-8: raise ValueError(f"Unsupported {key}")
            p[key]=value
    if not p["volume_enabled"]: p.pop("minimum_volume_ratio",None)
    if template != "TREND_PULLBACK_V2": p.pop("stop_mode",None)
    if template == "RANGE_MEAN_REVERSION_V2":
        p.pop("target_r",None)
        if p["rsi_lower"] >= p["rsi_upper"]: raise ValueError("RSI extremes must be ordered")
    return dict(sorted(p.items()))

def classify_regime_v2(candle: dict[str,Any], f: dict[str,Any]) -> dict[str,Any]:
    close=float(candle["close"]); ema=f.get("ema_20"); ma60=f.get("sma_60"); ma200=f.get("sma_200"); atr=f.get("atr"); width=f.get("bb_width")
    values={"close":close,"ema20":ema,"ma60":ma60,"ma200":ma200,"atr":atr,"atr_pct":(float(atr)/close if atr else None),"bb_width":width,"ma_separation":(abs(float(ma60)-float(ma200))/close if ma60 is not None and ma200 is not None else None),"ma60_slope":f.get("sma_60_slope"),"ma200_slope":f.get("sma_200_slope")}
    if any(values[x] is None for x in ("ema20","ma60","ma200","atr","bb_width","ma_separation","ma60_slope","ma200_slope")): return {"code":"TRANSITION","version":REGIME_VERSION,"features":values,"thresholds":{}}
    bull=close>float(ema)>float(ma60)>float(ma200) and float(f["sma_60_slope"])>0 and float(f["sma_200_slope"])>=0
    bear=close<float(ema)<float(ma60)<float(ma200) and float(f["sma_60_slope"])<0 and float(f["sma_200_slope"])<=0
    thresholds={"range_ma_separation_max":0.008,"low_vol_bb_width_max":0.035,"low_vol_atr_pct_max":0.008}
    if bull: code="BULL_TREND"
    elif bear: code="BEAR_TREND"
    elif float(values["ma_separation"])<=thresholds["range_ma_separation_max"]:
        code="RANGE_LOW_VOLATILITY" if float(width)<=thresholds["low_vol_bb_width_max"] and float(values["atr_pct"])<=thresholds["low_vol_atr_pct_max"] else "RANGE_HIGH_VOLATILITY"
    else: code="TRANSITION"
    return {"code":code,"version":REGIME_VERSION,"features":values,"thresholds":thresholds}

def _side_stop_target(template:str,p:dict[str,Any],c:dict[str,Any],f:dict[str,Any],side:str) -> tuple[str,float,float,str,float]:
    entry=float(c["close"]); atr=float(f["atr"]); sign=1 if side=="LONG" else -1
    if template=="TREND_PULLBACK_V2" and p.get("stop_mode")=="RECENT_SWING":
        anchor=float(f["recent_low"] if side=="LONG" else f["recent_high"]); stop=anchor-sign*atr*0.25; mode="RECENT_SWING"; stop_anchor="confirmed_recent_swing"
    elif template=="TREND_BREAKOUT_V2":
        anchor=float(f["recent_high"] if side=="LONG" else f["recent_low"]); stop=anchor-sign*atr*float(p["stop_atr_multiple"]); mode="BREAKOUT_LEVEL_ATR_BUFFER"; stop_anchor="prior_completed_breakout_level"
    else:
        stop=entry-sign*atr*float(p["stop_atr_multiple"]); mode="ATR"; stop_anchor="entry_close"
    distance=abs(entry-stop)
    if not math.isfinite(distance) or distance<=0: raise ValueError("invalid stop distance")
    if template=="RANGE_MEAN_REVERSION_V2": target=float(f["bb_mid"]); exit_mode="BOLLINGER_MIDLINE"; target_anchor="entry_candle_bb_mid"
    else: target=entry+sign*distance*float(p["target_r"]); exit_mode="FIXED_R"; target_anchor="entry_close_and_stop_distance"
    return mode,stop,target,exit_mode,distance

def evaluate_v2(template:str, parameters:dict[str,Any], candles:list[dict[str,Any]], index:int, instrument:str="UNKNOWN", timeframe:str="15m", dataset_fingerprint:str|None=None, fold_identity:dict[str,Any]|None=None, features:list[dict[str,Any]]|None=None) -> dict[str,Any]:
    p=normalize_parameters(template,parameters); features=features if features is not None else build_features(candles,{"ma_periods":[20,60,200],"atr_period":14,"bb_period":20,"rsi_period":14,"volume_period":20}); c=candles[index]; f=dict(features[index]); regime=classify_regime_v2(c,f); close=float(c["close"]); prior=candles[index-1] if index else None
    gates={"regime":False,"setup_ma":False,"setup_location":False,"setup_extreme":False,"setup_rsi":False,"trigger":False,"volume":True}
    side="WAIT"; trend_long=regime["code"]=="BULL_TREND"; trend_short=regime["code"]=="BEAR_TREND"; vr=f.get("volume_ratio"); gates["volume"]=(not p["volume_enabled"]) or (vr is not None and float(vr)>=p["minimum_volume_ratio"])
    if template=="TREND_PULLBACK_V2":
        side="LONG" if trend_long else "SHORT" if trend_short else "WAIT"; gates["regime"]=side!="WAIT"; gates["setup_ma"]=gates["regime"]
        d=abs(close-float(f["ema_20"]))/float(f["atr"]) if f.get("ema_20") and f.get("atr") else math.inf; gates["setup_location"]=d<=p["pullback_max_atr"]
        gates["trigger"]=bool(prior) and ((side=="LONG" and close>float(c["open"]) and close>float(prior["close"])) or (side=="SHORT" and close<float(c["open"]) and close<float(prior["close"])))
    elif template=="TREND_BREAKOUT_V2":
        side="LONG" if trend_long else "SHORT" if trend_short else "WAIT"; gates["regime"]=side!="WAIT"; gates["setup_ma"]=gates["regime"]
        lookback=p["breakout_lookback"]; prior_rows=candles[max(0,index-lookback):index]; level=max([float(x["high"]) for x in prior_rows],default=None) if side=="LONG" else min([float(x["low"]) for x in prior_rows],default=None)
        f["recent_high" if side=="LONG" else "recent_low"]=level; gates["setup_location"]=level is not None; gates["trigger"]=level is not None and ((side=="LONG" and close>level) or (side=="SHORT" and close<level))
    else:
        # The setup is an intrabar reach; the trigger is a confirmed close back
        # inside the band in the reversal direction.
        lower,upper=f.get("bb_lower"),f.get("bb_upper")
        side="LONG" if lower is not None and float(c["low"])<=float(lower) else "SHORT" if upper is not None and float(c["high"])>=float(upper) else "WAIT"; gates["regime"]=regime["code"] in {"RANGE_LOW_VOLATILITY","RANGE_HIGH_VOLATILITY"}; gates["setup_extreme"]=side!="WAIT"; rsi=f.get("rsi"); gates["setup_rsi"]=(side=="LONG" and rsi is not None and rsi<=p["rsi_lower"]) or (side=="SHORT" and rsi is not None and rsi>=p["rsi_upper"]); gates["trigger"]=bool(prior) and ((side=="LONG" and close>float(c["open"]) and close>float(prior["close"]) and close>float(lower)) or (side=="SHORT" and close<float(c["open"]) and close<float(prior["close"]) and close<float(upper)))
    applicable=("regime","setup_ma","setup_location","trigger","volume") if template!="RANGE_MEAN_REVERSION_V2" else ("regime","setup_extreme","setup_rsi","trigger","volume")
    allowed=side!="WAIT" and all(gates[name] for name in applicable) and bool(f.get("warm")); evidence={"strategy_family":DISCOVERY_STRATEGY_VERSION,"strategy_version":LIVE_STRATEGY_VERSION,"strategy_rules_version":STRATEGY_RULES_VERSION,"template":template,"template_version":TEMPLATE_VERSION[template],"regime":regime,"setup_gates":gates,"trigger_gates":{"confirmed":gates["trigger"]},"normalized_parameters":p,"feature_version":V2_FEATURE_VERSION,"source_candle_timestamp":int(c["ts"]),"causal_feature_timestamps":{"source_candle":int(c["ts"]),"breakout_level_end":int(candles[index-1]["ts"]) if index else None},"instrument":instrument,"timeframe":timeframe,"dataset_fingerprint":dataset_fingerprint,"fold_identity":fold_identity or {}}
    evidence["parameter_hash"]=canonical_json_hash({"strategy_version":DISCOVERY_STRATEGY_VERSION,"template":template,"template_version":TEMPLATE_VERSION[template],"feature_version":V2_FEATURE_VERSION,"parameters":p})
    evidence["evaluation_id"]=canonical_json_hash({"evaluation_identity_version":"strategy-v2-evaluation-v1","strategy_version":DISCOVERY_STRATEGY_VERSION,"template_version":TEMPLATE_VERSION[template],"parameter_hash":evidence["parameter_hash"],"feature_version":V2_FEATURE_VERSION,"execution_assumptions":{k:p[k] for k in ("risk_per_trade","max_notional_fraction","trading_fee","slippage")},"instrument":instrument,"timeframe":timeframe,"dataset_fingerprint":dataset_fingerprint,"fold_identity":fold_identity or {},"source_candle_timestamp":int(c["ts"])})
    if not allowed: return {"action":"WAIT","warmed":bool(f.get("warm")),"atr":f.get("atr"),"evidence":evidence}
    mode,stop,target,exit_mode,distance=_side_stop_target(template,p,c,f,side)
    if (side=="LONG" and not stop<close<target) or (side=="SHORT" and not target<close<stop): return {"action":"WAIT","warmed":True,"atr":f.get("atr"),"evidence":evidence}
    evidence.update({"stop_mode":mode,"stop_anchor":"prior_completed_level" if mode.startswith("BREAKOUT") else "confirmed_recent_swing" if mode=="RECENT_SWING" else "entry_close","stop_distance":distance,"stop_price":stop,"exit_mode":exit_mode,"target_anchor":"entry_candle_bb_mid" if exit_mode=="BOLLINGER_MIDLINE" else "entry_close_and_stop_distance","target_price":target,"expected_r":abs(target-close)/distance,"position_sizing":{"risk_budget_fraction":p["risk_per_trade"],"maximum_notional_fraction":p["max_notional_fraction"],"contract":"paper-accounting-v2"}})
    return {"action":side,"warmed":True,"atr":f["atr"],"stop_distance":distance,"target_r":evidence["expected_r"],"score":100.0,"signal_id":evidence["evaluation_id"],"strategy_version":LIVE_STRATEGY_VERSION,"config_hash":evidence["parameter_hash"],"signal_ts":int(c["ts"]),"evidence":evidence}
