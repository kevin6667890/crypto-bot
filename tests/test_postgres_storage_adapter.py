from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from dashboard.postgres_storage import (
    PARTITION_SQL_PATH,
    SCHEMA_SQL_PATH,
    TRADE_RANGE_PAGE_SQL,
    TRADE_UPSERT_SQL,
    PageCursor,
    PostgresTradeStorageAdapter,
    coverage_summary,
    deduplicate_trade_rows,
    epoch_ms_to_timestamptz,
    paginate_trade_rows,
    partition_name,
    sqlite_trade_to_postgres,
    timestamptz_to_epoch_ms,
)


def sqlite_trade(
    key: str = "trade-key",
    source_ts_ms: int = 1_785_207_975_051,
    **overrides,
):
    row = {
        "source": "OKX public trade",
        "source_version": "microstructure-v1",
        "instrument": "BTC-USDT-SWAP",
        "source_ts_ms": source_ts_ms,
        "ingested_at_ms": source_ts_ms + 7,
        "resolution": "trade",
        "state": "confirmed",
        "source_identity": "991",
        "uniqueness_key": key,
        "trade_id": "991",
        "side": "buy",
        "price": 118_500.25,
        "size": 3.0,
        "contract_value": 0.01,
        "notional": 3_555.0075,
        "provenance_table": None,
    }
    row.update(overrides)
    return row


def test_sqlite_fields_map_without_value_reinterpretation():
    source = sqlite_trade()
    mapped = sqlite_trade_to_postgres(source)
    for field, value in source.items():
        assert mapped[field] == value
    assert mapped["observed_at"].tzinfo == timezone.utc
    assert mapped["ingested_at"].tzinfo == timezone.utc


@pytest.mark.parametrize(
    "epoch_ms",
    [0, 1, -1, 1_785_207_975_051, 253_402_300_799_999],
)
def test_timestamp_semantics_round_trip_exact_milliseconds(epoch_ms):
    assert timestamptz_to_epoch_ms(epoch_ms_to_timestamptz(epoch_ms)) == epoch_ms
    with pytest.raises(ValueError):
        timestamptz_to_epoch_ms(datetime(2026, 7, 28))


def test_upsert_is_idempotent_by_partition_safe_unique_key():
    rows = deduplicate_trade_rows(
        [sqlite_trade(), sqlite_trade(), sqlite_trade("other")]
    )
    assert len(rows) == 2
    assert "ON CONFLICT (observed_at, uniqueness_key) DO NOTHING" in TRADE_UPSERT_SQL


def test_monthly_partition_routing_is_utc_and_deterministic():
    assert (
        partition_name("trade_flow_observations", 1_785_207_975_051)
        == "trade_flow_observations_2026_07"
    )
    assert "date_trunc('month'" in PARTITION_SQL_PATH.read_text(encoding="utf-8")


def test_range_query_reference_and_sql_sort_ascending():
    mapped = [
        sqlite_trade_to_postgres(sqlite_trade("b", 2000)),
        sqlite_trade_to_postgres(sqlite_trade("z", 1000)),
        sqlite_trade_to_postgres(sqlite_trade("a", 2000)),
    ]
    page = paginate_trade_rows(mapped, limit=10)
    assert [(row["source_ts_ms"], row["uniqueness_key"]) for row in page] == [
        (1000, "z"),
        (2000, "a"),
        (2000, "b"),
    ]
    assert "ORDER BY observed_at ASC, uniqueness_key ASC" in TRADE_RANGE_PAGE_SQL


def test_cursor_pagination_has_no_gap_or_overlap():
    mapped = [
        sqlite_trade_to_postgres(sqlite_trade(key, timestamp))
        for timestamp, key in [(1000, "a"), (1000, "b"), (2000, "c")]
    ]
    first = paginate_trade_rows(mapped, limit=2)
    cursor = PageCursor(first[-1]["observed_at"], first[-1]["uniqueness_key"])
    second = paginate_trade_rows(mapped, limit=2, cursor=cursor)
    assert [row["uniqueness_key"] for row in first + second] == ["a", "b", "c"]


