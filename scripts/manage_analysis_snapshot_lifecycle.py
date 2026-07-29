"""Audit and safely archive analysis_snapshots from an offline SQLite copy.

Dry-run is the default. Apply mode never deletes snapshot rows or runs VACUUM:
it writes verified content-addressed blobs, publishes a manifest, then replaces
large inline payloads with small verified archive stubs while retaining the
SQLite row identity and lineage metadata.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.analysis_snapshot_archive import (  # noqa: E402
    archive_stub,
    payload_sha256,
    read_archived_payload,
    stub_metadata,
)


TOOL_VERSION = "analysis-snapshot-lifecycle-v1"
MANIFEST_VERSION = "analysis-snapshot-archive-manifest-v1"
CHECKPOINT_VERSION = "analysis-snapshot-lifecycle-checkpoint-v1"
PRODUCTION_ROOTS = (
    Path("/opt/crypto-bot").resolve(),
    Path("/app/data_cache").resolve(),
    Path("/var/lib/docker/volumes").resolve(),
)
PROTECTED_TABLES = {
    "paper_trades",
    "paper_account",
    "decision_signals",
    "decision_signal_runs",
    "decision_evaluations",
    "backtest_trades",
}


def stable_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def row_identity(
    row_id: int, created_at: str, instrument: str | None, digest: str
) -> str:
    return stable_hash(
        {
            "id": row_id,
            "created_at": created_at,
            "instrument": instrument,
            "payload_sha256": digest,
        }
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _database_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _configured_production_paths() -> tuple[Path, ...]:
    configured = os.getenv("CRYPTO_BOT_PRODUCTION_DB_PATHS", "")
    values = [
        Path(value).expanduser().resolve()
        for value in configured.split(os.pathsep)
        if value.strip()
    ]
    return (*PRODUCTION_ROOTS, *values)


def refuse_production_apply(path: Path) -> None:
    resolved = path.resolve()
    for root in _configured_production_paths():
        if resolved == root or root in resolved.parents:
            raise PermissionError(
                f"--apply is forbidden for a production path: {resolved}"
            )


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    return connection


def connect_apply(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _snapshot_columns(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(analysis_snapshots)")
    }


def _reference_columns(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for table in sorted(_table_names(connection)):
        if table.startswith("sqlite_") or table == "analysis_snapshots":
            continue
        for foreign_key in connection.execute(
            f'PRAGMA foreign_key_list("{table}")'
        ):
            if foreign_key[2] == "analysis_snapshots":
                references.append((table, str(foreign_key[3])))
        for column in connection.execute(f'PRAGMA table_info("{table}")'):
            name = str(column[1]).lower()
            if name in {"analysis_snapshot_id", "snapshot_id"}:
                references.append((table, str(column[1])))
    return sorted(set(references))


def referenced_snapshot_ids(
    connection: sqlite3.Connection,
    snapshot_ids: Iterable[int],
) -> set[int]:
    selected = set(snapshot_ids)
    found: set[int] = set()
    if not selected:
        return found
    for table, column in _reference_columns(connection):
        # Bounded by the selected batch and usable even on legacy tables.
        for row_id in selected:
            row = connection.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}"=? LIMIT 1',
                (row_id,),
            ).fetchone()
            if row:
                found.add(row_id)
    return found


def _snapshot_type(payload: str) -> str:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return "INVALID_JSON"
    if not isinstance(value, Mapping):
        return "NON_OBJECT_JSON"
    for key in ("snapshot_type", "analysis_type", "type", "kind"):
        if value.get(key) not in (None, ""):
            return str(value[key])
    if "decision_input_summary" in value or "signal_id" in value:
        return "DECISION_ANALYSIS"
    return "LEGACY_ANALYSIS"


def _parse_created(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _eligible(
    row: sqlite3.Row,
    *,
    cutoff: datetime | None,
    snapshot_type: str | None,
) -> bool:
    if stub_metadata(str(row["payload"])) is not None:
        return False
    if cutoff is not None:
        created = _parse_created(str(row["created_at"]))
        if created is None or created >= cutoff:
            return False
    return snapshot_type is None or _snapshot_type(str(row["payload"])) == snapshot_type


def select_candidates(
    connection: sqlite3.Connection,
    *,
    older_than_days: int | None,
    snapshot_type: str | None,
    deduplicate: bool,
    max_rows: int | None,
    max_bytes: int | None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if "analysis_snapshots" not in _table_names(connection):
        raise ValueError("analysis_snapshots table is absent")
    required = {"id", "created_at", "payload"}
    if not required <= _snapshot_columns(connection):
        raise ValueError("analysis_snapshots schema is unsupported")
    cutoff = (
        (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
        if older_than_days is not None
        else None
    )
    instrument_expression = (
        "instrument" if "instrument" in _snapshot_columns(connection) else "NULL"
    )
    rows = connection.execute(
        f"""SELECT id,created_at,{instrument_expression} instrument,payload,
                   length(payload) payload_size
            FROM analysis_snapshots ORDER BY id"""
    )
    seen: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    selected_bytes = 0
    exact_duplicate_rows = 0
    scanned = 0
    for row in rows:
        scanned += 1
        payload = str(row["payload"])
        digest = payload_sha256(payload)
        duplicate_of = seen.get(digest)
        if duplicate_of is None:
            seen[digest] = int(row["id"])
        else:
            exact_duplicate_rows += 1
        if not _eligible(
            row, cutoff=cutoff, snapshot_type=snapshot_type
        ):
            continue
        if deduplicate and duplicate_of is None:
            continue
        size = int(row["payload_size"])
        if max_rows is not None and len(selected) >= max_rows:
            break
        if max_bytes is not None and selected and selected_bytes + size > max_bytes:
            break
        if max_bytes is not None and not selected and size > max_bytes:
            break
        selected.append(
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"]),
                "instrument": row["instrument"],
                "payload": payload,
                "payload_size": size,
                "payload_sha256": digest,
                "snapshot_type": _snapshot_type(payload),
                "duplicate_of": duplicate_of,
                "row_identity": row_identity(
                    int(row["id"]),
                    str(row["created_at"]),
                    row["instrument"],
                    digest,
                ),
            }
        )
        selected_bytes += size
    references = referenced_snapshot_ids(
        connection, (item["id"] for item in selected)
    )
    for item in selected:
        item["referenced"] = item["id"] in references
    return selected, {
        "rows_scanned": scanned,
        "unique_payload_hashes": len(seen),
        "exact_duplicate_rows": exact_duplicate_rows,
        "selected_rows": len(selected),
        "selected_bytes": selected_bytes,
        "reference_columns": [
            {"table": table, "column": column}
            for table, column in _reference_columns(connection)
        ],
    }


def _blob_relative(digest: str, codec: str) -> Path:
    extension = ".json.gz" if codec == "gzip" else ".json"
    return Path("blobs") / "sha256" / digest[:2] / f"{digest}{extension}"


def _encoded(payload: str, codec: str) -> bytes:
    raw = payload.encode("utf-8")
    if codec == "gzip":
        return gzip.compress(raw, compresslevel=9, mtime=0)
    if codec == "none":
        return raw
    raise ValueError(f"unsupported compression: {codec}")


def _decoded(path: Path, codec: str) -> bytes:
    value = path.read_bytes()
    return gzip.decompress(value) if codec == "gzip" else value


def archive_candidates(
    candidates: list[dict[str, Any]],
    archive_directory: Path,
    *,
    compression: str,
    database_fingerprint: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    root = archive_directory.resolve()
    entries: list[dict[str, Any]] = []
    for item in candidates:
        relative = _blob_relative(item["payload_sha256"], compression)
        blob = root / relative
        encoded = _encoded(item["payload"], compression)
        if not blob.exists():
            _atomic_write(blob, encoded)
        raw = _decoded(blob, compression)
        if payload_sha256(raw) != item["payload_sha256"]:
            raise ValueError(f"archive verification failed for snapshot {item['id']}")
        if len(raw) != item["payload_size"]:
            raise ValueError(f"archive size verification failed for snapshot {item['id']}")
        entries.append(
            {
                "snapshot_id": item["id"],
                "created_at": item["created_at"],
                "instrument": item["instrument"],
                "snapshot_type": item["snapshot_type"],
                "payload_sha256": item["payload_sha256"],
                "original_size": item["payload_size"],
                "codec": compression,
                "uri": relative.as_posix(),
                "row_identity": item["row_identity"],
                "duplicate_of": item["duplicate_of"],
                "referenced": item["referenced"],
            }
        )
    body = {
        "manifest_version": MANIFEST_VERSION,
        "tool_version": TOOL_VERSION,
        "database_fingerprint": dict(database_fingerprint),
        "entries": entries,
    }
    body["manifest_sha256"] = stable_hash(body)
    manifest = root / "manifests" / f"{body['manifest_sha256']}.json"
    _atomic_write(
        manifest,
        (json.dumps(body, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    verify_manifest(manifest, root)
    return manifest, body


def verify_manifest(path: Path, archive_directory: Path) -> dict[str, Any]:
    manifest = dict(_read_json(path))
    supplied = manifest.pop("manifest_sha256", None)
    if supplied != stable_hash(manifest):
        raise ValueError("archive manifest SHA-256 mismatch")
    root = archive_directory.resolve()
    for entry in manifest.get("entries", []):
        blob = (root / str(entry["uri"])).resolve()
        try:
            blob.relative_to(root)
        except ValueError as error:
            raise ValueError("manifest URI escapes archive directory") from error
        raw = _decoded(blob, str(entry["codec"]))
        if len(raw) != int(entry["original_size"]):
            raise ValueError("archive entry original size mismatch")
        if payload_sha256(raw) != entry["payload_sha256"]:
            raise ValueError("archive entry payload hash mismatch")
        expected = row_identity(
            int(entry["snapshot_id"]),
            str(entry["created_at"]),
            entry.get("instrument"),
            str(entry["payload_sha256"]),
        )
        if expected != entry["row_identity"]:
            raise ValueError("archive entry row identity mismatch")
    return {
        "verified": True,
        "entries": len(manifest.get("entries", [])),
        "manifest_sha256": supplied,
    }


def _checkpoint_payload(
    database: Path, manifest_hash: str, completed_ids: list[int]
) -> dict[str, Any]:
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "database_fingerprint": _database_fingerprint(database),
        "manifest_sha256": manifest_hash,
        "completed_snapshot_ids": completed_ids,
    }


def apply_manifest(
    database: Path,
    manifest_path: Path,
    archive_directory: Path,
    *,
    checkpoint: Path | None,
    resume: bool,
) -> dict[str, Any]:
    refuse_production_apply(database)
    verified = verify_manifest(manifest_path, archive_directory)
    manifest = _read_json(manifest_path)
    if (
        not resume
        and manifest.get("database_fingerprint") != _database_fingerprint(database)
    ):
        raise ValueError("database changed after dry-run/archive planning")
    completed: set[int] = set()
    if resume:
        if checkpoint is None or not checkpoint.is_file():
            raise ValueError("--resume requires an existing --checkpoint")
        saved = _read_json(checkpoint)
        if saved.get("checkpoint_version") != CHECKPOINT_VERSION:
            raise ValueError("checkpoint version mismatch")
        if saved.get("manifest_sha256") != manifest.get("manifest_sha256"):
            raise ValueError("checkpoint manifest mismatch")
        completed = {
            int(value) for value in saved.get("completed_snapshot_ids", [])
        }
    changed = 0
    with connect_apply(database) as connection:
        before_protected = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in PROTECTED_TABLES
            if table in _table_names(connection)
        }
        for entry in manifest.get("entries", []):
            row_id = int(entry["snapshot_id"])
            if row_id in completed:
                continue
            row = connection.execute(
                """SELECT id,created_at,instrument,payload
                   FROM analysis_snapshots WHERE id=?""",
                (row_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"snapshot disappeared before apply: {row_id}")
            existing_stub = stub_metadata(str(row["payload"]))
            if existing_stub is not None:
                if existing_stub["sha256"] != entry["payload_sha256"]:
                    raise ValueError("existing archive stub hash mismatch")
            else:
                digest = payload_sha256(str(row["payload"]))
                identity = row_identity(
                    row_id,
                    str(row["created_at"]),
                    row["instrument"],
                    digest,
                )
                if digest != entry["payload_sha256"] or identity != entry["row_identity"]:
                    raise ValueError(f"snapshot identity changed before apply: {row_id}")
                replacement = archive_stub(
                    uri=str(entry["uri"]),
                    digest=digest,
                    codec=str(entry["codec"]),
                    original_size=int(entry["original_size"]),
                )
                # Verify the adapter before changing the offline copy.
                if read_archived_payload(replacement, archive_directory) != str(
                    row["payload"]
                ):
                    raise ValueError("archive adapter round-trip mismatch")
                connection.execute(
                    "UPDATE analysis_snapshots SET payload=? WHERE id=?",
                    (replacement, row_id),
                )
                changed += 1
            connection.commit()
            completed.add(row_id)
            if checkpoint is not None:
                payload = _checkpoint_payload(
                    database,
                    str(manifest["manifest_sha256"]),
                    sorted(completed),
                )
                _atomic_write(
                    checkpoint,
                    (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                )
        after_protected = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
            for table in before_protected
        }
        if before_protected != after_protected:
            raise RuntimeError("protected order/accounting/lineage tables changed")
    return {
        **verified,
        "applied_rows": changed,
        "completed_rows": len(completed),
        "delete_statements": 0,
        "vacuum_executed": False,
        "protected_tables_unchanged": True,
    }


def audit_database(path: Path) -> dict[str, Any]:
    with connect_read_only(path) as connection:
        candidates, summary = select_candidates(
            connection,
            older_than_days=None,
            snapshot_type=None,
            deduplicate=False,
            max_rows=None,
            max_bytes=None,
        )
    sizes = sorted(item["payload_size"] for item in candidates)

    def percentile(value: float) -> int | None:
        if not sizes:
            return None
        index = max(0, int((len(sizes) * value + 0.999999)) - 1)
        return sizes[min(index, len(sizes) - 1)]

    types = Counter(item["snapshot_type"] for item in candidates)
    instruments = Counter(item["instrument"] or "UNKNOWN" for item in candidates)
    return {
        "database_fingerprint": _database_fingerprint(path),
        "analysis_snapshots": {
            **summary,
            "payload_size": {
                "p50": percentile(0.50),
                "p90": percentile(0.90),
                "p95": percentile(0.95),
                "p99": percentile(0.99),
                "max": max(sizes) if sizes else None,
                "total": sum(sizes),
            },
            "snapshot_types": dict(sorted(types.items())),
            "instruments": dict(sorted(instruments.items())),
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--database", type=Path, required=True)
    result.add_argument("--report", type=Path)
    result.add_argument("--older-than-days", type=int)
    result.add_argument("--snapshot-type")
    result.add_argument("--deduplicate", action="store_true")
    result.add_argument("--archive-directory", type=Path)
    result.add_argument(
        "--compression", choices=("gzip", "none"), default="gzip"
    )
    result.add_argument("--verify", type=Path, metavar="MANIFEST")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--max-rows", type=int)
    result.add_argument("--max-bytes", type=int)
    result.add_argument("--resume", action="store_true")
    result.add_argument("--checkpoint", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    database = arguments.database.resolve()
    if arguments.max_rows is not None and arguments.max_rows <= 0:
        raise SystemExit("--max-rows must be positive")
    if arguments.max_bytes is not None and arguments.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")
    if arguments.older_than_days is not None and arguments.older_than_days < 0:
        raise SystemExit("--older-than-days cannot be negative")
    if arguments.apply:
        refuse_production_apply(database)
        if arguments.archive_directory is None:
            raise SystemExit("--apply requires --archive-directory")
    if arguments.resume and not arguments.apply:
        raise SystemExit("--resume requires --apply")

    if arguments.verify:
        if arguments.archive_directory is None:
            raise SystemExit("--verify requires --archive-directory")
        payload: dict[str, Any] = verify_manifest(
            arguments.verify, arguments.archive_directory
        )
    else:
        before = database.read_bytes() if not arguments.apply else None
        if arguments.apply and arguments.resume:
            if arguments.checkpoint is None or not arguments.checkpoint.is_file():
                raise SystemExit("--resume requires an existing --checkpoint")
            saved = _read_json(arguments.checkpoint)
            manifest_hash = str(saved.get("manifest_sha256", ""))
            manifest_path = (
                arguments.archive_directory.resolve()
                / "manifests"
                / f"{manifest_hash}.json"
            )
            payload = {
                "tool_version": TOOL_VERSION,
                "mode": "APPLY_RESUME",
                "database_fingerprint": _database_fingerprint(database),
                "manifest": {
                    "path": str(manifest_path),
                    "sha256": manifest_hash,
                },
                "apply": apply_manifest(
                    database,
                    manifest_path,
                    arguments.archive_directory.resolve(),
                    checkpoint=arguments.checkpoint,
                    resume=True,
                ),
            }
            rendered = json.dumps(payload, sort_keys=True, indent=2)
            if arguments.report:
                _atomic_write(
                    arguments.report, (rendered + "\n").encode("utf-8")
                )
            print(rendered)
            return 0
        with connect_read_only(database) as connection:
            candidates, selection = select_candidates(
                connection,
                older_than_days=arguments.older_than_days,
                snapshot_type=arguments.snapshot_type,
                deduplicate=arguments.deduplicate,
                max_rows=arguments.max_rows,
                max_bytes=arguments.max_bytes,
            )
        payload = {
            "tool_version": TOOL_VERSION,
            "mode": "APPLY" if arguments.apply else "DRY_RUN",
            "database_fingerprint": _database_fingerprint(database),
            "selection": selection,
            "actions": {
                "archive_rows": len(candidates),
                "replace_payload_with_stub": len(candidates),
                "delete_rows": 0,
                "vacuum": False,
            },
        }
        if arguments.apply:
            archive = arguments.archive_directory.resolve()
            manifest_path, manifest = archive_candidates(
                candidates,
                archive,
                compression=arguments.compression,
                database_fingerprint=_database_fingerprint(database),
            )
            payload["manifest"] = {
                "path": str(manifest_path),
                "sha256": manifest["manifest_sha256"],
            }
            payload["apply"] = apply_manifest(
                database,
                manifest_path,
                archive,
                checkpoint=arguments.checkpoint,
                resume=arguments.resume,
            )
        else:
            payload["database_unchanged"] = database.read_bytes() == before
    rendered = json.dumps(payload, sort_keys=True, indent=2)
    if arguments.report:
        _atomic_write(arguments.report, (rendered + "\n").encode("utf-8"))
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
