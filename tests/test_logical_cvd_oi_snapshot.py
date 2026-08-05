import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.logical_cvd_oi_snapshot import import_chunk, manifest, verify


def test_logical_snapshot_is_bounded_verified_and_resumable(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        for table in ("trade_flow_observations", "oi_observations"):
            connection.execute(
                f"CREATE TABLE {table}(source_ts_ms INTEGER,identity TEXT UNIQUE)"
            )
            connection.executemany(
                f"INSERT INTO {table} VALUES(?,?)", [(1, "a"), (2, "b")]
            )
        connection.execute(
            "CREATE TABLE collection_gaps(lane TEXT,instrument TEXT,"
            "start_ms INTEGER,end_ms INTEGER,reason TEXT,detected_at_ms INTEGER,"
            "resolved_at_ms INTEGER)"
        )
        connection.execute(
            "INSERT INTO collection_gaps VALUES('trades','BTC-USDT-SWAP',1,2,'test',3,NULL)"
        )
    frozen = manifest(source, 10)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(frozen), encoding="utf-8")
    destination = tmp_path / "shadow.db"
    script = Path(__file__).parents[1] / "scripts" / "logical_cvd_oi_snapshot.py"
    for table, specification in frozen["tables"].items():
        chunk = tmp_path / f"{table}.jsonl.gz"
        with chunk.open("wb") as output:
            subprocess.run(
                [sys.executable, str(script), "export", "--source", str(source),
                 "--table", table, "--low", "1", "--high",
                 str(specification["max_rowid"]), "--watermark",
                 str(specification["source_watermark_ms"])],
                stdout=output, check=True,
            )
        first = import_chunk(destination, manifest_path, table, chunk)
        resumed = import_chunk(destination, manifest_path, table, chunk)
        assert first["rows"] == (1 if table == "collection_gaps" else 2)
        assert resumed["resumed"] is True
        assert first["sha256"] == resumed["sha256"]
    report = verify(destination, manifest_path)
    assert report["ok"] is True
    assert report["quick_check"] == "ok"
