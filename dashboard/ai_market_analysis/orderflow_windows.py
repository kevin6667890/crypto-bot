"""Causal alignment of confirmed price events and order-flow windows."""
from __future__ import annotations

from typing import Any

from .canonical import identity
from .versions import AI_ORDERFLOW_WINDOW_VERSION, TIMEFRAME_SECONDS

PHASE_WINDOWS = ("PRE_STRUCTURE", "BREAKOUT_ATTEMPT", "BREAKOUT_CONFIRMATION", "IMPULSE",
                 "POST_IMPULSE", "PULLBACK", "RETEST", "CURRENT", "FAILED_BREAKOUT", "REVERSAL")
EVENT_PHASE = {"BREAKOUT_ATTEMPT": "BREAKOUT_ATTEMPT", "BREAKOUT_CONFIRMED": "BREAKOUT_CONFIRMATION",
               "IMPULSE": "IMPULSE", "POST_BREAKOUT_PULLBACK": "PULLBACK", "RETEST": "RETEST",
               "FAILED_BREAKOUT": "FAILED_BREAKOUT", "REVERSAL": "REVERSAL"}


def resolve_phase_windows(timeline: dict[str, Any], instrument: str, watermark: int) -> list[dict[str, Any]]:
    """Return [start,end) windows based only on confirmed event timestamps."""
    events = sorted(timeline.get("events", []), key=lambda e: (e["started_at"], e["confirmed_at"], e["event_id"]))
    cutoff = int(watermark)
    width = TIMEFRAME_SECONDS.get(timeline.get("timeframe", "15m"), 900)
    relevant = [e for e in events if e.get("event_type") in EVENT_PHASE and e["started_at"] < cutoff]
    windows: list[dict[str, Any]] = []
    first_break = next((e for e in relevant if e["event_type"] == "BREAKOUT_ATTEMPT"), None)
    if first_break:
        structure = next((e for e in reversed(events) if e["event_type"] in {"RANGE_BUILDING", "COMPRESSION"}
                          and e["started_at"] < first_break["started_at"]), None)
        start = max(structure["started_at"] if structure else first_break["started_at"]-12*width,
                    first_break["started_at"]-48*width)
        windows.append(_window("PRE_STRUCTURE", structure, instrument, timeline["timeframe"], start,
                               first_break["started_at"], width))
    cursor = first_break["started_at"] if first_break else cutoff
    for event in relevant:
        raw_start = int(event["started_at"])
        start = max(int(cursor), raw_start)
        event_end = int(event.get("confirmed_at") or event.get("ended_at") or start+width)
        end = min(cutoff, max(start+width, event_end))
        if end > start:
            windows.append(_window(EVENT_PHASE[event["event_type"]], event, instrument,
                                   timeline["timeframe"], start, end, width))
            cursor = end
        if event["event_type"] == "IMPULSE" and end < cutoff:
            next_event = next((e for e in relevant if int(e["started_at"]) >= end), None)
            next_start = int(next_event["started_at"]) if next_event else end
            if next_start > end:
                windows.append(_window("POST_IMPULSE", event, instrument, timeline["timeframe"], end, next_start, width))
                cursor = next_start
    current_start = max(0, cutoff-2*width)
    if current_start >= cutoff:
        current_start = max(0, cutoff-width)
    windows.append(_window("CURRENT", relevant[-1] if relevant else None, instrument,
                           timeline.get("timeframe", "15m"), current_start, cutoff, width))
    # Exact duplicates can arise when CURRENT equals the active event; retain both semantic views.
    return sorted(windows, key=lambda w: (w["start"], PHASE_WINDOWS.index(w["phase"]), w["phase_id"]))


def _window(phase: str, event: dict[str, Any] | None, instrument: str, timeframe: str,
            start: int, end: int, bucket: int) -> dict[str, Any]:
    stable = {"phase": phase, "event_id": event.get("event_id") if event else None,
              "instrument": instrument, "timeframe": timeframe, "start": start, "end": end,
              "version": AI_ORDERFLOW_WINDOW_VERSION}
    return {"phase_id": identity("phase", stable), **stable, "start_inclusive": True,
            "end_exclusive": True, "duration_seconds": end-start,
            "source_timeline_event": event.get("event_id") if event else None,
            "expected_buckets": max(0, (end-start)//bucket), "actual_buckets": 0,
            "gap_count": 0, "largest_gap_seconds": 0, "quality": "UNKNOWN",
            "window_fingerprint": identity("window", stable)}
