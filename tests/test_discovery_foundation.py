"""Deterministic foundation tests; no OKX/network calls are made here."""
from __future__ import annotations

import dashboard.dataset_service as dataset_module
from dashboard.dataset_service import END_TS, START_TS, DiscoveryDatasetService, quality
from dashboard.discovery_features import build_features
from dashboard.discovery_templates import parameter_hash, signal, validate
from dashboard.research_repository import ResearchRepository


def _rows(count: int = 250) -> list[dict[str, float | int]]:
    return [{"ts": START_TS + index * 900, "open": 100.0 + index, "high": 101.0 + index,
             "low": 99.0 + index, "close": 100.5 + index, "volume": 10.0 + index}
            for index in range(count)]


def test_discovery_quality_is_end_exclusive_and_reports_bad_rows() -> None:
    rows = _rows(3)
    rows.extend([rows[1].copy(), {**rows[2], "ts": START_TS + 3 * 900, "close": 0.0}])
    result = quality(rows, "15m", START_TS, START_TS + 3 * 900)
    assert result["expected_rows"] == 3
    assert result["actual_rows"] == 3
    assert result["duplicate_rows"] == 1
    assert result["missing_rows"] == 0
    assert result["status"] == "INCOMPLETE"


def test_future_mutation_does_not_change_prior_causal_features() -> None:
    original = _rows()
    changed = [dict(row) for row in original]
    changed[-1].update({"open": 999999.0, "high": 1000000.0, "low": 999998.0, "close": 999999.0, "volume": 999999.0})
    assert build_features(original)[:-1] == build_features(changed)[:-1]


def test_templates_are_bounded_and_cannot_enable_unavailable_flow() -> None:
    config = validate({"template": "TREND_PULLBACK", "parameters": {"fast_period": 20, "slow_period": 200}})
    assert config["template_version"] == "trend-pullback-v1"
    assert parameter_hash(config) == parameter_hash(config)
    try:
        validate({"template": "TREND_PULLBACK", "parameters": {"fast_period": 200, "slow_period": 20}})
    except ValueError:
        pass
    else:
        raise AssertionError("invalid MA ordering was accepted")
    try:
        validate({"template": "TREND_PULLBACK", "parameters": {"fast_period": 20, "slow_period": 200, "cvd_enabled": True}})
    except ValueError:
        pass
    else:
        raise AssertionError("PRICE_ONLY accepted CVD")


def test_discovery_manifest_and_partition_fingerprint_are_durable(tmp_path) -> None:
    repository = ResearchRepository(tmp_path / "research.db")
    dataset = repository.create_or_get_discovery_dataset("crypto-discovery-2024-2025-v1", START_TS, END_TS, ["BTC-USDT"], ["15m"])
    rows = _rows(2)
    report = quality(rows, "15m", START_TS, START_TS + 2 * 900)
    repository.upsert_discovery_partition(dataset["id"], "BTC-USDT", "15m", rows, report)
    first = repository.finish_discovery_dataset(dataset["id"])
    second = repository.finish_discovery_dataset(dataset["id"])
    assert first["dataset_fingerprint"] == second["dataset_fingerprint"]
    assert repository.discovery_dataset(dataset["id"])["partitions"][0]["fingerprint"] == report["fingerprint"]


def test_dataset_preparation_fixture_smoke_is_resumable_without_network(tmp_path, monkeypatch) -> None:
    start, end = START_TS, START_TS + 2 * 900
    monkeypatch.setattr(dataset_module, "START_TS", start)
    monkeypatch.setattr(dataset_module, "END_TS", end)
    repository = ResearchRepository(tmp_path / "fixture.db")
    service = DiscoveryDatasetService(repository)

    def fixture_download(instrument, timeframe, requested_start, requested_end, warmup_bars, **_kwargs):
        rows = [{"ts": requested_start + index * 900, "open": 100.0, "high": 101.0,
                 "low": 99.0, "close": 100.5, "volume": 1.0, "confirmed": 1}
                for index in range((requested_end - requested_start) // 900)]
        repository.upsert_candles(instrument, timeframe, rows)
        return rows, {"source": "fixture"}

    monkeypatch.setattr(service.history, "get_candles", fixture_download)
    result = service.prepare({"instruments": ["BTC-USDT"], "timeframes": ["15m"]})
    assert result["status"] == "COMPLETE"
    # A complete partition is reused; a second prepare does not fetch it again.
    assert service.prepare({"instruments": ["BTC-USDT"], "timeframes": ["15m"]})["dataset_fingerprint"] == result["dataset_fingerprint"]
