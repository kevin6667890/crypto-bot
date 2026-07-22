"""Bounded, transparent discovery template configurations and signal rules."""
from __future__ import annotations
from typing import Any
from .discovery_identity import TEMPLATE_VERSION, TEMPLATES, build_parameter_identity, normalize_template_parameters
def parameter_hash(value:dict[str,Any])->str:
    return build_parameter_identity(value["template"], value.get("parameters",value))
def validate(config:dict[str,Any], mode:str="PRICE_ONLY")->dict[str,Any]:
    t=config.get("template");
    if t not in TEMPLATES: raise ValueError("Unknown discovery template.")
    p=config.get("parameters",config)
    return {"template":t,"template_version":TEMPLATE_VERSION[t],"parameters":normalize_template_parameters(t,p)}
def signal(template:str,p:dict[str,Any],c:dict[str,Any],f:dict[str,Any])->str:
    if not f.get("warm"): return "WAIT"
    fast=f.get("ema_%s"%p.get("fast_period",20)) if p.get("fast_ma_type","EMA")=="EMA" else f.get("sma_%s"%p.get("fast_period",20)); slow=f.get("sma_%s"%p.get("slow_period",200))
    if fast is None or slow is None:return "WAIT"
    close=float(c["close"]); long=close>fast>slow; short=close<fast<slow; vol=f.get("volume_ratio")
    if p.get("volume_enabled") and (vol is None or vol<float(p.get("minimum_volume_ratio",1))): return "WAIT"
    if template=="TREND_PULLBACK":
        distance=abs(close-float(fast))/float(fast); ok=distance<=float(p.get("maximum_distance",.004)); return "LONG" if ok and long else "SHORT" if ok and short else "WAIT"
    if template=="VOLATILITY_BREAKOUT": return "LONG" if long and f.get("bb_upper") is not None and close>float(f["bb_upper"]) else "SHORT" if short and f.get("bb_lower") is not None and close<float(f["bb_lower"]) else "WAIT"
    if template=="MEAN_REVERSION":
        r=f.get("rsi"); return "LONG" if f.get("bb_lower") and close<=float(f["bb_lower"]) and r is not None and r<=float(p.get("rsi_lower",35)) else "SHORT" if f.get("bb_upper") and close>=float(f["bb_upper"]) and r is not None and r>=float(p.get("rsi_upper",65)) else "WAIT"
    recent_high, recent_low = f.get("recent_high"), f.get("recent_low")
    if recent_high is None or recent_low is None:
        return "WAIT"
    return "LONG" if long and close>=float(recent_high) else "SHORT" if short and close<=float(recent_low) else "WAIT"
