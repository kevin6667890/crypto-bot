"""Side-effect-free runtime wiring for the standalone thesis scheduler."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .market_context_v2 import BoundedMarketDataReaderV2
from .thesis_derivatives import CurrentDerivativeReaderV1, DerivativeSnapshotReaderV1
from .thesis_event_engine import ThesisTestServiceV1
from .thesis_event_engine_v2 import ThesisTestServiceV2, thesis_capabilities_v2
from .thesis_historical_data import HistoricalDataSelectionPolicyV1, HistoricalStoreV1
from .thesis_tracking import CurrentFeatureEvaluatorV1, ThesisTrackingRepositoryV1, ThesisTrackingServiceV1
from .thesis_tracking_v2 import CurrentExpressionEvaluatorV2, MixedVersionThesisTrackingService, ThesisTrackingServiceV2


def _verified_derivative_reader() -> DerivativeSnapshotReaderV1 | None:
    path = os.getenv("THESIS_DERIVATIVES_DB_PATH")
    expected_sha = os.getenv("THESIS_DERIVATIVES_DB_SHA256")
    dataset_id = os.getenv("THESIS_DERIVATIVES_DATASET_ID")
    manifest_path = os.getenv("THESIS_DERIVATIVES_MANIFEST_PATH")
    if not all((path, expected_sha, dataset_id, manifest_path)):
        return None


def _derivative_readiness(reader: DerivativeSnapshotReaderV1 | None) -> dict[str, dict[str, Any]]:
    if reader is None:
        return {group: {"status": "BLOCKED", "reason": "DERIVATIVE_SNAPSHOT_NOT_VERIFIED"}
                for group in ("OI", "FUNDING", "BASIS")}
    coverage = reader.readiness().get("coverage", {})
    mapping = {"OI": "OPEN_INTEREST_USD", "FUNDING": "FUNDING_RATE", "BASIS": "BASIS_PCT"}
    expected = {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"}
    minimum_rows = {"OI": 180, "FUNDING": 540, "BASIS": 4_320}
    maximum_cadence = {"OI": 90_000_000, "FUNDING": 30_000_000, "BASIS": 3_900_000}
    maximum_gap = {"OI": 2 * 86_400_000, "FUNDING": 16 * 3_600_000,
                   "BASIS": 2 * 3_600_000}
    output: dict[str, dict[str, Any]] = {}
    for group, data_type in mapping.items():
        item = coverage.get(data_type) if isinstance(coverage, dict) else None
        start, end = ((item or {}).get("start_ms"), (item or {}).get("end_ms"))
        rows = int((item or {}).get("rows") or 0) if isinstance(item, dict) else 0
        cadence = int((item or {}).get("cadence_ms") or 0) if isinstance(item, dict) else 0
        gap = int((item or {}).get("max_gap_ms") or 0) if isinstance(item, dict) else 0
        instruments = {part.get("instrument") for part in (item or {}).get("instruments", [])
                       if isinstance(part, dict)}
        span_days = ((int(end) - int(start)) // 86_400_000
                     if isinstance(start, int) and isinstance(end, int) else 0)
        ready = (span_days >= 180 and instruments == expected and rows >= minimum_rows[group]
                 and 0 < cadence <= maximum_cadence[group] and 0 < gap <= maximum_gap[group])
        supported = (["1D"] if group == "OI" and cadence >= 86_400_000
                     else ["15m", "1H", "4H", "1D"])
        output[group] = {
            "status": "READY" if ready else "LIMITED",
            "reason": None if ready else "DERIVATIVE_COVERAGE_OR_DENSITY_NOT_QUALIFIED",
            "historical_range": {"start_ms": start, "end_ms": end},
            "span_days": span_days, "rows": rows, "supported_timeframes": supported,
        }
    return output
    try:
        manifest: dict[str, Any] = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        reader = DerivativeSnapshotReaderV1(
            Path(path), expected_sha256=expected_sha, dataset_id=dataset_id, manifest=manifest)
        return reader if reader.readiness().get("status") == "READY" else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def build_tracking_service_from_environment() -> MixedVersionThesisTrackingService:
    """Build only the audited tracking dependencies, without importing the API app."""
    paper_path = Path(os.environ["PAPER_DB_PATH"])
    microstructure_path = Path(os.environ["MICROSTRUCTURE_DB_PATH"])
    tracking_path = Path(os.getenv("THESIS_TRACKING_DB_PATH", paper_path.with_name("thesis_tracking.db")))
    candle_reader = BoundedMarketDataReaderV2(paper_path, microstructure_path)

    stores: list[HistoricalStoreV1] = []
    historical_path = os.getenv("THESIS_HISTORICAL_DB_PATH")
    if historical_path:
        stores.append(HistoricalStoreV1(
            Path(historical_path), "frozen_research", 0,
            os.getenv("THESIS_HISTORICAL_DB_SHA256") or None,
            os.getenv("THESIS_HISTORICAL_DATASET_ID") or None,
        ))
    stores.append(HistoricalStoreV1(paper_path, "current_canonical", 100))
    selection_policy = HistoricalDataSelectionPolicyV1(stores)
    derivative_reader = _verified_derivative_reader()
    current_derivative = (CurrentDerivativeReaderV1(microstructure_path, derivative_reader)
                          if derivative_reader is not None else None)
    current_readiness = (current_derivative.readiness() if current_derivative is not None else
                         {"status": "BLOCKED", "reason": "CURRENT_DERIVATIVE_ADAPTER_NOT_CONFIGURED",
                          "supported_timeframes": ["1D"]})
    derivative_readiness = _derivative_readiness(derivative_reader)
    capabilities = thesis_capabilities_v2({
        **derivative_readiness, "OI_CURRENT": current_readiness,
        **{f"{group}_CURRENT": {"status": "BLOCKED",
                                  "reason": "CURRENT_DERIVATIVE_ADAPTER_NOT_CONFIGURED"}
           for group in ("FUNDING", "BASIS")},
    })
    v1_test = ThesisTestServiceV1(candle_reader, selection_policy=selection_policy)
    v2_test = ThesisTestServiceV2(candle_reader, selection_policy=selection_policy,
                                  derivative_reader=derivative_reader, capabilities=capabilities)

    repository = ThesisTrackingRepositoryV1(tracking_path)
    v1 = ThesisTrackingServiceV1(repository, v1_test, CurrentFeatureEvaluatorV1(candle_reader))
    v2_evaluator = CurrentExpressionEvaluatorV2(
        candle_reader, v2_test.registry, derivative_reader=current_derivative)
    v2 = ThesisTrackingServiceV2(repository, v2_evaluator, v2_test)
    return MixedVersionThesisTrackingService(repository, v1, v2)
