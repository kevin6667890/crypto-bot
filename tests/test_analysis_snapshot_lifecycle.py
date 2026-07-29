from __future__ import annotations

from datetime import datetime, timezone
import gzip
import inspect
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from dashboard.analysis_snapshot_archive import (
    payload_sha256,
    read_archived_payload,
    stub_metadata,
)
from scripts import manage_analysis_snapshot_lifecycle as lifecycle


def _database(path: Path, payloads: list[str] | None = None) -> Path:
    payloads = payloads or [
        json.dumps({"snapshot_type": "DECISION", "value": index})
        for index in range(4)
    ]
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE analysis_snapshots(
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                instrument TEXT, payload TEXT NOT NULL);
            CREATE TABLE snapshot_references(
                id INTEGER PRIMARY KEY, snapshot_id INTEGER,
                FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots(id));
            CREATE TABLE paper_trades(id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE paper_account(account_id INTEGER PRIMARY KEY);
            CREATE TABLE decision_signals(id INTEGER PRIMARY KEY);
            """
        )
        connection.executemany(
            "INSERT INTO analysis_snapshots VALUES(?,?,?,?)",
            [
                (
                    index,
                    f"2026-01-{index:02d}T00:00:00+00:00",
                    "BTC-USDT",
                    payload,
                )
                for index, payload in enumerate(payloads, 1)
            ],
        )
        connection.execute("INSERT INTO snapshot_references VALUES(1,1)")
        connection.execute("INSERT INTO paper_trades VALUES(1,'CLOSED')")
        connection.execute("INSERT INTO paper_account VALUES(1)")
        connection.execute("INSERT INTO decision_signals VALUES(1)")
    return path


def _apply(
    database: Path,
    archive: Path,
    *,
    checkpoint: Path | None = None,
    max_rows: int | None = None,
    max_bytes: int | None = None,
) -> dict:
    with lifecycle.connect_read_only(database) as connection:
        candidates, _ = lifecycle.select_candidates(
            connection,
            older_than_days=1,
            snapshot_type=None,
            deduplicate=False,
            max_rows=max_rows,
            max_bytes=max_bytes,
            now=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )
    manifest_path, _ = lifecycle.archive_candidates(
        candidates,
        archive,
        compression="gzip",
        database_fingerprint=lifecycle._database_fingerprint(database),
    )
    return lifecycle.apply_manifest(
        database,
        manifest_path,
        archive,
        checkpoint=checkpoint,
        resume=False,
    )


def test_production_path_always_rejects_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path / "paper_trades.db")
    monkeypatch.setenv("CRYPTO_BOT_PRODUCTION_DB_PATHS", str(tmp_path))
    with pytest.raises(PermissionError, match="production path"):
        lifecycle.refuse_production_apply(database)


def test_dry_run_does_not_modify_database(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    before = database.read_bytes()
    assert lifecycle.main(
        ["--database", str(database), "--max-rows", "2"]
    ) == 0
    assert database.read_bytes() == before


def test_payload_hash_is_stable() -> None:
    assert payload_sha256('{"a":1}') == payload_sha256(b'{"a":1}')
    assert payload_sha256('{"a":1}') != payload_sha256('{"a":2}')


def test_duplicate_payload_detection_is_exact(tmp_path: Path) -> None:
    repeated = json.dumps({"same": True})
    database = _database(
        tmp_path / "paper_trades.db",
        [repeated, repeated, json.dumps({"same": False})],
    )
    with lifecycle.connect_read_only(database) as connection:
        candidates, summary = lifecycle.select_candidates(
            connection,
            older_than_days=None,
            snapshot_type=None,
            deduplicate=True,
            max_rows=None,
            max_bytes=None,
        )
    assert summary["exact_duplicate_rows"] == 1
    assert [row["id"] for row in candidates] == [2]
    assert candidates[0]["duplicate_of"] == 1


def test_referenced_snapshot_is_preserved(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    _apply(database, tmp_path / "archive")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload FROM analysis_snapshots WHERE id=1"
        ).fetchone()
        assert row is not None
        assert stub_metadata(row[0]) is not None
        assert connection.execute(
            "SELECT snapshot_id FROM snapshot_references"
        ).fetchone()[0] == 1


def test_archive_manifest_is_complete_and_verified(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    archive = tmp_path / "archive"
    with lifecycle.connect_read_only(database) as connection:
        candidates, _ = lifecycle.select_candidates(
            connection,
            older_than_days=None,
            snapshot_type=None,
            deduplicate=False,
            max_rows=None,
            max_bytes=None,
        )
    path, manifest = lifecycle.archive_candidates(
        candidates,
        archive,
        compression="gzip",
        database_fingerprint=lifecycle._database_fingerprint(database),
    )
    assert len(manifest["entries"]) == 4
    assert all(
        {
            "snapshot_id",
            "payload_sha256",
            "original_size",
            "codec",
            "uri",
            "row_identity",
            "referenced",
        }
        <= set(entry)
        for entry in manifest["entries"]
    )
    assert lifecycle.verify_manifest(path, archive)["verified"] is True


def test_compressed_payload_round_trip_is_verified(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    archive = tmp_path / "archive"
    original = sqlite3.connect(database).execute(
        "SELECT payload FROM analysis_snapshots WHERE id=1"
    ).fetchone()[0]
    _apply(database, archive, max_rows=1)
    payload = sqlite3.connect(database).execute(
        "SELECT payload FROM analysis_snapshots WHERE id=1"
    ).fetchone()[0]
    metadata = stub_metadata(payload)
    assert metadata is not None
    assert gzip.decompress((archive / metadata["uri"]).read_bytes()).decode() == original
    assert read_archived_payload(payload, archive) == original


def test_checkpoint_resume_is_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    archive = tmp_path / "archive"
    checkpoint = tmp_path / "checkpoint.json"
    result = _apply(database, archive, checkpoint=checkpoint, max_rows=2)
    assert result["applied_rows"] == 2
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    manifest = archive / "manifests" / f"{saved['manifest_sha256']}.json"
    before = database.read_bytes()
    resumed = lifecycle.apply_manifest(
        database, manifest, archive, checkpoint=checkpoint, resume=True
    )
    assert resumed["applied_rows"] == 0
    assert database.read_bytes() == before


def test_archive_failure_does_not_modify_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path / "paper_trades.db")
    before = database.read_bytes()
    with lifecycle.connect_read_only(database) as connection:
        candidates, _ = lifecycle.select_candidates(
            connection,
            older_than_days=None,
            snapshot_type=None,
            deduplicate=False,
            max_rows=None,
            max_bytes=None,
        )
    monkeypatch.setattr(
        lifecycle, "_encoded", lambda *_: (_ for _ in ()).throw(OSError("stop"))
    )
    with pytest.raises(OSError, match="stop"):
        lifecycle.archive_candidates(
            candidates,
            tmp_path / "archive",
            compression="gzip",
            database_fingerprint=lifecycle._database_fingerprint(database),
        )
    assert database.read_bytes() == before


def test_max_rows_and_bytes_are_enforced(tmp_path: Path) -> None:
    payloads = [json.dumps({"value": "x" * size}) for size in (10, 20, 30)]
    database = _database(tmp_path / "paper_trades.db", payloads)
    with lifecycle.connect_read_only(database) as connection:
        rows, summary = lifecycle.select_candidates(
            connection,
            older_than_days=None,
            snapshot_type=None,
            deduplicate=False,
            max_rows=2,
            max_bytes=len(payloads[0]) + len(payloads[1]),
        )
    assert len(rows) == 2
    assert summary["selected_bytes"] <= len(payloads[0]) + len(payloads[1])


def test_order_accounting_and_lineage_tables_are_unchanged(tmp_path: Path) -> None:
    database = _database(tmp_path / "paper_trades.db")
    with sqlite3.connect(database) as connection:
        before = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("paper_trades", "paper_account", "decision_signals")
        }
    _apply(database, tmp_path / "archive")
    with sqlite3.connect(database) as connection:
        after = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in before
        }
    assert after == before


def test_tool_never_executes_vacuum_or_delete() -> None:
    source = inspect.getsource(lifecycle).upper()
    assert 'EXECUTE("VACUUM' not in source
    assert "DELETE FROM ANALYSIS_SNAPSHOTS" not in source
