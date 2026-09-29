"""Strict confirmed-bar alignment; never forward/back fills cross-asset inputs."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CoverageAudit:
    primary_rows: int
    context_rows: int
    aligned_rows: int
    primary_missing_context: int
    context_extra_rows: int
    duplicate_primary_timestamps: int
    duplicate_context_timestamps: int
    timestamp_mismatches: int
    coverage_ratio: float
    status: str
    reason: str | None

    def serialize(self) -> dict: return asdict(self)


def _confirmed(rows: Iterable[Mapping]) -> list[Mapping]:
    return [row for row in rows if bool(row.get("confirmed", 1))]


def audit_confirmed_alignment(primary: Iterable[Mapping], context: Iterable[Mapping], *,
                              minimum_coverage: float = .995) -> CoverageAudit:
    """Audit exact timestamp intersection; neither side is filled or shifted."""
    left, right = _confirmed(primary), _confirmed(context)
    left_ts, right_ts = [int(row["ts"]) for row in left], [int(row["ts"]) for row in right]
    left_set, right_set = set(left_ts), set(right_ts)
    aligned = left_set & right_set
    duplicates_left = sum(count - 1 for count in Counter(left_ts).values() if count > 1)
    duplicates_right = sum(count - 1 for count in Counter(right_ts).values() if count > 1)
    missing = len(left_set - right_set)
    extra = len(right_set - left_set)
    coverage = len(aligned) / len(left_set) if left_set else 0.0
    valid = bool(left_set and right_set and not duplicates_left and not duplicates_right and coverage >= minimum_coverage)
    reason = None if valid else ("EMPTY_CONFIRMED_SERIES" if not left_set or not right_set else
                                "DUPLICATE_TIMESTAMP" if duplicates_left or duplicates_right else
                                "INSUFFICIENT_ALIGNMENT_COVERAGE")
    return CoverageAudit(len(left), len(right), len(aligned), missing, extra, duplicates_left,
                         duplicates_right, missing + extra, coverage, "COMPLETE" if valid else "UNAVAILABLE", reason)


def align_confirmed(primary, context):
    """Return only same-timestamp confirmed bars, with no possible future value."""
    by_ts={int(x['ts']):x for x in _confirmed(context)}; out=[]
    for row in _confirmed(primary):
        ts=int(row['ts'])
        if ts in by_ts: out.append((row,by_ts[ts]))
    return out
