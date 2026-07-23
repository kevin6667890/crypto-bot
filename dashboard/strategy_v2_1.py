"""Causal stateful Strategy v2.1 setup/trigger/re-arm architecture."""
from __future__ import annotations
from typing import Any
from .discovery_features import FEATURE_VERSION
from .discovery_identity import canonical_json_hash
from .strategy_v2 import (normalize_parameters as normalize_v2, classify_regime_v2,
    _side_stop_target, DISCOVERY_STRATEGY_VERSION as V2_STRATEGY_VERSION)

DISCOVERY_STRATEGY_VERSION = "discovery-strategy-v2.1"
STRATEGY_RULES_VERSION = "strategy-rules-v2.1"
TEMPLATE_VERSION = {
    "TREND_PULLBACK_V2_1": "trend-pullback-v2.1",
    "TREND_BREAKOUT_V2_1": "trend-breakout-v2.1",
    "RANGE_MEAN_REVERSION_V2_1": "range-mean-reversion-v2.1",
}
TEMPLATES = tuple(TEMPLATE_VERSION)
REGIME_STABILITY_VERSION = "causal-regime-confirmation-v2.1"
SETUP_LIFECYCLE_VERSION = "setup-lifecycle-v2.1"
BASE_TEMPLATE = {name: name.replace("_V2_1", "_V2") for name in TEMPLATES}

def normalize_parameters(template: str, raw: dict[str, Any]) -> dict[str, Any]:
    if template not in TEMPLATES: raise ValueError("Unknown v2.1 template")
    return normalize_v2(BASE_TEMPLATE[template], raw)

