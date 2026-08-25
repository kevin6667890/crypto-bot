"""Versioned initial-forecast cadence for prospective collection.

Phase 1 deliberately permits one formal LLM forecast per market and forecast
methodology.  Forecast updates need a separate series design and are not
silently represented as repeated initial forecasts.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from .evidence import EVIDENCE_POLICY_VERSION
from .models import stable_hash

FORECAST_CADENCE_POLICY_VERSION = "polymarket-initial-forecast-once-v1"


def forecast_cadence_policy() -> dict[str, Any]:
    return {
        "version": FORECAST_CADENCE_POLICY_VERSION,
        "scope": "market_id+forecast_methodology_hash",
        "maximum_initial_forecasts": 1,
        "missed_schedule_backfill": False,
        "updates": "unsupported",
    }


def forecast_cadence_policy_hash() -> str:
    return stable_hash(forecast_cadence_policy())


def forecast_methodology(
    *,
    provider_identity: Mapping[str, Any],
    forecast_schema_version: str,
    prompt_version: str,
    provider_policy_version: str,
    provider_policy_hash: str,
) -> dict[str, Any]:
    """Return the stable methodology identity; never include timestamps/data."""
    return {
        "forecast_schema_version": forecast_schema_version,
        "prompt_version": prompt_version,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "provider": provider_identity.get("provider"),
        "model": provider_identity.get("model"),
        "model_version": provider_identity.get("model_version"),
        "provider_policy_version": provider_policy_version,
        "provider_policy_hash": provider_policy_hash,
    }


def forecast_methodology_hash(**kwargs: Any) -> str:
    return stable_hash(forecast_methodology(**kwargs))


def has_initial_forecast(
    repo: Any,
    market_id: str,
    methodology_hash: str,
    *,
    prompt_version: str,
    provider_identity: Mapping[str, Any],
) -> bool:
    """Restart-safe read guard compatible with old v2 producer identities.

    Historical v2 forecasts predate ``methodology_hash``.  Matching their
    prompt version still prevents a collector upgrade from immediately
    duplicating the same formal initial forecast.
    """
    with repo.connect() as connection:
        rows = connection.execute(
            "SELECT producer_identity_json FROM forecasts WHERE market_id=? AND producer_kind='LLM'",
            (market_id,),
        ).fetchall()
    for row in rows:
        raw = row["producer_identity_json"] if hasattr(row, "keys") else row[0]
        try:
            identity = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if identity.get("methodology_hash") == methodology_hash:
            return True
        if (
            not identity.get("methodology_hash")
            and identity.get("prompt_version") == prompt_version
            and identity.get("provider") == provider_identity.get("provider")
            and identity.get("model") == provider_identity.get("model")
        ):
            return True
    return False
