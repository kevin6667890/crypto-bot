from __future__ import annotations

from typing import Any

from .canonical import identity
from .range_compression import detect_compression, detect_range
from .versions import AI_MARKET_TIMELINE_VERSION


TIMELINE_PARAMETERS = {
    "attempt_distance_atr": .15, "strong_confirmation_distance_atr": .50,
    "confirmation_hold_bars": 1, "confirmation_volume_ratio": 1.20,
    "impulse_atr": 1.0, "pullback_excursion_fraction": .20,
    "retest_zone_atr": .25, "continuation_distance_atr": .50,
    "failure_inside_closes": 2, "expiry_bars": 48,
}
EVENT_TYPES = ("RANGE_BUILDING", "COMPRESSION", "BREAKOUT_ATTEMPT", "BREAKOUT_CONFIRMED",
               "IMPULSE", "POST_BREAKOUT_PULLBACK", "RETEST", "CONTINUATION",
               "FAILED_BREAKOUT", "REVERSAL", "EXPIRED", "UNCLASSIFIED")


def _event(event_type: str, direction: str, timeframe: str, started: int, confirmed: int,
           sources: list[int], structure_id: str, bounds: dict[str, float], trigger: str,
           confidence: str, invalidation: str, *, ended: int | None = None,
           active: bool = False, counter: list[str] | None = None) -> dict[str, Any]:
    stable = {"event_type": event_type, "direction": direction, "timeframe": timeframe,
              "started_at": started, "confirmed_at": confirmed,
              "source_structure_identity": structure_id, "price_bounds": bounds,
              "version": AI_MARKET_TIMELINE_VERSION}
    return {
        "event_id": identity("event", stable), **stable, "ended_at": ended, "active": active,
        "trigger": trigger, "evidence": [trigger], "counterevidence": counter or [],
        "confidence": confidence, "invalidation": invalidation,
        "input_bar_timestamps": sorted(set(sources)),
    }


