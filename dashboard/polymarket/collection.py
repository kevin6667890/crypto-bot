"""Dependency-injected forecast collection orchestration.

Universe freezing, repository schema and CLI wiring remain outside this module.
The runner is intentionally small so the production CLI and tests exercise the
same deterministic selection/skip semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class ForecastCandidate:
    market_id: str
    market_snapshot_id: str
    eligibility_decision_id: str


def collect_forecast_batch(
    candidates: Sequence[ForecastCandidate],
    *,
    max_forecasts: int,
    provider_ready: bool,
    already_forecast: Callable[[ForecastCandidate], bool],
    retrieve_evidence: Callable[[ForecastCandidate], Sequence[str]],
    commit_forecast: Callable[[ForecastCandidate, Sequence[str]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run one bounded, deterministic initial-forecast pass.

    ``max_forecasts`` bounds selected markets, not retries.  The provider owns
    its separately bounded two-attempt format policy.  Market price is absent
    from every callback input; reveal remains a post-commit responsibility of
    ``commit_forecast``.
    """
    if max_forecasts < 1:
        raise ValueError("max_forecasts must be positive")
    ordered = sorted(candidates, key=lambda item: item.market_id)
    selected = ordered[:max_forecasts]
    result: dict[str, Any] = {
        "selected": [item.market_id for item in selected],
        "attempted": 0,
        "successful": [],
        "failed": [],
        "skipped_existing_initial": [],
        "skipped_insufficient_evidence": [],
        "skipped_provider_not_ready": [],
    }
    for item in selected:
        if already_forecast(item):
            result["skipped_existing_initial"].append(item.market_id)
            continue
        if not provider_ready:
            result["skipped_provider_not_ready"].append(item.market_id)
            continue
        evidence_ids = list(retrieve_evidence(item))[:3]
        if not evidence_ids:
            result["skipped_insufficient_evidence"].append(item.market_id)
            continue
        result["attempted"] += 1
        try:
            committed = dict(commit_forecast(item, evidence_ids))
        except Exception as exc:
            result["failed"].append({"market_id": item.market_id, "failure_code": getattr(exc, "code", type(exc).__name__)})
        else:
            result["successful"].append(committed)
    return result
