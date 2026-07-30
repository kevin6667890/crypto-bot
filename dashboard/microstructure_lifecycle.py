"""Verified raw-trade cold archives and bounded hot-store pruning."""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Mapping


MICROSTRUCTURE_RAW_RETENTION_VERSION = "microstructure-raw-retention-v1"
RAW_TRADE_ARCHIVE_VERSION = "microstructure-trade-archive-v1"
OFFHOST_ACK_VERSION = "microstructure-offhost-ack-v1"
DAY_MS = 86_400_000
DEFAULT_HOT_DAYS = int(os.getenv("MICROSTRUCTURE_RAW_HOT_DAYS", "7"))


class MicrostructureLifecycleError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_raw_trade_manifest(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_file"] = manifest_path.name
    manifest["manifest_sha256"] = file_sha256(manifest_path)
    return manifest


def utc_day_bounds(day: str) -> tuple[int, int]:
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError(f"invalid UTC day: {day}") from error
    start_ms = int(start.timestamp() * 1000)
    return start_ms, start_ms + DAY_MS


def is_cold_day(
    day: str, *, now: datetime | None = None, hot_days: int = DEFAULT_HOT_DAYS
) -> bool:
    current_day = (now or datetime.now(timezone.utc)).date()
    return date.fromisoformat(day) < current_day - timedelta(days=hot_days)


def _create_trade_table(
    target: sqlite3.Connection, source: sqlite3.Connection
) -> list[str]:
    columns = source.execute(
        "PRAGMA table_info(trade_flow_observations)"
    ).fetchall()
    if not columns:
        raise MicrostructureLifecycleError("trade_flow_observations is absent")
    definitions = []
    names = []
    for row in columns:
        name, data_type = str(row[1]), str(row[2] or "BLOB")
        names.append(name)
        definition = f'"{name}" {data_type}'
        if row[3]:
            definition += " NOT NULL"
        if row[5]:
            definition += " PRIMARY KEY"
        definitions.append(definition)
    target.execute(
        f"CREATE TABLE trade_flow_observations({','.join(definitions)})"
    )
    target.execute(
        """CREATE INDEX idx_archive_trade_time
           ON trade_flow_observations(instrument,source_ts_ms,uniqueness_key)"""
    )
    return names


def _reconciliation(
    source: sqlite3.Connection,
    instrument: str,
    start_ms: int,
    end_ms: int,
    raw_minutes: Mapping[int, tuple[float, float, int]],
) -> dict[str, Any]:
    aggregate_rows = source.execute(
        """SELECT bucket_ms,buy_notional,sell_notional,observation_count
           FROM cvd_aggregates
           WHERE instrument=? AND resolution='1m'
             AND bucket_ms>=? AND bucket_ms<?
           ORDER BY bucket_ms""",
        (instrument, start_ms, end_ms),
    ).fetchall()
    aggregate = {
        int(row[0]): (float(row[1]), float(row[2]), int(row[3]))
        for row in aggregate_rows
    }
    comparable = sorted(set(raw_minutes) & set(aggregate))
    mismatches = []
    for minute in comparable:
        raw = raw_minutes[minute]
        saved = aggregate[minute]
        tolerance = max(0.01, abs(raw[0]) * 1e-9, abs(raw[1]) * 1e-9)
        if (
            abs(raw[0] - saved[0]) > tolerance
            or abs(raw[1] - saved[1]) > tolerance
            or raw[2] != saved[2]
        ):
            mismatches.append(minute)
    expected_minutes = sorted(raw_minutes)
    complete = bool(expected_minutes) and len(comparable) == len(expected_minutes)
    return {
        "status": "PASS" if complete and not mismatches else "FAIL",
        "raw_minute_count": len(raw_minutes),
        "aggregate_minute_count": len(aggregate),
        "comparable_minute_count": len(comparable),
        "mismatch_count": len(mismatches),
        "first_mismatches": mismatches[:20],
    }


def archive_raw_trade_day(
    source_database: Path | str,
    archive_directory: Path | str,
    *,
    instrument: str,
    utc_day: str,
    compression: str = "gzip",
) -> dict[str, Any]:
    source = Path(source_database).resolve()
    archive_root = Path(archive_directory).resolve()
    start_ms, end_ms = utc_day_bounds(utc_day)
    archive_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="raw-trades-", dir=archive_root))
    shard = workspace / f"trades-{instrument}-{utc_day}.sqlite"
    raw_minutes: dict[int, list[float | int]] = defaultdict(
        lambda: [0.0, 0.0, 0]
    )
    row_count = 0
    earliest = latest = None
    trade_ids: list[str] = []
    signed_volume = buy_volume = sell_volume = 0.0
    prices: list[float] = []
    source_fingerprint = hashlib.sha256()
    try:
        with closing(
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        ) as src:
            src.row_factory = sqlite3.Row
            with closing(sqlite3.connect(shard)) as dst:
                names = _create_trade_table(dst, src)
                placeholders = ",".join("?" for _ in names)
                select = ",".join(f'"{name}"' for name in names)
                insert = (
                    f"INSERT INTO trade_flow_observations({select}) "
                    f"VALUES({placeholders})"
                )
                last_key: tuple[int, str] | None = None
                batch = []
                for row in src.execute(
                    f"""SELECT {select} FROM trade_flow_observations
                        WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<?
                        ORDER BY source_ts_ms,uniqueness_key""",
                    (instrument, start_ms, end_ms),
                ):
                    mapping = dict(row)
                    key = (
                        int(mapping["source_ts_ms"]),
                        str(mapping["uniqueness_key"]),
                    )
                    if last_key is not None and key <= last_key:
                        raise MicrostructureLifecycleError(
                            "raw archive input is duplicated or not strictly sorted"
                        )
                    last_key = key
                    values = tuple(mapping[name] for name in names)
                    batch.append(values)
                    encoded = canonical_json(list(values)).encode()
                    source_fingerprint.update(encoded)
                    source_fingerprint.update(b"\n")
                    timestamp = int(mapping["source_ts_ms"])
                    earliest = timestamp if earliest is None else min(earliest, timestamp)
                    latest = timestamp if latest is None else max(latest, timestamp)
                    side = str(mapping["side"])
                    notional = float(mapping["notional"])
                    minute = timestamp // 60_000 * 60_000
                    if side == "buy":
                        buy_volume += notional
                        signed_volume += notional
                        raw_minutes[minute][0] += notional
                    else:
                        sell_volume += notional
                        signed_volume -= notional
                        raw_minutes[minute][1] += notional
                    raw_minutes[minute][2] += 1
                    prices.append(float(mapping["price"]))
                    if mapping.get("trade_id") is not None:
                        trade_ids.append(str(mapping["trade_id"]))
                    row_count += 1
                    if len(batch) >= 10_000:
                        dst.executemany(insert, batch)
                        dst.commit()
                        batch.clear()
                if batch:
                    dst.executemany(insert, batch)
                reconciliation = _reconciliation(
                    src,
                    instrument,
                    start_ms,
                    end_ms,
                    {
                        key: (float(value[0]), float(value[1]), int(value[2]))
                        for key, value in raw_minutes.items()
                    },
                )
                gap_rows = [
                    dict(row)
                    for row in src.execute(
                        """SELECT lane,instrument,start_ms,end_ms,reason,resolved_at_ms
                           FROM collection_gaps
                           WHERE instrument=? AND lane='trades'
                             AND start_ms<? AND end_ms>?
                           ORDER BY start_ms""",
                        (instrument, end_ms, start_ms),
                    )
                ]
                dst.execute(
                    """CREATE TABLE archive_manifest(
                       manifest_json TEXT NOT NULL)"""
                )
                manifest = {
                    "version": RAW_TRADE_ARCHIVE_VERSION,
                    "retention_version": MICROSTRUCTURE_RAW_RETENTION_VERSION,
                    "instrument": instrument,
                    "utc_day": utc_day,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "earliest_ms": earliest,
                    "latest_ms": latest,
                    "row_count": row_count,
                    "trade_id_min": min(trade_ids) if trade_ids else None,
                    "trade_id_max": max(trade_ids) if trade_ids else None,
                    "signed_notional": signed_volume,
                    "buy_notional": buy_volume,
                    "sell_notional": sell_volume,
                    "price_min": min(prices) if prices else None,
                    "price_max": max(prices) if prices else None,
                    "source_fingerprint": source_fingerprint.hexdigest(),
                    "aggregate_reconciliation": reconciliation,
                    "gap_summary": {
                        "overlapping_gap_count": len(gap_rows),
                        "unresolved_gap_count": sum(
                            row["resolved_at_ms"] is None for row in gap_rows
                        ),
                    },
                    "archive_time": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                }
                dst.execute(
                    "INSERT INTO archive_manifest VALUES(?)",
                    (canonical_json(manifest),),
                )
                dst.commit()
                integrity = dst.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise MicrostructureLifecycleError(
                        f"raw shard quick_check failed: {integrity}"
                    )
        shard_hash = file_sha256(shard)
        if compression != "gzip":
            raise ValueError("only dependency-free gzip is currently supported")
        compressed = archive_root / (
            f"trades-{instrument}-{utc_day}-{shard_hash[:16]}.sqlite.gz"
        )
        temporary = compressed.with_suffix(".gz.tmp")
        with shard.open("rb") as source_handle, temporary.open("wb") as raw_target:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_target, mtime=0
            ) as target_handle:
                shutil_copyfileobj(source_handle, target_handle)
        os.replace(temporary, compressed)
        compressed_hash = file_sha256(compressed)
        manifest.update(
            {
                "archive_file": compressed.name,
                "archive_sha256": compressed_hash,
                "uncompressed_sha256": shard_hash,
                "uncompressed_bytes": shard.stat().st_size,
                "compressed_bytes": compressed.stat().st_size,
                "codec": "gzip",
            }
        )
        manifest_raw = canonical_json(manifest)
        manifest_path = compressed.with_suffix(".manifest.json")
        manifest_path.write_text(manifest_raw + "\n", encoding="utf-8")
        manifest["manifest_file"] = manifest_path.name
        manifest["manifest_sha256"] = file_sha256(manifest_path)
        verification = verify_raw_trade_archive(compressed, manifest_path)
        return {**manifest, "verification": verification}
    finally:
        for item in workspace.iterdir() if workspace.exists() else ():
            item.unlink(missing_ok=True)
        workspace.rmdir()


