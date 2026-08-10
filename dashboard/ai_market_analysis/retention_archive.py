"""Verified 30-day hot retention and content-addressed AI report archives."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .report_migrations import MigrationError

HOT_RETENTION_DAYS = 30
ARCHIVE_MAX_DAYS = 365
LOCAL_ARCHIVE_CAP_BYTES = 10 * 1024**3
DISK_SAFETY_RESERVE_BYTES = 15 * 1024**3
TERMINAL_REQUEST_EVENTS = frozenset({"COMPLETED", "FAILED_FINAL", "CANCELLED", "BUDGET_BLOCKED", "VALIDATION_FAILED"})
ACTIVE_AUDIT_EVENTS = frozenset({"AUDIT_QUEUED", "AUDIT_RUNNING", "AUDIT_INTERRUPTED"})
REGISTRY_UPDATE_TRIGGER = """CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_update BEFORE UPDATE ON ai_report_registry_snapshots
BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END"""
REGISTRY_DELETE_TRIGGER = """CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_delete BEFORE DELETE ON ai_report_registry_snapshots
BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END"""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rows(connection: sqlite3.Connection, query: str, arguments: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, arguments).fetchall()]


def _request_bundle(connection: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    request = _rows(connection, "SELECT * FROM ai_report_requests WHERE request_id=?", (request_id,))
    if len(request) != 1:
        raise MigrationError("ARCHIVE_REQUEST_IDENTITY_MISSING")
    context_id = request[0]["context_id"]
    reports = _rows(connection, "SELECT * FROM ai_market_reports WHERE request_id=?", (request_id,))
    report_ids = [row["report_id"] for row in reports]
    audits: list[dict[str, Any]] = []
    audit_inputs: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []
    for report_id in report_ids:
        audits.extend(_rows(connection, "SELECT * FROM ai_report_audits WHERE report_id=? ORDER BY created_at,audit_id", (report_id,)))
        audit_inputs.extend(_rows(connection, "SELECT * FROM ai_report_audit_inputs WHERE report_id=?", (report_id,)))
        audit_events.extend(_rows(connection, "SELECT * FROM ai_report_audit_events WHERE report_id=? ORDER BY event_id", (report_id,)))
    return {
        "schema_version": "ai6b-request-archive-v1",
        "request_id": request_id,
        "tables": {
            "ai_report_requests": request,
            "ai_report_request_events": _rows(connection, "SELECT * FROM ai_report_request_events WHERE request_id=? ORDER BY event_id", (request_id,)),
            "ai_report_attempts": _rows(connection, "SELECT * FROM ai_report_attempts WHERE request_id=? ORDER BY attempt_number", (request_id,)),
            "ai_market_contexts": _rows(connection, "SELECT * FROM ai_market_contexts WHERE context_id=?", (context_id,)),
            "ai_report_registry_snapshots": _rows(connection, "SELECT * FROM ai_report_registry_snapshots WHERE request_id=?", (request_id,)),
            "ai_market_reports": reports,
            "ai_report_audit_inputs": audit_inputs,
            "ai_report_audits": audits,
            "ai_report_audit_events": audit_events,
        },
    }


def _identity_manifest(bundle: dict[str, Any], blob_sha256: str, blob_size: int, archived_at: str) -> dict[str, Any]:
    tables = bundle["tables"]
    request = tables["ai_report_requests"][0]
    registry = tables["ai_report_registry_snapshots"]
    reports = tables["ai_market_reports"]
    audits = tables["ai_report_audits"]
    return {
        "schema_version": "ai6b-archive-identity-manifest-v1",
        "request_id": bundle["request_id"],
        "archived_at": archived_at,
        "payload_blob_sha256": blob_sha256,
        "payload_blob_size_bytes": blob_size,
        "identity": {
            "request_id": request["request_id"],
            "request_identity": request["request_identity"],
            "context_id": request["context_id"],
            "registry_snapshot_ids": [row["registry_snapshot_id"] for row in registry],
            "report_ids_and_hashes": [[row["report_id"], row["response_hash"]] for row in reports],
            "audit_ids_report_ids_hashes": [[row["audit_id"], row["report_id"], row["report_hash"], row["context_hash"], row["payload_hash"]] for row in audits],
        },
        "table_row_counts": {name: len(rows) for name, rows in tables.items()},
        "identity_manifest_retention": "INDEFINITE",
        "payload_retention_days": ARCHIVE_MAX_DAYS,
    }


def verify_archive(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    blob = path.parents[1] / "blobs" / f"{manifest['payload_blob_sha256']}.json.gz"
    compressed = blob.read_bytes()
    if len(compressed) != manifest["payload_blob_size_bytes"] or _sha256(compressed) != manifest["payload_blob_sha256"]:
        raise MigrationError("ARCHIVE_BLOB_HASH_MISMATCH")
    bundle = json.loads(gzip.decompress(compressed))
    expected = _identity_manifest(bundle, _sha256(compressed), len(compressed), manifest["archived_at"])
    if expected != manifest:
        raise MigrationError("ARCHIVE_IDENTITY_RELATIONSHIP_MISMATCH")
    return {"verified": True, "request_id": manifest["request_id"], "blob_sha256": manifest["payload_blob_sha256"]}


def _archive_usage(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _write_archive(bundle: dict[str, Any], archive_root: Path, *, archived_at: str) -> Path:
    raw = _canonical(bundle)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    cap = int(os.getenv("AI_REPORT_LOCAL_ARCHIVE_CAP_BYTES", str(LOCAL_ARCHIVE_CAP_BYTES)))
    free = shutil.disk_usage(archive_root.parent if archive_root.parent.exists() else archive_root.parent.parent).free
    if _archive_usage(archive_root) + len(compressed) > cap:
        raise MigrationError("LOCAL_ARCHIVE_CAP_REACHED")
    if free - (len(compressed) * 2) < DISK_SAFETY_RESERVE_BYTES:
        raise MigrationError("ARCHIVE_TEMPORARY_SPACE_UNSAFE")
    blob_hash = _sha256(compressed)
    blob = archive_root / "blobs" / f"{blob_hash}.json.gz"
    manifest_path = archive_root / "identities" / f"{bundle['request_id']}.json"
    blob.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        fd, temp_name = tempfile.mkstemp(prefix=".archive-", dir=blob.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(compressed);stream.flush();os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600);os.replace(temp_name, blob);os.chmod(blob,0o400)
        finally:
            Path(temp_name).unlink(missing_ok=True)
    if manifest_path.exists():
        existing=json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest=_identity_manifest(bundle,blob_hash,len(compressed),existing["archived_at"])
        if existing != manifest:
            raise MigrationError("ARCHIVE_IDENTITY_ALREADY_EXISTS_DIFFERENT")
        verify_archive(manifest_path);return manifest_path
    else:
        manifest = _identity_manifest(bundle, blob_hash, len(compressed), archived_at)
        fd,temp_name=tempfile.mkstemp(prefix=".identity-",dir=manifest_path.parent)
        try:
            with os.fdopen(fd,"wb") as stream:stream.write(_canonical(manifest)+b"\n");stream.flush();os.fsync(stream.fileno())
            os.chmod(temp_name,0o600);os.replace(temp_name,manifest_path);os.chmod(manifest_path,0o400)
        finally:Path(temp_name).unlink(missing_ok=True)
    verify_archive(manifest_path)
    return manifest_path


def archive_hot_expired(database: str | Path, archive_directory: str | Path, *, now: datetime | None = None, apply: bool = False, limit: int = 10) -> dict[str, Any]:
    """Archive terminal identity closures older than 30 days; dry-run by default."""
    db = Path(database).resolve();archive_root = Path(archive_directory).resolve()
    if db.name.lower() in {"paper_trades.db", "market_microstructure.db"}:
        raise MigrationError("LEGACY_DATABASE_TARGET_FORBIDDEN")
    current = now or _now();cutoff = (current - timedelta(days=HOT_RETENTION_DAYS)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode={'rw' if apply else 'ro'}", uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    archived: list[dict[str, Any]] = []
    try:
        candidates = connection.execute("""SELECT r.request_id FROM ai_report_requests r
          JOIN ai_report_request_events e ON e.event_id=(SELECT MAX(x.event_id) FROM ai_report_request_events x WHERE x.request_id=r.request_id)
          WHERE r.created_at<? AND e.event_type IN (?,?,?,?,?) ORDER BY r.created_at,r.request_id LIMIT ?""",
          (cutoff, *sorted(TERMINAL_REQUEST_EVENTS), limit)).fetchall()
        for candidate in candidates:
            request_id = str(candidate[0]);bundle = _request_bundle(connection, request_id)
            active_audits = [row for row in bundle["tables"]["ai_report_audit_events"] if row["event_type"] in ACTIVE_AUDIT_EVENTS]
            if active_audits:
                continue
            sources = [json.loads(row["payload_json"]).get("position_context", {}).get("source", "NONE") for row in bundle["tables"]["ai_market_contexts"]]
            if any(source not in {"NONE", "PAPER"} for source in sources):
                raise MigrationError("ARCHIVE_PRIVACY_SCOPE_FORBIDDEN")
            item = {"request_id": request_id, "row_counts": {name: len(rows) for name, rows in bundle["tables"].items()}}
            if not apply:
                archived.append(item);continue
            manifest_path = _write_archive(bundle, archive_root, archived_at=current.replace(microsecond=0).isoformat().replace("+00:00", "Z"))
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_bundle = _request_bundle(connection, request_id)
                if _canonical(current_bundle) != _canonical(bundle):
                    raise MigrationError("HOT_ROWS_CHANGED_AFTER_ARCHIVE")
                report_ids = [row["report_id"] for row in bundle["tables"]["ai_market_reports"]]
                for report_id in report_ids:
                    connection.execute("DELETE FROM ai_report_audit_events WHERE report_id=?", (report_id,))
                    connection.execute("DELETE FROM ai_report_audits WHERE report_id=?", (report_id,))
                    connection.execute("DELETE FROM ai_report_audit_inputs WHERE report_id=?", (report_id,))
                connection.execute("DELETE FROM ai_market_reports WHERE request_id=?", (request_id,))
                connection.execute("DROP TRIGGER IF EXISTS trg_ai_registry_snapshot_no_delete")
                connection.execute("DELETE FROM ai_report_registry_snapshots WHERE request_id=?", (request_id,))
                connection.execute(REGISTRY_DELETE_TRIGGER)
                connection.execute("DELETE FROM ai_report_attempts WHERE request_id=?", (request_id,))
                connection.execute("DELETE FROM ai_report_request_events WHERE request_id=?", (request_id,))
                context_id = bundle["tables"]["ai_report_requests"][0]["context_id"]
                connection.execute("DELETE FROM ai_report_requests WHERE request_id=?", (request_id,))
                remaining = connection.execute("SELECT 1 FROM ai_report_requests WHERE context_id=? LIMIT 1", (context_id,)).fetchone()
                if not remaining:
                    connection.execute("DELETE FROM ai_market_contexts WHERE context_id=?", (context_id,))
                if verify_archive(manifest_path)["request_id"] != request_id:
                    raise MigrationError("ARCHIVE_POST_PRUNE_VERIFICATION_FAILED")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK");raise
            archived.append({**item, "manifest": str(manifest_path), "pruned": True})
    finally:
        connection.close()
    return {"hot_retention_days": HOT_RETENTION_DAYS, "cutoff": cutoff, "apply": apply, "archived": archived, "vacuum_used": False}


def expire_archive_payloads(archive_directory: str | Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Delete >365-day verified blobs while retaining identity manifests forever."""
    root = Path(archive_directory).resolve();current = now or _now();expired = []
    receipts = root / "expiry-receipts";receipts.mkdir(parents=True, exist_ok=True)
    for manifest_path in sorted((root / "identities").glob("*.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (receipts / manifest_path.name).exists():continue
        archived_at = datetime.fromisoformat(manifest["archived_at"].replace("Z", "+00:00"))
        if current - archived_at <= timedelta(days=ARCHIVE_MAX_DAYS):continue
        verified = verify_archive(manifest_path)
        blob = root / "blobs" / f"{verified['blob_sha256']}.json.gz"
        receipt = {"schema_version":"ai6b-archive-expiry-v1","request_id":verified["request_id"],"payload_blob_sha256":verified["blob_sha256"],"expired_at":current.replace(microsecond=0).isoformat().replace("+00:00","Z"),"identity_manifest_retained":True}
        receipt_path = receipts / f"{verified['request_id']}.json"
        receipt_path.write_bytes(_canonical(receipt)+b"\n");os.chmod(receipt_path,0o600)
        os.chmod(blob,0o600);blob.unlink();expired.append(verified["request_id"])
    return {"expired_payloads":expired,"identity_manifests_deleted":0,"vacuum_used":False}
