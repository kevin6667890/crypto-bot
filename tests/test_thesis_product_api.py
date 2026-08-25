from __future__ import annotations

from io import BytesIO
import hashlib
from http import HTTPStatus
import json
from pathlib import Path
import sqlite3

import pytest

from dashboard import paper_api
from dashboard.thesis_tracking import TrackingError


def post_handler(path: str, body: dict):
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
    sent = []
    instance._send = lambda payload, status=HTTPStatus.OK: sent.append((payload, status))
    return instance, sent


def ready() -> dict:
    return {"historical_thesis_data": {"status": "READY"}}


def test_track_api_create_evaluate_archive_and_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    service = type("Tracking", (), {
        "create": lambda self, payload: calls.append(("create", payload)) or {"created": True},
        "evaluate": lambda self, track_id: calls.append(("evaluate", track_id)) or {"outcome": "NO_CHANGE"},
    })()
    repository = type("Repository", (), {
        "archive": lambda self, track_id: calls.append(("archive", track_id)) or {"track_id": track_id},
    })()
    monkeypatch.setattr(paper_api, "THESIS_TRACKING_SERVICE_V1", service)
    monkeypatch.setattr(paper_api, "THESIS_TRACKING_REPOSITORY_V1", repository)
    monkeypatch.setattr(paper_api, "thesis_product_readiness", ready)

    instance, sent = post_handler("/api/research/thesis/tracks", {"version": "track-thesis-request-v1"})
    buckets = []
    instance._limited = lambda *args: buckets.append(args) or False
    instance.do_POST()
    assert buckets == [("thesis-track-create-minute", 10, 60)]
    assert sent[0][0]["created"] is True
    assert sent[0][1] == HTTPStatus.CREATED

    instance, sent = post_handler("/api/research/thesis/tracks/t-1/evaluate", {})
    instance.do_POST()
    assert sent == [({"outcome": "NO_CHANGE"}, HTTPStatus.OK)]
    instance, sent = post_handler("/api/research/thesis/tracks/t-1/archive", {})
    instance.do_POST()
    assert sent[0][0]["track"]["track_id"] == "t-1"
    assert calls == [("create", {"version": "track-thesis-request-v1"}),
                     ("evaluate", "t-1"), ("archive", "t-1")]


def test_track_create_rejects_client_statistics_before_verification(tmp_path: Path) -> None:
    repository = paper_api.ThesisTrackingRepositoryV1(tmp_path / "tracking.db")
    evaluator = paper_api.CurrentFeatureEvaluatorV1(type("Reader", (), {})())
    service = paper_api.ThesisTrackingServiceV1(repository, object(), evaluator)
    with pytest.raises(TrackingError, match="unsupported fields"):
        service.create({"version": "track-thesis-request-v1", "N": 9999})


def test_list_detail_and_changes_are_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = type("Repository", (), {
        "list": lambda self, limit: [{"track": {"track_id": "a"}, "latest_evaluation": None}],
        "detail": lambda self, track_id: {"track": {"track_id": track_id}, "evaluation_history": []},
        "changes": lambda self, since_epoch, limit: [{"track": {"track_id": "a"}}],
    })()
    monkeypatch.setattr(paper_api, "THESIS_TRACKING_REPOSITORY_V1", repository)
    monkeypatch.setattr(paper_api, "recent_market_state_changes", lambda **_kwargs: [])
    instance, sent = get_handler("/api/research/thesis/tracks")
    instance.do_GET()
    assert sent[0][0]["tracks"][0]["track"]["track_id"] == "a"
    instance, sent = get_handler("/api/research/thesis/tracks/a")
    instance.do_GET()
    assert sent[0][0]["track"]["track_id"] == "a"
    instance, sent = get_handler("/api/research/thesis/changes?hours=24")
    instance.do_GET()
    assert sent[0][0]["changes"] == [{"track": {"track_id": "a"}}]


