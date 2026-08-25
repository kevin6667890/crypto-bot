"""Cohort-level Polymarket research statistics.

This module is deliberately independent of crypto metrics.  It accepts frozen
records, so callers can use it with SQLite rows, exports, or synthetic fixtures.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def cohort_statistics(*, forecasts: Iterable[Mapping[str, Any]],
                      scores: Iterable[Mapping[str, Any]],
                      llm_attempts: Iterable[Mapping[str, Any]] = (),
                      evidence_attempts: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Summarize only formal LLM forecasts; manual records are excluded."""
    forecast_rows = [_dict(row) for row in forecasts]
    ai = [row for row in forecast_rows if str(row.get("producer_kind")) == "LLM"]
    ai_ids = {str(row["forecast_id"]) for row in ai}
    score_rows = [_dict(row) for row in scores]
    scored = [row for row in score_rows if str(row.get("forecast_id")) in ai_ids]
    scored_ids = {str(row["forecast_id"]) for row in scored}
    explicitly_resolved = {
        str(row["forecast_id"]) for row in ai
        if row.get("resolution_classification") == "VALID_BINARY"
    }
    resolved_ids = explicitly_resolved or scored_ids

    def nums(field: str) -> list[float]:
        return [float(row[field]) for row in scored if row.get(field) is not None]

    brier_delta = nums("brier_delta")
    log_delta = nums("log_loss_delta")
    attempts = [_dict(row) for row in llm_attempts]
    evidence = [_dict(row) for row in evidence_attempts]
    trades = [row for row in scored if row.get("executable_side") in {"YES", "NO"}]
    no_trades = [row for row in scored if row.get("executable_side") == "NO_TRADE"]
    known_net = [float(row["executable_net_pnl"]) for row in trades if row.get("executable_net_pnl") is not None]

    calibration = []
    for low_pct in range(0, 100, 10):
        low, high = low_pct / 100, (low_pct + 10) / 100
        bucket = [row for row in scored if low <= float(row["forecast_probability"]) < high]
        calibration.append({
            "bin": f"{low_pct}-{low_pct + 10}%",
            "forecasts": len(bucket),
            "actual_yes_rate": _mean([float(row["outcome_value"]) for row in bucket]),
            "mean_predicted_probability": _mean([float(row["forecast_probability"]) for row in bucket]),
        })

    clusters = {
        str(row.get("statistical_cluster_id") or row.get("market_id"))
        for row in ai if str(row["forecast_id"]) in resolved_ids
    }
    admitted = sum(str(row.get("status")) in {"ACCEPTED", "SUCCEEDED"} for row in evidence)
    return {
        "forecast_count": len(ai),
        "resolved_count": len(resolved_ids),
        "unresolved_count": len(ai_ids - resolved_ids),
        "scored_count": len(scored),
        "mean_brier_model": _mean(nums("forecast_brier")),
        "mean_brier_market": _mean(nums("market_brier")),
        "mean_delta_brier": _mean(brier_delta),
        "mean_logloss_model": _mean(nums("forecast_log_loss")),
        "mean_logloss_market": _mean(nums("market_log_loss")),
        "mean_delta_logloss": _mean(log_delta),
        "wins_vs_market": sum(value > 0 for value in brier_delta),
        "losses_vs_market": sum(value < 0 for value in brier_delta),
        "ties": sum(value == 0 for value in brier_delta),
        "LLM_attempt_success_rate": (sum(row.get("status") == "SUCCEEDED" for row in attempts) / len(attempts)) if attempts else None,
        "evidence_admission_rate": admitted / len(evidence) if evidence else None,
        "trade_count": len(trades),
        "no_trade_count": len(no_trades),
        "gross_pnl": sum(float(row["executable_gross_pnl"]) for row in trades if row.get("executable_gross_pnl") is not None),
        "known_fee_net_pnl": sum(known_net) if known_net else None,
        "unknown_fee_count": sum(row.get("fee_status") == "UNKNOWN" for row in trades),
        "raw_resolved_forecasts": len(resolved_ids),
        "unique_statistical_clusters": len(clusters),
        "calibration_bins": calibration,
    }


def statistics_from_connection(connection: Any) -> dict[str, Any]:
    """Load the immutable ledgers and include snapshot cluster identity."""
    forecasts = connection.execute("""
        SELECT f.*, s.statistical_cluster_id,
               (SELECT r.classification FROM resolutions r
                WHERE r.market_id=f.market_id ORDER BY r.revision DESC LIMIT 1)
               AS resolution_classification
        FROM forecasts f JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id
    """).fetchall()
    # Only the score attached to the latest resolution revision is an active
    # observation.  Older score rows remain immutable audit history.
    scores = connection.execute("""
        SELECT sc.* FROM scores sc
        JOIN resolutions r ON r.resolution_id=sc.resolution_identity
        WHERE r.revision=(SELECT MAX(r2.revision) FROM resolutions r2 WHERE r2.market_id=r.market_id)
    """).fetchall()
    return cohort_statistics(
        forecasts=forecasts,
        scores=scores,
        llm_attempts=connection.execute("SELECT * FROM llm_forecast_attempts").fetchall(),
        evidence_attempts=connection.execute("SELECT * FROM evidence_retrieval_attempts").fetchall(),
    )
