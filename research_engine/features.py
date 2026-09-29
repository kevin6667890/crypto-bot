from __future__ import annotations
from dataclasses import dataclass

AVAILABLE=frozenset({"OHLCV","TREND","MOMENTUM","MEAN_REVERSION","VOLATILITY","VOLUME","BOLLINGER","VPVA_PROXY","RELATIVE_STRENGTH"})
UNAVAILABLE=frozenset({"TRUE_VPVA","CVD","OI","FUNDING","BASIS"})
@dataclass(frozen=True)
class Availability:
    feature: str; status: str; reason: str
class FeatureRegistry:
    def __init__(self, validated_datasets=()):
        self.validated_datasets={str(x).upper() for x in validated_datasets}
    def check(self, features):
        out=[]
        for value in features:
            key=str(value).upper()
            available=key in AVAILABLE or key in self.validated_datasets
            out.append(Availability(key,"AVAILABLE","confirmed and coverage-validated") if available else Availability(key,"UNAVAILABLE","formal verified history is unavailable"))
        return out
