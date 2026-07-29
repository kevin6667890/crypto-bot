"""Append-preserving registry for historical Phase 6 research evidence.

This module only reads already-produced reports and ledgers.  It deliberately
does not import any research runner, strategy service, or order API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence


GLOBAL_RESEARCH_REGISTRY_VERSION = "global-research-registry-v1"
GLOBAL_RESEARCH_REGISTRY_SCHEMA_VERSION = "global-research-registry-schema-v1"
CANONICAL_RESEARCH_IDENTITY_VERSION = "global-research-canonical-identity-v1"
DSR_ACCOUNTING_POLICY_VERSION = "global-research-dsr-accounting-v1"
DEFAULT_REGISTRY_PATH = Path(".research/global_research_registry.db")
SUPPORTED_PHASES = tuple(f"6{letter}" for letter in "ABCDEFG")
UNKNOWN = "UNKNOWN"
PARTIAL_METADATA_ONLY = "PARTIAL_METADATA_ONLY"

VOLATILE_IDENTITY_KEYS = {
    "created_at", "updated_at", "imported_at", "timestamp", "timestamps",
    "generated_at", "completed_at", "started_at", "finished_at",
    "runtime_seconds", "local_runtime_seconds", "elapsed_seconds",
    "report_path", "ledger_path", "source_path", "mtime", "metadata_timestamp",
}
STRUCTURAL_STATUSES = {
    "STRUCTURALLY_INVALID", "STRUCTURAL_INVALID", "INVALID_STRUCTURE",
    "SEMANTIC_DUPLICATE", "BEHAVIOR_DUPLICATE",
}
BUDGET_STATUSES = {
    "BUDGET_TRUNCATED", "BUDGET_CUTOFF", "TRUNCATED_BY_BUDGET",
    "NOT_EVALUATED_BUDGET",
}
FAILURE_STATUSES = {
    "FAILED", "FAILURE", "ERROR", "ELIMINATED", "REJECTED",
    "INSUFFICIENT_SAMPLE", "DUPLICATE", *STRUCTURAL_STATUSES, *BUDGET_STATUSES,
}
DIAGNOSTIC_STATUSES = {"DIAGNOSTIC", "DIAGNOSTIC_ONLY", "RETAIN_DIAGNOSTIC_ONLY"}
EVALUATED_STATUSES = {
    "EVALUATED", "COMPLETED", "SUCCESS", "SUCCEEDED", "CLASSIFIED",
    "ELIMINATED", "INSUFFICIENT_SAMPLE", "RETAINED", "REJECTED_AFTER_EVALUATION",
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS registry_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_runs(
    id INTEGER PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    phase TEXT NOT NULL,
    source_run_identity TEXT NOT NULL,
    dataset_identity TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    grammar_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    chronological_segment TEXT NOT NULL,
    run_status TEXT NOT NULL,
    raw_trial_count INTEGER,
    statistical_test_count INTEGER,
    effective_cluster_count INTEGER,
    selection_count INTEGER,
    locked_count INTEGER,
    final_classification TEXT NOT NULL,
    report_path TEXT,
    report_hash TEXT,
    ledger_path TEXT,
    ledger_hash TEXT,
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    import_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    missing_fields TEXT NOT NULL,
    import_state TEXT NOT NULL,
    locked_data_accessed INTEGER,
    unrecoverable_trials TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_runs_phase ON research_runs(phase);
CREATE TABLE IF NOT EXISTS research_trials(
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES research_runs(id),
    source_trial_identity TEXT NOT NULL,
    strategy_identity TEXT NOT NULL,
    factor_identity TEXT NOT NULL,
    program_identity TEXT NOT NULL,
    canonical_entity_key TEXT NOT NULL,
    canonical_trial_key TEXT NOT NULL,
    parent_identity TEXT NOT NULL,
    dataset_identity TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    grammar_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    chronological_segment TEXT NOT NULL,
    trial_status TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    raw_attempt INTEGER NOT NULL CHECK(raw_attempt IN (0,1)),
    statistically_evaluated INTEGER NOT NULL CHECK(statistically_evaluated IN (0,1)),
    effective_cluster_identity TEXT NOT NULL,
    entered_selection INTEGER NOT NULL CHECK(entered_selection IN (0,1)),
    entered_locked INTEGER NOT NULL CHECK(entered_locked IN (0,1)),
    final_classification TEXT NOT NULL,
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    import_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    missing_fields TEXT NOT NULL,
    raw_metadata TEXT NOT NULL,
    UNIQUE(run_id, source_trial_identity)
);
CREATE INDEX IF NOT EXISTS idx_research_trials_canonical
    ON research_trials(canonical_trial_key);
CREATE INDEX IF NOT EXISTS idx_research_trials_status
    ON research_trials(trial_status);
CREATE TABLE IF NOT EXISTS import_receipts(
    source_hash TEXT NOT NULL,
    phase TEXT NOT NULL,
    run_key TEXT NOT NULL,
    source_path TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(source_hash, phase, run_key)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _without_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_volatile(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in VOLATILE_IDENTITY_KEYS
            and str(key).lower() not in {"createdat", "updatedat", "importedat"}
            and not str(key).lower().endswith("_metadata_timestamp")
        }
    if isinstance(value, (list, tuple)):
        return [_without_volatile(item) for item in value]
    return value


def _decoded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped[:1] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def canonical_entity_key(
    *,
    strategy_identity: Any = None,
    factor_identity: Any = None,
    program_identity: Any = None,
    expression: Any = None,
    parameters: Any = None,
) -> str:
    """Return a cross-phase entity key without changing source identities."""
    kind = (
        "factor" if factor_identity not in (None, "", UNKNOWN) or expression is not None
        else "program" if program_identity not in (None, "", UNKNOWN)
        else "strategy"
    )
    definition = _decoded(expression)
    if definition is None:
        definition = (
            factor_identity if factor_identity not in (None, "", UNKNOWN)
            else program_identity if program_identity not in (None, "", UNKNOWN)
            else strategy_identity
        )
    payload = {
        "version": CANONICAL_RESEARCH_IDENTITY_VERSION,
        "kind": kind,
        "definition": _without_volatile(_decoded(definition)),
        "parameters": _without_volatile(_decoded(parameters or {})),
    }
    return f"entity:{_hash_payload(payload)}"


def canonical_trial_key(
    *,
    canonical_entity: str,
    dataset_identity: Any = UNKNOWN,
    snapshot_hash: Any = UNKNOWN,
    parameters: Any = None,
    instrument: Any = UNKNOWN,
    timeframe: Any = UNKNOWN,
    horizon: Any = UNKNOWN,
    chronological_segment: Any = UNKNOWN,
) -> str:
    """Return an attempt identity; phase, paths, and timestamps are excluded."""
    payload = {
        "version": CANONICAL_RESEARCH_IDENTITY_VERSION,
        "entity": canonical_entity,
        "dataset": dataset_identity or UNKNOWN,
        "snapshot": snapshot_hash or UNKNOWN,
        "parameters": _without_volatile(_decoded(parameters or {})),
        "instrument": instrument or UNKNOWN,
        "timeframe": timeframe or UNKNOWN,
        "horizon": horizon or UNKNOWN,
        "segment": chronological_segment or UNKNOWN,
    }
    return f"trial:{_hash_payload(payload)}"


def _first(data: Mapping[str, Any], *keys: str, default: Any = UNKNOWN) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _integer(data: Mapping[str, Any], *keys: str) -> int | None:
    value = _first(data, *keys, default=None)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _boolean(data: Mapping[str, Any], *keys: str) -> bool | None:
    value = _first(data, *keys, default=None)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.upper() in {"TRUE", "FALSE", "YES", "NO"}:
        return value.upper() in {"TRUE", "YES"}
    return None


def _normalized_status(item: Mapping[str, Any]) -> str:
    status = str(_first(
        item, "trial_status", "status", "structural_status", "classification",
        default=UNKNOWN)).upper()
    if status in {"INVALID", "STRUCTURAL_REJECTION"}:
        return "STRUCTURALLY_INVALID"
    if "BUDGET" in status and ("TRUNCAT" in status or "CUTOFF" in status):
        return "BUDGET_TRUNCATED"
    if status == "PARTIAL":
        return PARTIAL_METADATA_ONLY
    return status


def _statistically_evaluated(item: Mapping[str, Any], status: str) -> bool:
    explicit = _boolean(
        item, "statistically_evaluated", "statistical_test", "evaluated")
    if explicit is not None:
        return explicit
    if status in STRUCTURAL_STATUSES or status in BUDGET_STATUSES:
        return False
    if any(key in item for key in (
            "raw_p_value", "p_value", "pvalue", "fdr_q_value",
            "test_statistic", "statistic")):
        return True
    return status in EVALUATED_STATUSES


def _missing(data: Mapping[str, Any], fields: Mapping[str, Any]) -> list[str]:
    return sorted(key for key, value in fields.items()
                  if value in (None, "", UNKNOWN) and key not in data)


def _phase(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(?:phase[\s_-]*)?6[\s_-]*([a-g])", value, re.I)
    return f"6{match.group(1).upper()}" if match else None


def detect_phase(data: Mapping[str, Any], path: Path) -> str | None:
    for key in ("phase", "research_phase", "result_version", "report_version",
                "audit_version"):
        found = _phase(str(data.get(key, "")))
        if found:
            return found
    versions = data.get("versions")
    if isinstance(versions, Mapping):
        for value in versions.values():
            found = _phase(str(value))
            if found:
                return found
    return _phase(path.name)


@dataclass(frozen=True)
class ImportResult:
    phase: str
    run_key: str
    run_id: int
    source_path: str
    trials_imported: int
    import_state: str
    idempotent: bool = False


class GlobalResearchRegistry:
    def __init__(self, path: Path | str = DEFAULT_REGISTRY_PATH) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "INSERT OR REPLACE INTO registry_meta VALUES(?,?)",
                ("registry_version", GLOBAL_RESEARCH_REGISTRY_VERSION))
            connection.execute(
                "INSERT OR REPLACE INTO registry_meta VALUES(?,?)",
                ("schema_version", GLOBAL_RESEARCH_REGISTRY_SCHEMA_VERSION))

    def _upsert_run(
        self, phase: str, metadata: Mapping[str, Any], source_path: Path,
        source_hash: str, *, trial_rows: Sequence[Mapping[str, Any]],
        ledger_path: Path | None = None,
    ) -> tuple[int, str, str, bool]:
        dataset = _first(
            metadata, "dataset_identity", "dataset_fingerprint",
            "origin_dataset_fingerprint")
        snapshot = _first(metadata, "snapshot_hash", "dataset_sha256")
        source_run = _first(
            metadata, "run_id", "audit_run_id", "research_run_id",
            "phase6f_run_id", default=UNKNOWN)
        identity_fallback = _hash_payload({
            "phase": phase,
            "dataset": dataset,
            "snapshot": snapshot,
            "result_version": _first(
                metadata, "result_version", "report_version",
                "ledger_version", default=UNKNOWN),
            "content": _without_volatile(metadata),
        })
        source_run = identity_fallback if source_run == UNKNOWN else str(source_run)
        run_key = f"run:{_hash_payload({'phase': phase, 'source_run': source_run, 'dataset': dataset, 'snapshot': snapshot})}"
        raw_count = _integer(
            metadata, "raw_trial_count", "raw_trials", "raw_attempt_count",
            "raw_program_count", "proposal_count")
        statistical_count = _integer(
            metadata, "statistical_test_count", "statistical_tests",
            "statistically_applicable_trial_count",
            "statistically_evaluated_count")
        effective_count = _integer(
            metadata, "effective_cluster_count", "effective_trial_count",
            "phase6f_effective_clusters", "correlation_clusters")
        selection = _integer(
            metadata, "selection_count", "selection_trial_count",
            "entered_selection_count")
        locked = _integer(
            metadata, "locked_count", "locked_trial_count",
            "entered_locked_count")
        fields = {
            "dataset_identity": dataset,
            "snapshot_hash": snapshot,
            "source_scope": _first(metadata, "source_scope"),
            "grammar_version": _first(metadata, "grammar_version"),
            "schema_version": _first(
                metadata, "schema_version", "database_schema_version"),
            "evaluation_version": _first(metadata, "evaluation_version"),
            "instrument": _first(metadata, "instrument"),
            "timeframe": _first(metadata, "timeframe"),
            "horizon": _first(metadata, "horizon"),
            "chronological_segment": _first(
                metadata, "chronological_segment", "segment"),
        }
        missing = _missing(metadata, fields)
        explicit_complete = _boolean(
            metadata, "complete_trial_ledger", "trial_ledger_complete")
        incomplete_count = raw_count is not None and raw_count > len(trial_rows)
        partial = not trial_rows or incomplete_count or explicit_complete is False
        state = PARTIAL_METADATA_ONLY if partial else "COMPLETE_TRIAL_LEDGER"
        if partial and "trial_records" not in missing:
            missing.append("trial_records")
        unrecoverable = (
            f"{max(0, raw_count - len(trial_rows))} declared attempts lack trial rows"
            if raw_count is not None and raw_count > len(trial_rows)
            else "Trial-level history is not recoverable from this source"
            if not trial_rows else UNKNOWN)
        report_path = str(source_path.resolve()) if source_path.suffix.lower() != ".db" else None
        actual_ledger = ledger_path or (
            source_path if source_path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else None)
        locked_access = _boolean(
            metadata, "locked_data_accessed", "locked_accessed",
            "holdout_or_oot_accessed", "holdout_or_oot_accessed_by_discovery")
        values = (
            run_key, phase, source_run, str(dataset), str(snapshot),
            _canonical_json(fields["source_scope"]),
            str(fields["grammar_version"]), str(fields["schema_version"]),
            str(fields["evaluation_version"]), str(fields["instrument"]),
            str(fields["timeframe"]), str(fields["horizon"]),
            str(fields["chronological_segment"]),
            str(_first(metadata, "run_status", "status", default=state)),
            raw_count, statistical_count, effective_count, selection, locked,
            str(_first(metadata, "final_classification", "classification")),
            report_path, source_hash if report_path else None,
            str(actual_ledger.resolve()) if actual_ledger else None,
            file_sha256(actual_ledger) if actual_ledger and actual_ledger.exists() else None,
            str(_first(metadata, "created_at", default=UNKNOWN)), _now(),
            str(source_path.resolve()), "HIGH" if not partial else "PARTIAL",
            _canonical_json(sorted(set(missing))), state,
            None if locked_access is None else int(locked_access),
            unrecoverable, _canonical_json(metadata),
        )
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id,import_state FROM research_runs WHERE run_key=?",
                (run_key,)).fetchone()
            if existing:
                connection.execute(
                    """UPDATE research_runs SET
                       raw_trial_count=MAX(COALESCE(raw_trial_count,0),COALESCE(?,0)),
                       statistical_test_count=MAX(
                         COALESCE(statistical_test_count,0),COALESCE(?,0)),
                       effective_cluster_count=MAX(
                         COALESCE(effective_cluster_count,0),COALESCE(?,0)),
                       selection_count=MAX(COALESCE(selection_count,0),COALESCE(?,0)),
                       locked_count=MAX(COALESCE(locked_count,0),COALESCE(?,0)),
                       imported_at=? WHERE id=?""",
                    (raw_count, statistical_count, effective_count, selection,
                     locked, _now(), int(existing["id"])))
                connection.execute(
                    "INSERT OR IGNORE INTO import_receipts VALUES(?,?,?,?,?)",
                    (source_hash, phase, run_key, str(source_path.resolve()), _now()))
                return int(existing["id"]), run_key, str(existing["import_state"]), True
            cursor = connection.execute(
                """INSERT INTO research_runs(
                   run_key,phase,source_run_identity,dataset_identity,snapshot_hash,
                   source_scope,grammar_version,schema_version,evaluation_version,
                   instrument,timeframe,horizon,chronological_segment,run_status,
                   raw_trial_count,statistical_test_count,effective_cluster_count,
                   selection_count,locked_count,final_classification,report_path,
                   report_hash,ledger_path,ledger_hash,created_at,imported_at,
                   import_source,confidence,missing_fields,import_state,
                   locked_data_accessed,unrecoverable_trials,metadata_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values)
            connection.execute(
                "INSERT OR IGNORE INTO import_receipts VALUES(?,?,?,?,?)",
                (source_hash, phase, run_key, str(source_path.resolve()), _now()))
            return int(cursor.lastrowid), run_key, state, False

    def _insert_trials(
        self, run_id: int, run: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]], source_path: Path,
    ) -> int:
        count = 0
        for index, item in enumerate(rows, 1):
            status = _normalized_status(item)
            source_id = _first(
                item, "trial_id", "source_trial_identity", "phase6f_trial_id",
                "evaluation_id", "development_identity", default=f"ROW_{index}")
            strategy = _first(item, "strategy_identity", "strategy_id")
            factor = _first(
                item, "factor_identity", "factor_id", "phase6f_factor_identity")
            program = _first(
                item, "program_identity", "semantic_identity", "entry_identity")
            expression = _first(
                item, "canonical_expression", "expression_ast", "canonical_ast",
                "expression", default=None)
            parameters = _first(
                item, "parameter_values", "parameters", "normalized_parameters",
                default={})
            if (strategy, factor, program, expression) == (
                    UNKNOWN, UNKNOWN, UNKNOWN, None):
                strategy = source_id
            entity = canonical_entity_key(
                strategy_identity=strategy, factor_identity=factor,
                program_identity=program, expression=expression,
                parameters=parameters)
            dataset = _first(
                item, "dataset_identity", "dataset_fingerprint",
                default=_first(run, "dataset_identity", "dataset_fingerprint",
                               "origin_dataset_fingerprint"))
            snapshot = _first(
                item, "snapshot_hash", "dataset_sha256",
                default=_first(run, "snapshot_hash", "dataset_sha256"))
            instrument = _first(
                item, "instrument", default=_first(run, "instrument"))
            timeframe = _first(
                item, "timeframe", default=_first(run, "timeframe"))
            horizon = _first(item, "horizon", default=_first(run, "horizon"))
            segment = _first(
                item, "chronological_segment", "segment",
                default=_first(run, "chronological_segment", "segment"))
            trial_key = canonical_trial_key(
                canonical_entity=entity, dataset_identity=dataset,
                snapshot_hash=snapshot, parameters=parameters,
                instrument=instrument, timeframe=timeframe, horizon=horizon,
                chronological_segment=segment)
            classification = str(_first(
                item, "final_classification", "classification"))
            rejection = _first(
                item, "rejection_reason", "failure_reason", "error", "reason",
                "reasons")
            if isinstance(rejection, (list, dict)):
                rejection = _canonical_json(rejection)
            evaluated = _statistically_evaluated(item, status)
            entered_selection = bool(_boolean(
                item, "entered_selection", "selection") or
                str(_first(item, "segment", default="")).upper() == "SELECTION")
            entered_locked = bool(_boolean(
                item, "entered_locked", "locked") or
                "LOCKED" in str(_first(item, "segment", default="")).upper())
            fields = {
                "strategy_identity": strategy, "factor_identity": factor,
                "program_identity": program, "dataset_identity": dataset,
                "snapshot_hash": snapshot, "instrument": instrument,
                "timeframe": timeframe, "horizon": horizon,
                "chronological_segment": segment,
            }
            missing = _missing(item, fields)
            values = (
                run_id, str(source_id), str(strategy), str(factor), str(program),
                entity, trial_key,
                _canonical_json(_first(
                    item, "parent_identity", "parent_identities",
                    "parent_expressions")),
                str(dataset), str(snapshot),
                _canonical_json(_first(item, "source_scope",
                                       default=_first(run, "source_scope"))),
                str(_first(item, "grammar_version",
                           default=_first(run, "grammar_version"))),
                str(_first(item, "schema_version",
                           default=_first(run, "schema_version"))),
                str(_first(item, "evaluation_version",
                           default=_first(run, "evaluation_version"))),
                str(instrument), str(timeframe), str(horizon), str(segment),
                status, str(rejection), 1, int(evaluated),
                str(_first(item, "effective_cluster_identity",
                           "correlation_cluster")),
                int(entered_selection), int(entered_locked), classification,
                str(_first(item, "created_at")), _now(),
                str(source_path.resolve()), "HIGH",
                _canonical_json(missing), _canonical_json(item),
            )
            with self.connect() as connection:
                before = connection.total_changes
                connection.execute(
                    """INSERT OR IGNORE INTO research_trials(
                       run_id,source_trial_identity,strategy_identity,
                       factor_identity,program_identity,canonical_entity_key,
                       canonical_trial_key,parent_identity,dataset_identity,
                       snapshot_hash,source_scope,grammar_version,schema_version,
                       evaluation_version,instrument,timeframe,horizon,
                       chronological_segment,trial_status,rejection_reason,
                       raw_attempt,statistically_evaluated,
                       effective_cluster_identity,entered_selection,
                       entered_locked,final_classification,created_at,imported_at,
                       import_source,confidence,missing_fields,raw_metadata)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values)
                count += connection.total_changes - before
        return count

    def import_mapping(
        self, phase: str, metadata: Mapping[str, Any],
        *, source_path: Path | str, trials: Sequence[Mapping[str, Any]] = (),
        source_hash: str | None = None, ledger_path: Path | None = None,
    ) -> ImportResult:
        if phase not in SUPPORTED_PHASES:
            raise ValueError(f"Unsupported research phase: {phase}")
        self.migrate()
        path = Path(source_path)
        digest = source_hash or (
            file_sha256(path) if path.exists()
            else _hash_payload({"metadata": metadata, "trials": trials}))
        run_id, run_key, state, existing = self._upsert_run(
            phase, metadata, path, digest, trial_rows=trials,
            ledger_path=ledger_path)
        imported = self._insert_trials(run_id, metadata, trials, path)
        if trials and _boolean(
                metadata, "complete_trial_ledger",
                "trial_ledger_complete") is not False:
            with self.connect() as connection:
                row = connection.execute(
                    """SELECT raw_trial_count,missing_fields FROM research_runs
                       WHERE id=?""", (run_id,)).fetchone()
                actual = int(connection.execute(
                    "SELECT COUNT(*) FROM research_trials WHERE run_id=?",
                    (run_id,)).fetchone()[0])
                declared = row["raw_trial_count"]
                if declared is None or actual >= int(declared):
                    missing = [
                        item for item in json.loads(row["missing_fields"])
                        if item != "trial_records"]
                    connection.execute(
                        """UPDATE research_runs SET import_state=?,
                           confidence='HIGH',missing_fields=?,
                           unrecoverable_trials=? WHERE id=?""",
                        ("COMPLETE_TRIAL_LEDGER", _canonical_json(missing),
                         UNKNOWN, run_id))
                    state = "COMPLETE_TRIAL_LEDGER"
        return ImportResult(
            phase, run_key, run_id, str(path), imported, state,
            existing and imported == 0)

    def import_json(self, path: Path | str, phase: str | None = None) -> ImportResult:
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError(f"Expected a JSON object: {source}")
        selected_phase = phase or detect_phase(data, source)
        if selected_phase is None:
            raise ValueError(f"Cannot determine Phase 6A-6G for {source}")
        metadata = dict(data)
        if selected_phase == "6G" and isinstance(data.get("frozen_experiment"), Mapping):
            for key, value in data["frozen_experiment"].items():
                metadata.setdefault(key, value)
        if isinstance(data.get("multiple_testing"), Mapping):
            for key in ("effective_trial_count", "correlation_clusters"):
                if key in data["multiple_testing"]:
                    metadata.setdefault(key, data["multiple_testing"][key])
        rows: list[Mapping[str, Any]] = []
        for key in ("trials", "factor_trials", "programs", "evaluations"):
            value = data.get(key)
            if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
                rows.extend(value)
        # Libraries and retained/locked summaries are not substituted for the
        # full trial ledger.
        return self.import_mapping(
            selected_phase, metadata, source_path=source, trials=rows)

    def import_sqlite(self, path: Path | str, phase: str | None = None) -> list[ImportResult]:
        source = Path(path)
        uri = f"{source.resolve().as_uri()}?mode=ro"
        results: list[ImportResult] = []
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            if {"factor_runs", "factor_trials"} <= tables:
                for row in connection.execute("SELECT * FROM factor_runs"):
                    metadata = dict(row)
                    policy = _decoded(metadata.get("policy_json"))
                    if isinstance(policy, Mapping):
                        metadata.update({str(k): v for k, v in policy.items()})
                    saved_report = _decoded(metadata.get("report_json"))
                    if isinstance(saved_report, Mapping):
                        for key in (
                                "raw_trial_count", "effective_trial_count",
                                "correlation_clusters", "selection_trial_count",
                                "locked_trial_count"):
                            if key in saved_report:
                                metadata.setdefault(key, saved_report[key])
                        multiple = saved_report.get("multiple_testing")
                        if isinstance(multiple, Mapping):
                            for key in (
                                    "effective_trial_count",
                                    "correlation_clusters"):
                                if key in multiple:
                                    metadata.setdefault(key, multiple[key])
                    trials = [
                        dict(item) for item in connection.execute(
                            "SELECT * FROM factor_trials WHERE run_id=? ORDER BY sequence",
                            (row["run_id"],))
                    ]
                    metadata["raw_trial_count"] = len(trials)
                    if "factor_evaluations" in tables:
                        evaluated = connection.execute(
                            "SELECT COUNT(*) FROM factor_evaluations WHERE run_id=?",
                            (row["run_id"],)).fetchone()[0]
                        metadata["statistical_test_count"] = int(evaluated)
                        evaluated_ids = {
                            item[0] for item in connection.execute(
                                "SELECT DISTINCT trial_id FROM factor_evaluations WHERE run_id=?",
                                (row["run_id"],))
                        }
                        trials = [
                            {**item, "statistically_evaluated":
                             item.get("trial_id") in evaluated_ids}
                            for item in trials
                        ]
                    results.append(self.import_mapping(
                        phase or "6F", metadata, source_path=source,
                        trials=trials, ledger_path=source))
            elif "statistical_audit_runs" in tables:
                for row in connection.execute("SELECT * FROM statistical_audit_runs"):
                    metadata = dict(row)
                    saved_report = _decoded(metadata.get("report_json"))
                    if isinstance(saved_report, Mapping):
                        frozen = saved_report.get("frozen_experiment")
                        if isinstance(frozen, Mapping):
                            metadata.update({
                                str(key): value for key, value in frozen.items()
                                if key not in metadata or metadata[key] in (None, "")
                            })
                    run_id = _first(metadata, "audit_run_id", "run_id")
                    trial_rows: list[dict[str, Any]] = []
                    if "phase6f_phase6g_identity_map" in tables:
                        columns = {
                            item[1] for item in connection.execute(
                                "PRAGMA table_info(phase6f_phase6g_identity_map)")
                        }
                        key = "audit_run_id" if "audit_run_id" in columns else "run_id"
                        trial_rows = [
                            dict(item) for item in connection.execute(
                                f"SELECT * FROM phase6f_phase6g_identity_map WHERE {key}=?",
                                (run_id,))
                        ]
                        trial_rows = [{
                            **item,
                            "trial_id": item.get("phase6f_trial_id"),
                            "factor_identity": item.get("phase6f_factor_identity"),
                            "statistically_evaluated":
                                bool(item.get("statistically_applicable")),
                            "status": (
                                "EVALUATED"
                                if item.get("statistically_applicable")
                                else "STRUCTURALLY_INVALID"),
                            "rejection_reason": item.get("exclusion_reason"),
                        } for item in trial_rows]
                    metadata["raw_trial_count"] = len(trial_rows)
                    metadata["statistical_test_count"] = (
                        int(connection.execute(
                            """SELECT COUNT(*) FROM statistical_audit_evaluations
                               WHERE audit_run_id=?""", (run_id,)).fetchone()[0])
                        if "statistical_audit_evaluations" in tables
                        else sum(bool(item.get("statistically_applicable"))
                                 for item in trial_rows))
                    results.append(self.import_mapping(
                        phase or "6G", metadata, source_path=source,
                        trials=trial_rows, ledger_path=source))
            else:
                raise ValueError(f"No supported Phase 6 ledger tables in {source}")
        return results

    def import_markdown(self, path: Path | str, phase: str | None = None) -> ImportResult:
        source = Path(path)
        text = source.read_text(encoding="utf-8")
        selected_phase = phase or _phase(source.name) or _phase(text[:300])
        if selected_phase is None:
            raise ValueError(f"Cannot determine Phase 6A-6G for {source}")
        metadata: dict[str, Any] = {
            "status": PARTIAL_METADATA_ONLY,
            "source_scope": "NARRATIVE_AUDIT_DOCUMENT",
            "complete_trial_ledger": False,
            "document_title": text.splitlines()[0].lstrip("# ").strip()
            if text.splitlines() else source.name,
        }
        explicit = re.search(
            r"\ball\s+([\d,]+)\s+trial\s+identities\b", text, re.I)
        if explicit:
            # Phase 6G documents how many immutable Phase 6F identities it
            # maps.  That is known provenance, not a new raw-search count.
            metadata["referenced_phase6f_trial_count"] = int(
                explicit.group(1).replace(",", ""))
        locked = re.search(r"\blocked\b", text, re.I)
        metadata["locked_data_accessed"] = bool(locked) if selected_phase in {"6F", "6G"} else False
        return self.import_mapping(
            selected_phase, metadata, source_path=source, trials=())

    def import_path(self, path: Path | str, phase: str | None = None) -> list[ImportResult]:
        source = Path(path)
        suffix = source.suffix.lower()
        if suffix == ".json":
            return [self.import_json(source, phase)]
        if suffix in {".db", ".sqlite", ".sqlite3"}:
            return self.import_sqlite(source, phase)
        if suffix in {".md", ".markdown"}:
            return [self.import_markdown(source, phase)]
        raise ValueError(f"Unsupported research artifact: {source}")

    def runs(self) -> list[dict[str, Any]]:
        self.migrate()
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM research_runs ORDER BY phase,created_at,run_key")]

    def trials(self) -> list[dict[str, Any]]:
        self.migrate()
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM research_trials ORDER BY run_id,id")]

    def accounting(
        self, *, include_phases: Iterable[str] = SUPPORTED_PHASES,
        exclude_phases: Iterable[str] = (),
        exclusion_reasons: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.migrate()
        requested = list(dict.fromkeys(include_phases))
        excluded = set(exclude_phases)
        included = [phase for phase in requested if phase not in excluded]
        if any(phase not in SUPPORTED_PHASES for phase in requested + list(excluded)):
            raise ValueError("Accounting phases must be in Phase 6A-6G")
        reasons = dict(exclusion_reasons or {})
        phase_rows = []
        total_raw = total_tests = total_effective = 0
        partial = []
        incomplete_phases = []
        with self.connect() as connection:
            for phase in included:
                runs = [dict(row) for row in connection.execute(
                    "SELECT * FROM research_runs WHERE phase=?", (phase,))]
                raw = tests = effective = 0
                for run in runs:
                    trial = connection.execute(
                        """SELECT COUNT(*) raw,
                           SUM(statistically_evaluated) tests,
                           COUNT(DISTINCT CASE
                             WHEN statistically_evaluated=1 THEN
                               CASE WHEN effective_cluster_identity='UNKNOWN'
                                 THEN canonical_trial_key
                                 ELSE effective_cluster_identity END END) clusters
                           FROM research_trials WHERE run_id=?""",
                        (run["id"],)).fetchone()
                    raw += max(int(trial["raw"] or 0), int(run["raw_trial_count"] or 0))
                    tests += max(int(trial["tests"] or 0),
                                 int(run["statistical_test_count"] or 0))
                    effective += (
                        int(run["effective_cluster_count"])
                        if run["effective_cluster_count"] is not None
                        else int(trial["clusters"] or 0))
                    if run["import_state"] == PARTIAL_METADATA_ONLY:
                        partial.append({
                            "phase": phase, "run_key": run["run_key"],
                            "risk": run["unrecoverable_trials"],
                            "missing_fields": json.loads(run["missing_fields"]),
                        })
                phase_rows.append({
                    "phase": phase, "run_count": len(runs),
                    "raw_attempts": raw, "statistical_tests": tests,
                    "effective_clusters": effective,
                    "counts_complete": bool(runs) and all(
                        run["import_state"] != PARTIAL_METADATA_ONLY
                        for run in runs),
                })
                if not runs or any(
                        run["import_state"] == PARTIAL_METADATA_ONLY
                        for run in runs):
                    incomplete_phases.append(phase)
                total_raw += raw
                total_tests += tests
                total_effective += effective
        return {
            "registry_version": GLOBAL_RESEARCH_REGISTRY_VERSION,
            "accounting_policy_version": DSR_ACCOUNTING_POLICY_VERSION,
            "views": {
                "RAW_ATTEMPT_COUNT": total_raw,
                "STATISTICALLY_EVALUATED_COUNT": total_tests,
                "EFFECTIVE_CORRELATION_CLUSTER_COUNT": total_effective,
            },
            "phase_accounting": phase_rows,
            "included_phases": included,
            "excluded_phases": [
                {"phase": phase, "reason": reasons.get(
                    phase, "EXCLUDED_BY_CALLER")}
                for phase in sorted(excluded)
            ],
            "partial_metadata_risks": partial,
            "incomplete_count_phases": incomplete_phases,
            "counts_are_lower_bounds": bool(incomplete_phases),
            "policy": {
                "raw_attempts": (
                    "Every genuine search attempt is counted, including failed, "
                    "eliminated, duplicate, insufficient-sample, and "
                    "budget-truncated attempts."),
                "statistical_tests": (
                    "Only attempts with an explicit statistical evaluation or "
                    "test output count; pure structural invalidity and budget "
                    "cutoffs before evaluation do not."),
                "effective_clusters": (
                    "Use the ledger-declared correlation cluster count when "
                    "present; otherwise distinct explicit cluster identities, "
                    "falling back to evaluated canonical trial keys."),
                "fixtures": "Engineering fixtures are never discovered or imported.",
                "partial_metadata": (
                    "Declared run totals remain visible, but absent trial rows "
                    "are never synthesized."),
            },
        }


def discover_research_artifacts(
    repo: Path | str, supplied_paths: Iterable[Path | str] = (),
) -> list[Path]:
    """Find only report/ledger artifacts; never search test fixtures or OOT paths."""
    root = Path(repo).resolve()
    candidates: set[Path] = set()
    safe_roots = [root / "docs", root / "reports", root / ".research"]
    for base in safe_roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {
                    ".json", ".md", ".db", ".sqlite", ".sqlite3"}:
                continue
            if path.name.lower().startswith("global_research_registry"):
                continue
            lowered = str(path.relative_to(root)).lower()
            if any(part in lowered for part in (
                    "holdout", "out_of_time", "out-of-time", "oot",
                    "fixture", "__pycache__")):
                continue
            if _phase(path.name) or "phase6" in lowered or "research" in lowered:
                candidates.add(path.resolve())
    for value in supplied_paths:
        path = Path(value).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        if any(token in str(path).lower() for token in (
                "holdout", "out_of_time", "out-of-time", "\\oot\\", "/oot/")):
            raise ValueError(f"Refusing holdout/OOT artifact: {path}")
        candidates.add(path)
    return sorted(candidates)


def import_phase_6a(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6A")


def import_phase_6b(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6B")


def import_phase_6c(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6C")


def import_phase_6d(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6D")


def import_phase_6e(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6E")


def import_phase_6f(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6F")


def import_phase_6g(registry: GlobalResearchRegistry, path: Path | str) -> list[ImportResult]:
    return registry.import_path(path, "6G")


PHASE_IMPORTERS = {
    "6A": import_phase_6a, "6B": import_phase_6b, "6C": import_phase_6c,
    "6D": import_phase_6d, "6E": import_phase_6e, "6F": import_phase_6f,
    "6G": import_phase_6g,
}


def result_json(value: Any) -> str:
    if isinstance(value, ImportResult):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)
