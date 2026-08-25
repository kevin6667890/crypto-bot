"""Persistent, deterministic tracked-thesis lifecycle.

Historical evidence is accepted only through a server-verified Phase 3 result
and is stored as an immutable baseline.  Current evaluation reads a bounded
latest-confirmed-candle window from the live canonical reader.  The two
dataset identities deliberately have different fields and lifecycles.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

try:
    from market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from signal_identity import canonical_json
    from thesis_event_engine import (
        FEATURE_REGISTRY, CompiledEventDefinition, ThesisSpecV1,
        ThesisValidationError, _compare, compile_feature_rows, compile_thesis,
    )
except ImportError:
    from .market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from .signal_identity import canonical_json
    from .thesis_event_engine import (
        FEATURE_REGISTRY, CompiledEventDefinition, ThesisSpecV1,
        ThesisValidationError, _compare, compile_feature_rows, compile_thesis,
    )


TRACK_SCHEMA_VERSION = "tracked-thesis-v1"
CURRENT_EVALUATION_VERSION = "current-thesis-evaluation-v1"
CURRENT_EVALUATION_POLICY_VERSION = "current-thesis-evaluation-policy-v1"
CURRENT_DATASET_IDENTITY_VERSION = "current-canonical-dataset-v1"
DELTA_VERSION = "thesis-evaluation-delta-v1"
TRACKING_SCHEMA_MIGRATION_VERSION = 2
TRACK_CREATE_REQUEST_VERSION = "track-thesis-request-v1"
TRACK_ARCHIVE_VERSION = "track-thesis-archive-v1"
CURRENT_READER_LIMIT = 320


class TrackingError(ValueError):
    """Stable user-safe tracking failure."""


def _utc_iso(epoch: int | None = None) -> str:
    return datetime.fromtimestamp(epoch if epoch is not None else time.time(), timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decode(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TrackingError("stored tracking artifact is invalid")
    return parsed


def historical_baseline(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project only server-produced immutable historical evidence."""
    if result.get("result_version") != "thesis-test-result-v1" or result.get("status") != "COMPLETED":
        raise TrackingError("a completed, validated historical result is required")
    historical = result.get("historical_data")
    if not isinstance(historical, Mapping) or not historical.get("dataset_id"):
        raise TrackingError("historical dataset identity is required")
    aggregates = result.get("aggregates")
    if not isinstance(aggregates, Mapping):
        raise TrackingError("historical aggregate evidence is required")
    qualities = [str(item.get("sample_quality")) for item in aggregates.values()
                 if isinstance(item, Mapping) and item.get("sample_quality")]
    rank = {"INSUFFICIENT": 0, "LOW": 1, "MODERATE": 2, "ADEQUATE": 3}
    # A headline quality must not make a weak horizon look stronger because a
    # different horizon happened to retain more uncensored outcomes.
    sample_quality = min(qualities, key=lambda item: rank.get(item, -1), default="INSUFFICIENT")
    return {
        "version": "historical-thesis-baseline-v1",
        "result_hash": str(result["result_hash"]),
        "definition_hash": str(result["definition_hash"]),
        "historical_dataset_identity": str(historical["dataset_id"]),
        "historical_engine_version": str(result["engine_version"]),
        "historical_tested_range": dict(result.get("tested_range") or {}),
        "historical_summary": {
            "independent_event_count": int(result.get("independent_event_count", 0)),
            "sample_quality": sample_quality,
            "horizon_aggregates": {str(key): dict(value) for key, value in aggregates.items()
                                   if isinstance(value, Mapping)},
        },
        "historical_data": dict(historical),
        "captured_at": _utc_iso(),
    }


def _condition_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    # The same observation may intentionally appear as both required and
    # optional.  Keep those roles distinct when calculating deterministic
    # deltas.
    return (str(item.get("requirement")), str(item.get("feature")),
            str(item.get("operator")), canonical_json(item.get("value")))


