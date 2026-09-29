"""Versioned indicator registry and bounded, explainable combination recipes."""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product

@dataclass(frozen=True)
class Indicator:
    name:str; category:str; causal:bool=True
INDICATORS=tuple(Indicator(*x) for x in (("MA_DIRECTION","trend"),("ROC","momentum"),("RSI_EXTREME","mean_reversion"),("BOLL_Z","mean_reversion"),("ATR","volatility"),("BB_WIDTH","volatility"),("REL_VOLUME","volume"),("OBV_PROXY","volume"),("TREND_REGIME","regime"),("RANGE_REGIME","regime"),("BTC_REGIME","cross_asset")))
# Explicitly enumerated economic recipes; no Cartesian product is exposed.
RECIPES=(
 ("TREND_MOMENTUM","MA_DIRECTION","ROC","TREND_REGIME"),
 ("TREND_VOLUME","MA_DIRECTION","REL_VOLUME","TREND_REGIME"),
 ("TREND_VOLATILITY","MA_DIRECTION","BB_WIDTH","TREND_REGIME"),
 ("MOMENTUM_TREND","ROC","MA_DIRECTION","TREND_REGIME"),
 ("MOMENTUM_VOLUME","ROC","REL_VOLUME",None),
 ("BREAKOUT_VOLUME","BREAKOUT","REL_VOLUME","TREND_REGIME"),
 ("BREAKOUT_CONTRACTION","BREAKOUT","BB_WIDTH","LOW_VOL_REGIME"),
 ("MEANREV_RANGE","BOLL_Z","RSI_EXTREME","RANGE_REGIME"),
 ("MEANREV_VOLATILITY","BOLL_Z","ATR","RANGE_REGIME"),
 ("BASE_BTC_REGIME","MA_DIRECTION","BTC_REGIME",None),
)
def combinations(variants=("FAST","SLOW")):
    return [{"family":a,"base_signal":b,"confirmation":c,"regime_filter":d,"variant":v,"complexity":sum(x is not None for x in (b,c,d))} for a,b,c,d in RECIPES for v in variants]
