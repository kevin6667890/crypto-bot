"""Evidence gates that never infer history from a point-in-time sample."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


REQUIRED_EVIDENCE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class EvidenceGate:
    covered_seconds: int
    sample_count: int
    deployment_allowed: bool
    reason: str | None


def evidence_gate(points: Sequence[Mapping[str, Any]]) -> EvidenceGate:
    timestamps = sorted(
        int(point["timestamp"])
        for point in points
        if isinstance(point.get("timestamp"), (int, float))
    )
    covered = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0
    complete = covered >= REQUIRED_EVIDENCE_SECONDS
    return EvidenceGate(
        covered_seconds=covered,
        sample_count=len(timestamps),
        deployment_allowed=complete,
        reason=None if complete else "INSUFFICIENT_24H_PRODUCTION_EVIDENCE",
    )


def classify_stability(
    points: Sequence[Mapping[str, Any]], *, restart_counts: Sequence[int]
) -> str:
    """Classify only a complete evidence window; incomplete history is UNKNOWN."""
    gate = evidence_gate(points)
    if not gate.deployment_allowed:
        return "UNKNOWN_INSUFFICIENT_HISTORY"
    if any(value != 0 for value in restart_counts):
        return "UNSTABLE"
    critical = any((point.get("critical_gap_count") or 0) > 0 for point in points)
    blocked = any(bool(point.get("blocked_task")) for point in points)
    queue_growth = (
        len(points) >= 2
        and (points[-1].get("queue_depth") or 0)
        > max(100, (points[0].get("queue_depth") or 0) * 2)
    )
    if critical or blocked or queue_growth:
        return "UNSTABLE"
    minor = any(
        point.get("service_state")
        not in {"RUNNING", "HEALTHY"}
        for point in points
    )
    return "STABLE_WITH_MINOR_ISSUES" if minor else "STABLE"