def evaluation_delta(previous: Mapping[str, Any] | None,
                     current: Mapping[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {
            "version": DELTA_VERSION, "initial_evaluation": True,
            "status_changed": False, "previous_status": None,
            "current_status": current.get("overall_status"),
            "condition_changes": [], "quality_changes": [],
            "source_changes": [], "material_change": False,
        }
    prior_conditions = {_condition_key(item): item for item in previous.get("conditions", [])}
    condition_changes: list[dict[str, Any]] = []
    quality_changes: list[dict[str, Any]] = []
    for item in current.get("conditions", []):
        prior = prior_conditions.get(_condition_key(item))
        if prior is None:
            continue
        if prior.get("state") != item.get("state"):
            condition_changes.append({
                "feature": item.get("feature"), "requirement": item.get("requirement"),
                "from": prior.get("state"), "to": item.get("state"),
                "previous_observed_value": prior.get("observed_value"),
                "current_observed_value": item.get("observed_value"),
                "operator": item.get("operator"), "configured_value": item.get("value"),
            })
        if prior.get("quality") != item.get("quality"):
            quality_changes.append({"feature": item.get("feature"),
                                    "from": prior.get("quality"), "to": item.get("quality")})
    source_changes = []
    previous_identity = previous.get("current_dataset_identity") or {}
    current_identity = current.get("current_dataset_identity") or {}
    if (previous_identity.get("sources") != current_identity.get("sources") or
            previous.get("current_source_version") != current.get("current_source_version")):
        source_changes.append({
            "field": "current_source",
            "from": {"sources": previous_identity.get("sources"),
                     "versions": previous.get("current_source_version")},
            "to": {"sources": current_identity.get("sources"),
                   "versions": current.get("current_source_version")},
        })
    status_changed = previous.get("overall_status") != current.get("overall_status")
    return {
        "version": DELTA_VERSION, "initial_evaluation": False,
        "status_changed": status_changed,
        "previous_status": previous.get("overall_status"),
        "current_status": current.get("overall_status"),
        "condition_changes": condition_changes, "quality_changes": quality_changes,
        "source_changes": source_changes,
        "material_change": bool(status_changed or condition_changes or quality_changes or source_changes),
    }


class CurrentFeatureEvaluatorV1:
    """Evaluate one live snapshot with the historical registry's exact math."""

    version = CURRENT_EVALUATION_POLICY_VERSION

    def __init__(self, reader: Any, *, clock: Any = time.time) -> None:
        self.reader = reader
        self.clock = clock

    @staticmethod
    def _dataset_identity(rows: Sequence[Mapping[str, Any]], definition: CompiledEventDefinition) -> dict[str, Any]:
        stable = [{key: row.get(key) for key in (
            "ts", "candle_close_ts", "open", "high", "low", "close", "volume",
            "confirmed", "source", "source_version", "_source_store",
        )} for row in rows]
        sources = sorted({canonical_json({
            "source": row.get("source") or "unknown",
            "source_version": row.get("source_version"),
            "source_store": row.get("_source_store"),
        }) for row in rows})
        payload = {
            "version": CURRENT_DATASET_IDENTITY_VERSION,
            "instrument": definition.canonical_instrument,
            "timeframe": definition.timeframe,
            "latest_confirmed_candle": stable[-1]["candle_close_ts"] if stable else None,
            "row_count": len(stable),
            "sources": [json.loads(item) for item in sources],
            "content_sha256": _hash(stable),
        }
        return {**payload, "dataset_id": _hash(payload)}

    def _blocked(self, track: Mapping[str, Any], now: int, limitation: str,
                 *, status: str = "BLOCKED") -> dict[str, Any]:
        return {
            "version": CURRENT_EVALUATION_VERSION,
            "evaluation_version": CURRENT_EVALUATION_VERSION,
            "evaluation_policy_version": self.version,
            "track_id": track["track_id"], "definition_hash": track["definition_hash"],
            "evaluated_at": _utc_iso(now), "evaluated_at_epoch": now, "as_of": None,
            "source_candle_timestamp": None, "current_dataset_identity": None,
            "current_source_version": None, "overall_status": status,
            "required_match_count": 0,
            "required_condition_count": len(track["thesis_spec"].get("required_conditions", [])),
            "conditions": [], "freshness": {"state": "UNKNOWN", "age_seconds": None},
            "limitations": [limitation],
        }

    def evaluate(self, track: Mapping[str, Any], *, now: int | None = None) -> dict[str, Any]:
        current_time = int(self.clock() if now is None else now)
        if (track.get("schema_version") != TRACK_SCHEMA_VERSION or
                track.get("current_evaluation_policy_version") != self.version):
            return self._blocked(track, current_time, "TRACK_OR_EVALUATION_POLICY_VERSION_MISMATCH",
                                 status="BLOCKED_VERSION_MISMATCH")
        try:
            spec = ThesisSpecV1.from_dict(track["thesis_spec"])
            definition = compile_thesis(spec)
        except (KeyError, ThesisValidationError) as error:
            return self._blocked(track, current_time, f"DEFINITION_UNAVAILABLE:{error}",
                                 status="BLOCKED_VERSION_MISMATCH")
        saved = track.get("compiled_definition") or {}
        if (saved.get("version") != definition.version or
                saved.get("feature_versions") != dict(definition.feature_versions) or
                track.get("definition_hash") != definition.definition_hash):
            return self._blocked(track, current_time, "FEATURE_OR_DEFINITION_VERSION_MISMATCH",
                                 status="BLOCKED_VERSION_MISMATCH")
        if set(definition.source_requirements) - {"OHLCV"}:
            return self._blocked(track, current_time, "CURRENT_SOURCE_GROUP_UNSUPPORTED")
        try:
            raw = self.reader.candles(definition.canonical_instrument, definition.timeframe,
                                      current_time, CURRENT_READER_LIMIT)
        except (OSError, sqlite3.Error, ValueError):
            return self._blocked(track, current_time, "CURRENT_CANONICAL_READER_UNAVAILABLE")
        rows = confirmed_candles_as_of(raw, definition.timeframe, current_time)
        if not rows:
            return self._blocked(track, current_time, "NO_CONFIRMED_CURRENT_CANDLE")
        if rows[-1].get("_source_store") != "market_candles":
            return self._blocked(track, current_time, "LATEST_CANDLE_IS_NOT_FROM_CURRENT_LIVE_CANONICAL_STORE")
        width = TIMEFRAME_SECONDS[definition.timeframe]
        # Only the contiguous suffix can qualify a current rolling feature.
        # A gap older than every feature's warmup must not poison the latest
        # value, while a recent gap must leave the affected feature UNKNOWN.
        suffix_start = 0
        timestamps = [int(row["ts"]) for row in rows]
        for index, (left, right) in enumerate(zip(timestamps, timestamps[1:])):
            if right - left != width:
                suffix_start = index + 1
        rows = rows[suffix_start:]
        latest_close = int(rows[-1]["candle_close_ts"])
        age = max(0, current_time - latest_close)
        stale = age > width * 2
        features = compile_feature_rows(rows)
        current_row = features[-1]
        identity = self._dataset_identity(rows, definition)
        source_versions = sorted({str(row.get("source_version") or row.get("source") or "unknown") for row in rows})

        conditions: list[dict[str, Any]] = []
        for requirement, configured in (
                [("REQUIRED", item) for item in definition.required_conditions] +
                [("OPTIONAL", item) for item in definition.optional_conditions]):
            feature = FEATURE_REGISTRY[configured.feature]
            observed = feature.evaluator(current_row)
            limitation = None
            if stale:
                state, quality, limitation = "UNKNOWN", "STALE", "CURRENT_SOURCE_EXCEEDS_FRESHNESS_THRESHOLD"
            elif len(rows) < feature.minimum_history:
                state, quality, limitation = "UNKNOWN", "PARTIAL", "CURRENT_CONTIGUOUS_WARMUP_INCOMPLETE"
            elif observed is None:
                state, quality, limitation = "UNKNOWN", "PARTIAL", "FEATURE_WARMUP_OR_VALUE_UNAVAILABLE"
            else:
                compared = _compare(observed, configured.operator, configured.value)
                state, quality = ("TRUE" if compared is True else "FALSE" if compared is False else "UNKNOWN"), "AVAILABLE"
            conditions.append({
                "feature": configured.feature, "feature_version": feature.version,
                "requirement": requirement, "operator": configured.operator,
                "value": configured.value, "observed_value": observed,
                "state": state, "source_timestamp": latest_close,
                "quality": quality, "limitation": limitation,
            })
        required = [item for item in conditions if item["requirement"] == "REQUIRED"]
        if stale:
            overall = "STALE"
        elif any(item["state"] == "UNKNOWN" for item in required):
            overall = "PARTIAL"
        elif any(item["state"] == "FALSE" for item in required):
            overall = "NOT_MATCHING"
        else:
            overall = "MATCHING"
        return {
            "version": CURRENT_EVALUATION_VERSION,
            "evaluation_version": CURRENT_EVALUATION_VERSION,
            "evaluation_policy_version": self.version,
            "track_id": track["track_id"], "definition_hash": definition.definition_hash,
            "evaluated_at": _utc_iso(current_time), "evaluated_at_epoch": current_time,
            "as_of": latest_close, "source_candle_timestamp": latest_close,
            "current_dataset_identity": identity,
            "current_source_version": source_versions,
            "overall_status": overall,
            "required_match_count": sum(item["state"] == "TRUE" for item in required),
            "required_condition_count": len(required), "conditions": conditions,
            "freshness": {"state": "STALE" if stale else "FRESH", "age_seconds": age,
                          "threshold_seconds": width * 2},
            "limitations": sorted({item["limitation"] for item in conditions if item["limitation"]}),
        }


class ThesisTrackingRepositoryV1:
    """Small namespaced SQLite store with transactional idempotency."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._init_lock = threading.Lock()
        self._schema_version: int | None = None
        self.initialize()

    def _require_supported_schema(self) -> None:
        if self._schema_version != TRACKING_SCHEMA_MIGRATION_VERSION:
            raise TrackingError(
                f"unsupported thesis tracking database schema version: {self._schema_version}"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._init_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                schema_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='thesis_tracking_schema'"
                ).fetchone()
                if schema_table:
                    existing = connection.execute(
                        "SELECT version FROM thesis_tracking_schema WHERE singleton=1"
                    ).fetchone()
                    existing_version = int(existing[0]) if existing else None
                    if (existing_version is not None and
                            existing_version > TRACKING_SCHEMA_MIGRATION_VERSION):
                        self._schema_version = existing_version
                        return
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS tracked_theses (
                        track_id TEXT PRIMARY KEY,
                        schema_version TEXT NOT NULL,
                        definition_hash TEXT NOT NULL,
                        historical_result_hash TEXT NOT NULL,
                        historical_dataset_identity TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'WATCHING',
                        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                        created_at_epoch INTEGER NOT NULL,
                        updated_at_epoch INTEGER NOT NULL,
                        archived_at_epoch INTEGER,
                        artifact_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tracked_theses_active_updated
                        ON tracked_theses(is_active,updated_at_epoch DESC);
                    CREATE INDEX IF NOT EXISTS idx_tracked_theses_definition
                        ON tracked_theses(definition_hash,historical_result_hash,is_active);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_theses_active_identity
                        ON tracked_theses(definition_hash,historical_result_hash)
                        WHERE is_active=1;
                    CREATE TABLE IF NOT EXISTS thesis_current_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        track_id TEXT NOT NULL REFERENCES tracked_theses(track_id),
                        idempotency_key TEXT NOT NULL UNIQUE,
                        definition_hash TEXT NOT NULL,
                        overall_status TEXT NOT NULL,
                        source_candle_timestamp INTEGER,
                        current_dataset_id TEXT,
                        evaluated_at_epoch INTEGER NOT NULL,
                        material_change INTEGER NOT NULL CHECK(material_change IN (0,1)),
                        evaluation_json TEXT NOT NULL,
                        delta_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_thesis_evaluations_track_time
                        ON thesis_current_evaluations(track_id,evaluated_at_epoch DESC);
                    CREATE INDEX IF NOT EXISTS idx_thesis_evaluations_changes
                        ON thesis_current_evaluations(material_change,evaluated_at_epoch DESC);
                    CREATE TABLE IF NOT EXISTS thesis_current_snapshots (
                        track_id TEXT PRIMARY KEY REFERENCES tracked_theses(track_id),
                        idempotency_key TEXT NOT NULL UNIQUE,
                        source_candle_timestamp INTEGER,
                        evaluated_at_epoch INTEGER NOT NULL,
                        evaluation_json TEXT NOT NULL,
                        delta_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS thesis_tracking_schema (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL
                    );
                    INSERT INTO thesis_tracking_schema(singleton,version) VALUES(1,2)
                        ON CONFLICT(singleton) DO UPDATE SET version=MAX(version,excluded.version);
                """)
                row = connection.execute(
                    "SELECT version FROM thesis_tracking_schema WHERE singleton=1"
                ).fetchone()
                self._schema_version = int(row[0]) if row else None

    @staticmethod
    def _track(row: sqlite3.Row) -> dict[str, Any]:
        return _decode(str(row["artifact_json"]))

    @staticmethod
    def _evaluation(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = _decode(str(row["evaluation_json"]))
        value["delta"] = _decode(str(row["delta_json"]))
        return value

    def create(self, artifact: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        self._require_supported_schema()
        now = int(time.time())
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT artifact_json FROM tracked_theses
                   WHERE definition_hash=? AND historical_result_hash=? AND is_active=1 LIMIT 1""",
                (artifact["definition_hash"], artifact["historical_result_hash"]),
            ).fetchone()
            if existing:
                connection.commit()
                return _decode(str(existing["artifact_json"])), False
            stored = dict(artifact)
            stored["created_at"] = _utc_iso(now)
            stored["updated_at"] = stored["created_at"]
            artifact_json = canonical_json(stored)
            # Return exactly the representation that a duplicate request or a
            # restarted process will read (JSON arrays, not Python tuples).
            stored = _decode(artifact_json)
            connection.execute(
                """INSERT INTO tracked_theses(track_id,schema_version,definition_hash,
                       historical_result_hash,historical_dataset_identity,status,is_active,
                       created_at_epoch,updated_at_epoch,artifact_json)
                   VALUES(?,?,?,?,?,'WATCHING',1,?,?,?)""",
                (stored["track_id"], TRACK_SCHEMA_VERSION, stored["definition_hash"],
                 stored["historical_result_hash"], stored["historical_dataset_identity"],
                 now, now, artifact_json),
            )
            connection.commit()
            return stored, True

    def get(self, track_id: str, *, include_archived: bool = False) -> dict[str, Any] | None:
        self._require_supported_schema()
        with closing(self._connect()) as connection:
            clause = "" if include_archived else " AND is_active=1"
            row = connection.execute(
                f"SELECT artifact_json FROM tracked_theses WHERE track_id=?{clause}", (track_id,)
            ).fetchone()
            return _decode(str(row["artifact_json"])) if row else None

    def list(self, *, active_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        self._require_supported_schema()
        with closing(self._connect()) as connection:
            clause = "WHERE is_active=1" if active_only else ""
            rows = connection.execute(
                f"SELECT * FROM tracked_theses {clause} ORDER BY updated_at_epoch DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            output = []
            for row in rows:
                track = self._track(row)
                latest = connection.execute(
                    "SELECT * FROM thesis_current_snapshots WHERE track_id=?", (track["track_id"],)
                ).fetchone()
                output.append({"track": track, "latest_evaluation": self._evaluation(latest)})
            return output

    def detail(self, track_id: str, *, history_limit: int = 50) -> dict[str, Any] | None:
        self._require_supported_schema()
        track = self.get(track_id, include_archived=True)
        if track is None:
            return None
        with closing(self._connect()) as connection:
            latest = connection.execute(
                "SELECT * FROM thesis_current_snapshots WHERE track_id=?", (track_id,)
            ).fetchone()
            rows = connection.execute(
                """SELECT * FROM thesis_current_evaluations WHERE track_id=?
                   ORDER BY evaluated_at_epoch DESC,rowid DESC LIMIT ?""",
                (track_id, max(1, min(int(history_limit), 100))),
            ).fetchall()
        history = [self._evaluation(row) for row in rows]
        return {"track": track, "latest_evaluation": self._evaluation(latest),
                "evaluation_history": history}

    def archive(self, track_id: str) -> dict[str, Any] | None:
        self._require_supported_schema()
        now = int(time.time())
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM tracked_theses WHERE track_id=?", (track_id,)).fetchone()
            if row is None:
                connection.rollback()
                return None
            artifact = self._track(row)
            artifact.update({"is_active": False, "archived_at": _utc_iso(now), "updated_at": _utc_iso(now)})
            connection.execute(
                """UPDATE tracked_theses SET is_active=0,status='ARCHIVED',archived_at_epoch=?,
                   updated_at_epoch=?,artifact_json=? WHERE track_id=?""",
                (now, now, canonical_json(artifact), track_id),
            )
            connection.commit()
            return artifact

    def record_evaluation(self, evaluation: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        self._require_supported_schema()
        source_timestamp = evaluation.get("source_candle_timestamp")
        freshness = evaluation.get("freshness")
        freshness_state = (freshness.get("state") if isinstance(freshness, Mapping)
                           else "UNKNOWN")
        source_marker: Any = source_timestamp
        if source_marker is None:
            source_marker = {"missing_source_status": evaluation.get("overall_status"),
                             "limitations": evaluation.get("limitations", [])}
        idempotency_key = _hash({
            "track_id": evaluation["track_id"], "definition_hash": evaluation["definition_hash"],
            "source_candle_timestamp": source_marker,
            "evaluation_policy_version": evaluation["evaluation_policy_version"],
            # The same candle can legitimately transition from fresh to stale
            # while no newer confirmed candle exists.  Store that material
            # quality transition once, while repeated refreshes in either
            # freshness state remain no-ops.
            "freshness_state": freshness_state,
        })
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM thesis_current_snapshots WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                existing_value = self._evaluation(existing) or {}
                if (existing_value.get("current_dataset_identity") !=
                        evaluation.get("current_dataset_identity")):
                    connection.rollback()
                    raise TrackingError("current canonical dataset identity changed for an already evaluated candle")
                stable_fields = ("overall_status", "conditions", "source_candle_timestamp",
                                 "definition_hash", "evaluation_policy_version")
                if any(existing_value.get(key) != evaluation.get(key) for key in stable_fields):
                    connection.rollback()
                    raise TrackingError("current evaluation semantics changed for an idempotent source candle")
                connection.commit()
                return existing_value, False
            previous_row = connection.execute(
                "SELECT * FROM thesis_current_snapshots WHERE track_id=?", (evaluation["track_id"],)
            ).fetchone()
            previous = self._evaluation(previous_row)
            delta = evaluation_delta(previous, evaluation)
            evaluation_id = _hash({"idempotency_key": idempotency_key, "evaluation": evaluation})
            current_identity = evaluation.get("current_dataset_identity")
            current_dataset_id = current_identity.get("dataset_id") if isinstance(current_identity, Mapping) else None
            stored_evaluation = canonical_json({**evaluation, "evaluation_id": evaluation_id})
            stored_delta = canonical_json(delta)
            history_created = previous is None or bool(delta["material_change"])
            if history_created:
                connection.execute(
                    """INSERT INTO thesis_current_evaluations(evaluation_id,track_id,idempotency_key,
                           definition_hash,overall_status,source_candle_timestamp,current_dataset_id,
                           evaluated_at_epoch,material_change,evaluation_json,delta_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (evaluation_id, evaluation["track_id"], idempotency_key,
                     evaluation["definition_hash"], evaluation["overall_status"], source_timestamp,
                     current_dataset_id, evaluation["evaluated_at_epoch"], int(delta["material_change"]),
                     stored_evaluation, stored_delta),
                )
            connection.execute(
                """INSERT INTO thesis_current_snapshots(track_id,idempotency_key,
                       source_candle_timestamp,evaluated_at_epoch,evaluation_json,delta_json)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(track_id) DO UPDATE SET
                       idempotency_key=excluded.idempotency_key,
                       source_candle_timestamp=excluded.source_candle_timestamp,
                       evaluated_at_epoch=excluded.evaluated_at_epoch,
                       evaluation_json=excluded.evaluation_json,
                       delta_json=excluded.delta_json""",
                (evaluation["track_id"], idempotency_key, source_timestamp,
                 evaluation["evaluated_at_epoch"], stored_evaluation, stored_delta),
            )
            now = int(evaluation["evaluated_at_epoch"])
            track_row = connection.execute(
                "SELECT artifact_json FROM tracked_theses WHERE track_id=? AND is_active=1",
                (evaluation["track_id"],),
            ).fetchone()
            if track_row is None:
                connection.rollback()
                raise TrackingError("tracked thesis is archived or unavailable")
            track = _decode(str(track_row["artifact_json"]))
            track["updated_at"] = _utc_iso(now)
            track["status"] = evaluation["overall_status"]
            connection.execute(
                "UPDATE tracked_theses SET status=?,updated_at_epoch=?,artifact_json=? WHERE track_id=?",
                (evaluation["overall_status"], now, canonical_json(track), evaluation["track_id"]),
            )
            connection.commit()
            return {**evaluation, "evaluation_id": evaluation_id, "delta": delta}, history_created

    def changes(self, *, since_epoch: int, limit: int = 50) -> list[dict[str, Any]]:
        self._require_supported_schema()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT e.*,t.artifact_json FROM thesis_current_evaluations e
                   JOIN tracked_theses t ON t.track_id=e.track_id
                   WHERE e.material_change=1 AND e.evaluated_at_epoch>=?
                   ORDER BY e.evaluated_at_epoch DESC,e.rowid DESC LIMIT ?""",
                (int(since_epoch), max(1, min(int(limit), 100))),
            ).fetchall()
        return [{"track": _decode(str(row["artifact_json"])),
                 "evaluation": self._evaluation(row)} for row in rows]

    def readiness(self) -> dict[str, Any]:
        try:
            with closing(self._connect()) as connection:
                version = connection.execute("SELECT version FROM thesis_tracking_schema WHERE singleton=1").fetchone()
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            schema_version = int(version[0]) if version else None
            if schema_version != TRACKING_SCHEMA_MIGRATION_VERSION:
                return {"status": "BLOCKED", "schema_version": schema_version,
                        "reason": "TRACKING_SCHEMA_VERSION_UNSUPPORTED"}
            return {"status": "READY", "schema_version": schema_version, "reason": None}
        except (OSError, sqlite3.Error):
            return {"status": "BLOCKED", "schema_version": None,
                    "reason": "TRACKING_DATABASE_UNAVAILABLE"}


class ThesisTrackingServiceV1:
    def __init__(self, repository: ThesisTrackingRepositoryV1, thesis_service: Any,
                 evaluator: CurrentFeatureEvaluatorV1) -> None:
        self.repository = repository
        self.thesis_service = thesis_service
        self.evaluator = evaluator

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"version", "result_hash", "thesis_spec", "language", "original_text"}
        if not isinstance(payload, Mapping) or set(payload) - allowed:
            raise TrackingError("track request contains unsupported fields")
        if payload.get("version") != TRACK_CREATE_REQUEST_VERSION:
            raise TrackingError(f"version must be {TRACK_CREATE_REQUEST_VERSION}")
        spec_payload = payload.get("thesis_spec")
        if not isinstance(spec_payload, Mapping):
            raise TrackingError("thesis_spec is required")
        try:
            result, _ = self.thesis_service.verified_result(spec_payload, str(payload.get("result_hash", "")))
        except ThesisValidationError as error:
            raise TrackingError(str(error)) from error
        baseline = historical_baseline(result)
        language = str(payload.get("language") or "en")
        if language not in {"en", "zh"}:
            raise TrackingError("language must be en or zh")
        original_text = payload.get("original_text")
        if original_text is not None and (not isinstance(original_text, str) or len(original_text) > 2000):
            raise TrackingError("original_text must be a string of at most 2000 characters")
        artifact = {
            "schema_version": TRACK_SCHEMA_VERSION, "track_id": str(uuid4()),
            "original_text": original_text, "language": language,
            "thesis_spec": result["thesis_spec"],
            "compiled_definition": result["compiled_definition"],
            "definition_hash": result["definition_hash"],
            "historical_result_hash": result["result_hash"],
            "historical_dataset_identity": baseline["historical_dataset_identity"],
            "historical_engine_version": result["engine_version"],
            "historical_tested_range": result["tested_range"],
            "historical_baseline": baseline,
            "current_evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION,
            "is_active": True, "status": "WATCHING",
        }
        track, created = self.repository.create(artifact)
        evaluated = self.evaluate(track["track_id"])
        return {**evaluated, "created": created}

    def evaluate(self, track_id: str, *, now: int | None = None) -> dict[str, Any]:
        track = self.repository.get(track_id)
        if track is None:
            raise TrackingError("tracked thesis not found")
        evaluation = self.evaluator.evaluate(track, now=now)
        stored, created = self.repository.record_evaluation(evaluation)
        return {"track": self.repository.get(track_id), "latest_evaluation": stored,
                "evaluation_created": created, "outcome": "EVALUATED" if created else "NO_CHANGE"}

    def evaluate_active(self, *, now: int | None = None) -> dict[str, int]:
        summary = {"evaluated": 0, "no_change": 0, "failed": 0}
        for bundle in self.repository.list(limit=100):
            try:
                result = self.evaluate(bundle["track"]["track_id"], now=now)
                summary["evaluated" if result["evaluation_created"] else "no_change"] += 1
            except (TrackingError, sqlite3.Error, OSError):
                summary["failed"] += 1
        return summary


class ThesisTrackingSchedulerV1:
    """Optional bounded scheduler; candle identity makes every tick idempotent."""

    def __init__(self, service: ThesisTrackingServiceV1, *, cadence_seconds: int = 900,
                 clock: Any = time.time) -> None:
        self.service = service
        self.cadence_seconds = max(60, int(cadence_seconds))
        self.clock = clock
        self.enabled = False
        self.last_tick: str | None = None
        self.last_result: dict[str, int] | None = None
        self.last_error: str | None = None

    def tick(self) -> dict[str, int]:
        self.last_tick = _utc_iso(int(self.clock()))
        try:
            self.last_result = self.service.evaluate_active(now=int(self.clock()))
            self.last_error = None
        except Exception as error:  # scheduler boundary must stay alive
            self.last_error = type(error).__name__
            self.last_result = {"evaluated": 0, "no_change": 0, "failed": 1}
        return self.last_result

    def run_forever(self) -> None:
        self.enabled = True
        while self.enabled:
            self.tick()
            time.sleep(self.cadence_seconds)

    def state(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "cadence_seconds": self.cadence_seconds,
                "last_tick": self.last_tick, "last_result": self.last_result,
                "last_error": self.last_error}
