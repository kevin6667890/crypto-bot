from __future__ import annotations

import sqlite3

from dashboard.ai_market_analysis.readonly_adapter import ReadOnlyOrderflowAdapter
from dashboard.microstructure import (
    LIQUIDATION_QUERY_INDEX_NAME,
    MicrostructureStore,
)
from scripts.add_liquidation_lookup_index import MIGRATION_SHA256,apply_index


def _store(tmp_path):
    store=MicrostructureStore(tmp_path/"market_microstructure.db");store.initialize()
    for index,instrument in enumerate(("BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP")):
        store.insert_liquidation(instrument,{"ts":1_700_000_000_000+index,"ordId":str(index),
                                             "side":"sell","sz":"1","px":"100"})
    return store


def _plan(connection):
    return " ".join(str(row[3]).upper() for row in connection.execute(
        """EXPLAIN QUERY PLAN SELECT source_ts_ms,side,size,price,reliability_note
           FROM liquidation_observations WHERE instrument=? AND source_ts_ms>=?
           AND source_ts_ms<? ORDER BY source_ts_ms""",
        ("BTC-USDT-SWAP",0,2_000_000_000_000),
    ))


def test_fresh_schema_has_bounded_liquidation_index_and_plan(tmp_path):
    store=_store(tmp_path)
    with store.connect(readonly=True) as connection:
        indexes={row[1]:[column[2] for column in connection.execute(
            f'PRAGMA index_info("{row[1]}")')] for row in connection.execute(
                "PRAGMA index_list(liquidation_observations)")}
        plan=_plan(connection)
    assert indexes[LIQUIDATION_QUERY_INDEX_NAME]==["instrument","source_ts_ms"]
    assert "SEARCH LIQUIDATION_OBSERVATIONS USING INDEX" in plan
    assert "SCAN LIQUIDATION_OBSERVATIONS" not in plan
    assert "USE TEMP B-TREE" not in plan
    result=ReadOnlyOrderflowAdapter(store.path).read(
        "BTC-USDT-SWAP",1_699_999_000,1_700_001_000,"15m"
    )
    assert len(result["liquidation"])==1


def test_index_migration_preserves_data_and_is_idempotent(tmp_path):
    store=_store(tmp_path)
    with store.connect() as connection:
        connection.execute(f'DROP INDEX "{LIQUIDATION_QUERY_INDEX_NAME}"')
    first=apply_index(store.path,MIGRATION_SHA256)
    second=apply_index(store.path,MIGRATION_SHA256)
    assert first["rows_preserved"] is second["rows_preserved"] is True
    assert first["before"]["row_count"]==first["after"]["row_count"]==3
    assert first["before"]["content_sha256"]==first["after"]["content_sha256"]
    assert second["before"]==second["after"]
    assert first["integrity_check"]==second["integrity_check"]=="ok"
    plan=" ".join(first["after"]["plan"]).upper()
    assert "SEARCH LIQUIDATION_OBSERVATIONS USING INDEX" in plan
    assert "SCAN LIQUIDATION_OBSERVATIONS" not in plan
    assert "USE TEMP B-TREE" not in plan
