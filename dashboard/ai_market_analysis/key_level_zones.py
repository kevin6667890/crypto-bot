"""ATR-aware level clustering, state resolution, strength and relevance ranking."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .canonical import identity
from .key_level_candidates import SOURCE_FAMILY
from .versions import AI_KEY_LEVEL_ZONE_VERSION

STRENGTHS=("WEAK","MODERATE","STRONG","MAJOR")
LEVEL_STATES=("ACTIVE","TOUCHED","BROKEN","FLIPPED","EXPIRED")


def merge_level_zones(candidates: list[dict[str,Any]], current_price: float, atr: float | None,
                      bars: list[dict[str,Any]], decision_time: int, direction: str="NONE",
                      *, max_total: int=12) -> list[dict[str,Any]]:
    threshold=max(current_price*.0025,(atr or current_price*.005)*.35)
    clusters=[]
    for candidate in sorted(candidates,key=lambda c:(c["price"],c["candidate_id"])):
        prior=clusters[-1] if clusters else []
        merge_distance=threshold
        if prior and (candidate["timeframe"] in {"1D","1W"} or candidate["source_family"]=="VPVR" or
                      any(c["timeframe"] in {"1D","1W"} or c["source_family"]=="VPVR" for c in prior)):
            merge_distance=max(merge_distance,atr or threshold,current_price*.01)
        if prior and candidate["zone_low"]-max(c["zone_high"] for c in prior) <= merge_distance:
            clusters[-1].append(candidate)
        else: clusters.append([candidate])
    zones=[]
    for cluster in clusters:
        low=min(c["zone_low"] for c in cluster); high=max(c["zone_high"] for c in cluster)
        prices=sorted(c["price"] for c in cluster); rep=prices[len(prices)//2]
        families=sorted({c["source_family"] for c in cluster}); timeframes=sorted({c["timeframe"] for c in cluster})
        static=any(not c["dynamic"] for c in cluster)
        role="SUPPORT" if high < current_price else "RESISTANCE" if low > current_price else "PIVOT"
        state,broken_at,flipped_at=_state(cluster,bars,low,high,role,direction,static,decision_time)
        if state=="FLIPPED":
            role="SUPPORT" if direction=="UP" else "RESISTANCE" if direction=="DOWN" else role
        score=_strength_score(cluster,families,timeframes,state)
        strength="MAJOR" if score>=10 else "STRONG" if score>=7 else "MODERATE" if score>=4 else "WEAK"
        if families==["PSYCHOLOGICAL_LEVEL"]: strength="WEAK"
        stable={"low":round(low,10),"high":round(high,10),"sources":[c["candidate_id"] for c in cluster],
                "state":state,"role":role,"decision_time":decision_time,"version":AI_KEY_LEVEL_ZONE_VERSION}
        zones.append({"level_id":identity("level",stable),"representative_price":rep,"zone_low":low,"zone_high":high,
                      "role":role,"state":state,"strength":strength,"source_candidates":cluster,"timeframes":timeframes,
                      "confluences":families,"touch_count":sum(c["touch_count"] for c in cluster),
                      "first_detected":min(c["detected"] for c in cluster),"last_tested":_last_test(bars,low,high),
                      "observed_at":decision_time,
                      "source_fact":sorted({p for c in cluster for p in c["evidence_paths"]}),
                      "broken_at":broken_at,"flipped_at":flipped_at,
                      "invalidation":_invalidation(role,state,low,high,timeframes),
                      "evidence_paths":sorted({p for c in cluster for p in c["evidence_paths"]}),
                      "quality":"VALID" if all(c["quality"]=="VALID" for c in cluster) else "PARTIAL",
                      "version":AI_KEY_LEVEL_ZONE_VERSION,"_score":score})
    selected=[]
    for role,cap in (("SUPPORT",5),("RESISTANCE",5),("PIVOT",3)):
        group=[z for z in zones if z["role"]==role and z["state"]!="INVALIDATED"]
        group.sort(key=lambda z:(abs(z["representative_price"]-current_price)/current_price,-z["_score"],z["level_id"]))
        selected.extend(group[:cap])
    selected.sort(key=lambda z:(abs(z["representative_price"]-current_price)/current_price,-z["_score"],z["level_id"]))
    for zone in selected[:max_total]: zone.pop("_score",None)
    return selected[:max_total]


def _state(cluster,bars,low,high,role,direction,static,decision_time):
    if not static:
        valid = [int(c["valid_until"]) for c in cluster if c.get("valid_until") is not None]
        return ("EXPIRED", None, None) if valid and max(valid) < decision_time else ("ACTIVE",None,None)
    detected=min(c["detected"] for c in cluster)
    closes=[(int(r.get("close_time",r.get("ts",0))),float(r["close"])) for r in bars if int(r.get("close_time",r.get("ts",0)))>=detected]
    boundary_sources={c["source"] for c in cluster}
    if direction=="UP" and boundary_sources&{"BREAKOUT_BOUNDARY","RANGE_HIGH"}:
        broken=next((t for t,c in closes if c>high),None)
        return ("FLIPPED",broken,broken) if broken else ("UNCONFIRMED",None,None)
    if direction=="DOWN" and boundary_sources&{"BREAKOUT_BOUNDARY","RANGE_LOW"}:
        broken=next((t for t,c in closes if c<low),None)
        return ("FLIPPED",broken,broken) if broken else ("UNCONFIRMED",None,None)
    broken=next((t for (t,c),(t2,c2) in zip(closes,closes[1:]) if (role=="SUPPORT" and c<low and c2<low) or (role=="RESISTANCE" and c>high and c2>high)),None)
    return ("BROKEN",broken,None) if broken else ("ACTIVE",None,None)


def _strength_score(cluster,families,timeframes,state):
    weights={"15m":1,"1H":2,"4H":3,"1D":4,"1W":5,"MULTI":1}
    score=max(weights.get(tf,1) for tf in timeframes)+min(3,len(families)-1)+min(2,sum(c["touch_count"] for c in cluster)//2)
    if any(c["source"] in {"BREAKOUT_BOUNDARY","RETEST_ZONE","CONFIRMED_SWING_HIGH","CONFIRMED_SWING_LOW"} for c in cluster): score+=2
    if state=="BROKEN": score-=2
    return score


def _last_test(bars,low,high):
    tests=[int(r.get("close_time",r.get("ts",0))) for r in bars if float(r["low"])<=high and float(r["high"])>=low]
    return max(tests,default=None)


def _invalidation(role,state,low,high,timeframes):
    tf=max(timeframes,key=lambda x:{"15m":1,"1H":2,"4H":3,"1D":4,"1W":5,"MULTI":0}.get(x,0))
    side="below zone low" if role=="SUPPORT" else "above zone high" if role=="RESISTANCE" else "outside zone followed by failed reclaim"
    return {"rule":f"two confirmed {tf} closes {side}","timeframe":tf,"boundary":low if role=="SUPPORT" else high}
