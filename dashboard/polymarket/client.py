"""Fail-closed public Gamma/CLOB GET client; it never signs or submits orders."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import time
from typing import Any

import requests

from .models import decimal_text, parse_json_field

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
GAMMA_PAGINATION_POLICY_VERSION = "gamma-keyset-after-cursor-id-ascending-v1"
# The legacy offset endpoint hard-stops around offset 2,000. The keyset endpoint
# uses `after_cursor` and returns an explicit `next_cursor`, allowing the full
# active universe to be traversed without silently truncating it.
GAMMA_MAX_PAGE_SIZE = 100
GAMMA_MAX_PAGES = 10_000
GAMMA_PAGE_INTERVAL_SECONDS = 0.35


class PolymarketClient:
    def __init__(self, *, session: requests.Session | None = None, timeout: float = 15.0) -> None:
        self.session = session or requests.Session()
        headers = getattr(self.session, "headers", None)
        if headers is not None:  # small fake sessions in deterministic tests
            headers.setdefault("User-Agent", "crypto-bot-polymarket-research/1.0 (+local-read-only)")
            headers.setdefault("Accept", "application/json")
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        # Gamma occasionally rate-limits long keyset walks.  Retry only
        # transient public-read responses, respecting Retry-After when given;
        # never turn a collection failure into partial universe success.
        response: requests.Response | None = None
        for attempt in range(7):
            response = self.session.get(url, params=params, timeout=self.timeout)
            # Test doubles deliberately implement only raise_for_status/json.
            if getattr(response, "status_code", 200) not in {403, 429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response.json()
            if attempt < 6:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(max(float(retry_after), 1.0), 30.0) if retry_after else min(2.0 ** attempt, 30.0)
                except (TypeError, ValueError):
                    delay = min(2.0 ** attempt, 8.0)
                time.sleep(delay)
        assert response is not None
        response.raise_for_status()
        raise AssertionError("unreachable")

    def fetch_active_markets(self, limit: int | None = None, *, page_size: int = 500, as_of: str | None = None) -> list[dict[str, Any]]:
        """Fetch the complete active universe using Gamma keyset pagination."""
        page_size = min(max(int(page_size), 1), GAMMA_MAX_PAGE_SIZE)
        wanted = None if limit is None else max(int(limit), 0)
        prospective_as_of = as_of or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cursor: str | None = None
        page_number, seen_ids, seen_cursors, result = 0, set(), set(), []
        while wanted is None or len(result) < wanted:
            if page_number >= GAMMA_MAX_PAGES:
                raise ValueError("Gamma pagination exceeded the fail-closed page limit")
            size = page_size if wanted is None else min(page_size, wanted - len(result))
            params: dict[str, Any] = {
                "active": "true", "closed": "false", "limit": size,
                "order": "id", "ascending": "true", "end_date_min": prospective_as_of,
            }
            if cursor is not None:
                params["after_cursor"] = cursor
            payload = self._get(
                f"{GAMMA_URL}/markets/keyset", params,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
                raise ValueError("Gamma keyset response is not a markets page")
            page = payload["markets"]
            if len(page) > size:
                raise ValueError("Gamma active-markets page exceeds the requested size")
            for item in page:
                if not isinstance(item, dict) or item.get("id") in (None, ""):
                    raise ValueError("Gamma active-markets payload has missing/invalid market id")
                market_id = str(item["id"]).strip()
                if not market_id:
                    raise ValueError("Gamma active-markets payload has missing/invalid market id")
                if market_id in seen_ids:
                    raise ValueError(f"Gamma pagination returned duplicate market id {market_id}")
                seen_ids.add(market_id); result.append(item)
            if wanted is not None and len(result) >= wanted:
                break
            next_cursor = payload.get("next_cursor")
            if next_cursor in (None, ""):
                break
            if not isinstance(next_cursor, str) or next_cursor == cursor or next_cursor in seen_cursors:
                raise ValueError("Gamma keyset cursor did not advance")
            if not page:
                raise ValueError("Gamma keyset returned an empty page with a continuation cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            page_number += 1
            # The full universe is more than 1,500 pages. Keep the production
            # walk below Gamma's sustained public-read edge limit instead of
            # relying on burst retries after a long cursor chain.
            if isinstance(self.session, requests.Session):
                time.sleep(GAMMA_PAGE_INTERVAL_SECONDS)
        # Gamma ordering is not a causal selection rule; canonical ordering is.
        return sorted(result, key=lambda item: str(item["id"]))

    def fetch_market(self, market_id: str) -> dict[str, Any]:
        payload = self._get(f"{GAMMA_URL}/markets/{market_id}")
        if not isinstance(payload, dict):
            raise ValueError("Gamma market response is not an object")
        return payload

    def token_mapping(self, market: dict[str, Any]) -> dict[str, str]:
        outcomes = parse_json_field(market.get("outcomes"), "outcomes")
        token_ids = parse_json_field(market.get("clobTokenIds"), "clobTokenIds")
        if not isinstance(outcomes, list) or not isinstance(token_ids, list) or len(outcomes) != len(token_ids):
            raise ValueError("outcomes/clobTokenIds mapping is unavailable")
        mapping: dict[str, str] = {}
        for label, token in zip(outcomes, token_ids):
            if not isinstance(label, str) or not isinstance(token, str) or not token:
                raise ValueError("outcomes/clobTokenIds mapping has invalid entries")
            key = label.strip().upper()
            if key in mapping:
                raise ValueError("outcome mapping has duplicate labels")
            mapping[key] = token
        if set(mapping) != {"YES", "NO"}:
            raise ValueError("market is not an exact binary YES/NO market")
        return mapping

    def fetch_orderbook(self, token_id: str) -> dict[str, Any]:
        payload = self._get(f"{CLOB_URL}/book", {"token_id": token_id})
        if not isinstance(payload, dict):
            raise ValueError("CLOB orderbook response is not an object")
        return payload

    @staticmethod
    def quote(orderbook: dict[str, Any]) -> dict[str, str | None]:
        def prices(side: str) -> list[Decimal]:
            rows = orderbook.get(side)
            if not isinstance(rows, list):
                return []
            found: list[Decimal] = []
            for row in rows:
                raw = row.get("price") if isinstance(row, dict) else None
                text = decimal_text(raw)
                if text is not None:
                    found.append(Decimal(text))
            return found
        bids, asks = prices("bids"), prices("asks")
        bid, ask = (max(bids) if bids else None), (min(asks) if asks else None)
        midpoint = (bid + ask) / 2 if bid is not None and ask is not None else None
        return {"best_bid": format(bid, "f") if bid is not None else None,
                "best_ask": format(ask, "f") if ask is not None else None,
                "midpoint": format(midpoint, "f") if midpoint is not None else None}