class StrategyV21Evaluator:
    """One evaluator belongs to exactly one candidate/fold/instrument/timeframe."""
    def __init__(self, template: str, parameters: dict[str, Any], instrument: str,
                 timeframe: str, dataset_fingerprint: str | None, fold_identity: dict[str, Any]):
        self.template=template; self.base=BASE_TEMPLATE[template]; self.p=normalize_parameters(template,parameters)
        self.instrument=instrument; self.timeframe=timeframe; self.dataset_fingerprint=dataset_fingerprint
        self.fold_identity=dict(fold_identity); self.state="IDLE"; self.setup_id=None; self.activation_ts=None
        self.activation_context={}
        self.side=None; self.frozen_level=None; self.sequence=0; self.rearm_reason="INITIAL"
        self.confirmed_regime="TRANSITION"; self.raw_candidate=None; self.raw_count=0

    def _regime(self, candle, feature):
        raw=classify_regime_v2(candle,feature)
        code=raw["code"]
        if code==self.raw_candidate: self.raw_count+=1
        else: self.raw_candidate=code; self.raw_count=1
        reason="UNCHANGED"
        if self.raw_count>=2 and code!=self.confirmed_regime:
            prior=self.confirmed_regime; self.confirmed_regime=code; reason=f"CONFIRMED_AFTER_2:{prior}->{code}"
        return raw,{"version":REGIME_STABILITY_VERSION,"raw_regime":code,"confirmed_regime":self.confirmed_regime,
                    "confirmation_count":self.raw_count,"transition_reason":reason}

    def _activate(self, ts, side, level=None, context=None):
        self.sequence+=1; self.setup_id=canonical_json_hash({"version":SETUP_LIFECYCLE_VERSION,
            "template":self.template,"instrument":self.instrument,"timeframe":self.timeframe,
            "fold":self.fold_identity,"sequence":self.sequence,"activation_ts":ts})
        self.activation_ts=ts; self.side=side; self.frozen_level=level; self.activation_context=dict(context or {}); self.state="ARMED"

    def _evidence(self, ts, raw_regime, stable, prior_state, gates, resulting, trigger=False):
        return {"strategy_family":DISCOVERY_STRATEGY_VERSION,"legacy_family":V2_STRATEGY_VERSION,
          "strategy_rules_version":STRATEGY_RULES_VERSION,"template":self.template,
          "template_version":TEMPLATE_VERSION[self.template],"feature_version":FEATURE_VERSION,
          "normalized_parameters":self.p,"instrument":self.instrument,"timeframe":self.timeframe,
          "dataset_fingerprint":self.dataset_fingerprint,"fold_identity":self.fold_identity,
          "source_candle_timestamp":ts,"raw_regime":raw_regime,"regime_stability":stable,
          "setup_lifecycle_version":SETUP_LIFECYCLE_VERSION,"setup_id":self.setup_id,
          "setup_activation_timestamp":self.activation_ts,"setup_activation_context":self.activation_context,
          "trigger_timestamp":ts if trigger else None,
          "rearm_reason":self.rearm_reason,"prior_state":prior_state,"resulting_state":resulting,
          "frozen_level":self.frozen_level,"gates":gates}

    def evaluate(self, candles: list[dict[str,Any]], features: list[dict[str,Any]], index: int):
        c=candles[index]; f=dict(features[index]); ts=int(c["ts"]); prior=candles[index-1] if index else None
        pf=features[index-1] if index else None; prior_state=self.state
        raw,stable=self._regime(c,f); regime=self.confirmed_regime; action="WAIT"; gates={}; trigger=False
        trend_side="LONG" if regime=="BULL_TREND" else "SHORT" if regime=="BEAR_TREND" else None

        if self.template=="TREND_PULLBACK_V2_1":
            distance=abs(float(c["close"])-float(f["ema_20"]))/float(f["atr"]) if f.get("atr") else 999.
            prior_distance=abs(float(prior["close"])-float(pf["ema_20"]))/float(pf["atr"]) if prior and pf and pf.get("atr") else 999.
            against=bool(prior) and ((trend_side=="LONG" and float(prior["close"])<=float(prior["open"])) or
                                     (trend_side=="SHORT" and float(prior["close"])>=float(prior["open"])))
            excursion=trend_side is not None and distance<=self.p["pullback_max_atr"] and (against or prior_distance>self.p["pullback_max_atr"])
            gates={"trend_side":trend_side,"distance_atr":distance,"prior_distance_atr":prior_distance,"genuine_excursion":excursion}
            if self.state=="IDLE" and excursion: self._activate(ts,trend_side,context=gates)
            elif self.state=="ARMED":
                reclaim=bool(prior) and self.side==trend_side and ((self.side=="LONG" and float(c["close"])>float(prior["high"]) and float(c["close"])>float(f["ema_20"])) or
                    (self.side=="SHORT" and float(c["close"])<float(prior["low"]) and float(c["close"])<float(f["ema_20"])))
                gates["resumption_crossing"]=reclaim
                if reclaim: action=self.side; trigger=True; self.state="REARM_REQUIRED"
                elif trend_side!=self.side: self.state="INVALIDATED"
            elif self.state in {"REARM_REQUIRED","INVALIDATED"}:
                outside=distance>self.p["pullback_max_atr"]*1.25
                if outside: self.state="IDLE"; self.rearm_reason="LEFT_PULLBACK_ZONE"

        elif self.template=="TREND_BREAKOUT_V2_1":
            lookback=self.p["breakout_lookback"]; prior_rows=candles[max(0,index-lookback):index]
            level=max((float(x["high"]) for x in prior_rows),default=None) if trend_side=="LONG" else min((float(x["low"]) for x in prior_rows),default=None) if trend_side=="SHORT" else None
            crossing=level is not None and prior is not None and ((trend_side=="LONG" and float(prior["close"])<=level<float(c["close"])) or
                (trend_side=="SHORT" and float(prior["close"])>=level>float(c["close"])))
            gates={"trend_side":trend_side,"prior_completed_level":level,"actual_crossing":crossing}
            if self.state=="IDLE" and crossing:
                self._activate(ts,trend_side,level,gates); action=trend_side; trigger=True; self.state="REARM_REQUIRED"
            elif self.state=="REARM_REQUIRED":
                inside=self.frozen_level is not None and ((self.side=="LONG" and float(c["close"])<=self.frozen_level) or
                    (self.side=="SHORT" and float(c["close"])>=self.frozen_level))
                if inside: self.state="IDLE"; self.rearm_reason="RETURNED_INSIDE_FROZEN_LEVEL"

        else:
            lower,upper=f.get("bb_lower"),f.get("bb_upper"); rsi=f.get("rsi")
            outside_long=lower is not None and float(c["low"])<=float(lower) and rsi is not None and rsi<=self.p["rsi_lower"]
            outside_short=upper is not None and float(c["high"])>=float(upper) and rsi is not None and rsi>=self.p["rsi_upper"]
            gates={"range_regime":regime in {"RANGE_LOW_VOLATILITY","RANGE_HIGH_VOLATILITY"},
                   "band_excursion":outside_long or outside_short,"rsi":rsi}
            if self.state=="IDLE" and gates["range_regime"] and (outside_long or outside_short):
                self._activate(ts,"LONG" if outside_long else "SHORT",float(lower if outside_long else upper),gates)
            elif self.state=="ARMED":
                rsi_reversal=pf is not None and rsi is not None and pf.get("rsi") is not None and ((self.side=="LONG" and rsi>pf["rsi"]) or (self.side=="SHORT" and rsi<pf["rsi"]))
                reentry=bool(prior) and ((self.side=="LONG" and lower is not None and float(prior["low"])<=float(pf["bb_lower"]) and float(c["close"])>float(lower)) or
                    (self.side=="SHORT" and upper is not None and float(prior["high"])>=float(pf["bb_upper"]) and float(c["close"])<float(upper)))
                gates.update({"band_reentry":reentry,"rsi_directional_reversal":rsi_reversal})
                if gates["range_regime"] and reentry and rsi_reversal: action=self.side; trigger=True; self.state="REARM_REQUIRED"
                elif not gates["range_regime"]: self.state="INVALIDATED"
            elif self.state in {"REARM_REQUIRED","INVALIDATED"}:
                neutral=lower is not None and upper is not None and float(lower)<float(c["close"])<float(upper) and rsi is not None and 40<rsi<60
                if neutral: self.state="IDLE"; self.rearm_reason="RETURNED_TO_NEUTRAL_RANGE"

        evidence=self._evidence(ts,raw,stable,prior_state,gates,self.state,trigger)
        if action=="WAIT" or not f.get("warm"): return {"action":"WAIT","warmed":bool(f.get("warm")),"atr":f.get("atr"),"evidence":evidence}
        if self.template=="TREND_BREAKOUT_V2_1":
            f["recent_high" if action=="LONG" else "recent_low"]=self.frozen_level
        mode,stop,target,exit_mode,distance=_side_stop_target(self.base,self.p,c,f,action)
        if (action=="LONG" and not stop<float(c["close"])<target) or (action=="SHORT" and not target<float(c["close"])<stop):
            self.state="INVALIDATED"; evidence["resulting_state"]=self.state; evidence["geometry_valid"]=False
            return {"action":"WAIT","warmed":True,"atr":f.get("atr"),"evidence":evidence}
        target_r=abs(target-float(c["close"]))/distance
        parameter_hash=canonical_json_hash({"strategy_version":DISCOVERY_STRATEGY_VERSION,"template":self.template,
            "template_version":TEMPLATE_VERSION[self.template],"feature_version":FEATURE_VERSION,"parameters":self.p})
        evidence.update({"geometry_valid":True,"stop_mode":mode,"stop_price":stop,"stop_distance":distance,
                         "exit_mode":exit_mode,"target_price":target,"expected_r":target_r,"parameter_hash":parameter_hash})
        signal_id=canonical_json_hash({"setup_id":self.setup_id,"trigger_ts":ts,"parameter_hash":parameter_hash})
        return {"action":action,"warmed":True,"atr":f["atr"],"stop_distance":distance,"target_r":target_r,
                "score":100.,"signal_id":signal_id,"strategy_version":DISCOVERY_STRATEGY_VERSION,
                "config_hash":parameter_hash,"signal_ts":ts,"evidence":evidence}
