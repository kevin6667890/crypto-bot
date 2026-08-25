"""Conservative deterministic eligibility policy for point-in-time research."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .models import parse_json_field, stable_hash

POLICY_VERSION = "polymarket-eligibility-v1"
POLICY_CONFIG = {
    "maximum_spread": "0.10",
    "minimum_crypto_horizon_hours": 24,
    "sports_keywords": ["nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis", "baseball", "basketball", "hockey", "ufc", "fifa"],
    "crypto_keywords": ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"],
}


def policy_hash() -> str:
    return stable_hash({"version": POLICY_VERSION, "config": POLICY_CONFIG})


def evaluate(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return stable reasons sorted by code. Missing data always rejects."""
    market = snapshot.get("market") if isinstance(snapshot.get("market"), dict) else {}
    quotes = snapshot.get("quotes") if isinstance(snapshot.get("quotes"), dict) else {}
    mapping = snapshot.get("token_mapping") if isinstance(snapshot.get("token_mapping"), dict) else {}
    reasons: list[str] = []
    outcomes = snapshot.get("outcomes")
    if outcomes != ["YES", "NO"]:
        reasons.append("NOT_BINARY_YES_NO")
    if market.get("active") is not True or market.get("closed") is True:
        reasons.append("NOT_ACTIVE_OPEN")
    if not snapshot.get("end_date"):
        reasons.append("MISSING_END_DATE")
    if not snapshot.get("resolution_rule_text"):
        reasons.append("MISSING_RESOLUTION_RULES")
    if set(mapping) != {"YES", "NO"}:
        reasons.append("AMBIGUOUS_TOKEN_MAPPING")
    if market.get("negRisk") is True or market.get("negRiskMarketID") not in (None, "", "0"):
        reasons.append("NEGRISK_OR_COMPLEX_MARKET")
    question = str(market.get("question") or "").lower()
    category_text = " ".join(str(x).lower() for x in (market.get("tags") or []) if isinstance(x, str))
    searchable = f"{question} {category_text}"
    if any(word in searchable for word in POLICY_CONFIG["sports_keywords"]):
        reasons.append("OBVIOUS_LIVE_SPORTS")
    try:
        end = datetime.fromisoformat(str(snapshot.get("end_date")).replace("Z", "+00:00"))
        captured = datetime.fromisoformat(str(snapshot.get("captured_at")).replace("Z", "+00:00"))
        if any(word in searchable for word in POLICY_CONFIG["crypto_keywords"]) and (end - captured).total_seconds() < POLICY_CONFIG["minimum_crypto_horizon_hours"] * 3600:
            reasons.append("ULTRA_SHORT_CRYPTO")
    except (TypeError, ValueError):
        if snapshot.get("end_date"):
            reasons.append("INVALID_END_DATE")
    for side in ("YES", "NO"):
        quote = quotes.get(side) if isinstance(quotes.get(side), dict) else {}
        if not quote.get("best_bid") or not quote.get("best_ask"):
            reasons.append(f"{side}_ORDERBOOK_UNAVAILABLE")
            continue
        try:
            spread = Decimal(str(quote["best_ask"])) - Decimal(str(quote["best_bid"]))
            if spread < 0 or spread > Decimal(POLICY_CONFIG["maximum_spread"]):
                reasons.append(f"{side}_SPREAD_OUT_OF_POLICY")
        except Exception:
            reasons.append(f"{side}_ORDERBOOK_UNAVAILABLE")
    reasons = sorted(set(reasons))
    return {"eligible": not reasons, "policy_version": POLICY_VERSION, "policy_hash": policy_hash(), "reasons": reasons}


POLICY_V2_VERSION = "polymarket-eligibility-v2.2"
POLICY_V2_CONFIG = {
    **POLICY_CONFIG,
    "negrisk": "metadata_only; exact standalone binary contract required",
    "condition_id": "required and non-empty",
    "unsupported_contract_types": ["categorical", "multi", "range", "scalar"],
    "dependent_contract_flags": [
        "requiresSiblingMarkets",
        "requires_sibling_markets",
        "resolutionRequiresEvent",
        "resolution_requires_event",
    ],
    "live_sports_markers": ["in-play", "in play", "live game", "live match"],
    "minimum_gamma_liquidity_usd": "1000",
    "end_date_must_be_future": True,
    "explicit_orderbook_flags": "reject_only_when_false",
}


def policy_v2_hash() -> str:
    return stable_hash({"version": POLICY_V2_VERSION, "config": POLICY_V2_CONFIG})


def _as_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _market_rule(market: dict[str, Any]) -> str:
    return next(
        (
            market[key].strip()
            for key in ("resolutionCriteria", "resolutionRule", "rules", "description")
            if isinstance(market.get(key), str) and market[key].strip()
        ),
        "",
    )


