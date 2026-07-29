"""Translate current production summary payloads without scanning raw tables."""

from __future__ import annotations

from typing import Any, Mapping


UNKNOWN = "UNKNOWN"
FEATURES = {
    "CVD": "cvd",
    "OI": "oi",
    "CVD+OI": "cvd_oi",
    "funding+OI": "funding_oi",
    "basis+OI": "basis_oi",
}
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")


def _value(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return UNKNOWN


def evaluate_production_summary(
    eligibility: Mapping[str, Any],
    health: Mapping[str, Any],
    *,
    source_kind: str = "CURRENT_PRODUCTION_SUMMARY",
) -> dict[str, Any]:
    """Use persisted aggregates only; unavailable acceptance fields stay UNKNOWN."""
    if source_kind != "CURRENT_PRODUCTION_SUMMARY":
        return {
            "source_kind": source_kind,
            "status": "STALE_SOURCE_NOT_PRODUCTION_CONCLUSION",
            "results": [],
            "raw_table_scans": 0,
        }
    groups = eligibility.get("feature_groups")
    groups = groups if isinstance(groups, Mapping) else {}
    critical = (
        (health.get("gap_summary") or {}).get("critical_live_gaps")
        if isinstance(health.get("gap_summary"), Mapping)
        else None
    )
    critical_count = len(critical) if isinstance(critical, list) else UNKNOWN
    results: list[dict[str, Any]] = []
    for label, key in FEATURES.items():
        group = groups.get(key)
        group = group if isinstance(group, Mapping) else {}
        instruments = group.get("instruments")
        instruments = instruments if isinstance(instruments, Mapping) else {}
        for instrument in INSTRUMENTS:
            row = instruments.get(instrument)
            row = row if isinstance(row, Mapping) else {}
            results.append(
                {
                    "instrument": instrument,
                    "feature": label,
                    "source_freshness": _value(
                        row, "source_latest_ms", "label_latest_ms"
                    ),
                    "natural_days": _value(row, "source_days"),
                    "usable_days": _value(
                        row, "gap_adjusted_usable_days", "overlap_usable_days"
                    ),
                    "longest_continuous_days": _value(
                        row, "max_continuous_usable_days"
                    ),
                    "coverage_30d": _value(row, "recent_30d_coverage"),
                    "independent_events": _value(
                        row, "event_count", "source_observation_count"
                    ),
                    "non_overlap_labels": _value(
                        row, "non_overlapping_label_count"
                    ),
                    "critical_gap": critical_count,
                    "readiness_status": _value(
                        row, "event_study_status", "source_data_status"
                    ),
                    "blocker": _value(row, "blocking_reason"),
                }
            )
    return {
        "source_kind": source_kind,
        "status": "EVALUATED_FROM_CURRENT_SUMMARY",
        "results": results,
        "raw_table_scans": 0,
        "research_jobs_created": 0,
        "factor_generation_calls": 0,
        "strategy_or_order_api_calls": 0,
    }


def engine_acceptance_assets_available(
    manifest: str | None, ledger: str | None
) -> bool:
    """A ledger or narrative report never substitutes for a task manifest."""
    from pathlib import Path

    return bool(
        manifest
        and ledger
        and Path(manifest).is_file()
        and Path(ledger).is_file()
    )