def shutil_copyfileobj(source: Any, target: Any) -> None:
    while block := source.read(8 * 1024 * 1024):
        target.write(block)


def verify_raw_trade_archive(
    archive: Path | str, manifest_path: Path | str
) -> dict[str, Any]:
    archive_path = Path(archive).resolve()
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if file_sha256(archive_path) != manifest["archive_sha256"]:
        raise MicrostructureLifecycleError("raw archive SHA-256 mismatch")
    descriptor, temporary_name = tempfile.mkstemp(suffix=".sqlite")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with gzip.open(archive_path, "rb") as source, temporary.open("wb") as target:
            shutil_copyfileobj(source, target)
        if file_sha256(temporary) != manifest["uncompressed_sha256"]:
            raise MicrostructureLifecycleError("raw shard SHA-256 mismatch")
        with closing(
            sqlite3.connect(temporary.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()[0]
            row = connection.execute(
                """SELECT COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms),
                          COUNT(DISTINCT uniqueness_key)
                   FROM trade_flow_observations"""
            ).fetchone()
            embedded = json.loads(
                connection.execute(
                    "SELECT manifest_json FROM archive_manifest"
                ).fetchone()[0]
            )
        if quick != "ok":
            raise MicrostructureLifecycleError("restored shard quick_check failed")
        if int(row[0]) != int(manifest["row_count"]) or int(row[3]) != int(row[0]):
            raise MicrostructureLifecycleError("raw archive row count/uniqueness mismatch")
        if row[1] != manifest["earliest_ms"] or row[2] != manifest["latest_ms"]:
            raise MicrostructureLifecycleError("raw archive time range mismatch")
        if embedded["source_fingerprint"] != manifest["source_fingerprint"]:
            raise MicrostructureLifecycleError("embedded raw manifest mismatch")
        return {
            "verified": True,
            "quick_check": quick,
            "row_count": int(row[0]),
            "unique_row_count": int(row[3]),
            "earliest_ms": row[1],
            "latest_ms": row[2],
        }
    finally:
        temporary.unlink(missing_ok=True)


def build_offhost_ack(
    manifest: Mapping[str, Any], *, local_verification_time: str | None = None
) -> dict[str, Any]:
    if not manifest.get("verification", {}).get("verified"):
        raise MicrostructureLifecycleError("archive is not locally verified")
    ack = {
        "version": OFFHOST_ACK_VERSION,
        "archive_sha256": manifest["archive_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "local_verification_time": local_verification_time
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "row_count": manifest["row_count"],
        "source_window": {
            "instrument": manifest["instrument"],
            "utc_day": manifest["utc_day"],
            "start_ms": manifest["start_ms"],
            "end_ms": manifest["end_ms"],
        },
    }
    ack["ack_sha256"] = hashlib.sha256(canonical_json(ack).encode()).hexdigest()
    return ack


def verify_offhost_ack(
    ack: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    unsigned = {key: value for key, value in ack.items() if key != "ack_sha256"}
    expected = hashlib.sha256(canonical_json(unsigned).encode()).hexdigest()
    if ack.get("version") != OFFHOST_ACK_VERSION or ack.get("ack_sha256") != expected:
        raise MicrostructureLifecycleError("off-host ACK checksum is invalid")
    for key in ("archive_sha256", "manifest_sha256", "row_count"):
        if ack.get(key) != manifest.get(key):
            raise MicrostructureLifecycleError(f"off-host ACK {key} mismatch")
    window = ack.get("source_window") or {}
    for key in ("instrument", "utc_day", "start_ms", "end_ms"):
        if window.get(key) != manifest.get(key):
            raise MicrostructureLifecycleError(
                f"off-host ACK source window {key} mismatch"
            )


def prune_archived_raw_trades(
    database: Path | str,
    manifest: Mapping[str, Any],
    ack: Mapping[str, Any] | None,
    *,
    apply: bool = False,
    max_rows: int = 50_000,
    wall_clock_seconds: float = 20,
    queue_depth: int = 0,
    writer_lag_ms: int = 0,
    critical_gap_count: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if ack is None:
        raise MicrostructureLifecycleError("off-host ACK is required")
    verify_offhost_ack(ack, manifest)
    if manifest.get("aggregate_reconciliation", {}).get("status") != "PASS":
        raise MicrostructureLifecycleError("aggregate reconciliation did not pass")
    if manifest.get("gap_summary", {}).get("unresolved_gap_count"):
        raise MicrostructureLifecycleError("archive window contains an unresolved gap")
    if critical_gap_count:
        raise MicrostructureLifecycleError("critical live gap blocks raw pruning")
    if queue_depth > 100:
        raise MicrostructureLifecycleError("collector queue is too high for pruning")
    if writer_lag_ms > 60_000:
        raise MicrostructureLifecycleError("collector writer lag is too high")
    if not is_cold_day(str(manifest["utc_day"]), now=now):
        raise MicrostructureLifecycleError("the archive is inside the protected hot window")
    report = {
        "version": MICROSTRUCTURE_RAW_RETENTION_VERSION,
        "apply": apply,
        "instrument": manifest["instrument"],
        "start_ms": manifest["start_ms"],
        "end_ms": manifest["end_ms"],
        "max_rows": max_rows,
        "vacuum": False,
        "deleted_rows": 0,
    }
    if not apply:
        return report
    deadline = time.monotonic() + wall_clock_seconds
    connection = sqlite3.connect(Path(database), timeout=5)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        before = connection.execute(
            """SELECT COUNT(*) FROM trade_flow_observations
               WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<?""",
            (
                manifest["instrument"],
                manifest["start_ms"],
                manifest["end_ms"],
            ),
        ).fetchone()[0]
        if time.monotonic() >= deadline:
            raise MicrostructureLifecycleError("prune wall-clock budget exhausted")
        cursor = connection.execute(
            """DELETE FROM trade_flow_observations WHERE rowid IN(
                 SELECT rowid FROM trade_flow_observations
                 WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<?
                 ORDER BY source_ts_ms,uniqueness_key LIMIT ?)""",
            (
                manifest["instrument"],
                manifest["start_ms"],
                manifest["end_ms"],
                max_rows,
            ),
        )
        deleted = max(0, int(cursor.rowcount))
        after = connection.execute(
            """SELECT COUNT(*) FROM trade_flow_observations
               WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<?""",
            (
                manifest["instrument"],
                manifest["start_ms"],
                manifest["end_ms"],
            ),
        ).fetchone()[0]
        if before - after != deleted:
            raise MicrostructureLifecycleError("prune row count reconciliation failed")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS microstructure_archive_manifest(
               archive_id TEXT PRIMARY KEY,lane TEXT NOT NULL,
               instrument TEXT NOT NULL,start_ms INTEGER NOT NULL,
               end_ms INTEGER NOT NULL,row_count INTEGER NOT NULL,
               archive_sha256 TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
               aggregate_reconciliation TEXT NOT NULL,
               gap_status TEXT NOT NULL,status TEXT NOT NULL,
               offhost_ack_json TEXT,updated_at_ms INTEGER NOT NULL)"""
        )
        status = "ARCHIVED_CONFIRMED" if after == 0 else "PRUNE_IN_PROGRESS"
        connection.execute(
            """INSERT OR REPLACE INTO microstructure_archive_manifest
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                manifest["archive_sha256"], "trades", manifest["instrument"],
                manifest["start_ms"], manifest["end_ms"], manifest["row_count"],
                manifest["archive_sha256"], manifest["manifest_sha256"],
                "PASS", "PASS", status, canonical_json(ack),
                int(time.time() * 1000),
            ),
        )
        if after == 0 and connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='source_runtime_summary'"""
        ).fetchone():
            connection.execute(
                """UPDATE source_runtime_summary
                   SET earliest_ms=(
                         SELECT source_ts_ms FROM trade_flow_observations
                         WHERE instrument=?
                         ORDER BY source_ts_ms LIMIT 1
                       ),
                       generated_at_ms=?
                   WHERE lane='trades' AND instrument=?""",
                (
                    manifest["instrument"],
                    int(time.time() * 1000),
                    manifest["instrument"],
                ),
            )
        connection.commit()
        report.update(
            {
                "deleted_rows": deleted,
                "remaining_rows": after,
                "status": status,
                "free_pages": connection.execute(
                    "PRAGMA freelist_count"
                ).fetchone()[0],
            }
        )
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