def _binary_metadata_reasons(market: dict[str, Any]) -> list[str]:
    try:
        outcomes = parse_json_field(market.get("outcomes"), "outcomes")
        token_ids = parse_json_field(market.get("clobTokenIds"), "clobTokenIds")
    except ValueError:
        return ["NOT_BINARY_YES_NO"]
    labels = [str(value).strip().upper() for value in outcomes] if isinstance(outcomes, list) else []
    valid_tokens = (
        isinstance(token_ids, list)
        and len(token_ids) == 2
        and all(isinstance(token, str) and token.strip() for token in token_ids)
        and len(set(token_ids)) == 2
    )
    return [] if len(labels) == 2 and set(labels) == {"YES", "NO"} and valid_tokens else ["NOT_BINARY_YES_NO"]


def _structure_reasons(market: dict[str, Any], rule: str) -> list[str]:
    question = str(market.get("question") or "").lower()
    contract_type = str(market.get("marketType") or market.get("market_type") or "").strip().lower()
    reasons: list[str] = []
    scalar_markers = ("between ", "range ", "scalar", " or more", " or less", "above ", "below ")
    if contract_type in POLICY_V2_CONFIG["unsupported_contract_types"] or any(marker in question for marker in scalar_markers):
        reasons.append("AMBIGUOUS_SCALAR_OR_RANGE")
    dependency_is_explicit = any(market.get(flag) is True for flag in POLICY_V2_CONFIG["dependent_contract_flags"])
    standalone = market.get("standaloneResolution", market.get("standalone_resolution"))
    dependency_text = f"{question} {rule.lower()}"
    dependency_markers = (
        "requires resolution of another market",
        "depends on the resolution of another market",
        "see the parent market rules",
        "resolved jointly with",
    )
    if dependency_is_explicit or standalone is False or any(marker in dependency_text for marker in dependency_markers):
        reasons.append("REQUIRES_SIBLING_MARKETS")
    return reasons


def _searchable(market: dict[str, Any]) -> str:
    tags = " ".join(str(value).lower() for value in (market.get("tags") or []) if isinstance(value, str))
    return f"{str(market.get('question') or '').lower()} {tags}"


def _obvious_live_sports(market: dict[str, Any], captured_at: str | None = None) -> bool:
    searchable = _searchable(market)
    is_sports = any(word in searchable for word in POLICY_CONFIG["sports_keywords"])
    explicit_live = market.get("live") is True or market.get("inPlay") is True or market.get("in_play") is True
    started = False
    game_start = market.get("gameStartTime") or market.get("eventStartTime")
    if is_sports and captured_at and game_start:
        try:
            started = _as_utc(game_start) <= _as_utc(captured_at)
        except (TypeError, ValueError):
            started = True
    return explicit_live or started or (is_sports and any(marker in searchable for marker in POLICY_V2_CONFIG["live_sports_markers"]))


def event_lineage(market: dict[str, Any]) -> dict[str, Any]:
    """Extract stable event grouping without treating a NegRisk event as invalid.

    Gamma has used both top-level event fields and an ``events`` array.  We only
    accept a concrete id; absent one deliberately falls back to the market id.
    """
    event_id = market.get("eventId") or market.get("event_id")
    event_slug = market.get("eventSlug") or market.get("event_slug")
    events = market.get("events")
    if isinstance(events, list):
        candidates = sorted(
            (item for item in events if isinstance(item, dict) and item.get("id") not in (None, "")),
            key=lambda item: str(item["id"]),
        )
        if candidates:
            event_id = event_id or candidates[0].get("id")
            event_slug = event_slug or candidates[0].get("slug")
    event_id = str(event_id) if event_id not in (None, "") else None
    event_slug = str(event_slug) if event_slug not in (None, "") else None
    market_id = str(market.get("id") or "")
    return {
        "event_id": event_id,
        "event_slug": event_slug,
        "neg_risk": market.get("negRisk") is True,
        "statistical_cluster_id": event_id or market_id,
    }


def metadata_prefilter(market: dict[str, Any], *, captured_at: str) -> list[str]:
    """Cheap fail-closed checks performed before any CLOB request."""
    end = market.get("endDate") or market.get("endDateIso") or market.get("end_date")
    rule = _market_rule(market)
    reasons: list[str] = []
    if market.get("active") is not True or market.get("closed") is True or market.get("resolved") is True:
        reasons.append("NOT_ACTIVE_OPEN")
    if market.get("enableOrderBook") is False or market.get("acceptingOrders") is False:
        reasons.append("CLOB_NOT_ACCEPTING_ORDERS")
    reasons.extend(_binary_metadata_reasons(market))
    condition_id = market.get("conditionId") or market.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id.strip():
        reasons.append("MISSING_CONDITION_ID")
    if not end:
        reasons.append("MISSING_END_DATE")
    if not rule:
        reasons.append("MISSING_RESOLUTION_RULES")
    reasons.extend(_structure_reasons(market, rule))
    searchable = _searchable(market)
    if _obvious_live_sports(market, captured_at):
        reasons.append("OBVIOUS_LIVE_SPORTS")
    try:
        if _as_utc(end) <= _as_utc(captured_at):
            reasons.append("END_DATE_NOT_FUTURE")
        if any(word in searchable for word in POLICY_CONFIG["crypto_keywords"]) and (_as_utc(end) - _as_utc(captured_at)).total_seconds() < POLICY_CONFIG["minimum_crypto_horizon_hours"] * 3600:
            reasons.append("ULTRA_SHORT_CRYPTO")
    except (TypeError, ValueError):
        if end:
            reasons.append("INVALID_END_DATE")
    raw_liquidity = market.get("liquidityNum", market.get("liquidity"))
    try:
        if raw_liquidity is not None and Decimal(str(raw_liquidity)) < Decimal(POLICY_V2_CONFIG["minimum_gamma_liquidity_usd"]):
            reasons.append("INSUFFICIENT_METADATA_LIQUIDITY")
    except Exception:
        reasons.append("INVALID_METADATA_LIQUIDITY")
    return sorted(set(reasons))


