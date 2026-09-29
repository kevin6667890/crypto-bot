"""Causal, versioned features used by the Strategy Discovery Lab.

Every value at index ``i`` is calculated from candles 0..i only.  This module
intentionally has no database or network dependencies so it is easy to test.
"""
from __future__ import annotations
from math import sqrt
from typing import Any

FEATURE_VERSION = "discovery-features-v2-causal-vpva-proxy"

def _mean(xs: list[float]) -> float: return sum(xs) / len(xs)

def _vpva_proxy(rows: list[dict[str, Any]], bins: int) -> dict[str, float] | None:
    """Causal OHLCV-only volume-profile proxy; it is not trade-level VPVA.

    Each completed candle assigns its aggregate volume to its typical price.
    The value area expands contiguously from the POC until it contains 70% of
    proxy volume.  Callers must preserve the ``proxy`` label in evidence.
    """
    if not rows: return None
    lo=min(float(x["low"]) for x in rows); hi=max(float(x["high"]) for x in rows)
    if hi <= lo: return None
    width=(hi-lo)/bins; profile=[0.0]*bins
    for row in rows:
        typical=(float(row["high"])+float(row["low"])+float(row["close"]))/3
        index=min(bins-1,max(0,int((typical-lo)/width)))
        profile[index]+=float(row["volume"])
    poc=max(range(bins),key=profile.__getitem__); left=right=poc; covered=profile[poc]; target=sum(profile)*.70
    while covered < target and (left > 0 or right < bins-1):
        below=profile[left-1] if left else -1.0; above=profile[right+1] if right < bins-1 else -1.0
        if above > below: right+=1; covered+=profile[right]
        else: left-=1; covered+=profile[left]
    return {"vpva_poc":lo+(poc+.5)*width,"vpva_value_low":lo+left*width,"vpva_value_high":lo+(right+1)*width,"vpva_proxy_coverage":covered/sum(profile) if sum(profile) else 0.0}

def build_features(candles: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[dict[str, float | None]]:
    config = config or {}; ma_periods = config.get("ma_periods", [6,20,60,200])
    atr_period = int(config.get("atr_period", 14)); bb_period = int(config.get("bb_period", 20)); rsi_period = int(config.get("rsi_period", 14)); volume_period = int(config.get("volume_period", 20)); vpva_lookback=int(config.get("vpva_lookback",100)); vpva_bins=int(config.get("vpva_bins",24))
    closes=[float(x["close"]) for x in candles]; volumes=[float(x["volume"]) for x in candles]; out=[]; emas={p:None for p in ma_periods}; atr=None
    for i,row in enumerate(candles):
        result: dict[str,float|None]={"warm": None}; close=closes[i]
        for p in ma_periods:
            result[f"sma_{p}"]=_mean(closes[i-p+1:i+1]) if i+1>=p else None
            emas[p]=close if emas[p] is None else close*(2/(p+1))+float(emas[p])*(1-2/(p+1)); result[f"ema_{p}"]=emas[p]
            # Difference from the causal SMA four closed bars ago.
            result[f"sma_{p}_slope"]=(result[f"sma_{p}"]-_mean(closes[i-p-3:i-3])) if i+1>=p+4 else None
        if i >= 1:
            tr=max(float(row["high"])-float(row["low"]),abs(float(row["high"])-closes[i-1]),abs(float(row["low"])-closes[i-1]))
            if i==atr_period: atr=_mean([max(float(candles[j]["high"])-float(candles[j]["low"]),abs(float(candles[j]["high"])-closes[j-1]),abs(float(candles[j]["low"])-closes[j-1])) for j in range(1,atr_period+1)])
            elif i>atr_period and atr is not None: atr=(atr*(atr_period-1)+tr)/atr_period
        result["atr"]=atr; result["atr_pct"]=(atr/close if atr else None)
        if i+1>=bb_period:
            sample=closes[i-bb_period+1:i+1]; mid=_mean(sample); sd=sqrt(_mean([(x-mid)**2 for x in sample])); upper=mid+2*sd; lower=mid-2*sd
            result.update({"bb_mid":mid,"bb_upper":upper,"bb_lower":lower,"bb_width":(upper-lower)/mid if mid else None,"bb_pct":(close-lower)/(upper-lower) if upper>lower else .5})
        else: result.update({"bb_mid":None,"bb_upper":None,"bb_lower":None,"bb_width":None,"bb_pct":None})
        if i>=rsi_period:
            changes=[closes[j]-closes[j-1] for j in range(i-rsi_period+1,i+1)]; gain=_mean([max(0,x) for x in changes]); loss=_mean([max(0,-x) for x in changes]); result["rsi"]=100 if loss==0 else 100-100/(1+gain/loss)
        else: result["rsi"]=None
        result["volume_ratio"]=volumes[i]/_mean(volumes[i-volume_period:i]) if i>=volume_period and _mean(volumes[i-volume_period:i]) else None
        # Exclude the current signal candle: its final volume is not known
        # before that candle closes and must never shape its own entry gate.
        vpva=_vpva_proxy(candles[max(0,i-vpva_lookback):i],vpva_bins) if i>=vpva_lookback else None
        result.update(vpva or {"vpva_poc":None,"vpva_value_low":None,"vpva_value_high":None,"vpva_proxy_coverage":None})
        result["body_range_ratio"]=abs(float(row["close"])-float(row["open"]))/max(float(row["high"])-float(row["low"]),1e-12)
        # Breakout levels are formed from completed *previous* candles.  Including
        # the current bar would let it redefine the threshold it is tested against.
        # The empty early-history window deliberately remains unavailable.
        prior = candles[max(0, i - 20):i]
        result["recent_high"] = max((float(x["high"]) for x in prior), default=None)
        result["recent_low"] = min((float(x["low"]) for x in prior), default=None)
        result["warm"] = i+1 >= max(max(ma_periods), atr_period+1, bb_period, rsi_period+1, volume_period+1, vpva_lookback+1); out.append(result)
    return out
