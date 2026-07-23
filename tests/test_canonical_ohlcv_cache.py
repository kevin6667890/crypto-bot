from __future__ import annotations

from dashboard.dataset_service import RAW_START_TS, START_TS, END_TS, fingerprint, quality
from dashboard.okx_history import OkxHistoryClient
from dashboard.research_repository import ResearchRepository


def candle(ts: int, confirmed: str = "1", close: str = "100.5") -> list[str]:
    return [str(ts * 1000), "100", "101", "99", close, "3", "0", "0", confirmed]


def test_mocked_pagination_resumes_and_is_idempotent(tmp_path, monkeypatch) -> None:
    repo = ResearchRepository(tmp_path / "cache.db")
    client = OkxHistoryClient(repo)
    pages = iter([[candle(START_TS + 900), candle(START_TS)], [candle(START_TS - 900)], []])
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: next(pages))
    client.materialize_partition("BTC-USDT", "15m", START_TS, START_TS + 2 * 900)
    assert [row["ts"] for row in repo.candles("BTC-USDT", "15m", START_TS, START_TS + 2 * 900)] == [START_TS, START_TS + 900]
    # A later empty/transient response cannot erase valid committed data.
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: [])
    client.materialize_partition("BTC-USDT", "15m", START_TS, START_TS + 2 * 900)
    assert len(repo.candles("BTC-USDT", "15m", START_TS, START_TS + 2 * 900)) == 2


def test_quality_rejects_bad_rows_and_reports_gaps_without_fabrication() -> None:
    rows = [
        {"ts": START_TS, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "confirmed": 1},
        {"ts": START_TS, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "confirmed": 1},
        {"ts": START_TS + 2 * 900, "open": 100, "high": 99, "low": 101, "close": float("nan"), "volume": 1, "confirmed": 1},
        {"ts": START_TS + 3 * 900, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "confirmed": 0},
        {"ts": START_TS + 4 * 900 + 1, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "confirmed": 1},
    ]
    report = quality(rows, "15m", START_TS, START_TS + 5 * 900)
    assert report["duplicate_rows"] == 1 and report["gap_count"] == 1
    assert report["gap_intervals"][0]["start"] == START_TS + 900
    assert report["malformed_nonfinite_rows"] and report["unconfirmed_rows"] == 1 and report["alignment_errors"]
    assert report["status"] == "INCOMPLETE"


def test_fingerprint_is_deterministic_and_ignores_metadata() -> None:
    rows = [{"ts": START_TS, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 0, "confirmed": 1, "downloaded_at": "old"}]
    changed_metadata = [{**rows[0], "downloaded_at": "new", "id": 99}]
    assert fingerprint(rows) == fingerprint(changed_metadata)


def test_warmup_is_loadable_but_discovery_guard_rejects_holdout(tmp_path) -> None:
    repo = ResearchRepository(tmp_path / "cache.db")
    repo.upsert_candles("BTC-USDT", "1D", [{"ts": RAW_START_TS, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}])
    assert repo.discovery_development_candles("BTC-USDT", "1D", RAW_START_TS, START_TS - 1)
    try:
        repo.discovery_development_candles("BTC-USDT", "1D", START_TS, 1746057600)
    except ValueError:
        pass
    else:
        raise AssertionError("holdout was readable by discovery")


def test_daily_ma200_has_raw_warmup_and_features_do_not_mutate_rows() -> None:
    from dashboard.discovery_features import build_features
    rows = [{"ts": RAW_START_TS + index * 86400, "open": 1 + index, "high": 2 + index, "low": 1 + index, "close": 1.5 + index, "volume": 1} for index in range(400)]
    before = [dict(row) for row in rows]
    assert build_features(rows)[199]["sma_200"] is not None
    assert rows == before