def evaluate_v2(snapshot: dict[str, Any]) -> dict[str, Any]:
    """NegRisk is lineage metadata, never a standalone rejection reason."""
    market = snapshot.get("market") if isinstance(snapshot.get("market"), dict) else {}
    quotes = snapshot.get("quotes") if isinstance(snapshot.get("quotes"), dict) else {}
    mapping = snapshot.get("token_mapping") if isinstance(snapshot.get("token_mapping"), dict) else {}
    reasons: list[str] = []
    outcomes = snapshot.get("outcomes")
    normalized_outcomes = [str(value).strip().upper() for value in outcomes] if isinstance(outcomes, list) else []
    if len(normalized_outcomes) != 2 or set(normalized_outcomes) != {"YES", "NO"}:
        reasons.append("NOT_BINARY_YES_NO")
    condition_id = market.get("conditionId") or market.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id.strip():
        reasons.append("MISSING_CONDITION_ID")
    if market.get("active") is not True or market.get("closed") is True or market.get("resolved") is True:
        reasons.append("NOT_ACTIVE_OPEN")
    if not snapshot.get("end_date"):
        reasons.append("MISSING_END_DATE")
    rule = str(snapshot.get("resolution_rule_text") or "").strip()
    if not rule:
        reasons.append("MISSING_RESOLUTION_RULES")
    if set(mapping) != {"YES", "NO"} or any(not isinstance(mapping.get(side), str) or not mapping[side].strip() for side in ("YES", "NO")) or mapping.get("YES") == mapping.get("NO"):
        reasons.append("AMBIGUOUS_TOKEN_MAPPING")
    reasons.extend(_structure_reasons(market, rule))
    searchable = _searchable(market)
    if _obvious_live_sports(market, str(snapshot.get("captured_at") or "")):
        reasons.append("OBVIOUS_LIVE_SPORTS")
    try:
        end = _as_utc(snapshot.get("end_date")); captured = _as_utc(snapshot.get("captured_at"))
        if end <= captured: reasons.append("END_DATE_NOT_FUTURE")
        if any(word in searchable for word in POLICY_CONFIG["crypto_keywords"]) and (end-captured).total_seconds()<POLICY_CONFIG["minimum_crypto_horizon_hours"]*3600: reasons.append("ULTRA_SHORT_CRYPTO")
    except (TypeError, ValueError):
        if snapshot.get("end_date"): reasons.append("INVALID_END_DATE")
    raw_liquidity = market.get("liquidityNum", market.get("liquidity"))
    try:
        if raw_liquidity is not None and Decimal(str(raw_liquidity)) < Decimal(POLICY_V2_CONFIG["minimum_gamma_liquidity_usd"]): reasons.append("INSUFFICIENT_METADATA_LIQUIDITY")
    except Exception: reasons.append("INVALID_METADATA_LIQUIDITY")
    for side in ("YES","NO"):
        quote = quotes.get(side) if isinstance(quotes.get(side), dict) else {}
        if quote.get("best_bid") in (None, "") or quote.get("best_ask") in (None, ""):
            reasons.append(f"{side}_ORDERBOOK_UNAVAILABLE")
            continue
        try:
            bid = Decimal(str(quote["best_bid"])); ask = Decimal(str(quote["best_ask"])); spread = ask - bid
            if not bid.is_finite() or not ask.is_finite() or bid < 0 or ask > 1:
                raise ValueError("invalid probability quote")
            if spread < 0 or spread > Decimal(POLICY_CONFIG["maximum_spread"]):
                reasons.append(f"{side}_SPREAD_OUT_OF_POLICY")
        except Exception:
            reasons.append(f"{side}_ORDERBOOK_UNAVAILABLE")
    reasons = sorted(set(reasons))
    return {"eligible": not reasons, "policy_version": POLICY_V2_VERSION, "policy_hash": policy_v2_hash(), "reasons": reasons, **event_lineage(market)}
