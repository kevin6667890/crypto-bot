"""Compact, deterministic storage for live analysis snapshots.

The decision/evaluation ledgers remain authoritative.  This module stores a
small replay/audit projection and references canonical source ranges instead of
copying candles, trades, CVD, OI, or other long series into every poll.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from typing import Any, Mapping, Sequence


ANALYSIS_SNAPSHOT_STORAGE_VERSION = "analysis-snapshot-storage-v2"
ANALYSIS_SNAPSHOT_SCHEMA_VERSION = "analysis-snapshot-compact-v2"
ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES = int(
    os.getenv("ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES", "32768")
)
MAX_SEQUENCE_ITEMS = int(os.getenv("ANALYSIS_SNAPSHOT_MAX_SEQUENCE_ITEMS", "32"))
MAX_NESTING_DEPTH = int(os.getenv("ANALYSIS_SNAPSHOT_MAX_DEPTH", "10"))

PERMANENT_LEDGER = "PERMANENT_LEDGER"
REPRODUCIBILITY_MANIFEST = "REPRODUCIBILITY_MANIFEST"
DEBUG_SAMPLE = "DEBUG_SAMPLE"
ERROR_FORENSIC = "ERROR_FORENSIC"
RESEARCH_ARTIFACT = "RESEARCH_ARTIFACT"

_COLUMN_DECLARATIONS = {
    "payload_storage_mode": "TEXT",
    "payload_schema_version": "TEXT",
    "payload_sha256": "TEXT",
    "original_payload_bytes": "INTEGER",
    "compact_payload_bytes": "INTEGER",
    "archive_bundle_id": "TEXT",
    "archive_member": "TEXT",
    "archive_codec": "TEXT",
    "archive_verified_at": "TEXT",
    "reconstructable": "INTEGER NOT NULL DEFAULT 1",
    "retention_class": "TEXT",
    "source_manifest_json": "TEXT",
}


class SnapshotPayloadError(ValueError):
    """A payload violates the compact snapshot contract."""


@dataclass(frozen=True)
class CompactSnapshot:
    payload: str
    original_sha256: str
    original_bytes: int
    compact_bytes: int
    source_manifest_json: str
    stripped_paths: tuple[str, ...]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def stable_sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def ensure_snapshot_v2_schema(connection: sqlite3.Connection) -> None:
    """Add metadata columns without replacing the existing table or its rows."""
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(analysis_snapshots)")
    }
    for name, declaration in _COLUMN_DECLARATIONS.items():
        if name not in columns:
            connection.execute(
                f'ALTER TABLE analysis_snapshots ADD COLUMN "{name}" {declaration}'
            )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS analysis_snapshot_storage_telemetry(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            original_payload_bytes INTEGER NOT NULL,
            compact_payload_bytes INTEGER NOT NULL,
            stripped_paths_json TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            FOREIGN KEY(snapshot_id) REFERENCES analysis_snapshots(id))"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_snapshot_storage_telemetry_created
           ON analysis_snapshot_storage_telemetry(created_at DESC)"""
    )


def _scalar_mapping(
    value: Any, keys: Sequence[str], *, allow_small: bool = False
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            result[key] = item
        elif allow_small and isinstance(item, Mapping):
            result[key] = {
                str(k): v
                for k, v in item.items()
                if v is None or isinstance(v, (str, int, float, bool))
            }
    return result


def _source_manifest(analysis: Mapping[str, Any]) -> dict[str, Any]:
    flow = analysis.get("flow")
    flow = flow if isinstance(flow, Mapping) else {}
    quality = flow.get("decision_quality")
    quality = quality if isinstance(quality, Mapping) else {}
    professional = flow.get("professional")
    professional = professional if isinstance(professional, Mapping) else {}
    collection = professional.get("collection")
    collection = collection if isinstance(collection, Mapping) else {}
    candle_end = analysis.get("candle_close_ts")
    candle_start = None
    timeframes = analysis.get("timeframes")
    if isinstance(timeframes, Mapping):
        stamps = [
            frame.get("candle_close_ts")
            for frame in timeframes.values()
            if isinstance(frame, Mapping)
            and isinstance(frame.get("candle_close_ts"), (int, float))
        ]
        if stamps:
            candle_start = min(stamps)
    watermarks = {
        "candle": candle_end,
        "cvd": quality.get("cvd_timestamp"),
        "oi": quality.get("oi_timestamp"),
        "market_snapshot": quality.get("snapshot_timestamp"),
        "last_trade": quality.get("last_trade_ts"),
    }
    row_counts = {
        "trade_count": quality.get("trade_count"),
        "oi_samples": quality.get("oi_samples"),
        "vpvr_trade_count": collection.get("trade_count"),
    }
    decision_input = analysis.get("decision_input_summary")
    decision_input = (
        decision_input if isinstance(decision_input, Mapping) else {}
    )
    source_versions = {
        "market": analysis.get("market_source")
        or decision_input.get("market_source"),
        "flow": flow.get("source"),
        "decision_engine": analysis.get("decision_engine_version"),
        "strategy": analysis.get("strategy_version"),
    }
    fingerprint_input = {
        "instrument": analysis.get("instrument"),
        "timeframe": analysis.get("execution_timeframe") or "15m",
        "input_start": candle_start,
        "input_end": candle_end,
        "watermarks": watermarks,
        "row_counts": row_counts,
        "source_versions": source_versions,
    }
    return {
        **fingerprint_input,
        "source_fingerprints": {
            "combined": stable_sha256(canonical_json(fingerprint_input))
        },
    }


def _small_gate_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:MAX_SEQUENCE_ITEMS]:
        rows.append(
            _scalar_mapping(
                item,
                (
                    "key", "label", "status", "passed", "reason",
                    "actual", "threshold", "value", "detail_code",
                ),
            )
        )
    return rows


