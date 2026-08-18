"""Consistent backup and isolated restore verification for the AI report DB."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .report_migrations import FORBIDDEN_DATABASE_MARKERS, MigrationError

_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_database(path: Path) -> None:
    if any(marker in path.name.lower() for marker in FORBIDDEN_DATABASE_MARKERS):
        raise MigrationError("LEGACY_DATABASE_TARGET_FORBIDDEN")


def _artifact(path: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "mode": oct(path.stat().st_mode & 0o777),
    }


def _copy_stable_file(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        before = os.fstat(reader.fileno())
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        target.unlink(missing_ok=True)
        raise RuntimeError("STATE_FILE_CHANGED_DURING_BACKUP")


def create_consistent_backup(
    database: str | Path,
    output_directory: str | Path,
    *,
    state_files: Mapping[str, str | Path] | None = None,
    backup_id: str | None = None,
) -> dict[str, Any]:
    """Use SQLite's online backup API; never copy a live DB/WAL tuple directly."""
    source_path = Path(database).resolve()
    _guard_database(source_path)
    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError("BACKUP_DIRECTORY_ALREADY_EXISTS")
    output.mkdir(parents=True, mode=0o700)
    os.chmod(output, 0o700)
    artifacts: list[dict[str, Any]] = []
    database_status = "DATABASE_NOT_YET_PRESENT"
    if source_path.exists():
        destination_path = output / "ai_market_reports.db"
        source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=5)
        destination = sqlite3.connect(destination_path)
        try:
            source.execute("PRAGMA query_only=ON")
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"BACKUP_INTEGRITY_FAILED:{integrity}")
        finally:
            destination.close()
            source.close()
        os.chmod(destination_path, 0o600)
        artifacts.append(_artifact(destination_path, "ai_report_database"))
        database_status = "BACKED_UP"
    for index, (kind, raw_path) in enumerate(sorted((state_files or {}).items()), 1):
        if not _KIND.fullmatch(kind):
            raise ValueError("INVALID_STATE_FILE_KIND")
        source = Path(raw_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"STATE_FILE_MISSING:{kind}")
        suffix = "".join(source.suffixes)[-24:]
        target = output / f"state-{index:02d}-{kind}{suffix}"
        _copy_stable_file(source, target)
        os.chmod(target, 0o600)
        artifacts.append(_artifact(target, kind))
    manifest = {
        "backup_id": backup_id or f"ai6b-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        "created_at": _now(),
        "method": "sqlite-online-backup-api",
        "database_status": database_status,
        "consistent": True,
        "artifacts": artifacts,
        "restore_command": "python scripts/verify_ai_report_restore.py --backup-directory <secured-backup-dir>",
    }
    manifest_path = output / "backup-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {**manifest, "manifest_sha256": sha256_file(manifest_path)}


def verify_isolated_restore(backup_directory: str | Path) -> dict[str, Any]:
    backup = Path(backup_directory).resolve()
    manifest_path = backup / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified: list[str] = []
    for item in manifest.get("artifacts", []):
        path = backup / item["file"]
        if not path.is_file() or path.stat().st_size != item["size_bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError(f"BACKUP_ARTIFACT_MISMATCH:{item.get('kind')}")
        verified.append(str(item["kind"]))
    result: dict[str, Any] = {
        "backup_id": manifest["backup_id"],
        "verified_at": _now(),
        "artifact_hashes_valid": True,
        "verified_artifact_kinds": verified,
        "database_status": manifest["database_status"],
        "temporary_copy_deleted": False,
    }
    database = backup / "ai_market_reports.db"
    with tempfile.TemporaryDirectory(prefix="ai6b-restore-") as folder:
        if database.is_file():
            restored = Path(folder) / "restored-ai-market-reports.db"
            shutil.copy2(database, restored)
            connection = sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True, timeout=5)
            try:
                connection.execute("PRAGMA query_only=ON")
                result["integrity_check"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
                result["table_count"] = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
                result["schema_versions"] = [list(row) for row in connection.execute(
                    "SELECT migration_key,schema_version FROM ai_report_migrations ORDER BY migration_key"
                )]
                sample_tables = ("ai_market_contexts", "ai_market_reports", "ai_report_audits", "ai_report_registry_snapshots")
                result["read_samples"] = {
                    table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in sample_tables
                }
            finally:
                connection.close()
            if result["integrity_check"] != "ok":
                raise sqlite3.DatabaseError("RESTORE_INTEGRITY_FAILED")
        else:
            result["integrity_check"] = "NOT_APPLICABLE_DATABASE_NOT_YET_PRESENT"
            result["table_count"] = 0
            result["schema_versions"] = []
            result["read_samples"] = {}
    result["temporary_copy_deleted"] = True
    return result
