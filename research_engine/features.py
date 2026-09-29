from __future__ import annotations
from dataclasses import dataclass

AVAILABLE=frozenset({"OHLCV","TREND","MOMENTUM","MEAN_REVERSION","VOLATILITY","VOLUME","BOLLINGER","VPVA_PROXY","RELATIVE_STRENGTH"})
UNAVAILABLE=frozenset({"TRUE_VPVA","CVD","OI","FUNDING","BASIS"})
@dataclass(frozen=True)
class Availability:
    feature: str; status: str; reason: str
class FeatureRegistry:
    def check(self, features):
        out=[]
        for value in features:
            key=str(value).upper()
            out.append(Availability(key,"AVAILABLE","confirmed local OHLCV") if key in AVAILABLE else Availability(key,"UNAVAILABLE","formal verified history is unavailable"))
        return out
