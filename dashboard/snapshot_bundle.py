"""Monthly, deduplicated archive bundles and compact paper DB rebuilding."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable

from .snapshot_storage import (
    ANALYSIS_SNAPSHOT_SCHEMA_VERSION,
    canonical_json,
    compact_analysis_snapshot,
    ensure_snapshot_v2_schema,
    validate_compact_payload,
)


SNAPSHOT_BUNDLE_VERSION = "analysis-snapshot-monthly-bundle-v2"
COMPACT_DATABASE_VERSION = "paper-compact-rebuild-v2"


class SnapshotBundleError(RuntimeError):
    pass


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _compress(raw: bytes, preferred: str) -> tuple[str, bytes]:
    if preferred == "zstd":
        try:
            import zstandard  # type: ignore[import-not-found]
        except ImportError:
            preferred = "gzip"
        else:
            return "zstd", zstandard.ZstdCompressor(level=9).compress(raw)
    if preferred == "gzip":
        return "gzip", gzip.compress(raw, compresslevel=9, mtime=0)
    raise ValueError(f"unsupported compression: {preferred}")


def _decompress(codec: str, payload: bytes) -> bytes:
    if codec == "gzip":
        return gzip.decompress(payload)
    if codec == "zstd":
        try:
            import zstandard  # type: ignore[import-not-found]
        except ImportError as error:
            raise SnapshotBundleError("zstandard is required to restore this bundle") from error
        return zstandard.ZstdDecompressor().decompress(payload)
    raise SnapshotBundleError(f"unsupported codec: {codec}")


def _month(created_at: str) -> str:
    try:
        value = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise SnapshotBundleError(f"invalid snapshot timestamp: {created_at}") from error
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def _initialize_bundle(
    path: Path, *, month: str, source_hash: str, source_size: int, archive_time: str
) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """PRAGMA journal_mode=DELETE;
           PRAGMA synchronous=FULL;
           CREATE TABLE bundle_metadata(
             key TEXT PRIMARY KEY,value TEXT NOT NULL);
           CREATE TABLE payload_blobs(
             payload_sha256 TEXT PRIMARY KEY,
             codec TEXT NOT NULL,
             original_bytes INTEGER NOT NULL,
             compressed_bytes INTEGER NOT NULL,
             compressed_payload BLOB NOT NULL);
           CREATE TABLE snapshot_manifest(
             snapshot_id INTEGER PRIMARY KEY,
             created_at TEXT NOT NULL,
             instrument TEXT,
             payload_sha256 TEXT NOT NULL,
             original_bytes INTEGER NOT NULL,
             codec TEXT NOT NULL,
             source_row_identity TEXT NOT NULL,
             FOREIGN KEY(payload_sha256) REFERENCES payload_blobs(payload_sha256));
           CREATE INDEX idx_snapshot_manifest_created
             ON snapshot_manifest(created_at,snapshot_id);"""
    )
    metadata = {
        "version": SNAPSHOT_BUNDLE_VERSION,
        "utc_month": month,
        "source_database_sha256": source_hash,
        "source_database_bytes": str(source_size),
        "archive_time": archive_time,
        "restore_instructions": (
            "Use scripts/restore_analysis_snapshot.py --archive <bundle> "
            "--snapshot-id <id> --verify --payload"
        ),
    }
    connection.executemany(
        "INSERT INTO bundle_metadata VALUES(?,?)", sorted(metadata.items())
    )
    return connection


def _row_identity(
    snapshot_id: int, created_at: str, instrument: str | None, payload_hash: str
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "snapshot_id": snapshot_id,
                "created_at": created_at,
                "instrument": instrument,
                "payload_sha256": payload_hash,
            }
        ).encode()
    ).hexdigest()


def build_snapshot_bundles(
    source_database: Path | str,
    archive_directory: Path | str,
    *,
    compression: str = "zstd",
) -> dict[str, Any]:
    source = Path(source_database).resolve()
    target = Path(archive_directory).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    target.mkdir(parents=True, exist_ok=True)
    source_hash = file_sha256(source)
    archive_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    connections: dict[str, sqlite3.Connection] = {}
    pending: dict[str, Path] = {}
    counts: dict[str, dict[str, int]] = {}
    try:
        uri = source.as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as source_connection:
            source_connection.row_factory = sqlite3.Row
            for row in source_connection.execute(
                """SELECT id,created_at,instrument,payload
                   FROM analysis_snapshots ORDER BY created_at,id"""
            ):
                month = _month(str(row["created_at"]))
                if month not in connections:
                    path = target / f".analysis-snapshots-{month}.pending.sqlite"
                    if path.exists():
                        raise FileExistsError(path)
                    pending[month] = path
                    connections[month] = _initialize_bundle(
                        path,
                        month=month,
                        source_hash=source_hash,
                        source_size=source.stat().st_size,
                        archive_time=archive_time,
                    )
                    counts[month] = {
                        "snapshots": 0,
                        "original_bytes": 0,
                        "compressed_bytes": 0,
                        "unique_payloads": 0,
                    }
                connection = connections[month]
                raw = str(row["payload"]).encode("utf-8")
                digest = hashlib.sha256(raw).hexdigest()
                existing = connection.execute(
                    "SELECT codec FROM payload_blobs WHERE payload_sha256=?",
                    (digest,),
                ).fetchone()
                if existing is None:
                    codec, compressed = _compress(raw, compression)
                    connection.execute(
                        "INSERT INTO payload_blobs VALUES(?,?,?,?,?)",
                        (digest, codec, len(raw), len(compressed), compressed),
                    )
                    counts[month]["unique_payloads"] += 1
                    counts[month]["compressed_bytes"] += len(compressed)
                else:
                    codec = str(existing[0])
                identity = _row_identity(
                    int(row["id"]), str(row["created_at"]), row["instrument"], digest
                )
                connection.execute(
                    "INSERT INTO snapshot_manifest VALUES(?,?,?,?,?,?,?)",
                    (
                        int(row["id"]), str(row["created_at"]), row["instrument"],
                        digest, len(raw), codec, identity,
                    ),
                )
                counts[month]["snapshots"] += 1
                counts[month]["original_bytes"] += len(raw)
        bundles: list[dict[str, Any]] = []
        for month, connection in connections.items():
            connection.execute(
                "INSERT INTO bundle_metadata VALUES(?,?)",
                ("snapshot_count", str(counts[month]["snapshots"])),
            )
            connection.execute(
                "INSERT INTO bundle_metadata VALUES(?,?)",
                ("original_payload_bytes", str(counts[month]["original_bytes"])),
            )
            connection.commit()
            connection.execute("PRAGMA optimize")
            connection.close()
            digest = file_sha256(pending[month])
            destination = target / (
                f"analysis-snapshots-{month}-{digest[:16]}.bundle.sqlite"
            )
            os.replace(pending[month], destination)
            bundles.append(
                {
                    "utc_month": month,
                    "path": destination.name,
                    "bundle_sha256": digest,
                    "bundle_bytes": destination.stat().st_size,
                    **counts[month],
                }
            )
        index = {
            "version": SNAPSHOT_BUNDLE_VERSION,
            "source_database_sha256": source_hash,
            "source_database_bytes": source.stat().st_size,
            "archive_time": archive_time,
            "bundles": sorted(bundles, key=lambda item: item["utc_month"]),
        }
        index_raw = (json.dumps(index, sort_keys=True, indent=2) + "\n").encode()
        index["index_sha256"] = hashlib.sha256(index_raw).hexdigest()
        index_path = target / "archive-index.json"
        temporary = index_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(index, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, index_path)
        return index
    finally:
        for connection in connections.values():
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _bundle_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def restore_snapshot(
    bundle: Path | str, snapshot_id: int, *, verify: bool = True
) -> tuple[dict[str, Any], str]:
    path = Path(bundle).resolve()
    with closing(_bundle_connection(path)) as connection:
        row = connection.execute(
            """SELECT m.*,b.compressed_payload,b.compressed_bytes
               FROM snapshot_manifest m JOIN payload_blobs b USING(payload_sha256)
               WHERE snapshot_id=?""",
            (int(snapshot_id),),
        ).fetchone()
        if row is None:
            raise SnapshotBundleError(
                f"snapshot {snapshot_id} does not exist in {path.name}"
            )
        raw = _decompress(str(row["codec"]), bytes(row["compressed_payload"]))
        if verify:
            if len(raw) != int(row["original_bytes"]):
                raise SnapshotBundleError("snapshot payload size mismatch")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row["payload_sha256"]:
                raise SnapshotBundleError("snapshot payload SHA-256 mismatch")
            expected_identity = _row_identity(
                int(row["snapshot_id"]), str(row["created_at"]),
                row["instrument"], digest,
            )
            if expected_identity != row["source_row_identity"]:
                raise SnapshotBundleError("snapshot row identity mismatch")
        metadata = {
            key: row[key]
            for key in (
                "snapshot_id", "created_at", "instrument", "payload_sha256",
                "original_bytes", "codec", "source_row_identity",
            )
        }
        return metadata, raw.decode("utf-8")


def verify_snapshot_bundle(
    bundle: Path | str, *, sample_size: int = 20
) -> dict[str, Any]:
    path = Path(bundle).resolve()
    checked = 0
    total_bytes = 0
    ids: list[int] = []
    with closing(_bundle_connection(path)) as connection:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise SnapshotBundleError(f"bundle quick_check failed: {integrity}")
        rows = connection.execute(
            "SELECT snapshot_id FROM snapshot_manifest ORDER BY snapshot_id"
        ).fetchall()
        ids = [int(row[0]) for row in rows]
    for snapshot_id in ids:
        metadata, payload = restore_snapshot(path, snapshot_id, verify=True)
        checked += 1
        total_bytes += len(payload.encode("utf-8"))
        if int(metadata["snapshot_id"]) != snapshot_id:
            raise SnapshotBundleError("snapshot enumeration mismatch")
    rng = random.Random(0)
    sampled = rng.sample(ids, min(sample_size, len(ids))) if ids else []
    for snapshot_id in sampled:
        restore_snapshot(path, snapshot_id, verify=True)
    return {
        "bundle": path.name,
        "bundle_sha256": file_sha256(path),
        "quick_check": "ok",
        "snapshot_count": checked,
        "original_payload_bytes": total_bytes,
        "sampled_restore_count": len(sampled),
        "verified": True,
    }


def _archive_lookup(archive_directory: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    index = json.loads(
        (archive_directory / "archive-index.json").read_text(encoding="utf-8")
    )
    for bundle in index["bundles"]:
        path = archive_directory / bundle["path"]
        with closing(_bundle_connection(path)) as connection:
            for row in connection.execute(
                """SELECT snapshot_id,payload_sha256,original_bytes,codec
                   FROM snapshot_manifest"""
            ):
                result[int(row["snapshot_id"])] = {
                    "archive_bundle_id": bundle["bundle_sha256"],
                    "archive_member": str(row["snapshot_id"]),
                    "archive_codec": row["codec"],
                    "payload_sha256": row["payload_sha256"],
                    "original_payload_bytes": int(row["original_bytes"]),
                    "bundle_path": bundle["path"],
                }
    return result


def _table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
        )
    ]


def canonical_table_hash(
    connection: sqlite3.Connection, table: str, *, exclude: Iterable[str] = ()
) -> str:
    columns = [
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
        if str(row[1]) not in set(exclude)
    ]
    digest = hashlib.sha256()
    select = ",".join(f'"{column}"' for column in columns)
    order = ",".join(f'"{column}"' for column in columns)
    for row in connection.execute(f'SELECT {select} FROM "{table}" ORDER BY {order}'):
        values = [
            {"_bytes_hex": bytes(value).hex()}
            if isinstance(value, (bytes, bytearray, memoryview))
            else value
            for value in row
        ]
        digest.update(canonical_json(values).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def build_compact_database(
    source_database: Path | str,
    archive_directory: Path | str,
    output_database: Path | str,
) -> dict[str, Any]:
    source = Path(source_database).resolve()
    archive = Path(archive_directory).resolve()
    output = Path(output_database).resolve()
    if output.exists():
        raise FileExistsError(output)
    lookup = _archive_lookup(archive)
    output.parent.mkdir(parents=True, exist_ok=True)
    work_directory = Path(tempfile.mkdtemp(prefix="paper-compact-", dir=output.parent))
    working = work_directory / "working.db"
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(working)) as dst:
                src.backup(dst, pages=2048)
        with closing(sqlite3.connect(working)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            ensure_snapshot_v2_schema(connection)
            ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM analysis_snapshots ORDER BY id"
                )
            ]
            if set(ids) != set(lookup):
                missing = sorted(set(ids) - set(lookup))[:10]
                extra = sorted(set(lookup) - set(ids))[:10]
                raise SnapshotBundleError(
                    f"archive mapping is incomplete: missing={missing}, extra={extra}"
                )
            for row in connection.execute(
                """SELECT id,created_at,instrument,payload
                   FROM analysis_snapshots ORDER BY id"""
            ):
                try:
                    analysis = json.loads(str(row["payload"]))
                except json.JSONDecodeError as error:
                    raise SnapshotBundleError(
                        f"snapshot {row['id']} is not valid JSON"
                    ) from error
                if not isinstance(analysis, dict):
                    raise SnapshotBundleError(
                        f"snapshot {row['id']} payload is not an object"
                    )
                compact = compact_analysis_snapshot(analysis)
                parsed = json.loads(compact.payload)
                exact_original_hash = hashlib.sha256(
                    str(row["payload"]).encode("utf-8")
                ).hexdigest()
                parsed["snapshot_id"] = int(row["id"])
                parsed["storage_mode"] = "INLINE_COMPACT_WITH_OFFHOST_ARCHIVE"
                parsed["original_artifact_hash"] = exact_original_hash
                payload = canonical_json(parsed)
                compact_bytes = validate_compact_payload(parsed)
                archive_row = lookup[int(row["id"])]
                if exact_original_hash != archive_row["payload_sha256"]:
                    raise SnapshotBundleError(
                        f"archive hash mismatch for snapshot {row['id']}"
                    )
                connection.execute(
                    """UPDATE analysis_snapshots SET
                       payload=?,payload_storage_mode=?,
                       payload_schema_version=?,payload_sha256=?,
                       original_payload_bytes=?,compact_payload_bytes=?,
                       archive_bundle_id=?,archive_member=?,archive_codec=?,
                       archive_verified_at=?,reconstructable=1,
                       retention_class=?,source_manifest_json=?
                       WHERE id=?""",
                    (
                        payload, "INLINE_COMPACT_WITH_OFFHOST_ARCHIVE",
                        ANALYSIS_SNAPSHOT_SCHEMA_VERSION,
                        archive_row["payload_sha256"],
                        archive_row["original_payload_bytes"], compact_bytes,
                        archive_row["archive_bundle_id"],
                        archive_row["archive_member"], archive_row["archive_codec"],
                        datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                        "PERMANENT_LEDGER", compact.source_manifest_json,
                        int(row["id"]),
                    ),
                )
            connection.commit()
            quoted = str(output).replace("'", "''")
            connection.execute(f"VACUUM INTO '{quoted}'")
        return verify_compact_database(source, output, archive)
    finally:
        shutil.rmtree(work_directory, ignore_errors=True)


def verify_compact_database(
    source_database: Path | str,
    compact_database: Path | str,
    archive_directory: Path | str,
) -> dict[str, Any]:
    source = Path(source_database).resolve()
    compact = Path(compact_database).resolve()
    lookup = _archive_lookup(Path(archive_directory).resolve())
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        with closing(sqlite3.connect(compact.as_uri() + "?mode=ro", uri=True)) as new:
            old.row_factory = sqlite3.Row
            new.row_factory = sqlite3.Row
            quick = new.execute("PRAGMA quick_check").fetchone()[0]
            foreign = new.execute("PRAGMA foreign_key_check").fetchall()
            old_schema = [
                tuple(row)
                for row in old.execute(
                    """SELECT type,name,tbl_name,sql FROM sqlite_master
                       WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
                )
            ]
            new_schema = [
                tuple(row)
                for row in new.execute(
                    """SELECT type,name,tbl_name,sql FROM sqlite_master
                       WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
                )
            ]
            old_tables = _table_names(old)
            protected: dict[str, Any] = {}
            for table in old_tables:
                if table == "analysis_snapshots":
                    continue
                protected[table] = {
                    "old_rows": old.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0],
                    "new_rows": new.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0],
                    "old_hash": canonical_table_hash(old, table),
                    "new_hash": canonical_table_hash(new, table),
                }
            old_snapshot = [
                tuple(row)
                for row in old.execute(
                    """SELECT id,created_at,instrument
                       FROM analysis_snapshots ORDER BY id"""
                )
            ]
            new_snapshot = [
                tuple(row)
                for row in new.execute(
                    """SELECT id,created_at,instrument
                       FROM analysis_snapshots ORDER BY id"""
                )
            ]
            archive_count = new.execute(
                """SELECT COUNT(*) FROM analysis_snapshots
                   WHERE archive_bundle_id IS NOT NULL
                   AND payload_sha256 IS NOT NULL"""
            ).fetchone()[0]
            schema_additions = {
                "analysis_snapshot_storage_telemetry",
                "idx_snapshot_storage_telemetry_created",
            }
            old_objects = {(row[0], row[1], row[2]) for row in old_schema}
            new_objects = {(row[0], row[1], row[2]) for row in new_schema}
            schema_compatible = old_objects <= new_objects and all(
                name in schema_additions
                or (kind, name, table) in old_objects
                or table == "analysis_snapshots"
                for kind, name, table in new_objects
            )
            protected_ok = all(
                item["old_rows"] == item["new_rows"]
                and item["old_hash"] == item["new_hash"]
                for item in protected.values()
            )
            return {
                "version": COMPACT_DATABASE_VERSION,
                "source_database_sha256": file_sha256(source),
                "compact_database_sha256": file_sha256(compact),
                "source_bytes": source.stat().st_size,
                "compact_bytes": compact.stat().st_size,
                "quick_check": quick,
                "foreign_key_violations": len(foreign),
                "schema_compatible": schema_compatible,
                "non_snapshot_tables": protected,
                "non_snapshot_tables_match": protected_ok,
                "snapshot_ids_and_metadata_match": old_snapshot == new_snapshot,
                "snapshot_count": len(new_snapshot),
                "archive_mapping_count": archive_count,
                "archive_mapping_complete": (
                    archive_count == len(new_snapshot) == len(lookup)
                ),
                "user_version_old": old.execute(
                    "PRAGMA user_version"
                ).fetchone()[0],
                "user_version_new": new.execute(
                    "PRAGMA user_version"
                ).fetchone()[0],
            }
