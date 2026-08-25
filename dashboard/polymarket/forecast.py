"""Manual/mock forecast validation and immutable commit orchestration."""
from __future__ import annotations

from typing import Sequence

from .models import stable_hash, utc_now
from .repository import PolymarketRepository

FORECAST_SCHEMA_VERSION = "polymarket-forecast-v1"


def commit_manual_forecast(repo: PolymarketRepository, market_id: str, probability: float, producer: str, rationale: str, evidence_ids: Sequence[str] = ()) -> str:
    probability = float(probability)
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    if producer not in {"MANUAL", "MOCK"}:
        raise ValueError("Phase 1A producer must be MANUAL or MOCK")
    decision = repo.latest_eligible(market_id)
    if not decision:
        raise ValueError("market has no eligible snapshot")
    now = utc_now()
    config = {"forecast_schema_version": FORECAST_SCHEMA_VERSION, "producer_kind": producer}
    return repo.insert_forecast({"market_id": market_id, "market_snapshot_id": decision["market_snapshot_id"], "eligibility_decision_id": decision["decision_id"], "forecasted_at": now, "evidence_cutoff_at": now, "forecast_schema_version": FORECAST_SCHEMA_VERSION, "producer_kind": producer, "producer_identity": {"kind": producer, "version": "phase1a"}, "config_hash": stable_hash(config), "probability": probability, "rationale": rationale or "manual forecast", "committed_at": now}, evidence_ids)
