from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from dashboard.ai_market_analysis.readonly_adapter import MAX_ORDERFLOW_QUERY_SECONDS,ReadOnlyOrderflowAdapter


def database(path:Path,indexed=True):
    with sqlite3.connect(path) as c:
        key="PRIMARY KEY(instrument,resolution,bucket_ms)" if indexed else ""
        for table in ("cvd_aggregates","oi_aggregates","basis_aggregates"):
            c.execute(f"CREATE TABLE {table}(instrument TEXT,resolution TEXT,bucket_ms INTEGER,payload_json TEXT {','+key if key else ''})")
            c.execute(f"INSERT INTO {table} VALUES(?,?,?,?)",("ETH-USDT-SWAP","15m",0,json.dumps({"value":1})))


def test_query_only_bounded_indexed_reads(tmp_path):
    path=tmp_path/"flow.db"; database(path)
    adapter=ReadOnlyOrderflowAdapter(path); out=adapter.read("ETH-USDT-SWAP",0,900,"15m")
    assert set(out)=={"cvd","oi","basis","funding","liquidation","liquidation_complete"} and adapter.query_plans
    assert all("SEARCH" in str(row[-1]).upper() for row in adapter.query_plans)


def test_full_scan_is_rejected(tmp_path):
    path=tmp_path/"flow.db"; database(path,indexed=False)
    with pytest.raises(RuntimeError,match="scan"):
        ReadOnlyOrderflowAdapter(path).read("ETH-USDT-SWAP",0,900,"15m")


def test_adapter_never_creates_tables(tmp_path):
    path=tmp_path/"flow.db"; sqlite3.connect(path).close()
    ReadOnlyOrderflowAdapter(path).read("ETH-USDT-SWAP",0,900,"15m")
    with sqlite3.connect(path) as c: assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]==0


def test_bounded_range_guard(tmp_path):
    path=tmp_path/"flow.db"; database(path)
    with pytest.raises(ValueError): ReadOnlyOrderflowAdapter(path).read("ETH-USDT-SWAP",0,400*86400,"15m")


def test_exact_maximum_range_is_allowed(tmp_path):
    path=tmp_path/"flow.db"; database(path)
    ReadOnlyOrderflowAdapter(path).read("ETH-USDT-SWAP",0,MAX_ORDERFLOW_QUERY_SECONDS,"15m")


def test_one_second_over_maximum_range_is_rejected(tmp_path):
    path=tmp_path/"flow.db"; database(path)
    with pytest.raises(ValueError,match="bounded to 366 days"):
        ReadOnlyOrderflowAdapter(path).read(
            "ETH-USDT-SWAP",0,MAX_ORDERFLOW_QUERY_SECONDS+1,"15m"
        )


def test_supported_instrument_guard(tmp_path):
    path=tmp_path/"flow.db"; database(path)
    with pytest.raises(ValueError): ReadOnlyOrderflowAdapter(path).read("DOGE-USDT-SWAP",0,900,"15m")


def test_import_has_no_network_thread_or_database_side_effect(tmp_path):
    before=set(tmp_path.iterdir())
    subprocess.run([sys.executable,"-c","import dashboard.ai_market_analysis.orderflow_context_adapter"],cwd=Path(__file__).resolve().parents[2],check=True)
    assert set(tmp_path.iterdir())==before
