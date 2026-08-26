"""Side-effect-free runtime wiring for the standalone thesis scheduler."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .market_context_v2 import BoundedMarketDataReaderV2
from .thesis_derivatives import CurrentDerivativeReaderV1, DerivativeSnapshotReaderV1
from .thesis_event_engine import ThesisTestServiceV1
from .thesis_event_engine_v2 import ThesisTestServiceV2
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
    v1_test = ThesisTestServiceV1(candle_reader, selection_policy=selection_policy)
    v2_test = ThesisTestServiceV2(candle_reader, selection_policy=selection_policy,
                                  derivative_reader=derivative_reader)

    current_derivative = (CurrentDerivativeReaderV1(microstructure_path, derivative_reader)
                          if derivative_reader is not None else None)
    repository = ThesisTrackingRepositoryV1(tracking_path)
    v1 = ThesisTrackingServiceV1(repository, v1_test, CurrentFeatureEvaluatorV1(candle_reader))
    v2_evaluator = CurrentExpressionEvaluatorV2(
        candle_reader, v2_test.registry, derivative_reader=current_derivative)
    v2 = ThesisTrackingServiceV2(repository, v2_evaluator, v2_test)
    return MixedVersionThesisTrackingService(repository, v1, v2)
