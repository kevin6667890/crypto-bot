from __future__ import annotations

from io import BytesIO
import json
from http import HTTPStatus

from dashboard import paper_api
from dashboard.thesis_event_engine import THESIS_SPEC_VERSION, ThesisValidationError


class StubService:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.payloads = response, error, []

    def test(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.response


def handler(body: dict, path="/api/research/thesis/test"):
    instance = object.__new__(paper_api.Handler)
    instance.path = path
    raw = json.dumps(body).encode()
    instance.rfile = BytesIO(raw)
    instance.headers = {"Content-Length": str(len(raw))}
    instance._limited = lambda *_args, **_kwargs: False
    sent = []
    instance._send = lambda payload, status=HTTPStatus.OK: sent.append((payload, status))
    return instance, sent


def get_handler(path: str):
    instance = object.__new__(paper_api.Handler)
    instance.path = path
    instance._send = lambda payload, status=HTTPStatus.OK: sent.append((payload, status))
    sent = []
    return instance, sent


def test_thesis_http_endpoint_is_a_thin_structured_boundary(monkeypatch):
    body = {"version": THESIS_SPEC_VERSION, "instrument": "BTC", "timeframe": "4H",
            "required_conditions": [{"feature": "VOLUME_RATIO", "operator": "gte", "value": 1.2}],
            "optional_conditions": [], "forward_horizons": ["4H", "12H", "24H"],
            "requested_as_of": 1_700_000_000}
    service = StubService({"result_version": "thesis-test-result-v1", "status": "COMPLETED"})
    monkeypatch.setattr(paper_api, "THESIS_TEST_SERVICE_V1", service)
    instance, sent = handler(body)
    instance.do_POST()
    assert service.payloads == [body]
    assert sent == [(service.response, HTTPStatus.OK)]


def test_thesis_http_endpoint_returns_typed_validation_error(monkeypatch):
    service = StubService(error=ThesisValidationError("unsupported feature: EVIL"))
    monkeypatch.setattr(paper_api, "THESIS_TEST_SERVICE_V1", service)
    instance, sent = handler({})
    instance.do_POST()
    assert sent == [({"error": {"code": "INVALID_THESIS_SPEC",
                                "message": "unsupported feature: EVIL"}}, HTTPStatus.BAD_REQUEST)]


def test_thesis_http_endpoint_hides_internal_exception(monkeypatch):
    service = StubService(error=RuntimeError("secret path and stack"))
    monkeypatch.setattr(paper_api, "THESIS_TEST_SERVICE_V1", service)
    instance, sent = handler({})
    instance.do_POST()
    assert sent == [({"error": {"code": "THESIS_TEST_FAILED",
                                "message": "Thesis test could not be completed."}},
                     HTTPStatus.INTERNAL_SERVER_ERROR)]
    assert "secret" not in json.dumps(sent)


def test_thesis_parse_endpoint_is_rate_limited_separate_from_test(monkeypatch):
    service = type("Parser", (), {"parse": lambda self, payload: {"version": "thesis-parse-result-v1", "status": "READY", "original_text": payload["text"]}})()
    monkeypatch.setattr(paper_api, "THESIS_PARSER_SERVICE_V1", service)
    instance, sent = handler({"text": "BTC 4H RSI > 70"}, "/api/research/thesis/parse")
    buckets = []
    instance._limited = lambda *args: buckets.append(args) or False
    instance.do_POST()
    assert buckets == [("thesis-parse-minute", 6, 60)]
    assert sent[0][0]["status"] == "READY"


def test_thesis_parse_endpoint_sanitizes_internal_failure(monkeypatch):
    service = type("Parser", (), {"parse": lambda *_: (_ for _ in ()).throw(RuntimeError("secret provider response"))})()
    monkeypatch.setattr(paper_api, "THESIS_PARSER_SERVICE_V1", service)
    instance, sent = handler({"text": "BTC 4H RSI > 70"}, "/api/research/thesis/parse")
    instance.do_POST()
    assert sent == [({"error": {"code": "THESIS_PARSE_FAILED", "message": "Your idea could not be interpreted."}},
                     HTTPStatus.INTERNAL_SERVER_ERROR)]
    assert "secret" not in json.dumps(sent)


def test_capabilities_endpoint_uses_registry_projection():
    instance, sent = get_handler("/api/research/thesis/capabilities")
    instance.do_GET()
    assert sent[0][0]["version"] == "thesis-capabilities-v1"
    assert {item["code"] for item in sent[0][0]["features"]}


def test_ai_parser_failure_does_not_change_deterministic_test_endpoint(monkeypatch):
    parser = type("Parser", (), {"parse": lambda *_: {"status": "ERROR", "warnings": ["AI_UNAVAILABLE"]}})()
    test_service = StubService({"result_version": "thesis-test-result-v1", "status": "COMPLETED"})
    monkeypatch.setattr(paper_api, "THESIS_PARSER_SERVICE_V1", parser)
    monkeypatch.setattr(paper_api, "THESIS_TEST_SERVICE_V1", test_service)
    parse_instance, parse_sent = handler({"text": "BTC 4H RSI > 70"}, "/api/research/thesis/parse")
    parse_instance.do_POST()
    test_instance, test_sent = handler({"version": THESIS_SPEC_VERSION}, "/api/research/thesis/test")
    test_instance.do_POST()
    assert parse_sent[0][0]["status"] == "ERROR"
    assert test_sent[0][0]["status"] == "COMPLETED"
