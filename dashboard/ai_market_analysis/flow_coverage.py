"""Deterministic, gap-aware order-flow coverage classification."""
from __future__ import annotations

FLOW_COVERAGE_POLICY_VERSION = "flow-coverage-v1"
FLOW_COVERAGE_POLICY = {
    "minimum_complete_ratio": 0.995, "minimum_partial_ratio": 0.90,
    "partial_max_gap_minutes": 2, "partial_max_consecutive_gap_minutes": 1,
    "partial_recent_gap_age_minutes": 5,
}


def classify_flow_coverage(*, snapshot_start: int, snapshot_end: int,
                           bucket_seconds: int, timestamps: list[int],
                           explicit_gap_timestamps: list[int] | None = None) -> dict:
    """Classify real canonical coverage; never fills or interpolates a gap."""
    expected = list(range(snapshot_start, snapshot_end, bucket_seconds))
    observed = {int(value) for value in timestamps if snapshot_start <= int(value) < snapshot_end}
    explicit = {int(value) for value in (explicit_gap_timestamps or []) if snapshot_start <= int(value) < snapshot_end}
    missing = sorted((set(expected) - observed) | explicit)
    runs: list[list[int]] = []
    for timestamp in missing:
        if not runs or timestamp != runs[-1][-1] + bucket_seconds: runs.append([timestamp])
        else: runs[-1].append(timestamp)
    total_gap_seconds = len(missing) * bucket_seconds
    max_consecutive_seconds = max((len(run) * bucket_seconds for run in runs), default=0)
    coverage_ratio = round((len(expected) - len(missing)) / len(expected), 4) if expected else 0.0
    recent_gap_age_seconds = snapshot_end - max(missing) if missing else None
    p = FLOW_COVERAGE_POLICY
    if not missing and coverage_ratio >= p["minimum_complete_ratio"]:
        state = "FLOW_COMPLETE"
    elif (coverage_ratio >= p["minimum_partial_ratio"] and total_gap_seconds <= p["partial_max_gap_minutes"] * 60
          and max_consecutive_seconds <= p["partial_max_consecutive_gap_minutes"] * 60
          and (recent_gap_age_seconds is None or recent_gap_age_seconds > p["partial_recent_gap_age_minutes"] * 60)):
        state = "FLOW_PARTIAL_USABLE"
    else:
        state = "FLOW_UNAVAILABLE"
    return {"policy_version": FLOW_COVERAGE_POLICY_VERSION, "state": state,
            "snapshot_window_seconds": max(0, snapshot_end - snapshot_start),
            "expected_bucket_count": len(expected), "actual_bucket_count": len(observed),
            "coverage_ratio": float(coverage_ratio), "gap_count": len(runs),
            "total_gap_minutes": round(total_gap_seconds / 60, 3),
            "max_consecutive_gap_minutes": round(max_consecutive_seconds / 60, 3),
            "recent_gap_age_minutes": round(recent_gap_age_seconds / 60, 3) if recent_gap_age_seconds is not None else None,
            "gap_runs": [{"start": run[0], "end": run[-1] + bucket_seconds, "minutes": round(len(run) * bucket_seconds / 60, 3)} for run in runs],
            "synthetic_data": False, "interpolation": False}
