from __future__ import annotations

import inspect
import json
from pathlib import Path

from dashboard.production_acceptance import (
    classify_stability,
    evidence_gate,
)
from dashboard.production_readiness_summary import (
    engine_acceptance_assets_available,
    evaluate_production_summary,
)


def test_insufficient_24h_evidence_never_allows_deployment() -> None:
    points = [
        {"timestamp": 100, "service_state": "RUNNING"},
        {"timestamp": 100 + 23 * 3600, "service_state": "RUNNING"},
    ]
    gate = evidence_gate(points)
    assert gate.deployment_allowed is False
    assert gate.reason == "INSUFFICIENT_24H_PRODUCTION_EVIDENCE"
    assert classify_stability(points, restart_counts=[0, 0, 0]) == (
        "UNKNOWN_INSUFFICIENT_HISTORY"
    )


def test_old_backup_is_not_interpreted_as_production_outage() -> None:
    result = evaluate_production_summary(
        {}, {}, source_kind="OFFLINE_BACKUP"
    )
    assert result["status"] == "STALE_SOURCE_NOT_PRODUCTION_CONCLUSION"
    assert result["results"] == []


def test_current_summary_evaluation_has_no_raw_scan() -> None:
    eligibility = {
        "feature_groups": {
            "cvd": {
                "instruments": {
                    "BTC-USDT-SWAP": {
                        "source_days": 9.2,
                        "gap_adjusted_usable_days": 5.0,
                        "event_count": 480,
                        "event_study_status": "EXPLORATORY_ONLY",
                        "blocking_reason": "Source coverage is below 14 days",
                    }
                }
            }
        }
    }
    result = evaluate_production_summary(
        eligibility, {"gap_summary": {"critical_live_gaps": []}}
    )
    row = next(
        item
        for item in result["results"]
        if item["instrument"] == "BTC-USDT-SWAP" and item["feature"] == "CVD"
    )
    assert row["natural_days"] == 9.2
    assert row["longest_continuous_days"] == "UNKNOWN"
    assert result["raw_table_scans"] == 0
    assert result["research_jobs_created"] == 0


def test_missing_real_manifest_blocks_engine_acceptance(tmp_path: Path) -> None:
    ledger = tmp_path / "real-ledger.db"
    ledger.write_bytes(b"real ledger marker")
    assert engine_acceptance_assets_available(None, str(ledger)) is False
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    assert engine_acceptance_assets_available(str(manifest), str(ledger)) is True


def test_readiness_summary_never_calls_strategy_or_order_api() -> None:
    result = evaluate_production_summary({}, {})
    assert result["strategy_or_order_api_calls"] == 0
    source = inspect.getsource(evaluate_production_summary)
    assert "urlopen(" not in source
    assert "requests." not in source