def _compact_projection(
    analysis: Mapping[str, Any], *, code_commit: str, retention_class: str
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    source_manifest = _source_manifest(analysis)
    flow = analysis.get("flow")
    flow = flow if isinstance(flow, Mapping) else {}
    decision_flow = analysis.get("flow_context")
    decision_flow = decision_flow if isinstance(decision_flow, Mapping) else {}
    risk = analysis.get("risk")
    risk = risk if isinstance(risk, Mapping) else {}
    input_start = source_manifest.get("input_start")
    input_end = source_manifest.get("input_end")
    dataset_identity = stable_sha256(
        canonical_json(
            {
                "instrument": analysis.get("instrument"),
                "start": input_start,
                "end": input_end,
                "sources": source_manifest["source_fingerprints"],
            }
        )
    )
    compact: dict[str, Any] = {
        "snapshot_id": None,
        "created_at": analysis.get("updated_at")
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "instrument": analysis.get("instrument"),
        "timeframe": analysis.get("execution_timeframe") or "15m",
        "run_identity": analysis.get("evaluation_id"),
        "decision_identity": analysis.get("evaluation_id"),
        "signal_setup_id": analysis.get("signal_setup_id"),
        "strategy_identity": analysis.get("strategy_version"),
        "dataset_identity": dataset_identity,
        "code_commit": code_commit,
        "storage_version": ANALYSIS_SNAPSHOT_STORAGE_VERSION,
        "schema_version": ANALYSIS_SNAPSHOT_SCHEMA_VERSION,
        "model_version": analysis.get("decision_engine_version"),
        "input_start": input_start,
        "input_end": input_end,
        "source_manifest": source_manifest,
        "feature_values": {
            **_scalar_mapping(
                analysis,
                (
                    "price", "ema20", "rsi14", "atr14", "volume_ratio",
                    "distance_ema20_pct", "score",
                ),
            ),
            **_scalar_mapping(
                decision_flow,
                ("cvd_delta", "oi_change_pct", "cvd_timestamp", "oi_timestamp"),
            ),
        },
        "indicator_values": _scalar_mapping(
            analysis.get("indicator_values"),
            ("fast_ma", "slow_ma", "ema", "atr", "rsi", "volume_ratio"),
        ),
        "action": analysis.get("action"),
        "final_decision": analysis.get("action"),
        "classification": analysis.get("classification")
        or analysis.get("bias")
        or analysis.get("regime"),
        "bias": analysis.get("bias"),
        "regime": analysis.get("regime"),
        "reason": analysis.get("reason")
        or analysis.get("blocking_reason")
        or analysis.get("summary"),
        "confidence": analysis.get("confidence") or analysis.get("score"),
        "gate_results": _small_gate_rows(analysis.get("gate_results")),
        "contributions": _small_gate_rows(analysis.get("contributions")),
        "conditions": _small_gate_rows(analysis.get("conditions")),
        "risk_references": _scalar_mapping(
            risk,
            (
                "allowed", "open_positions", "daily_pnl_r",
                "consecutive_losses", "cooldown_until",
            ),
        ),
        "accounting_references": {
            "accounting_version": analysis.get("accounting_version"),
        },
        "order_references": {
            "trade_id": analysis.get("trade_id"),
            "order_id": analysis.get("order_id"),
        },
        "lineage_references": {
            "evaluation_id": analysis.get("evaluation_id"),
            "signal_setup_id": analysis.get("signal_setup_id"),
            "config_hash": analysis.get("config_hash"),
        },
        "flow": {
            **_scalar_mapping(
                flow,
                (
                    "cvd", "cvd_delta", "decision_cvd_delta", "oi",
                    "oi_change_pct", "decision_oi_change_pct", "source",
                ),
            ),
            "decision_quality": _scalar_mapping(
                flow.get("decision_quality"),
                (
                    "ready", "window_seconds", "coverage_seconds",
                    "trade_count", "last_trade_age_seconds", "oi_samples",
                    "last_oi_age_seconds", "cvd_timestamp", "oi_timestamp",
                ),
            ),
        },
        "original_artifact_hash": None,
        "storage_mode": "INLINE_COMPACT",
        "retention_class": retention_class,
        "reconstructable": True,
    }
    stripped = (
        "flow.cvd_series",
        "flow.oi_history",
        "flow.professional.cvd_series",
        "flow.professional.oi_series",
        "flow.professional.price_series",
        "flow.professional.raw_trades",
        "vpvr.rows",
        "candles",
        "trades",
    )
    return compact, source_manifest, stripped


def validate_compact_payload(
    value: Any,
    *,
    max_bytes: int = ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES,
    max_sequence_items: int = MAX_SEQUENCE_ITEMS,
    max_depth: int = MAX_NESTING_DEPTH,
) -> int:
    """Recursively enforce shape and total-size limits, not field-name filters."""
    def visit(item: Any, depth: int, path: str) -> None:
        if depth > max_depth:
            raise SnapshotPayloadError(f"payload nesting exceeds limit at {path}")
        if item is None or isinstance(item, (str, int, float, bool)):
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise SnapshotPayloadError(f"non-string key at {path}")
                visit(child, depth + 1, f"{path}.{key}")
            return
        if isinstance(item, (list, tuple)):
            if len(item) > max_sequence_items:
                raise SnapshotPayloadError(
                    f"sequence length {len(item)} exceeds {max_sequence_items} at {path}"
                )
            for index, child in enumerate(item):
                visit(child, depth + 1, f"{path}[{index}]")
            return
        raise SnapshotPayloadError(
            f"unsupported payload type {type(item).__name__} at {path}"
        )

    visit(value, 0, "$")
    size = len(canonical_json(value).encode("utf-8"))
    if size > max_bytes:
        raise SnapshotPayloadError(
            f"compact payload is {size} bytes; maximum is {max_bytes}"
        )
    return size


def compact_analysis_snapshot(
    analysis: Mapping[str, Any],
    *,
    code_commit: str | None = None,
    retention_class: str = PERMANENT_LEDGER,
) -> CompactSnapshot:
    original = canonical_json(analysis)
    compact, source_manifest, stripped = _compact_projection(
        analysis,
        code_commit=code_commit or os.getenv("GIT_COMMIT", "unknown"),
        retention_class=retention_class,
    )
    digest = stable_sha256(original)
    compact["original_artifact_hash"] = digest
    compact_size = validate_compact_payload(compact)
    payload = canonical_json(compact)
    return CompactSnapshot(
        payload=payload,
        original_sha256=digest,
        original_bytes=len(original.encode("utf-8")),
        compact_bytes=compact_size,
        source_manifest_json=canonical_json(source_manifest),
        stripped_paths=stripped,
    )


def write_compact_snapshot(
    connection: sqlite3.Connection,
    *,
    created_at: str,
    instrument: str,
    analysis: Mapping[str, Any],
    retention_class: str = PERMANENT_LEDGER,
    code_commit: str | None = None,
) -> int:
    """Always persist the compact core, even when the original was oversized."""
    ensure_snapshot_v2_schema(connection)
    compact = compact_analysis_snapshot(
        analysis, code_commit=code_commit, retention_class=retention_class
    )
    cursor = connection.execute(
        """INSERT INTO analysis_snapshots(
            created_at,instrument,payload,payload_storage_mode,
            payload_schema_version,payload_sha256,original_payload_bytes,
            compact_payload_bytes,reconstructable,retention_class,
            source_manifest_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            created_at, instrument, compact.payload, "INLINE_COMPACT",
            ANALYSIS_SNAPSHOT_SCHEMA_VERSION, compact.original_sha256,
            compact.original_bytes, compact.compact_bytes, 1,
            retention_class, compact.source_manifest_json,
        ),
    )
    snapshot_id = int(cursor.lastrowid)
    parsed = json.loads(compact.payload)
    parsed["snapshot_id"] = snapshot_id
    final_payload = canonical_json(parsed)
    final_size = validate_compact_payload(final_payload_as_json(final_payload))
    connection.execute(
        """UPDATE analysis_snapshots
           SET payload=?,compact_payload_bytes=? WHERE id=?""",
        (final_payload, final_size, snapshot_id),
    )
    if compact.original_bytes > ANALYSIS_SNAPSHOT_MAX_INLINE_BYTES:
        connection.execute(
            """INSERT INTO analysis_snapshot_storage_telemetry(
                snapshot_id,created_at,event_type,original_payload_bytes,
                compact_payload_bytes,stripped_paths_json,detail_json)
               VALUES(?,?,?,?,?,?,?)""",
            (
                snapshot_id, created_at, "OVERSIZED_INPUT_COMPACTED",
                compact.original_bytes, final_size,
                canonical_json(compact.stripped_paths),
                canonical_json({"core_ledger_persisted": True}),
            ),
        )
    return snapshot_id


def final_payload_as_json(payload: str) -> Any:
    return json.loads(payload)


def snapshot_payload_for_reader(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise SnapshotPayloadError("snapshot payload is not a JSON object")
    return value
