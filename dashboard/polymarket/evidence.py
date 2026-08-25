"""Small deterministic, timestamp-safe evidence pipeline."""
from __future__ import annotations
import re
import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from urllib.parse import quote_plus
import requests
from .models import stable_hash, utc_now

EVIDENCE_POLICY_VERSION = "polymarket-evidence-v2"
QUERY_POLICY_VERSION = "polymarket-evidence-query-v1"
MAX_EVIDENCE_PER_MARKET = 5
MAX_CANDIDATES_PER_MARKET = 20
_NEWS = frozenset({"reuters.com", "apnews.com", "bbc.com", "nytimes.com", "wsj.com", "ft.com", "bloomberg.com", "theguardian.com", "cnn.com", "npr.org"})

def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value: raise ValueError("timestamp must be a non-empty ISO-8601 string")
    try: result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc: raise ValueError("timestamp must be valid ISO-8601") from exc
    if result.tzinfo is None: raise ValueError("timestamp must include a UTC offset")
    return result.astimezone(timezone.utc)

def canonical_utc(value: str) -> str: return parse_utc(value).replace(microsecond=0).isoformat()

def query_policy() -> dict[str, Any]:
    return {"version": QUERY_POLICY_VERSION, "templates": ["exact_question", "subject_event", "resolution_entity_event"], "max_queries": 3}

def query_policy_hash() -> str: return stable_hash(query_policy())

def deterministic_queries(question: str, resolution_rule_text: str = "", end_date: str | None = None) -> list[str]:
    if not isinstance(question, str) or not question.strip(): raise ValueError("question is required")
    q = " ".join(question.split())
    subject = re.sub(r"^(will|does|is|are|did|has|have)\s+", "", q, flags=re.I).rstrip("?")
    rule = " ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", resolution_rule_text))[:180]
    out: list[str] = []
    for candidate in (q, subject, f"{subject} {rule}".strip()):
        if candidate and candidate not in out: out.append(candidate)
    return out[:3]

def source_type_for_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host.endswith(".gov") or host.endswith(".int") or host in {"europa.eu", "who.int", "un.org"}: return "official_government_or_institution"
    if host in _NEWS or any(host.endswith("." + item) for item in _NEWS): return "mainstream_news"
    return None # company-official cannot safely be inferred from hostname

def strict_evidence_eligible(record: Mapping[str, Any], evidence_cutoff_at: str) -> tuple[bool, str | None]:
    if record.get("source_type") not in {"official_government_or_institution", "official_company", "mainstream_news", "official"}: return False, "STRICT_REJECT_SOURCE_TYPE"
    published_at = record.get("published_at")
    # Keep the public helper's legacy spelling; retrieval attempts normalize it
    # to the explicit audit status required by the v2 policy.
    if not published_at: return False, "timestamp_unknown"
    try:
        if parse_utc(str(published_at)) > parse_utc(evidence_cutoff_at): return False, "published_after_cutoff"
    except ValueError: return False, "published_at_invalid"
    return True, None

def build_evidence_payload(*, source_url: str, title: str, content: str, source_type: str, published_at: str | None = None, retrieved_at: str | None = None, raw_payload: Any | None = None) -> dict[str, Any]:
    if not all(isinstance(v, str) and v for v in (source_url, content, source_type)) or not isinstance(title, str): raise ValueError("source_url, content, source_type and title are required")
    published = canonical_utc(published_at) if published_at else None
    out: dict[str, Any] = {"source_url": source_url, "title": title, "content": content, "source_type": source_type, "retrieved_at": canonical_utc(retrieved_at or utc_now()), "published_at": published, "timestamp_status": "known" if published else "timestamp_unknown"}
    if raw_payload is not None: out["raw_payload"] = raw_payload
    out["content_hash"] = stable_hash({k: out[k] for k in ("source_url", "title", "content", "source_type", "published_at")})
    return out

