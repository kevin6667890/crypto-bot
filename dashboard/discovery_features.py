"""Causal, versioned features used by the Strategy Discovery Lab.

Every value at index ``i`` is calculated from candles 0..i only.  This module
intentionally has no database or network dependencies so it is easy to test.
"""
from __future__ import annotations
from math import sqrt
from typing import Any

FEATURE_VERSION = "discovery-features-v1"

def _mean(xs: list[float]) -> float: return sum(xs) / len(xs)

def build_features(candles: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[dict[str, float | None]]:
    config = config or {}; ma_periods = config.get("ma_periods", [6,20,60,200])
    atr_period = int(config.get("atr_period", 14)); bb_period = int(config.get("bb_period", 20)); rsi_period = int(config.get("rsi_period", 14)); volume_period = int(config.get("volume_period", 20))
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
        result["body_range_ratio"]=abs(float(row["close"])-float(row["open"]))/max(float(row["high"])-float(row["low"]),1e-12)
        result["recent_high"]=max(float(x["high"]) for x in candles[max(0,i-20):i+1]); result["recent_low"]=min(float(x["low"]) for x in candles[max(0,i-20):i+1])
        result["warm"] = i+1 >= max(max(ma_periods), atr_period+1, bb_period, rsi_period+1, volume_period+1); out.append(result)
    return out
