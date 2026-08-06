"""Deterministic, provenance-bearing key-level candidate extraction."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .canonical import identity
from .versions import AI_KEY_LEVEL_ENGINE_VERSION, TIMEFRAME_SECONDS

LEVEL_SOURCES = ("RANGE_HIGH", "RANGE_LOW", "COMPRESSION_HIGH", "COMPRESSION_LOW",
                 "BREAKOUT_BOUNDARY", "RETEST_ZONE", "IMPULSE_HIGH", "IMPULSE_LOW",
                 "CONFIRMED_SWING_HIGH", "CONFIRMED_SWING_LOW", "ROLLING_HIGH", "ROLLING_LOW",
                 "MA20", "MA30", "MA60", "MA200", "EMA20", "VPVR_POC", "VPVR_VAH", "VPVR_VAL",
                 "PREVIOUS_DAY_HIGH", "PREVIOUS_DAY_LOW", "PREVIOUS_WEEK_HIGH", "PREVIOUS_WEEK_LOW",
                 "PSYCHOLOGICAL_LEVEL", "FUNDING_EXTREME_PRICE", "BASIS_EXTREME_PRICE")
SOURCE_FAMILY = {"MA20": "MOVING_AVERAGE", "MA30": "MOVING_AVERAGE", "EMA20": "MOVING_AVERAGE",
                 "MA60": "MOVING_AVERAGE", "MA200": "MOVING_AVERAGE",
                 "RANGE_HIGH": "RANGE", "RANGE_LOW": "RANGE", "COMPRESSION_HIGH": "COMPRESSION",
                 "COMPRESSION_LOW": "COMPRESSION", "VPVR_POC": "VPVR", "VPVR_VAH": "VPVR", "VPVR_VAL": "VPVR"}


def build_level_candidates(facts: dict[str, dict[str, Any]], timelines: dict[str, dict[str, Any]],
                           swings: dict[str, list[dict[str, Any]]], decision_time: int,
                           current_price: float, auxiliary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    auxiliary = auxiliary or {}; result=[]
    def add(price: float | None, source: str, timeframe: str, detected: int, path: str,
            *, low: float | None = None, high: float | None = None, dynamic: bool = False,
            slope: float | None = None, valid_until: int | None = None, touches: int = 0) -> None:
        if price is None: return
        stable={"price": round(float(price), 10), "source": source, "timeframe": timeframe,
                "detected": detected, "dynamic": dynamic, "version": AI_KEY_LEVEL_ENGINE_VERSION}
        result.append({"candidate_id": identity("candidate", stable), **stable, "zone_low": float(low if low is not None else price),
                       "zone_high": float(high if high is not None else price), "source_family": SOURCE_FAMILY.get(source, source),
                       "slope": slope, "valid_until": valid_until, "touch_count": touches,
                       "evidence_paths": [path], "quality": "VALID"})
    for tf_index, timeframe in enumerate(facts):
        fact, timeline = facts[timeframe], timelines[timeframe]
        stamp=fact["latest_confirmed_bar_timestamp"] or decision_time
        rng=timeline.get("range")
        if rng:
            add(rng["high"], "RANGE_HIGH", timeframe, rng["end"], f"/market_timeline/range_high", touches=rng.get("upper_touches",0))
            add(rng["low"], "RANGE_LOW", timeframe, rng["end"], f"/market_timeline/range_low", touches=rng.get("lower_touches",0))
        comp=timeline.get("compression") or {}
        if comp.get("compression_id"):
            add(comp["price_high"], "COMPRESSION_HIGH", timeframe, comp["end"], f"/structure_events", touches=1)
            add(comp["price_low"], "COMPRESSION_LOW", timeframe, comp["end"], f"/structure_events", touches=1)
        breakout=timeline.get("breakout")
        if breakout and breakout.get("boundary") is not None:
            add(breakout["boundary"], "BREAKOUT_BOUNDARY", timeframe, breakout.get("timestamp",stamp), "/market_timeline/breakout_timestamp", touches=1)
        retest=timeline.get("retest")
        if retest:
            add((retest["zone_low"]+retest["zone_high"])/2, "RETEST_ZONE", timeframe, retest["first_entry"],
                "/market_timeline/current_phase", low=retest["zone_low"], high=retest["zone_high"], touches=1)
        impulse=timeline.get("impulse")
        if impulse:
            source="IMPULSE_HIGH" if timeline.get("direction")=="UP" else "IMPULSE_LOW"
            add(impulse["extreme"], source, timeframe, impulse["extreme_time"],
                "/market_timeline/impulse_high" if source.endswith("HIGH") else "/market_timeline/impulse_low", touches=1)
        for swing in swings.get(timeframe, [])[-8:]:
            source="CONFIRMED_SWING_HIGH" if swing["kind"]=="HIGH" else "CONFIRMED_SWING_LOW"
            add(swing["price"], source, timeframe, swing["confirmed_at"], f"/timeframe_structures/{tf_index}/swing_structure", touches=1)
        bars=fact.get("confirmed_bars",[])
        if bars:
            recent=bars[-20:]
            add(max(r["high"] for r in recent), "ROLLING_HIGH", timeframe, stamp, f"/timeframe_structures/{tf_index}/last_confirmed_close")
            add(min(r["low"] for r in recent), "ROLLING_LOW", timeframe, stamp, f"/timeframe_structures/{tf_index}/last_confirmed_close")
        for name in ("ma20","ma30","ma60","ma200"):
            metric=fact["moving_averages"][name]; value=metric["value"]
            if value is not None and fact["quality"]["warmup_complete"] and abs(current_price-value)/current_price <= .06:
                slope=fact["slopes"][name]["value"]
                add(value, name.upper(), timeframe, stamp, f"/timeframe_structures/{tf_index}/moving_averages/{name}",
                    dynamic=True, slope=slope, valid_until=stamp+TIMEFRAME_SECONDS[timeframe])
        ema=fact["moving_averages"]["ema20"]; value=ema["value"]
        if value is not None and fact["quality"]["warmup_complete"] and abs(current_price-value)/current_price <= .06:
            add(value, "EMA20", timeframe, stamp, f"/timeframe_structures/{tf_index}/moving_averages/ema20",
                dynamic=True, slope=fact["slopes"]["ema20"]["value"], valid_until=stamp+TIMEFRAME_SECONDS[timeframe])
    for name in ("POC","VAH","VAL"):
        item=(auxiliary.get("vpvr") or {}).get(name.lower())
        if item is not None: add(float(item), f"VPVR_{name}", auxiliary.get("vpvr_timeframe","4H"), decision_time, "/provenance/input_snapshot_ids")
    for source,key,tf in (("PREVIOUS_DAY_HIGH","previous_day_high","1D"),("PREVIOUS_DAY_LOW","previous_day_low","1D"),
                          ("PREVIOUS_WEEK_HIGH","previous_week_high","1W"),("PREVIOUS_WEEK_LOW","previous_week_low","1W")):
        if key in auxiliary: add(float(auxiliary[key]),source,tf,decision_time,"/provenance/input_snapshot_ids")
    for price in psychological_levels(current_price):
        add(price,"PSYCHOLOGICAL_LEVEL","MULTI",decision_time,"/market_timeline/observation_window")
    for source,key in (("FUNDING_EXTREME_PRICE","funding_extreme_prices"),("BASIS_EXTREME_PRICE","basis_extreme_prices")):
        for item in auxiliary.get(key,[]): add(float(item),source,"15m",decision_time,"/order_flow_phases")
    return sorted(result,key=lambda c:(c["price"],c["source"],c["timeframe"],c["candidate_id"]))


def psychological_levels(price: float) -> list[float]:
    magnitude=10**max(0,len(str(int(abs(price))))-2)
    step=Decimal(str(magnitude * (5 if price/magnitude >= 20 else 1)))
    center=(Decimal(str(price))/step).to_integral_value()*step
    return [float(center+step*i) for i in range(-2,3)]