def public_search_candidates(queries: Sequence[str], *, timeout_seconds: float = 10.0, max_candidates: int = MAX_CANDIDATES_PER_MARKET) -> list[dict[str, Any]]:
    """Bounded public Bing RSS discovery; discovery failures return no candidates.

    RSS timestamps are treated as unverified metadata: accepted strict evidence still
    requires a source type and a parseable timestamp, and callers retain rejects.
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        try:
            response = requests.get("https://www.bing.com/search?format=rss&q=" + quote_plus(query), timeout=timeout_seconds, headers={"User-Agent": "polymarket-paper-research/1"})
            response.raise_for_status(); root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError):
            continue
        for item in root.findall(".//item"):
            url = (item.findtext("link") or "").strip()
            if not url or url in seen: continue
            seen.add(url)
            stamp = item.findtext("pubDate")
            try: published = email.utils.parsedate_to_datetime(stamp).astimezone(timezone.utc).replace(microsecond=0).isoformat() if stamp else None
            except (TypeError, ValueError): published = None
            found.append({"url": url, "title": item.findtext("title") or "", "content": item.findtext("description") or "", "published_at": published, "source_type": source_type_for_url(url), "raw_payload": {"rss_pub_date": stamp, "query": query}})
            if len(found) >= max_candidates: return found
    return found

def retrieve_candidates(repo: Any, *, market_id: str, queries: Sequence[str], evidence_cutoff_at: str, candidates: Sequence[Mapping[str, Any]], max_evidence: int = MAX_EVIDENCE_PER_MARKET) -> list[str]:
    """Persist every candidate/strict rejection; inputs normally originate in public GET search."""
    cutoff, accepted, seen = canonical_utc(evidence_cutoff_at), [], set()
    query = queries[0] if queries else ""
    for item in list(candidates)[:MAX_CANDIDATES_PER_MARKET]:
        url, retrieved = item.get("url") or item.get("source_url"), canonical_utc(str(item.get("retrieved_at") or utc_now()))
        if not isinstance(url, str) or not url or url in seen:
            repo.insert_evidence_attempt(market_id=market_id, query=query, source_url=url if isinstance(url,str) else None, retrieved_at=retrieved, published_at=None, status="REJECTED", rejection_reason="STRICT_REJECT_INVALID_URL", payload_hash=None); continue
        seen.add(url)
        try:
            payload = build_evidence_payload(source_url=url, title=str(item.get("title") or ""), content=str(item.get("content") or item.get("excerpt") or ""), source_type=str(item.get("source_type") or source_type_for_url(url) or "unknown"), published_at=item.get("published_at"), retrieved_at=retrieved, raw_payload=item.get("raw_payload"))
        except (ValueError, TypeError) as exc:
            repo.insert_evidence_attempt(market_id=market_id, query=query, source_url=url, retrieved_at=retrieved, published_at=item.get("published_at"), status="REJECTED", rejection_reason=f"STRICT_REJECT_MALFORMED:{exc}", payload_hash=None); continue
        ok, reason = strict_evidence_eligible(payload, cutoff); ph = stable_hash(payload)
        if not ok or len(accepted) >= max_evidence:
            audit_reason = {"timestamp_unknown": "STRICT_REJECT_TIMESTAMP_UNKNOWN", "published_after_cutoff": "STRICT_REJECT_AFTER_CUTOFF", "published_at_invalid": "STRICT_REJECT_TIMESTAMP_INVALID"}.get(reason or "", reason or "STRICT_REJECT_LIMIT")
            repo.insert_evidence_attempt(market_id=market_id, query=query, source_url=url, retrieved_at=retrieved, published_at=payload["published_at"], status="REJECTED", rejection_reason=audit_reason, payload_hash=ph); continue
        eid = repo.insert_evidence(market_id, payload, source_url=url, cutoff_at=cutoff)
        repo.insert_evidence_attempt(market_id=market_id, query=query, source_url=url, retrieved_at=retrieved, published_at=payload["published_at"], status="ACCEPTED", rejection_reason=None, payload_hash=ph)
        accepted.append(eid)
    return accepted
