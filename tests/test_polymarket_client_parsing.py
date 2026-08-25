import pytest

from dashboard.polymarket.client import GAMMA_PAGINATION_POLICY_VERSION, PolymarketClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _PagedSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        markets, next_cursor = self.pages.get(params.get("after_cursor"), ([], None))
        return _Response({"markets": markets, "next_cursor": next_cursor})


def test_binary_mapping_and_quotes():
    client = PolymarketClient()
    assert client.token_mapping({"outcomes": '["Yes","No"]', "clobTokenIds": '["a","b"]'}) == {"YES": "a", "NO": "b"}
    assert client.quote({"bids": [{"price": "0.4"}, {"price": "0.5"}], "asks": [{"price": "0.7"}, {"price": "0.6"}]}) == {"best_bid": "0.5", "best_ask": "0.6", "midpoint": "0.55"}


def test_malformed_payload_fails_closed():
    with pytest.raises(ValueError):
        PolymarketClient().token_mapping({"outcomes": '["Yes"]', "clobTokenIds": '["a","b"]'})
    assert PolymarketClient.quote({"bids": "bad", "asks": []})["best_bid"] is None


def test_full_pagination_is_canonical_and_explicitly_ordered():
    session = _PagedSession({None: ([{"id": "20"}, {"id": "10"}], "cursor-1"), "cursor-1": ([{"id": "3"}], None)})
    result = PolymarketClient(session=session).fetch_active_markets(page_size=2)
    assert [market["id"] for market in result] == ["10", "20", "3"]
    assert [call.get("after_cursor") for call in session.calls] == [None, "cursor-1"]
    assert all(call["order"] == "id" and call["ascending"] == "true" for call in session.calls)
    assert GAMMA_PAGINATION_POLICY_VERSION == "gamma-keyset-after-cursor-id-ascending-v1"


def test_pagination_duplicate_or_invalid_page_fails_closed():
    duplicate = _PagedSession({None: ([{"id": "1"}], "cursor-1"), "cursor-1": ([{"id": "1"}], None)})
    with pytest.raises(ValueError, match="duplicate market id"):
        PolymarketClient(session=duplicate).fetch_active_markets(page_size=1)

    oversized = _PagedSession({None: ([{"id": "1"}, {"id": "2"}], None)})
    with pytest.raises(ValueError, match="exceeds the requested size"):
        PolymarketClient(session=oversized).fetch_active_markets(page_size=1)

    missing_id = _PagedSession({None: ([{"question": "missing id"}], None)})
    with pytest.raises(ValueError, match="missing/invalid market id"):
        PolymarketClient(session=missing_id).fetch_active_markets(page_size=2)
