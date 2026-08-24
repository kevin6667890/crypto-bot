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


def handler(body: dict):
    instance = object.__new__(paper_api.Handler)
    instance.path = "/api/research/thesis/test"
    raw = json.dumps(body).encode()
    instance.rfile = BytesIO(raw)
    instance.headers = {"Content-Length": str(len(raw))}
    instance._limited = lambda *_args, **_kwargs: False
    sent = []
    instance._send = lambda payload, status=HTTPStatus.OK: sent.append((payload, status))
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
