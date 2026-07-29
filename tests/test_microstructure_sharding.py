from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboard.microstructure_sharding import (
    DAY_MS,
    CursorError,
    ManifestError,
    MissingShardError,
    MonthlyMicrostructureStore,
    month_start_ms,
    months_for_range,
    utc_month,
)


JAN = month_start_ms("2024_01")
FEB = month_start_ms("2024_02")
MAR = month_start_ms("2024_03")
BACKUP = Path(r"C:\crypto-bot-offhost-backups\2026-07-28_030605Z")


def new_store(tmp_path: Path) -> MonthlyMicrostructureStore:
    return MonthlyMicrostructureStore(
        tmp_path / "prototype", hot_month="2024_01", create=True
    )


def add_trade(
    store: MonthlyMicrostructureStore,
    timestamp_ms: int,
    key: str,
    *,
    instrument: str = "BTC-USDT-SWAP",
    source: str = "OKX public trades",
    side: str = "buy",
    notional: float = 1.0,
) -> bool:
    return store.insert(
        "trades",
        instrument=instrument,
        source=source,
        timestamp_ms=timestamp_ms,
        payload={"side": side, "notional": notional},
        uniqueness_key=key,
        ingested_at_ms=timestamp_ms + 10,
    )


def populated_store(tmp_path: Path) -> MonthlyMicrostructureStore:
    store = new_store(tmp_path)
    add_trade(store, FEB - 3, "jan-c", source="source-b")
    add_trade(store, FEB - 3, "jan-b", source="source-a")
    add_trade(store, FEB - 10, "jan-a", source="source-a")
    store.rotate("2024_02")
    add_trade(store, FEB, "feb-a", source="source-a")
    add_trade(store, FEB + 5, "feb-b", source="source-b")
    add_trade(
        store,
        FEB + 8,
        "eth-a",
        instrument="ETH-USDT-SWAP",
        source="source-a",
    )
    return store