def build_timeline(facts: dict[str, Any], swings: list[dict[str, Any]]) -> dict[str, Any]:
    # Timeline reconstruction is deliberately finite; older bars remain in the
    # fact snapshot for MA200 but cannot make the current event stream unbounded.
    candles = facts["confirmed_bars"][-160:]
    timeframe = facts["timeframe"]
    atr = facts["atr14"]["value"]
    quality = facts["quality"]
    events: list[dict[str, Any]] = []
    selected_range = None
    breakout_index = None
    direction = "NONE"
    breakout_candidate_score = float("-inf")
    if atr and len(candles) >= 21:
        for end in range(20, len(candles)):
            bar = candles[end]
            minimum_window = candles[end-20:end]
            minimum_high = max(row["high"] for row in minimum_window)
            minimum_low = min(row["low"] for row in minimum_window)
            if (bar["close"]-minimum_high)/atr < TIMELINE_PARAMETERS["attempt_distance_atr"] and \
                    (minimum_low-bar["close"])/atr < TIMELINE_PARAMETERS["attempt_distance_atr"]:
                continue
            candidate = detect_range(candles, timeframe, atr, end_index=end)
            if not candidate:
                continue
            up_distance = (bar["close"]-candidate["high"])/atr
            down_distance = (candidate["low"]-bar["close"])/atr
            normalized_slope = abs(candidate["slope"])/candidate["width"] if candidate["width"] else 1.0
            candidate_score = (candidate["upper_touches"]+candidate["lower_touches"]+
                               10*candidate["bars_inside_ratio"]-100*normalized_slope+end*1e-6)
            if up_distance >= TIMELINE_PARAMETERS["attempt_distance_atr"]:
                if candidate_score > breakout_candidate_score:
                    selected_range, breakout_index, direction = candidate, end, "UP"
                    breakout_candidate_score = candidate_score
            elif down_distance >= TIMELINE_PARAMETERS["attempt_distance_atr"]:
                if candidate_score > breakout_candidate_score:
                    selected_range, breakout_index, direction = candidate, end, "DOWN"
                    breakout_candidate_score = candidate_score
            # Keep scanning so the bounded timeline represents the most recent
            # pre-existing structure break, not an older event in the window.
    if selected_range is None:
        selected_range = detect_range(candles, timeframe, atr)
    if selected_range:
        events.append(_event("RANGE_BUILDING", "NONE", timeframe, selected_range["start"],
                             selected_range["end"], selected_range["source_bar_timestamps"],
                             selected_range["range_id"], {"low": selected_range["low"], "high": selected_range["high"]},
                             "bounded ATR-normalized range satisfied touches, reversion, slope and inside-ratio rules",
                             selected_range["confidence"], selected_range["invalidation"],
                             active=breakout_index is None))
    compression = detect_compression(
        candles[:breakout_index] if breakout_index is not None else candles, timeframe,
        facts["volume_regime"]["classification"], selected_range, quality)
    if compression.get("compression_id"):
        ended = candles[breakout_index]["close_time"] if breakout_index is not None else None
        events.append(_event("COMPRESSION", "NONE", timeframe, compression["start"], compression["end"],
                             compression["source_bar_timestamps"], compression["compression_id"],
                             {"low": compression["price_low"], "high": compression["price_high"]},
                             "; ".join(compression["evidence"]), compression["confidence"],
                             "volatility/range/volume contraction conditions no longer hold",
                             ended=ended, active=breakout_index is None, counter=compression["counterevidence"]))
    if selected_range is None or breakout_index is None or atr is None:
        return {"version": AI_MARKET_TIMELINE_VERSION, "timeframe": timeframe,
                "events": events, "current_phase": events[-1]["event_type"] if events else "UNCLASSIFIED",
                "direction": "NONE", "range": selected_range, "compression": compression,
                "breakout": None, "impulse": None, "retest": None}
    boundary = selected_range["high"] if direction == "UP" else selected_range["low"]
    opposite = selected_range["low"] if direction == "UP" else selected_range["high"]
    sign = 1 if direction == "UP" else -1
    breach = candles[breakout_index]
    confidence = "LOW" if quality["gaps"] else "MEDIUM"
    bounds = {"range_low": selected_range["low"], "range_high": selected_range["high"],
              "breakout_boundary": boundary}
    events.append(_event("BREAKOUT_ATTEMPT", direction, timeframe, breach["open_time"], breach["close_time"],
                         [*selected_range["source_bar_timestamps"], breach["close_time"]],
                         selected_range["range_id"], bounds,
                         f"confirmed close exceeded pre-existing boundary by at least {TIMELINE_PARAMETERS['attempt_distance_atr']} ATR",
                         confidence, "confirmed close returns inside prior range", active=True))
    post = candles[breakout_index+1:]
    first_distance = sign*(breach["close"]-boundary)/atr
    volume_ratio = facts["volume_ratio"]["value"] if breakout_index == len(candles)-1 else None
    hold = bool(post and sign*(post[0]["close"]-boundary) > 0)
    strong = first_distance >= TIMELINE_PARAMETERS["strong_confirmation_distance_atr"] and (
        volume_ratio is None or volume_ratio >= TIMELINE_PARAMETERS["confirmation_volume_ratio"])
    confirmed = hold or strong
    if not confirmed:
        events[-1]["active"] = True
        return {"version": AI_MARKET_TIMELINE_VERSION, "timeframe": timeframe, "events": events,
                "current_phase": "BREAKOUT_ATTEMPT", "direction": direction, "range": selected_range,
                "compression": compression, "breakout": {"index": breakout_index, "boundary": boundary,
                "confirmed": False}, "impulse": None, "retest": None}
    confirm_bar = post[0] if hold else breach
    events[-1]["active"] = False
    events[-1]["ended_at"] = confirm_bar["close_time"]
    events.append(_event("BREAKOUT_CONFIRMED", direction, timeframe, breach["open_time"],
                         confirm_bar["close_time"], [breach["close_time"], confirm_bar["close_time"]],
                         selected_range["range_id"], bounds,
                         "boundary hold on a subsequent confirmed close" if hold else "large ATR-normalized close with volume condition",
                         "LOW" if quality["gaps"] else "HIGH", "two confirmed closes back inside range", active=True))
    considered = candles[breakout_index:]
    extreme_row = considered[0]
    extreme_value = extreme_row["high" if direction == "UP" else "low"]
    # Freeze the first impulse extreme when a material confirmed pullback begins.
    # Later highs/lows then qualify as continuation instead of rewriting history.
    for candidate in considered[1:]:
        candidate_extreme = candidate["high" if direction == "UP" else "low"]
        if sign*(candidate_extreme-extreme_value) > 0:
            extreme_value, extreme_row = candidate_extreme, candidate
        excursion_so_far = sign*(extreme_value-boundary)
        retracement_now = sign*(extreme_value-candidate["close"])
        if excursion_so_far > 0 and retracement_now/excursion_so_far >= TIMELINE_PARAMETERS["pullback_excursion_fraction"]:
            break
    excursion = sign*(extreme_value-boundary)
    impulse = {"boundary": boundary, "start": breach["open_time"], "extreme": extreme_value,
               "extreme_time": extreme_row["close_time"], "favorable_excursion": excursion,
               "atr_multiple": excursion/atr, "bars_elapsed": considered.index(extreme_row)+1,
               "volume_expansion": facts["volume_regime"]["classification"] in {"EXPANDING", "CLIMACTIC"}}
    if excursion >= TIMELINE_PARAMETERS["impulse_atr"]*atr:
        events[-1]["active"] = False
        events[-1]["ended_at"] = extreme_row["close_time"]
        events.append(_event("IMPULSE", direction, timeframe, breach["open_time"], extreme_row["close_time"],
                             [row["close_time"] for row in considered[:considered.index(extreme_row)+1]],
                             selected_range["range_id"], {**bounds, "impulse_extreme": extreme_value},
                             f"favorable excursion reached {round(excursion/atr, 4)} ATR", confidence,
                             "confirmed close invalidates breakout boundary", active=True))
    after_extreme = considered[considered.index(extreme_row)+1:]
    pullback = None
    if after_extreme:
        current = after_extreme[-1]
        first_pullback = next((row for row in after_extreme if excursion > 0 and
                               sign*(extreme_value-row["close"])/excursion >=
                               TIMELINE_PARAMETERS["pullback_excursion_fraction"]), None)
        if first_pullback is not None:
            retracement = max(sign*(extreme_value-row["close"]) for row in after_extreme)
            pullback = {"start": first_pullback["open_time"], "retracement": retracement/excursion,
                        "boundary_position_atr": sign*(current["close"]-boundary)/atr,
                        "structure_held": sign*(current["close"]-boundary) >= 0}
            if events:
                events[-1]["active"] = False
                events[-1]["ended_at"] = first_pullback["close_time"]
            events.append(_event("POST_BREAKOUT_PULLBACK", direction, timeframe, first_pullback["open_time"],
                                 current["close_time"], [row["close_time"] for row in after_extreme],
                                 selected_range["range_id"], {**bounds, "impulse_extreme": extreme_value},
                                 "confirmed close retraced at least 20% of breakout excursion", confidence,
                                 "two confirmed closes back inside range", active=True))
    retest = None
    if pullback:
        zone_low, zone_high = boundary-atr*.25, boundary+atr*.25
        touched = [row for row in after_extreme if row["low"] <= zone_high and row["high"] >= zone_low]
        if touched:
            first = touched[0]
            held = sign*(candles[-1]["close"]-boundary) >= 0
            retest = {"zone_low": zone_low, "zone_high": zone_high, "first_entry": first["open_time"],
                      "deepest_retracement": min(row["low"] for row in touched) if direction == "UP" else max(row["high"] for row in touched),
                      "close_held": held, "wick_penetrated": any(row["low"] < boundary for row in touched) if direction == "UP" else any(row["high"] > boundary for row in touched),
                      "rejection_candle": any(sign*(row["close"]-row["open"]) > 0 for row in touched),
                      "confirmed_complete": held}
            events[-1]["active"] = False
            events.append(_event("RETEST", direction, timeframe, first["open_time"], touched[-1]["close_time"],
                                 [row["close_time"] for row in touched], selected_range["range_id"],
                                 {**bounds, "zone_low": zone_low, "zone_high": zone_high},
                                 "confirmed bars entered ATR zone around breakout boundary", confidence,
                                 "confirmed closes remain inside prior range", active=True))
    # Failure requires two confirmed closes inside; a wick never qualifies.
    inside = [row for row in post if selected_range["low"] <= row["close"] <= selected_range["high"]]
    consecutive_inside = next((pair for pair in zip(post, post[1:]) if all(
        selected_range["low"] <= row["close"] <= selected_range["high"] for row in pair)), None)
    if consecutive_inside:
        for event in events:
            event["active"] = False
        failed_at = consecutive_inside[-1]["close_time"]
        events.append(_event("FAILED_BREAKOUT", direction, timeframe, consecutive_inside[0]["open_time"],
                             failed_at, [row["close_time"] for row in consecutive_inside],
                             selected_range["range_id"], bounds, "two confirmed closes returned inside prior range",
                             confidence, f"confirmed reclaim beyond {boundary}", active=True))
        later = [row for row in candles if row["close_time"] > failed_at]
        reverse = next((row for row in later if -sign*(row["close"]-opposite) > 0), None)
        if reverse:
            events[-1]["active"] = False
            events.append(_event("REVERSAL", "DOWN" if direction == "UP" else "UP", timeframe,
                                 reverse["open_time"], reverse["close_time"], [reverse["close_time"]],
                                 selected_range["range_id"], bounds,
                                 "after failed breakout, confirmed close broke the opposite range boundary",
                                 confidence, "confirmed reclaim of opposite boundary", active=True))
    elif retest and retest["confirmed_complete"]:
        last = candles[-1]
        continued = sign*(last["close"]-boundary) >= TIMELINE_PARAMETERS["continuation_distance_atr"]*atr or sign*(last["close"]-extreme_value) > 0
        if continued:
            events[-1]["active"] = False
            events.append(_event("CONTINUATION", direction, timeframe, last["open_time"], last["close_time"],
                                 [last["close_time"]], selected_range["range_id"], bounds,
                                 "confirmed close left retest zone or exceeded impulse extreme", confidence,
                                 "confirmed close returns through breakout boundary", active=True))
    return {"version": AI_MARKET_TIMELINE_VERSION, "timeframe": timeframe, "events": events,
            "current_phase": events[-1]["event_type"], "direction": events[-1]["direction"],
            "range": selected_range, "compression": compression,
            "breakout": {"index": breakout_index, "boundary": boundary, "confirmed": True,
                         "timestamp": breach["close_time"], "confirmation_timestamp": confirm_bar["close_time"]},
            "impulse": impulse, "pullback": pullback, "retest": retest}
