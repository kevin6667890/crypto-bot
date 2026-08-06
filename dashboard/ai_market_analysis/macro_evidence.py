"""Causal, immutable macro evidence contracts and evidence-set identities."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .canonical import identity, stable_hash
from .versions import AI_MACRO_EVIDENCE_SET_VERSION, AI_MACRO_EVIDENCE_VERSION

MACRO_CATEGORIES = ("MONETARY_POLICY", "INFLATION", "LABOUR_MARKET", "LIQUIDITY", "ETF_FLOW",
    "REGULATION", "EXCHANGE_EVENT", "PROTOCOL_EVENT", "ONCHAIN_EVENT", "RISK_ASSET_SENTIMENT", "OTHER")
MACRO_SOURCE_TYPES = ("OFFICIAL_PRIMARY", "OFFICIAL_DATA", "REPUTABLE_NEWS", "SECONDARY_RESEARCH", "USER_SUPPLIED")


def _time(value: Any, required: bool = True) -> str | None:
    if value is None and not required: return None
    if not isinstance(value, str): raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError("timestamp timezone required")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_macro_evidence(payload: dict[str, Any], decision_time: str) -> dict[str, Any]:
    category, source_type = payload.get("category"), payload.get("source_type")
    if category not in MACRO_CATEGORIES: raise ValueError("invalid macro category")
    if source_type not in MACRO_SOURCE_TYPES: raise ValueError("invalid macro source type")
    published, event, retrieved, cutoff = _time(payload.get("published_at")), _time(payload.get("event_time")), _time(payload.get("retrieved_at")), _time(decision_time)
    if published > cutoff or event > cutoff: raise ValueError("future macro evidence")
    url = str(payload.get("source_url") or "")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname: raise ValueError("invalid source URL")
    summary = str(payload.get("factual_summary") or "").strip()
    if not summary: raise ValueError("factual_summary required")
    quote = payload.get("direct_quote")
    if quote is not None and len(str(quote)) > 500: raise ValueError("direct_quote too long")
    core = {"schema_version": AI_MACRO_EVIDENCE_VERSION, "category": category,
        "title": str(payload.get("title") or "")[:300], "factual_summary": summary[:2000],
        "publisher": str(payload.get("publisher") or "")[:200], "source_name": str(payload.get("source_name") or "")[:200],
        "source_type": source_type, "source_url": url, "published_at": published, "event_time": event,
        "retrieved_at": retrieved, "valid_at_decision_time": True,
        "relevant_instruments": sorted(set(payload.get("relevant_instruments") or [])),
        "relevance_reason": str(payload.get("relevance_reason") or "")[:1000],
        "claims": [str(x)[:500] for x in payload.get("claims") or []], "direct_quote": quote,
        "quality": str(payload.get("quality") or "VALID"), "status": str(payload.get("status") or "ACTIVE"),
        "fixture": bool(payload.get("fixture", False)),
        "provenance": {**(payload.get("provenance") or {}), "retrieval": "SUPPLIED_STRUCTURED"}}
    content_hash = stable_hash(core)
    return {**core, "content_hash": content_hash,
            "evidence_id": payload.get("evidence_id") or identity("macro", core)}


def freeze_macro_evidence_set(items: list[dict[str, Any]], decision_time: str) -> dict[str, Any]:
    normalized = [normalize_macro_evidence(item, decision_time) for item in items]
    unique = {item["content_hash"]: item for item in normalized}
    ordered = sorted(unique.values(), key=lambda x: x["evidence_id"])
    fingerprints = [item["content_hash"] for item in ordered]
    fingerprint = stable_hash({"version": AI_MACRO_EVIDENCE_SET_VERSION,
                               "decision_time": _time(decision_time), "item_hashes": fingerprints})
    warnings = [] if ordered else ["本次未加入已验证宏观证据。"]
    return {"version": AI_MACRO_EVIDENCE_SET_VERSION,
        "evidence_set_id": identity("macroset", fingerprint), "decision_time": _time(decision_time),
        "items": ordered, "item_hashes": fingerprints, "source_count": len({i["source_url"] for i in ordered}),
        "category_count": len({i["category"] for i in ordered}),
        "latest_published_at": max((i["published_at"] for i in ordered), default=None),
        "quality": "VALID" if ordered else "UNAVAILABLE", "warnings": warnings,
        "set_fingerprint": fingerprint, "automatic_retrieval": "NOT_IMPLEMENTED"}
