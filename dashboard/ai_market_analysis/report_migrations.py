"""Verified, explicit and atomic migrations for the isolated AI report DB."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "migrations" / "ai_report"
MANIFEST_PATH = MIGRATION_ROOT / "manifest.json"
FORBIDDEN_DATABASE_MARKERS = ("paper_trades", "microstructure")
_ALTER_ADD_COLUMN = re.compile(
    r"^\s*ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE | re.DOTALL,
)


class MigrationError(RuntimeError):
    """Fail-closed migration or manifest error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256() -> str:
    return _sha256(MANIFEST_PATH)


def migration_manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    migrations = value.get("migrations")
    if not isinstance(migrations, list) or not migrations:
        raise MigrationError("MIGRATION_MANIFEST_EMPTY")
    orders = [item.get("order") for item in migrations]
    if orders != list(range(1, len(migrations) + 1)):
        raise MigrationError("MIGRATION_ORDER_INVALID")
    for item in migrations:
        path = MIGRATION_ROOT / str(item["file"])
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise MigrationError(f"MIGRATION_HASH_MISMATCH:{item.get('file')}")
        if item.get("destructive") or item.get("touches_paper_db") or item.get("touches_microstructure_db"):
            raise MigrationError(f"MIGRATION_SCOPE_FORBIDDEN:{item.get('file')}")
    return value


def _guard_database_path(path: Path) -> None:
    lowered = path.name.lower()
    if any(marker in lowered for marker in FORBIDDEN_DATABASE_MARKERS):
        raise MigrationError("LEGACY_DATABASE_TARGET_FORBIDDEN")


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for character in sql:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            if pending.strip().strip(";"):
                statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise MigrationError("INCOMPLETE_MIGRATION_STATEMENT")
    return statements


def _execute_statement(connection: sqlite3.Connection, statement: str) -> None:
    match = _ALTER_ADD_COLUMN.match(statement)
    if match:
        table, column = match.groups()
        existing = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if column in existing:
            return
    connection.execute(statement)


def _ensure_ledger_hash_column(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(ai_report_migrations)")}
    if columns and "file_sha256" not in columns:
        connection.execute(
            "ALTER TABLE ai_report_migrations ADD COLUMN file_sha256 TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'"
        )


def apply_migrations(path: str | Path) -> dict[str, Any]:
    """Apply every pending manifest migration in one rollback-safe transaction."""
    database = Path(path)
    _guard_database_path(database)
    manifest = migration_manifest()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=5, isolation_level=None)
    applied: list[str] = []
    skipped: list[str] = []
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for item in manifest["migrations"]:
                key = str(item["key"])
                try:
                    row = connection.execute(
                        "SELECT schema_version FROM ai_report_migrations WHERE migration_key=?", (key,)
                    ).fetchone()
                except sqlite3.OperationalError:
                    row = None
                if row:
                    skipped.append(key)
                    continue
                sql = (MIGRATION_ROOT / str(item["file"])).read_text(encoding="utf-8")
                for statement in _statements(sql):
                    _execute_statement(connection, statement)
                _ensure_ledger_hash_column(connection)
                connection.execute(
                    "INSERT INTO ai_report_migrations(migration_key,schema_version,file_sha256,completed_at) VALUES(?,?,?,?)",
                    (key, str(item["schema_version"]), str(item["sha256"]), _utc_now()),
                )
                applied.append(key)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return {
        "database": str(database),
        "manifest_sha256": manifest_sha256(),
        "applied": applied,
        "skipped": skipped,
        "schema_version": manifest["migrations"][-1]["schema_version"],
        "atomic_batch": True,
    }