def test_coverage_summary_matches_expected_bounds():
    rows = [
        sqlite_trade_to_postgres(
            sqlite_trade("btc-1", 1000, instrument="BTC-USDT-SWAP")
        ),
        sqlite_trade_to_postgres(
            sqlite_trade("btc-2", 3000, instrument="BTC-USDT-SWAP")
        ),
        sqlite_trade_to_postgres(
            sqlite_trade("eth-1", 2000, instrument="ETH-USDT-SWAP")
        ),
    ]
    assert coverage_summary(rows) == [
        {
            "instrument": "BTC-USDT-SWAP",
            "row_count": 2,
            "earliest_ms": 1000,
            "latest_ms": 3000,
            "earliest_at": epoch_ms_to_timestamptz(1000),
            "latest_at": epoch_ms_to_timestamptz(3000),
        },
        {
            "instrument": "ETH-USDT-SWAP",
            "row_count": 1,
            "earliest_ms": 2000,
            "latest_ms": 2000,
            "earliest_at": epoch_ms_to_timestamptz(2000),
            "latest_at": epoch_ms_to_timestamptz(2000),
        },
    ]


def test_duplicate_does_not_increase_staged_row_count():
    once = deduplicate_trade_rows([sqlite_trade()])
    twice = deduplicate_trade_rows([sqlite_trade(), sqlite_trade()])
    assert len(once) == len(twice) == 1


def test_missing_values_keep_sql_null_semantics():
    mapped = sqlite_trade_to_postgres(
        sqlite_trade(trade_id=None, provenance_table=None)
    )
    assert mapped["trade_id"] is None
    assert mapped["provenance_table"] is None
    with pytest.raises(KeyError):
        sqlite_trade_to_postgres({"source_ts_ms": 0})


def test_cvd_and_oi_columns_are_not_reinterpreted():
    schema = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    assert "cumulative_anchored double precision NOT NULL" in schema
    assert "oi_contracts double precision" in schema
    assert "oi_currency double precision" in schema
    assert "oi_usd double precision" in schema
    assert "generic_metric" not in schema


def _test_dsn():
    dsn = os.getenv("CRYPTO_BOT_POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip("CRYPTO_BOT_POSTGRES_TEST_DSN is not configured")
    if "prod" in dsn.lower() or "production" in dsn.lower():
        pytest.fail("refusing a production-like PostgreSQL test DSN")
    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg is not installed")
    return psycopg, dsn


def test_postgres_fixture_upsert_partition_range_and_coverage():
    """Optional integration: only an explicit, non-production test DSN runs."""
    psycopg, dsn = _test_dsn()
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
            cursor.execute(PARTITION_SQL_PATH.read_text(encoding="utf-8"))
            for parent in (
                "trade_flow_observations",
                "oi_observations",
                "price_observations",
                "funding_observations",
                "liquidation_observations",
                "cvd_aggregates",
                "oi_aggregates",
            ):
                cursor.execute(
                    "SELECT crypto_bot.ensure_monthly_partition(%s, %s)",
                    (parent, datetime(2026, 7, 1, tzinfo=timezone.utc)),
                )
            cursor.execute(
                "TRUNCATE crypto_bot.trade_flow_observations"
            )
        adapter = PostgresTradeStorageAdapter(connection)
        assert adapter.copy_batch([sqlite_trade(), sqlite_trade()]) == 1
        assert adapter.upsert(sqlite_trade()) == 0
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 1, tzinfo=timezone.utc)
        assert len(adapter.range_page(
            instrument="BTC-USDT-SWAP", start_at=start, end_at=end
        )) == 1
        assert adapter.coverage(start_at=start, end_at=end)[0][1] == 1
        connection.rollback()


def test_module_does_not_connect_or_read_production_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "intentionally-invalid-production-dsn")
    assert PostgresTradeStorageAdapter.__init__
    assert "DATABASE_URL" not in SCHEMA_SQL_PATH.read_text(encoding="utf-8")


def test_adapter_has_no_strategy_or_order_api_calls():
    source = (
        SCHEMA_SQL_PATH.read_text(encoding="utf-8")
        + PARTITION_SQL_PATH.read_text(encoding="utf-8")
        + __import__("inspect").getsource(PostgresTradeStorageAdapter)
    ).lower()
    assert "strategy api" not in source
    assert "order api" not in source
