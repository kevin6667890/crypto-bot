"""Restart-safe operational helpers for the isolated Polymarket ledger."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import utc_now
from .eligibility import POLICY_V2_VERSION
from .evidence import EVIDENCE_POLICY_VERSION
from .llm_forecast import LLM_FORECAST_SCHEMA_VERSION, PROMPT_VERSION
from .llm_provider import DEEPSEEK_PROVIDER_POLICY_VERSION, configured_provider
from .repository import (DATABASE_SCHEMA_IDENTITY, DATABASE_SCHEMA_VERSION,
                         UNIVERSE_MANIFEST_SCHEMA, PolymarketRepository)
from .scoring import EXECUTION_POLICY_VERSION, SCORING_VERSION

GIB = 1024 ** 3


def online_backup(repo: PolymarketRepository, directory: Path) -> dict[str, Any]:
    """Use SQLite's online backup API; this is safe while WAL writers are active."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = directory / f"polymarket_research-{stamp}.sqlite"
    source = sqlite3.connect(repo.path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close(); source.close()
    digest_builder = hashlib.sha256()
    with target.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            digest_builder.update(chunk)
    digest = digest_builder.hexdigest()
    # Re-open and run the inexpensive integrity check before advertising success.
    verify = verify_backup(target)
    manifest = target.with_suffix(target.suffix + '.sha256.json')
    manifest.write_text(json.dumps({'file': target.name, 'created_at': utc_now(), 'sha256': digest,
                                    'size_bytes': target.stat().st_size, 'verify': verify}, indent=2), encoding='utf-8')
    return {'path': str(target), 'manifest_path': str(manifest), 'sha256': digest, 'size_bytes': target.stat().st_size, 'verify': verify}


def verify_backup(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True)
    try:
        result = connection.execute('PRAGMA integrity_check').fetchone()[0]
        tables = int(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
    finally:
        connection.close()
    return {'valid': result == 'ok', 'integrity_check': result, 'table_count': tables}


def disk_guard(repo: PolymarketRepository, backup_dir: Path | None = None) -> dict[str, Any]:
    """Bounded preflight that prevents a collection from exhausting its volume."""
    usage = shutil.disk_usage(repo.path.parent)
    db_size = repo.path.stat().st_size if repo.path.exists() else 0
    directory = backup_dir or Path(os.getenv('POLYMARKET_BACKUP_DIR', str(repo.path.parent / 'polymarket_backups')))
    backup_size = sum(path.stat().st_size for path in directory.glob('*') if path.is_file()) if directory.exists() else 0
    configured_floor = int(os.getenv('POLYMARKET_MIN_FREE_BYTES', str(10 * GIB)))
    ratio_floor = float(os.getenv('POLYMARKET_MIN_FREE_RATIO', '0.10'))
    # Keep room for one WAL-safe full backup plus normal collection growth.
    required = max(configured_floor, int(usage.total * ratio_floor), db_size * 2)
    return {'safe': usage.free >= required, 'free_bytes': usage.free, 'required_free_bytes': required,
            'total_bytes': usage.total, 'db_size_bytes': db_size, 'backup_size_bytes': backup_size,
            'backup_directory': str(directory), 'policy': 'polymarket-disk-guard-v1'}


def database_maintenance(repo: PolymarketRepository) -> dict[str, Any]:
    """Safe online maintenance only: never VACUUM and never remove ledger rows."""
    started_at = utc_now()
    with repo.connect() as connection:
        checkpoint = list(connection.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone())
        integrity = str(connection.execute('PRAGMA integrity_check').fetchone()[0])
        if integrity != 'ok':
            raise sqlite3.DatabaseError(f'integrity_check: {integrity}')
        connection.execute('ANALYZE')
    return {'started_at': started_at, 'completed_at': utc_now(), 'wal_checkpoint': {
                'busy': int(checkpoint[0]), 'log_pages': int(checkpoint[1]), 'checkpointed_pages': int(checkpoint[2])},
            'integrity_check': integrity, 'analyze': 'completed', 'vacuum': 'not_run',
            'disk_guard': disk_guard(repo), 'storage': repo.storage_status()}


def health(repo: PolymarketRepository, backup_dir: Path | None = None) -> dict[str, Any]:
    """Read-only health projection, independent from crypto-paper health."""
    now = datetime.now(timezone.utc)
    operational = repo.operational_status()
    lease = repo.collection_lease()
    try:
        with repo.connect() as c:
            c.execute('BEGIN IMMEDIATE'); c.execute('ROLLBACK')
            writable = True
            # A full quick_check walks the whole multi-GB file and turns a
            # liveness endpoint into an expensive maintenance operation.
            # Reaching schema metadata is the bounded health check; every
            # online backup is separately reopened and integrity-checked.
            c.execute('PRAGMA schema_version').fetchone()[0]
            integrity = 'schema_read_ok'
            latest_run = c.execute('SELECT * FROM collection_runs ORDER BY completed_at DESC LIMIT 1').fetchone()
            latest_success = c.execute("SELECT * FROM collection_runs WHERE status='SUCCEEDED' ORDER BY completed_at DESC LIMIT 1").fetchone()
            attempts = c.execute("SELECT status,failure_code,attempted_at FROM llm_forecast_attempts ORDER BY attempted_at DESC LIMIT 20").fetchall()
    except sqlite3.Error:
        writable, integrity, latest_run, latest_success, attempts = False, 'unavailable', None, None, []
    # Health is called frequently by a web/API monitor.  Avoid storage-status,
    # which intentionally performs table counts and sample projections.
    status = repo.database_file_status()
    directory = backup_dir or repo.path.parent / 'polymarket_backups'
    latest_backup = max(directory.glob('polymarket_research-*.sqlite'), key=lambda p: p.stat().st_mtime, default=None) if directory.exists() else None
    backup_verified = False
    backup_manifest = None
    if latest_backup:
        manifest_path = latest_backup.with_suffix(latest_backup.suffix + '.sha256.json')
        if manifest_path.is_file():
            try:
                backup_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                backup_verified = (backup_manifest.get('file') == latest_backup.name and
                    int(backup_manifest.get('size_bytes', -1)) == latest_backup.stat().st_size and
                    backup_manifest.get('verify', {}).get('valid') is True)
            except (OSError, ValueError, TypeError):
                backup_verified = False
    backup_age = max(0.0, now.timestamp() - latest_backup.stat().st_mtime) if latest_backup else None
    latest_success_at = str(latest_success['completed_at']) if latest_success else None
    freshness = max(0.0, (now - datetime.fromisoformat(latest_success_at)).total_seconds()) if latest_success_at else None
    interval_hours = float(os.getenv('POLYMARKET_COLLECTION_INTERVAL_HOURS', '3'))
    next_expected = (datetime.fromisoformat(latest_success_at) + timedelta(hours=interval_hours)).isoformat() if latest_success_at else None
    run_duration = None
    if latest_run:
        run_duration = max(0.0, (datetime.fromisoformat(latest_run['completed_at']) - datetime.fromisoformat(latest_run['started_at'])).total_seconds())
    stale_after = int(os.getenv('POLYMARKET_COLLECTION_LEASE_STALE_SECONDS', '7200'))
    lease_age = max(0.0, (now - datetime.fromisoformat(lease['heartbeat_at'])).total_seconds()) if lease else None
    recent_successes = sum(row['status'] == 'SUCCEEDED' for row in attempts)
    latest_provider_error = next((row['failure_code'] for row in attempts if row['status'] == 'FAILED'), None)
    provider = configured_provider()
    guard = disk_guard(repo, directory)
    accepted, rejected = operational['evidence']['accepted'], operational['evidence']['rejected']
    return {'collector': {'latest_status': latest_run['status'] if latest_run else None,
                          'latest_duration_seconds': run_duration, 'latest_completed_at': latest_run['completed_at'] if latest_run else None,
                          'last_successful_run': latest_success_at, 'freshness_age_seconds': freshness,
                          'fresh': freshness is not None and freshness <= interval_hours * 3600 * 2,
                          'next_expected_at': next_expected, 'active_lock': lease, 'lock_age_seconds': lease_age,
                          'stale_lock': bool(lease and lease_age is not None and lease_age > stale_after),
                          'last_error': latest_run['error_code'] if latest_run else None},
            'provider': {'configured': bool(provider.api_key), 'provider': provider.provider, 'model': provider.model,
                         'recent_sample_size': len(attempts),
                         'recent_success_rate': recent_successes / len(attempts) if attempts else None,
                         'latest_error': latest_provider_error},
            'database': {'writable': writable, 'db_size_bytes': status['db_size_bytes'], 'wal_size_bytes': status['wal_size_bytes'],
                         'free_bytes': guard['free_bytes'], 'required_free_bytes': guard['required_free_bytes'],
                         'disk_guard_safe': guard['safe'], 'integrity_status': integrity},
            'backup': {'latest_path': str(latest_backup) if latest_backup else None,
                       'latest_verified': backup_verified, 'latest_age_seconds': backup_age,
                       'manifest_path': str(latest_backup.with_suffix(latest_backup.suffix + '.sha256.json')) if latest_backup else None},
            'research': {'llm_forecasts': operational['forecasts']['LLM'], 'unresolved': operational['forecasts']['unresolved'],
                         'resolved': operational['forecasts']['resolved'], 'scored': operational['scoring']['scored_count'],
                         'evidence_admission_rate': accepted / (accepted + rejected) if accepted + rejected else None},
            'identity': {'host': socket.gethostname(), 'pid': os.getpid(),
                         'git_sha': os.getenv('GIT_COMMIT') or os.getenv('RELEASE_GIT_SHA') or 'unknown',
                         'build_timestamp': os.getenv('POLYMARKET_BUILD_TIMESTAMP') or 'unknown',
                         'database_schema': DATABASE_SCHEMA_IDENTITY,
                         'database_user_version': DATABASE_SCHEMA_VERSION,
                         'storage_schema': UNIVERSE_MANIFEST_SCHEMA,
                         'methodology': {'eligibility': POLICY_V2_VERSION, 'evidence': EVIDENCE_POLICY_VERSION,
                                         'forecast': LLM_FORECAST_SCHEMA_VERSION, 'prompt': PROMPT_VERSION,
                                         'provider_policy': DEEPSEEK_PROVIDER_POLICY_VERSION,
                                         'scoring': SCORING_VERSION, 'execution_simulation': EXECUTION_POLICY_VERSION}}}