def all_pages(
    store: MonthlyMicrostructureStore,
    *,
    limit: int,
    instrument: str | None = None,
    source: str | None = None,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    cursor = None
    while True:
        page = store.query(
            "trades",
            start_ms=FEB - 100,
            end_ms=FEB + 100,
            instrument=instrument,
            source=source,
            limit=limit,
            cursor=cursor,
        )
        result.extend(page.rows)
        if page.next_cursor is None:
            return result
        cursor = page.next_cursor


def test_utc_month_routing_is_timezone_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = FEB - 1
    assert utc_month(before) == "2024_01"
    assert utc_month(FEB) == "2024_02"
    assert months_for_range(before, FEB + 1) == ("2024_01", "2024_02")
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    if hasattr(os, "tzset"):
        os.tzset()
    assert utc_month(before) == "2024_01"
    store = new_store(tmp_path)
    assert add_trade(store, before, "utc-route")
    assert store.query(
        "trades", start_ms=before, end_ms=FEB
    ).rows[0]["month"] == "2024_01"


def test_cross_month_ordering_is_timestamp_then_identity(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    rows = store.query(
        "trades", start_ms=FEB - 100, end_ms=FEB + 100
    ).rows
    order = [(row["timestamp_ms"], row["uniqueness_key"]) for row in rows]
    assert order == sorted(order)
    assert [row["uniqueness_key"] for row in rows[:5]] == [
        "jan-a",
        "jan-b",
        "jan-c",
        "feb-a",
        "feb-b",
    ]


def test_cross_month_pagination_has_no_duplicates(tmp_path: Path) -> None:
    rows = all_pages(populated_store(tmp_path), limit=2)
    keys = [row["uniqueness_key"] for row in rows]
    assert len(keys) == len(set(keys))


def test_cross_month_pagination_has_no_omissions(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    whole = store.query(
        "trades", start_ms=FEB - 100, end_ms=FEB + 100
    ).rows
    paged = all_pages(store, limit=2)
    assert [row["uniqueness_key"] for row in paged] == [
        row["uniqueness_key"] for row in whole
    ]


def test_instrument_filter_and_index_swap_mapping(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    btc = store.query(
        "trades",
        start_ms=FEB - 100,
        end_ms=FEB + 100,
        instrument="BTC-USDT-SWAP",
    ).rows
    assert btc and {row["instrument"] for row in btc} == {"BTC-USDT-SWAP"}
    store.insert(
        "index",
        instrument="BTC-USDT-SWAP",
        source="OKX index candles",
        timestamp_ms=FEB + 20,
        payload={"close": 42_000},
        uniqueness_key="index-a",
    )
    mapped = store.query(
        "index",
        start_ms=FEB,
        end_ms=FEB + 100,
        instrument="BTC-USDT-SWAP",
    ).rows
    assert [row["instrument"] for row in mapped] == ["BTC-USDT"]


def test_source_filter(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    rows = store.query(
        "trades",
        start_ms=FEB - 100,
        end_ms=FEB + 100,
        source="source-b",
    ).rows
    assert [row["uniqueness_key"] for row in rows] == ["jan-c", "feb-b"]


def test_missing_shard_is_an_explicit_error(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    with pytest.raises(MissingShardError, match="2024_03"):
        store.query("trades", start_ms=FEB, end_ms=MAR + 1)


def test_manifest_checksum_paths_and_states_are_validated(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.validate_manifest()
    payload = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    payload["shards"][0]["path"] = "../../escape.db"
    store.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match="checksum"):
        MonthlyMicrostructureStore(store.root)


def test_cvd_resets_at_cross_month_utc_midnight(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    add_trade(store, FEB - 2, "buy-jan", side="buy", notional=10)
    add_trade(store, FEB - 1, "sell-jan", side="sell", notional=3)
    store.rotate("2024_02")
    add_trade(store, FEB, "sell-feb", side="sell", notional=2)
    add_trade(store, FEB + 1, "buy-feb", side="buy", notional=5)
    values = store.cvd_daily(
        start_ms=FEB - 10,
        end_ms=FEB + 10,
        instrument="BTC-USDT-SWAP",
    )
    assert [row["cumulative"] for row in values] == [10, 7, -2, 3]
    assert values[1]["utc_day_ms"] + DAY_MS == values[2]["utc_day_ms"]


def test_oi_absolute_values_are_continuous_across_month(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.insert(
        "oi",
        instrument="BTC-USDT-SWAP",
        source="OKX open interest",
        timestamp_ms=FEB - 1,
        payload={"oi_usd": 100.0},
        uniqueness_key="oi-jan",
    )
    store.rotate("2024_02")
    store.insert(
        "oi",
        instrument="BTC-USDT-SWAP",
        source="OKX open interest",
        timestamp_ms=FEB,
        payload={"oi_usd": 105.0},
        uniqueness_key="oi-feb",
    )
    rows = store.oi_absolute(
        start_ms=FEB - 2,
        end_ms=FEB + 1,
        instrument="BTC-USDT-SWAP",
    )
    assert [row["payload"]["oi_usd"] for row in rows] == [100.0, 105.0]


def test_late_data_routes_to_overlay_for_its_utc_month(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.rotate("2024_02")
    assert add_trade(store, FEB - 1_000, "late-jan")
    with sqlite3.connect(store.central_path) as connection:
        target = connection.execute(
            "SELECT target_month FROM late_arrivals WHERE uniqueness_key='late-jan'"
        ).fetchone()[0]
    assert target == "2024_01"
    rows = store.query(
        "trades", start_ms=FEB - 2_000, end_ms=FEB
    ).rows
    assert [row["uniqueness_key"] for row in rows] == ["late-jan"]


def test_duplicate_rows_are_idempotent_in_hot_and_late_paths(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert add_trade(store, JAN + 1, "same")
    assert not add_trade(store, JAN + 1, "same")
    store.rotate("2024_02")
    assert add_trade(store, JAN + 2, "late-same")
    assert not add_trade(store, JAN + 2, "late-same")
    rows = store.query("trades", start_ms=JAN, end_ms=FEB).rows
    assert [row["uniqueness_key"] for row in rows] == ["same", "late-same"]


def test_hot_cold_boundary_keeps_cold_file_read_only(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    add_trade(store, FEB - 1, "base")
    cold_path = store.shard_path("2024_01")
    store.rotate("2024_02")
    before = (cold_path.stat().st_size, cold_path.stat().st_mtime_ns)
    add_trade(store, FEB - 2, "late")
    store.query("trades", start_ms=FEB - 10, end_ms=FEB + 1)
    after = (cold_path.stat().st_size, cold_path.stat().st_mtime_ns)
    assert before == after
    assert any(
        item["path"] == str(cold_path) and item["mode"] == "ro-immutable"
        for item in store.connection_audit
    )
    assert add_trade(store, FEB, "hot")
    with sqlite3.connect(store.shard_path("2024_02")) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM trade_flow_observations"
        ).fetchone()[0] == 1


def test_offhost_backup_is_opened_immutable_and_unchanged() -> None:
    database = BACKUP / "market_microstructure.db"
    manifest = json.loads(
        (BACKUP / "manifest.json").read_text(encoding="utf-8-sig")
    )
    expected = manifest["files"]["market_microstructure"]
    before = (database.stat().st_size, database.stat().st_mtime_ns)
    uri = database.as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master"
        ).fetchone()[0] > 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden_write(value INTEGER)")
    after = (database.stat().st_size, database.stat().st_mtime_ns)
    assert before == after
    assert database.stat().st_size == expected["size"]


def test_prototype_never_accesses_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sqlite3.connect
    opened: list[str] = []

    def guarded(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        target = str(database)
        lowered = target.lower()
        assert "/opt/crypto-bot" not in lowered
        assert "data_cache/market_microstructure" not in lowered.replace("\\", "/")
        opened.append(target)
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded)
    store = new_store(tmp_path)
    add_trade(store, JAN + 1, "local")
    store.query("trades", start_ms=JAN, end_ms=JAN + 2)
    assert opened
    assert all("/opt/" not in item.replace("\\", "/") for item in opened)


def test_prototype_has_no_strategy_order_or_network_api_dependency() -> None:
    module = (
        Path(__file__).parents[1] / "dashboard" / "microstructure_sharding.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "requests.",
        "httpx.",
        "aiohttp.",
        "place_order",
        "submit_order",
        "strategy_service",
        "order_api",
    )
    assert not any(token in module for token in forbidden)


def test_funding_events_and_query_plan_cache(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.insert(
        "funding_settled",
        instrument="BTC-USDT-SWAP",
        source="OKX funding history",
        timestamp_ms=FEB - 1,
        payload={"funding_rate": 0.0001, "funding_time_ms": FEB - 1},
        uniqueness_key="funding-jan",
    )
    store.rotate("2024_02")
    store.insert(
        "funding_settled",
        instrument="BTC-USDT-SWAP",
        source="OKX funding history",
        timestamp_ms=FEB + 1,
        payload={"funding_rate": -0.0002, "funding_time_ms": FEB + 1},
        uniqueness_key="funding-feb",
    )
    first = store.funding_events(
        settled=True,
        start_ms=FEB - 2,
        end_ms=FEB + 2,
        instrument="BTC-USDT-SWAP",
    )
    second = store.funding_events(
        settled=True,
        start_ms=FEB - 2,
        end_ms=FEB + 2,
        instrument="BTC-USDT-SWAP",
    )
    assert [row["uniqueness_key"] for row in first] == [
        "funding-jan",
        "funding-feb",
    ]
    assert first == second
    assert store.cache_info()["hits"] >= 1


def test_cursor_is_bound_to_filter_set(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    page = store.query(
        "trades", start_ms=FEB - 100, end_ms=FEB + 100, limit=1
    )
    assert page.next_cursor
    with pytest.raises(CursorError, match="does not belong"):
        store.query(
            "trades",
            start_ms=FEB - 100,
            end_ms=FEB + 100,
            source="source-a",
            limit=1,
            cursor=page.next_cursor,
        )