def _historical_fixture(path: Path, dataset_id: str = "catalog-v1") -> str:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE historical_candles(ts INTEGER)")
        connection.execute("CREATE TABLE thesis_dataset_manifest(dataset_id TEXT NOT NULL)")
        connection.execute("INSERT INTO thesis_dataset_manifest VALUES(?)", (dataset_id,))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_readiness_validates_sha_and_manifest_identity(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "frozen.db"
    digest = _historical_fixture(path)
    monkeypatch.setenv("THESIS_HISTORICAL_REQUIRE_IMMUTABLE", "true")
    monkeypatch.setenv("THESIS_HISTORICAL_DB_PATH", str(path))
    monkeypatch.setenv("THESIS_HISTORICAL_DB_SHA256", "0" * 64)
    monkeypatch.setenv("THESIS_HISTORICAL_DATASET_ID", "catalog-v1")
    paper_api._HISTORICAL_SHA_CACHE.clear()
    assert paper_api.thesis_product_readiness()["historical_thesis_data"]["reason"] == "HISTORICAL_STORE_SHA256_MISMATCH"

    monkeypatch.setenv("THESIS_HISTORICAL_DB_SHA256", digest)
    monkeypatch.setenv("THESIS_HISTORICAL_DATASET_ID", "wrong-catalog")
    assert paper_api.thesis_product_readiness()["historical_thesis_data"]["reason"] == "HISTORICAL_DATASET_ID_MISMATCH"
    monkeypatch.setenv("THESIS_HISTORICAL_DATASET_ID", "catalog-v1")
    readiness = paper_api.thesis_product_readiness()
    assert readiness["historical_thesis_data"]["status"] == "READY"
    assert str(path) not in json.dumps(readiness)


def test_historical_block_is_503_but_current_track_refresh_remains_available(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_api, "thesis_product_readiness",
                        lambda: {"historical_thesis_data": {"status": "BLOCKED"}})
    historical = type("Historical", (), {"test": lambda *_: (_ for _ in ()).throw(
        AssertionError("must not fall back"))})()
    current = type("Current", (), {"evaluate": lambda self, track_id: {"track_id": track_id,
                                                                        "outcome": "NO_CHANGE"}})()
    monkeypatch.setattr(paper_api, "THESIS_TEST_SERVICE_V1", historical)
    monkeypatch.setattr(paper_api, "THESIS_TRACKING_SERVICE_V1", current)
    instance, sent = post_handler("/api/research/thesis/test", {})
    instance.do_POST()
    assert sent[0][1] == HTTPStatus.SERVICE_UNAVAILABLE
    assert sent[0][0]["error"]["code"] == "HISTORICAL_THESIS_DATA_BLOCKED"

    instance, sent = post_handler("/api/research/thesis/tracks/saved/evaluate", {})
    instance.do_POST()
    assert sent == [({"track_id": "saved", "outcome": "NO_CHANGE"}, HTTPStatus.OK)]


def test_production_container_defaults_historical_evidence_to_fail_closed() -> None:
    compose = (paper_api.ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    example = (paper_api.ROOT / ".env.example").read_text(encoding="utf-8")
    assert "THESIS_HISTORICAL_REQUIRE_IMMUTABLE: ${THESIS_HISTORICAL_REQUIRE_IMMUTABLE:-true}" in compose
    assert "THESIS_HISTORICAL_REQUIRE_IMMUTABLE=true" in example


def test_backend_itself_defaults_historical_evidence_to_fail_closed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("THESIS_HISTORICAL_REQUIRE_IMMUTABLE", "THESIS_HISTORICAL_DB_PATH",
                 "THESIS_HISTORICAL_DB_SHA256", "THESIS_HISTORICAL_DATASET_ID"):
        monkeypatch.delenv(name, raising=False)
    readiness = paper_api.thesis_product_readiness()["historical_thesis_data"]
    assert readiness["status"] == "BLOCKED"
    assert readiness["immutable_required"] is True


def test_market_state_changes_use_only_recent_confirmed_live_candles(
        monkeypatch: pytest.MonkeyPatch) -> None:
    now, width = 1_800_000_000, 14_400
    rows = [
        {"ts": now - width * 2, "candle_close_ts": now - width,
         "confirmed": True, "_source_store": "market_candles"},
        {"ts": now - width, "candle_close_ts": now,
         "confirmed": True, "_source_store": "market_candles"},
        {"ts": now, "candle_close_ts": now + width,
         "confirmed": True, "_source_store": "market_candles"},
    ]
    reader = type("Reader", (), {"candles": lambda *_args: rows})()
    calls: list[int] = []
    context = type("Context", (), {"context": lambda _self, _instrument, **kwargs:
                    calls.append(kwargs["as_of"]) or {"as_of": kwargs["as_of"]}})()
    engine = type("Engine", (), {"compare": lambda _self, _previous, current: {
        "transitions": [{"transition_timestamp": current["as_of"]}]}})()
    monkeypatch.setattr(paper_api.time, "time", lambda: now)
    monkeypatch.setattr(paper_api, "MARKET_DATA_READER_V2", reader)
    monkeypatch.setattr(paper_api, "MARKET_CONTEXT_V2", context)
    monkeypatch.setattr(paper_api, "MARKET_STATE_ENGINE_V2", engine)
    changes = paper_api.recent_market_state_changes(hours=24)
    assert calls == [now - width, now]
    assert changes[0]["current_as_of"] == now

    rows[-2]["_source_store"] = "historical_candles"
    assert paper_api.recent_market_state_changes(hours=24) == []

    rows[-2].update({"_source_store": "market_candles", "ts": now - width * 20,
                     "candle_close_ts": now - width * 19})
    rows[-3].update({"ts": now - width * 21, "candle_close_ts": now - width * 20})
    rows.pop()
    assert paper_api.recent_market_state_changes(hours=24) == []
