from __future__ import annotations

from http import HTTPStatus

from dashboard import paper_api


def handler(path: str, headers=None):
    item = object.__new__(paper_api.Handler)
    item.path = path; item.headers = headers or {}; item.client_address = ("127.0.0.1", 1234)
    captured = []
    item._send = lambda payload, status=HTTPStatus.OK: captured.append((status, payload))
    return item, captured


def test_presentation_flag_defaults_closed(monkeypatch):
    monkeypatch.delenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", raising=False)
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL")
    item.do_GET()
    assert captured == [(HTTPStatus.NOT_FOUND, {"error": {"code": "PRESENTATION_DISABLED", "message": "Shadow presentation is disabled."}})]


def test_presentation_requires_existing_admin_boundary(monkeypatch):
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL")
    item.do_GET()
    assert captured[0][0] == HTTPStatus.UNAUTHORIZED


def test_token_in_query_is_never_authorization(monkeypatch):
    monkeypatch.setenv("AI_MARKET_ANALYSIS_PRESENTATION_ENABLED", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    item, captured = handler("/api/ai-market-analysis/v1/presentations/latest?instrument=ETH-USDT-SWAP&mode=FULL&token=secret")
    item.do_GET()
    assert captured[0][0] == HTTPStatus.UNAUTHORIZED
