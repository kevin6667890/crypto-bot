"""Fail-closed resolution classification from frozen public Gamma metadata.

The classifier deliberately distinguishes "not settled yet" from terminal states
that cannot be scored.  Only ``VALID_BINARY`` carries an outcome usable by the
scorer.
"""
from __future__ import annotations

from typing import Any
from decimal import Decimal, InvalidOperation

from .client import PolymarketClient
from .models import parse_json_field


def classify_resolution(market: dict[str, Any]) -> dict[str, Any]:
    """Return only a verified binary settlement; never infer an outcome."""
    if not isinstance(market, dict):
        return {"classification": "INVALID", "outcome": None, "resolved_at": None}

    # Gamma data seen in the wild uses several terminal-status spellings.  These
    # states must never be interpreted from prices as a YES/NO settlement.
    status = str(market.get("status") or market.get("resolutionStatus") or "").strip().lower()
    resolution = str(market.get("resolution") or "").strip().lower()
    if market.get("cancelled") is True or status in {"cancelled", "canceled", "voided", "void"} or resolution in {"cancelled", "canceled", "voided", "void"}:
        return {"classification": "CANCELLED", "outcome": None, "resolved_at": market.get("resolvedAt") or market.get("closedTime")}
    if status in {"invalid", "invalidated"} or resolution in {"invalid", "invalidated"}:
        return {"classification": "INVALID", "outcome": None, "resolved_at": market.get("resolvedAt") or market.get("closedTime")}
    if status in {"unknown", "unsupported"} or resolution in {"unknown", "unsupported"}:
        return {"classification": "UNKNOWN", "outcome": None, "resolved_at": market.get("resolvedAt") or market.get("closedTime")}
    if resolution in {"50/50", "50-50", "0.5/0.5"}:
        return {"classification": "AMBIGUOUS_50_50", "outcome": None, "resolved_at": market.get("resolvedAt") or market.get("closedTime")}

    closed = market.get("closed") is True or market.get("resolved") is True
    if not closed:
        return {"classification": "UNRESOLVED", "outcome": None, "resolved_at": None}
    try:
        outcomes = parse_json_field(market.get("outcomes"), "outcomes")
        prices = parse_json_field(market.get("outcomePrices"), "outcomePrices")
    except ValueError:
        return {"classification": "INVALID", "outcome": None, "resolved_at": None}
    if not isinstance(outcomes, list) or not isinstance(prices, list):
        return {"classification": "INVALID", "outcome": None, "resolved_at": None}
    if len(outcomes) != 2 or len(prices) != 2:
        return {"classification": "AMBIGUOUS", "outcome": None, "resolved_at": None}
    try:
        parsed = {str(label).strip().upper(): Decimal(str(price).strip()) for label, price in zip(outcomes, prices)}
    except (InvalidOperation, ValueError):
        return {"classification": "INVALID", "outcome": None, "resolved_at": None}
    if len(parsed) != 2 or set(parsed) != {"YES", "NO"}:
        return {"classification": "AMBIGUOUS", "outcome": None, "resolved_at": None}
    if parsed == {"YES": Decimal("1"), "NO": Decimal("0")}:
        outcome = 1
    elif parsed == {"YES": Decimal("0"), "NO": Decimal("1")}:
        outcome = 0
    elif parsed == {"YES": Decimal("0.5"), "NO": Decimal("0.5")}:
        return {"classification": "AMBIGUOUS_50_50", "outcome": None, "resolved_at": None}
    else:
        return {"classification": "AMBIGUOUS", "outcome": None, "resolved_at": None}
    resolved_at = market.get("resolvedAt") or market.get("closedTime") or market.get("endDate")
    return {"classification": "VALID_BINARY", "outcome": outcome, "resolved_at": resolved_at}


def resolve_market(repo: Any, client: PolymarketClient, market_id: str) -> dict[str, Any]:
    payload = client.fetch_market(market_id)
    result = classify_resolution(payload)
    stored = repo.append_resolution(market_id, payload, **result)
    return {key: stored.get(key) for key in ("resolution_id", "market_id", "revision", "classification", "outcome_value")}
