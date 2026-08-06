"""Bounded, resumable execution for frozen factor-evaluation manifests.

This module is an execution layer only.  It deliberately does not import the
factor generator, strategy services, order APIs, or any holdout/OOT loader.
Workers receive one bounded task payload and never open the SQLite ledger; the
coordinator is the sole ledger writer.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import statistics
import time
import traceback
from typing import Any, Callable, Mapping, Sequence

from .factor_autoresearch import FACTOR_EVALUATION_VERSION
from .factor_statistics import (
    benjamini_hochberg,
    correlation_clusters,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)


FACTOR_EXECUTION_ENGINE_VERSION = "factor-execution-engine-v2"
DEFAULT_WORKERS = 2
MAX_WORKERS = 4
DEFAULT_CHUNK_SIZE = 50
DEFAULT_CHECKPOINT_SECONDS = 30.0
POST_PASS_STAGES = (
    "FDR",
    "CORRELATION_AND_EFFECTIVE_TRIALS",
    "PSR_DSR_PBO",
    "CLASSIFICATION",
    "COMPLETE",
)


class TaskState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    BASE_EVALUATED = "BASE_EVALUATED"
    PERSISTED = "PERSISTED"
    POST_PASS_PENDING = "POST_PASS_PENDING"
    COMPLETE = "COMPLETE"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"


class ExecutionInterrupted(RuntimeError):
    """A controlled or signal-triggered interruption after a safe commit."""


class IdentityMismatch(ValueError):
    """The checkpoint and frozen inputs do not identify the same experiment."""


class MemoryBudgetExceeded(RuntimeError):
    """A task is too large for the configured worker memory budget."""


class WorkerCrash(RuntimeError):
    """Synthetic crash marker supported by non-scientific execution fixtures."""


def _stable_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    )


def _stable_hash(*values: object) -> str:
    return hashlib.sha256(
        "\x1f".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecutionTask:
    """One frozen trial × instrument × segment × horizon evaluation."""

    trial_id: str
    factor_identity: str
    instrument: str
    segment: str
    horizon: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    family: str = "global"
    sequence: int = 0

    @property
    def task_id(self) -> str:
        return _stable_hash(
            "factor-execution-task-v2",
            self.trial_id,
            self.factor_identity,
            self.instrument,
            self.segment,
            self.horizon,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], sequence: int) -> "ExecutionTask":
        return cls(
            trial_id=str(value["trial_id"]),
            factor_identity=str(value["factor_identity"]),
            instrument=str(value["instrument"]),
            segment=str(value["segment"]),
            horizon=str(value["horizon"]),
            payload=dict(value.get("payload", {})),
            family=str(value.get("family", "global")),
            sequence=int(value.get("sequence", sequence)),
        )


@dataclass(frozen=True)
class FrozenExecutionManifest:
    run_id: str
    manifest_hash: str
    dataset_identity: str
    evaluation_version: str
    evaluation_policy: Mapping[str, Any]
    chronological_segments: tuple[str, ...]
    tasks: tuple[ExecutionTask, ...]
    bootstrap_seed: int = 20260727
    bootstrap_state: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> "FrozenExecutionManifest":
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        tasks = tuple(
            ExecutionTask.from_dict(task, sequence)
            for sequence, task in enumerate(value["tasks"], start=1)
        )
        canonical = {
            "run_id": str(value["run_id"]),
            "dataset_identity": str(value["dataset_identity"]),
            "evaluation_version": str(
                value.get("evaluation_version", FACTOR_EVALUATION_VERSION)
            ),
            "evaluation_policy": value["evaluation_policy"],
            "chronological_segments": value["chronological_segments"],
            "bootstrap_seed": int(value.get("bootstrap_seed", 20260727)),
            "bootstrap_state": value.get("bootstrap_state", {}),
            "tasks": [
                {
                    **asdict(task),
                    "payload": dict(task.payload),
                    "task_id": task.task_id,
                }
                for task in tasks
            ],
        }
        computed = _stable_hash(
            "frozen-factor-execution-manifest-v2", _stable_json(canonical)
        )
        supplied = value.get("manifest_hash")
        if supplied is not None and str(supplied) != computed:
            raise IdentityMismatch("manifest_hash does not match frozen contents")
        manifest = cls(
            run_id=canonical["run_id"],
            manifest_hash=computed,
            dataset_identity=canonical["dataset_identity"],
            evaluation_version=canonical["evaluation_version"],
            evaluation_policy=dict(canonical["evaluation_policy"]),
            chronological_segments=tuple(canonical["chronological_segments"]),
            tasks=tasks,
            bootstrap_seed=canonical["bootstrap_seed"],
            bootstrap_state=dict(canonical["bootstrap_state"]),
        )
        manifest.validate()
        return manifest

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FrozenExecutionManifest":
        # Keep one canonical loader path so file and in-memory manifests hash alike.
        tasks = tuple(
            ExecutionTask.from_dict(task, sequence)
            for sequence, task in enumerate(value["tasks"], start=1)
        )
        base = {
            "run_id": str(value["run_id"]),
            "dataset_identity": str(value["dataset_identity"]),
            "evaluation_version": str(
                value.get("evaluation_version", FACTOR_EVALUATION_VERSION)
            ),
            "evaluation_policy": dict(value["evaluation_policy"]),
            "chronological_segments": list(value["chronological_segments"]),
            "bootstrap_seed": int(value.get("bootstrap_seed", 20260727)),
            "bootstrap_state": dict(value.get("bootstrap_state", {})),
            "tasks": [
                {
                    **asdict(task),
                    "payload": dict(task.payload),
                    "task_id": task.task_id,
                }
                for task in tasks
            ],
        }
        manifest_hash = _stable_hash(
            "frozen-factor-execution-manifest-v2", _stable_json(base)
        )
        supplied = value.get("manifest_hash")
        if supplied is not None and str(supplied) != manifest_hash:
            raise IdentityMismatch("manifest_hash does not match frozen contents")
        result = cls(
            run_id=base["run_id"],
            manifest_hash=manifest_hash,
            dataset_identity=base["dataset_identity"],
            evaluation_version=base["evaluation_version"],
            evaluation_policy=base["evaluation_policy"],
            chronological_segments=tuple(base["chronological_segments"]),
            tasks=tasks,
            bootstrap_seed=base["bootstrap_seed"],
            bootstrap_state=base["bootstrap_state"],
        )
        result.validate()
        return result

    def validate(self) -> None:
        if not self.run_id or not self.dataset_identity:
            raise ValueError("run_id and dataset_identity are required")
        if not self.evaluation_policy or not self.chronological_segments:
            raise ValueError("evaluation policy and chronological segments are frozen")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("manifest contains duplicate evaluation identities")
        allowed = set(self.chronological_segments)
        if any(task.segment not in allowed for task in self.tasks):
            raise ValueError("task segment is outside chronological_segments")


@dataclass
class EvaluationResult:
    task_id: str
    evaluation_identity: str
    result: dict[str, Any]
    native_event: dict[str, Any]
    non_overlap: dict[str, Any]
    hac: dict[str, Any]
    bootstrap: dict[str, Any]
    portfolio: dict[str, Any]
    errors: list[dict[str, Any]]
    duration_seconds: float
    worker_pid: int
    worker_rss_bytes: int
    attempt: int
    checksum: str = ""

    def seal(self) -> "EvaluationResult":
        body = asdict(self)
        body["checksum"] = ""
        self.checksum = _stable_hash("evaluation-result-v2", _stable_json(body))
        return self


def _rss_bytes() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except (ImportError, OSError):
        try:
            import resource

            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return value if os.name == "nt" else value * 1024
        except (ImportError, OSError):
            return 0


def _default_evaluator(task: ExecutionTask) -> dict[str, Any]:
    """Deterministic fixture evaluator; production callers inject a frozen evaluator."""
    if task.payload.get("crash"):
        raise WorkerCrash("requested synthetic worker crash")
    if "result" in task.payload:
        value = task.payload["result"]
        return dict(value) if isinstance(value, Mapping) else {"value": value}
    samples = [float(value) for value in task.payload.get("samples", ())]
    mean = statistics.fmean(samples) if samples else 0.0
    variance = statistics.pvariance(samples) if len(samples) > 1 else 0.0
    p_value = min(1.0, math.exp(-abs(mean) * math.sqrt(max(1, len(samples)))))
    return {
        "metrics": {
            "count": len(samples),
            "mean": mean,
            "variance": variance,
            "raw_p_value": p_value,
            "sharpe": mean / math.sqrt(variance) if variance > 0 else 0.0,
        },
        "native_event": {"count": len(samples)},
        "non_overlap": {"count": len(samples), "returns": samples},
        "hac": {"available": False},
        "bootstrap": {"seeded": True},
        "portfolio": {"returns": samples},
    }


def _resolve_evaluator(
    evaluator: str | Callable[[ExecutionTask], Mapping[str, Any]] | None,
) -> Callable[[ExecutionTask], Mapping[str, Any]]:
    if evaluator is None:
        return _default_evaluator
    if callable(evaluator):
        return evaluator
    module_name, separator, attribute = evaluator.partition(":")
    if not separator:
        raise ValueError("evaluator must use 'module:function' syntax")
    function = getattr(importlib.import_module(module_name), attribute)
    if not callable(function):
        raise TypeError("configured evaluator is not callable")
    return function


def _worker_evaluate(
    task: ExecutionTask,
    attempt: int,
    evaluator: str | Callable[[ExecutionTask], Mapping[str, Any]] | None,
) -> EvaluationResult:
    started = time.monotonic()
    if attempt <= int(task.payload.get("crash_attempts", 0)):
        raise WorkerCrash(f"requested synthetic crash on attempt {attempt}")
    if attempt <= int(task.payload.get("hard_crash_attempts", 0)):
        os._exit(86)
    function = _resolve_evaluator(evaluator)
    errors: list[dict[str, Any]] = []
    try:
        raw = dict(function(task))
    except WorkerCrash:
        raise
    except BaseException as error:
        raw = {}
        errors.append(
            {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(limit=20),
            }
        )
    duration = time.monotonic() - started
    metrics = raw.get("metrics", raw.get("result", raw))
    identity = _stable_hash(
        "factor-evaluation-identity-v2",
        task.task_id,
        task.factor_identity,
        task.instrument,
        task.segment,
        task.horizon,
    )
    return EvaluationResult(
        task_id=task.task_id,
        evaluation_identity=identity,
        result=dict(metrics) if isinstance(metrics, Mapping) else {"value": metrics},
        native_event=dict(raw.get("native_event", {})),
        non_overlap=dict(raw.get("non_overlap", {})),
        hac=dict(raw.get("hac", {})),
        bootstrap=dict(raw.get("bootstrap", {})),
        portfolio=dict(raw.get("portfolio", {})),
        errors=errors,
        duration_seconds=duration,
        worker_pid=os.getpid(),
        worker_rss_bytes=_rss_bytes(),
        attempt=attempt,
    ).seal()


class ExecutionLedger:
    """Coordinator-owned transaction log and checkpoint store."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self._writer_pid = os.getpid()
        self._connection: sqlite3.Connection | None = None

    def _assert_writer(self) -> None:
        if os.getpid() != self._writer_pid:
            raise RuntimeError("SQLite ledger writes are coordinator-only")

    def connect(self) -> sqlite3.Connection:
        self._assert_writer()
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=60)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=60000")
            self._connection = connection
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def initialize(
        self,
        manifest: FrozenExecutionManifest,
        worker_config: Mapping[str, Any],
        *,
        resume: bool,
    ) -> None:
        connection = self.connect()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_runs(
                run_id TEXT PRIMARY KEY,
                engine_version TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                dataset_identity TEXT NOT NULL,
                evaluation_version TEXT NOT NULL,
                evaluation_policy_json TEXT NOT NULL,
                chronological_segments_json TEXT NOT NULL,
                worker_config_json TEXT NOT NULL,
                bootstrap_seed INTEGER NOT NULL,
                bootstrap_state_json TEXT NOT NULL,
                post_pass_stage TEXT NOT NULL,
                last_committed_sequence INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                writer_pid INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS execution_tasks(
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                trial_id TEXT NOT NULL,
                factor_identity TEXT NOT NULL,
                instrument TEXT NOT NULL,
                segment TEXT NOT NULL,
                horizon TEXT NOT NULL,
                family_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                last_error_json TEXT,
                committed_sequence INTEGER,
                PRIMARY KEY(run_id,task_id),
                UNIQUE(run_id,sequence));
            CREATE TABLE IF NOT EXISTS execution_evaluations(
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                evaluation_identity TEXT NOT NULL,
                result_json TEXT NOT NULL,
                native_event_json TEXT NOT NULL,
                non_overlap_json TEXT NOT NULL,
                hac_json TEXT NOT NULL,
                bootstrap_json TEXT NOT NULL,
                portfolio_json TEXT NOT NULL,
                errors_json TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                worker_pid INTEGER NOT NULL,
                worker_rss_bytes INTEGER NOT NULL,
                writer_pid INTEGER NOT NULL,
                attempt INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                committed_sequence INTEGER NOT NULL,
                PRIMARY KEY(run_id,task_id),
                UNIQUE(run_id,evaluation_identity));
            CREATE TABLE IF NOT EXISTS execution_chunks(
                run_id TEXT NOT NULL,
                committed_sequence INTEGER NOT NULL,
                evaluation_count INTEGER NOT NULL,
                reason TEXT NOT NULL,
                chunk_checksum TEXT NOT NULL,
                writer_pid INTEGER NOT NULL,
                committed_at TEXT NOT NULL,
                PRIMARY KEY(run_id,committed_sequence));
            CREATE TABLE IF NOT EXISTS execution_checkpoints(
                run_id TEXT NOT NULL,
                committed_sequence INTEGER NOT NULL,
                manifest_hash TEXT NOT NULL,
                dataset_identity TEXT NOT NULL,
                evaluation_version TEXT NOT NULL,
                completed_tasks_json TEXT NOT NULL,
                incomplete_tasks_json TEXT NOT NULL,
                retry_tasks_json TEXT NOT NULL,
                worker_config_json TEXT NOT NULL,
                bootstrap_seed INTEGER NOT NULL,
                bootstrap_state_json TEXT NOT NULL,
                post_pass_state_json TEXT NOT NULL,
                ledger_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(run_id,committed_sequence));
            CREATE TABLE IF NOT EXISTS execution_post_pass(
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                checksum TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                PRIMARY KEY(run_id,stage));
            """
        )
        existing = connection.execute(
            "SELECT * FROM execution_runs WHERE run_id=?", (manifest.run_id,)
        ).fetchone()
        if existing is not None:
            self._validate_identity(existing, manifest)
            if not resume:
                raise FileExistsError(
                    "ledger already contains this run; use --resume"
                )
            self.verify_checkpoint(manifest.run_id)
            self.rollback_incomplete(manifest.run_id)
            connection.execute(
                "UPDATE execution_runs SET status='RUNNING',updated_at=? WHERE run_id=?",
                (_utc_now(), manifest.run_id),
            )
            connection.commit()
            return
        if resume:
            raise FileNotFoundError("no checkpoint exists for requested run")
        now = _utc_now()
        with connection:
            connection.execute(
                """INSERT INTO execution_runs VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    manifest.run_id,
                    FACTOR_EXECUTION_ENGINE_VERSION,
                    manifest.manifest_hash,
                    manifest.dataset_identity,
                    manifest.evaluation_version,
                    _stable_json(manifest.evaluation_policy),
                    _stable_json(manifest.chronological_segments),
                    _stable_json(worker_config),
                    manifest.bootstrap_seed,
                    _stable_json(manifest.bootstrap_state),
                    "PENDING",
                    0,
                    "RUNNING",
                    self._writer_pid,
                    now,
                    now,
                ),
            )
            connection.executemany(
                """INSERT INTO execution_tasks VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL)""",
                [
                    (
                        manifest.run_id,
                        task.task_id,
                        task.sequence,
                        task.trial_id,
                        task.factor_identity,
                        task.instrument,
                        task.segment,
                        task.horizon,
                        task.family,
                        _stable_json(dict(task.payload)),
                        TaskState.PENDING.value,
                    )
                    for task in sorted(manifest.tasks, key=lambda item: item.sequence)
                ],
            )
        self.checkpoint(manifest.run_id, worker_config, reason="INITIALIZED")

    def verify_checkpoint(self, run_id: str) -> None:
        """Reject a torn, truncated, or logically modified committed prefix."""
        connection = self.connect()
        run_sequence = self.last_sequence(run_id)
        chunk_sequence = int(
            connection.execute(
                """SELECT COALESCE(MAX(committed_sequence),0)
                   FROM execution_chunks WHERE run_id=?""",
                (run_id,),
            ).fetchone()[0]
        )
        checkpoint = connection.execute(
            """SELECT committed_sequence,ledger_hash
               FROM execution_checkpoints
               WHERE run_id=? ORDER BY committed_sequence DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        if checkpoint is None:
            raise IdentityMismatch("resume rejected: checkpoint is missing")
        if (
            int(checkpoint["committed_sequence"]) != run_sequence
            or chunk_sequence != run_sequence
        ):
            raise IdentityMismatch("resume rejected: committed prefix is incomplete")
        if str(checkpoint["ledger_hash"]) != self.logical_hash(run_id):
            raise IdentityMismatch("resume rejected: ledger hash mismatch")

    @staticmethod
    def _validate_identity(
        row: sqlite3.Row, manifest: FrozenExecutionManifest
    ) -> None:
        expected = {
            "engine_version": FACTOR_EXECUTION_ENGINE_VERSION,
            "manifest_hash": manifest.manifest_hash,
            "dataset_identity": manifest.dataset_identity,
            "evaluation_version": manifest.evaluation_version,
        }
        for field_name, value in expected.items():
            if str(row[field_name]) != str(value):
                raise IdentityMismatch(
                    f"resume rejected: {field_name} changed "
                    f"({row[field_name]!r} != {value!r})"
                )

    def rollback_incomplete(self, run_id: str) -> None:
        """Return every non-transactionally persisted task to schedulable state."""
        self._assert_writer()
        connection = self.connect()
        with connection:
            connection.execute(
                """UPDATE execution_tasks
                   SET state=CASE
                       WHEN attempt > 0 THEN 'RETRYABLE_FAILED'
                       ELSE 'PENDING' END,
                       committed_sequence=NULL
                   WHERE run_id=? AND state IN(
                       'CLAIMED','RUNNING','BASE_EVALUATED')""",
                (run_id,),
            )

    def pending_tasks(self, run_id: str) -> list[tuple[ExecutionTask, int]]:
        rows = self.connect().execute(
            """SELECT * FROM execution_tasks
               WHERE run_id=? AND state IN('PENDING','RETRYABLE_FAILED')
               ORDER BY sequence""",
            (run_id,),
        ).fetchall()
        return [
            (
                ExecutionTask(
                    trial_id=str(row["trial_id"]),
                    factor_identity=str(row["factor_identity"]),
                    instrument=str(row["instrument"]),
                    segment=str(row["segment"]),
                    horizon=str(row["horizon"]),
                    payload=json.loads(row["payload_json"]),
                    family=str(row["family_name"]),
                    sequence=int(row["sequence"]),
                ),
                int(row["attempt"]),
            )
            for row in rows
        ]

    def mark_claimed(self, run_id: str, task_id: str, attempt: int) -> None:
        with self.connect():
            self.connect().execute(
                """UPDATE execution_tasks SET state='RUNNING',attempt=?
                   WHERE run_id=? AND task_id=?
                   AND state IN('PENDING','RETRYABLE_FAILED')""",
                (attempt, run_id, task_id),
            )

    def mark_retry(
        self, run_id: str, task_id: str, attempt: int, error: Mapping[str, Any]
    ) -> None:
        with self.connect():
            self.connect().execute(
                """UPDATE execution_tasks
                   SET state='RETRYABLE_FAILED',attempt=?,last_error_json=?
                   WHERE run_id=? AND task_id=?""",
                (attempt, _stable_json(error), run_id, task_id),
            )

    def mark_permanent(
        self, run_id: str, task_id: str, attempt: int, error: Mapping[str, Any]
    ) -> None:
        with self.connect():
            self.connect().execute(
                """UPDATE execution_tasks
                   SET state='PERMANENT_FAILED',attempt=?,last_error_json=?
                   WHERE run_id=? AND task_id=?""",
                (attempt, _stable_json(error), run_id, task_id),
            )

    def commit_chunk(
        self,
        run_id: str,
        results: Sequence[EvaluationResult],
        worker_config: Mapping[str, Any],
        *,
        reason: str,
    ) -> int:
        if not results:
            return self.last_sequence(run_id)
        self._assert_writer()
        connection = self.connect()
        sequence = self.last_sequence(run_id) + 1
        ordered = sorted(results, key=lambda result: result.task_id)
        chunk_checksum = _stable_hash(
            "execution-chunk-v2",
            sequence,
            *[result.checksum for result in ordered],
        )
        now = _utc_now()
        with connection:
            for result in ordered:
                connection.execute(
                    """INSERT INTO execution_evaluations VALUES(
                       ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(run_id,task_id) DO UPDATE SET
                       evaluation_identity=excluded.evaluation_identity,
                       result_json=excluded.result_json,
                       native_event_json=excluded.native_event_json,
                       non_overlap_json=excluded.non_overlap_json,
                       hac_json=excluded.hac_json,
                       bootstrap_json=excluded.bootstrap_json,
                       portfolio_json=excluded.portfolio_json,
                       errors_json=excluded.errors_json,
                       duration_seconds=excluded.duration_seconds,
                       worker_pid=excluded.worker_pid,
                       worker_rss_bytes=excluded.worker_rss_bytes,
                       writer_pid=excluded.writer_pid,
                       attempt=excluded.attempt,
                       checksum=excluded.checksum,
                       committed_sequence=excluded.committed_sequence""",
                    (
                        run_id,
                        result.task_id,
                        result.evaluation_identity,
                        _stable_json(result.result),
                        _stable_json(result.native_event),
                        _stable_json(result.non_overlap),
                        _stable_json(result.hac),
                        _stable_json(result.bootstrap),
                        _stable_json(result.portfolio),
                        _stable_json(result.errors),
                        result.duration_seconds,
                        result.worker_pid,
                        result.worker_rss_bytes,
                        self._writer_pid,
                        result.attempt,
                        result.checksum,
                        sequence,
                    ),
                )
                state = (
                    TaskState.RETRYABLE_FAILED.value
                    if result.errors
                    else TaskState.PERSISTED.value
                )
                connection.execute(
                    """UPDATE execution_tasks
                       SET state=?,attempt=?,committed_sequence=?,
                           last_error_json=CASE WHEN ?='RETRYABLE_FAILED'
                           THEN ? ELSE last_error_json END
                       WHERE run_id=? AND task_id=?""",
                    (
                        state,
                        result.attempt,
                        sequence,
                        state,
                        _stable_json(result.errors),
                        run_id,
                        result.task_id,
                    ),
                )
            connection.execute(
                "INSERT INTO execution_chunks VALUES(?,?,?,?,?,?,?)",
                (
                    run_id,
                    sequence,
                    len(ordered),
                    reason,
                    chunk_checksum,
                    self._writer_pid,
                    now,
                ),
            )
            connection.execute(
                """UPDATE execution_runs SET last_committed_sequence=?,
                   updated_at=? WHERE run_id=?""",
                (sequence, now, run_id),
            )
        self.checkpoint(run_id, worker_config, reason=reason)
        return sequence

    def last_sequence(self, run_id: str) -> int:
        row = self.connect().execute(
            "SELECT last_committed_sequence FROM execution_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def logical_hash(self, run_id: str) -> str:
        rows = self.connect().execute(
            """SELECT task_id,evaluation_identity,result_json,native_event_json,
                      non_overlap_json,hac_json,bootstrap_json,portfolio_json,
                      errors_json
               FROM execution_evaluations WHERE run_id=? ORDER BY task_id""",
            (run_id,),
        ).fetchall()
        return _stable_hash(
            "factor-execution-ledger-v2",
            *[
                _stable_json(dict(row))
                for row in rows
            ],
        )

    def checkpoint(
        self,
        run_id: str,
        worker_config: Mapping[str, Any],
        *,
        reason: str,
    ) -> None:
        connection = self.connect()
        sequence = self.last_sequence(run_id)
        rows = connection.execute(
            "SELECT task_id,state FROM execution_tasks WHERE run_id=? ORDER BY task_id",
            (run_id,),
        ).fetchall()
        completed = [
            str(row["task_id"])
            for row in rows
            if row["state"]
            in (
                TaskState.PERSISTED.value,
                TaskState.POST_PASS_PENDING.value,
                TaskState.COMPLETE.value,
            )
        ]
        retry = [
            str(row["task_id"])
            for row in rows
            if row["state"] == TaskState.RETRYABLE_FAILED.value
        ]
        incomplete = [
            str(row["task_id"])
            for row in rows
            if str(row["task_id"]) not in set(completed)
        ]
        run = connection.execute(
            "SELECT * FROM execution_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        post_state = {
            "stage": str(run["post_pass_stage"]),
            "reason": reason,
        }
        with connection:
            connection.execute(
                """INSERT OR REPLACE INTO execution_checkpoints VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    sequence,
                    str(run["manifest_hash"]),
                    str(run["dataset_identity"]),
                    str(run["evaluation_version"]),
                    _stable_json(completed),
                    _stable_json(incomplete),
                    _stable_json(retry),
                    _stable_json(worker_config),
                    int(run["bootstrap_seed"]),
                    str(run["bootstrap_state_json"]),
                    _stable_json(post_state),
                    self.logical_hash(run_id),
                    _utc_now(),
                ),
            )

    def telemetry(
        self,
        run_id: str,
        *,
        started: float,
        queue_depth: int = 0,
        result_depth: int = 0,
        worker_rss: Mapping[int, int] | None = None,
    ) -> dict[str, Any]:
        connection = self.connect()
        states = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                """SELECT state,COUNT(*) count FROM execution_tasks
                   WHERE run_id=? GROUP BY state""",
                (run_id,),
            )
        }
        completed = sum(
            states.get(state, 0)
            for state in (
                TaskState.PERSISTED.value,
                TaskState.POST_PASS_PENDING.value,
                TaskState.COMPLETE.value,
            )
        )
        failed = states.get(TaskState.PERMANENT_FAILED.value, 0)
        retrying = states.get(TaskState.RETRYABLE_FAILED.value, 0)
        total = sum(states.values())
        elapsed = max(0.000001, time.monotonic() - started)
        rate = completed / elapsed
        remaining = total - completed - failed
        checkpoint = connection.execute(
            """SELECT created_at FROM execution_checkpoints
               WHERE run_id=? ORDER BY committed_sequence DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        run = connection.execute(
            "SELECT post_pass_stage FROM execution_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return {
            "completed": completed,
            "remaining": remaining,
            "failed": failed,
            "retrying": retrying,
            "evaluations_per_sec": rate,
            "eta_seconds": remaining / rate if rate > 0 else None,
            "worker_rss": dict(worker_rss or {}),
            "coordinator_rss": _rss_bytes(),
            "task_queue_depth": queue_depth,
            "result_queue_depth": result_depth,
            "last_checkpoint": str(checkpoint[0]) if checkpoint else None,
            "ledger_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "current_post_pass_stage": str(run[0]) if run else "MISSING",
        }

    def evaluation_rows(self, run_id: str) -> list[sqlite3.Row]:
        return self.connect().execute(
            """SELECT t.task_id manifest_task_id,t.family_name,t.trial_id,
                      t.factor_identity,t.state,e.*
               FROM execution_tasks t
               LEFT JOIN execution_evaluations e USING(run_id,task_id)
               WHERE t.run_id=? ORDER BY t.task_id""",
            (run_id,),
        ).fetchall()

    def save_post_pass(
        self,
        run_id: str,
        stage: str,
        payload: Mapping[str, Any],
        worker_config: Mapping[str, Any],
    ) -> None:
        serialized = _stable_json(dict(payload))
        with self.connect():
            self.connect().execute(
                "INSERT OR REPLACE INTO execution_post_pass VALUES(?,?,?,?,?)",
                (
                    run_id,
                    stage,
                    serialized,
                    _stable_hash("post-pass-v2", stage, serialized),
                    _utc_now(),
                ),
            )
            self.connect().execute(
                """UPDATE execution_runs SET post_pass_stage=?,updated_at=?
                   WHERE run_id=?""",
                (stage, _utc_now(), run_id),
            )
        self.checkpoint(run_id, worker_config, reason=f"POST_PASS_{stage}")


class FactorExecutionEngineV2:
    """Coordinator for bounded processes, durable chunks, and global post-pass."""

    def __init__(
        self,
        manifest: FrozenExecutionManifest,
        ledger_path: Path | str,
        *,
        workers: int = DEFAULT_WORKERS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        checkpoint_seconds: float = DEFAULT_CHECKPOINT_SECONDS,
        memory_budget_mb: int = 512,
        max_tasks_per_worker: int = 100,
        max_retries: int = 2,
        evaluator: str | Callable[[ExecutionTask], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if workers < 1 or workers > MAX_WORKERS:
            raise ValueError("workers must be between 1 and 4")
        if chunk_size < 1 or checkpoint_seconds <= 0:
            raise ValueError("chunk_size and checkpoint_seconds must be positive")
        self.manifest = manifest
        self.ledger = ExecutionLedger(ledger_path)
        self.workers = workers
        self.chunk_size = chunk_size
        self.checkpoint_seconds = checkpoint_seconds
        self.memory_budget_mb = memory_budget_mb
        self.max_tasks_per_worker = max_tasks_per_worker
        self.max_retries = max_retries
        self.evaluator = evaluator
        self.clock = clock
        self.task_queue_capacity = max(1, workers * 2)
        self.result_queue_capacity = max(chunk_size, workers * 2)
        self._stop_requested = False
        self._worker_rss: dict[int, int] = {}
        self._previous_handlers: dict[int, Any] = {}

    @property
    def worker_config(self) -> dict[str, Any]:
        return {
            "workers": self.workers,
            "chunk_size": self.chunk_size,
            "checkpoint_seconds": self.checkpoint_seconds,
            "memory_budget_mb": self.memory_budget_mb,
            "max_tasks_per_worker": self.max_tasks_per_worker,
            "task_queue_capacity": self.task_queue_capacity,
            "result_queue_capacity": self.result_queue_capacity,
        }

    def validate_task_graph(self) -> dict[str, Any]:
        self.manifest.validate()
        maximum = 0
        for task in self.manifest.tasks:
            size = len(_stable_json(dict(task.payload)).encode("utf-8"))
            maximum = max(maximum, size)
            if size > self.memory_budget_mb * 1024 * 1024:
                raise MemoryBudgetExceeded(
                    f"task {task.task_id} payload {size} bytes exceeds "
                    f"{self.memory_budget_mb} MiB budget"
                )
        return {
            "engine_version": FACTOR_EXECUTION_ENGINE_VERSION,
            "run_id": self.manifest.run_id,
            "manifest_hash": self.manifest.manifest_hash,
            "dataset_identity": self.manifest.dataset_identity,
            "evaluation_version": self.manifest.evaluation_version,
            "tasks": len(self.manifest.tasks),
            "maximum_payload_bytes": maximum,
            "workers": self.workers,
            "task_queue_capacity": self.task_queue_capacity,
            "result_queue_capacity": self.result_queue_capacity,
            "dry_run": True,
        }

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        self._stop_requested = True

    def _install_signal_handlers(self) -> None:
        if os.getpid() != self.ledger._writer_pid:
            return
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def _restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def run(
        self,
        *,
        resume: bool = False,
        interrupt_after: int | None = None,
        run_post_pass: bool = True,
    ) -> dict[str, Any]:
        self.validate_task_graph()
        self.ledger.initialize(
            self.manifest, self.worker_config, resume=resume
        )
        started = self.clock()
        pending = self.ledger.pending_tasks(self.manifest.run_id)
        self._install_signal_handlers()
        buffered: list[EvaluationResult] = []
        last_commit = self.clock()
        try:
            with ProcessPoolExecutor(
                max_workers=self.workers,
                max_tasks_per_child=self.max_tasks_per_worker,
            ) as executor:
                inflight: dict[Any, tuple[ExecutionTask, int]] = {}
                cursor = 0
                pool_broken = False
                while cursor < len(pending) or inflight:
                    while (
                        not self._stop_requested
                        and not pool_broken
                        and cursor < len(pending)
                        and len(inflight) < self.task_queue_capacity
                    ):
                        task, previous_attempt = pending[cursor]
                        cursor += 1
                        attempt = previous_attempt + 1
                        if attempt > self.max_retries + 1:
                            self.ledger.mark_permanent(
                                self.manifest.run_id,
                                task.task_id,
                                attempt,
                                {"type": "RetryLimit", "message": "retry limit reached"},
                            )
                            continue
                        self.ledger.mark_claimed(
                            self.manifest.run_id, task.task_id, attempt
                        )
                        try:
                            future = executor.submit(
                                _worker_evaluate, task, attempt, self.evaluator
                            )
                        except BrokenProcessPool as error:
                            self.ledger.mark_retry(
                                self.manifest.run_id,
                                task.task_id,
                                attempt,
                                {"type": type(error).__name__, "message": str(error)},
                            )
                            pool_broken = True
                            break
                        inflight[future] = (task, attempt)
                    timeout = max(
                        0.0,
                        self.checkpoint_seconds - (self.clock() - last_commit),
                    )
                    if inflight:
                        done, _ = wait(
                            inflight,
                            timeout=min(timeout, 0.25),
                            return_when=FIRST_COMPLETED,
                        )
                    else:
                        done = set()
                    for future in done:
                        task, attempt = inflight.pop(future)
                        try:
                            result = future.result()
                        except BaseException as error:
                            if isinstance(error, BrokenProcessPool):
                                pool_broken = True
                            self.ledger.mark_retry(
                                self.manifest.run_id,
                                task.task_id,
                                attempt,
                                {
                                    "type": type(error).__name__,
                                    "message": str(error),
                                },
                            )
                        else:
                            self._worker_rss[result.worker_pid] = (
                                result.worker_rss_bytes
                            )
                            buffered.append(result)
                    while len(buffered) >= self.chunk_size:
                        self.ledger.commit_chunk(
                            self.manifest.run_id,
                            buffered[: self.chunk_size],
                            self.worker_config,
                            reason="CHUNK_SIZE",
                        )
                        del buffered[: self.chunk_size]
                        last_commit = self.clock()
                    elapsed = self.clock() - last_commit
                    reason = None
                    if buffered and elapsed >= self.checkpoint_seconds:
                        reason = "WALL_CLOCK"
                    elif self._stop_requested and buffered:
                        reason = "INTERRUPT_SIGNAL"
                    if reason:
                        self.ledger.commit_chunk(
                            self.manifest.run_id,
                            buffered,
                            self.worker_config,
                            reason=reason,
                        )
                        buffered.clear()
                        last_commit = self.clock()
                    completed = self.ledger.telemetry(
                        self.manifest.run_id, started=started
                    )["completed"]
                    if (
                        interrupt_after is not None
                        and completed + len(buffered) >= interrupt_after
                    ):
                        self._stop_requested = True
                    if self._stop_requested:
                        for future in inflight:
                            future.cancel()
                        break
                    if pool_broken and not inflight:
                        break
                if buffered:
                    self.ledger.commit_chunk(
                        self.manifest.run_id,
                        buffered,
                        self.worker_config,
                        reason="WORKER_EXIT"
                        if not self._stop_requested
                        else "INTERRUPT_SIGNAL",
                    )
                    buffered.clear()
            if self._stop_requested:
                self.ledger.rollback_incomplete(self.manifest.run_id)
                self.ledger.checkpoint(
                    self.manifest.run_id,
                    self.worker_config,
                    reason="INTERRUPTED",
                )
                raise ExecutionInterrupted("execution interrupted at safe checkpoint")
            # Retry worker failures in the same invocation, using a fresh pool.
            retry = self.ledger.pending_tasks(self.manifest.run_id)
            if retry and any(attempt <= self.max_retries for _, attempt in retry):
                return self.run(resume=True, run_post_pass=run_post_pass)
            remaining = self.ledger.pending_tasks(self.manifest.run_id)
            for task, attempt in remaining:
                self.ledger.mark_permanent(
                    self.manifest.run_id,
                    task.task_id,
                    attempt,
                    {"type": "RetryLimit", "message": "worker did not complete task"},
                )
            with self.ledger.connect():
                self.ledger.connect().execute(
                    """UPDATE execution_tasks SET state='POST_PASS_PENDING'
                       WHERE run_id=? AND state='PERSISTED'""",
                    (self.manifest.run_id,),
                )
                self.ledger.connect().execute(
                    """UPDATE execution_runs SET post_pass_stage='PENDING',
                       updated_at=? WHERE run_id=?""",
                    (_utc_now(), self.manifest.run_id),
                )
            if run_post_pass:
                self.run_post_pass()
            return self.ledger.telemetry(
                self.manifest.run_id,
                started=started,
                worker_rss=self._worker_rss,
            )
        finally:
            self._restore_signal_handlers()

    def run_post_pass(
        self, *, interrupt_after_stage: str | None = None
    ) -> dict[str, Any]:
        connection = self.ledger.connect()
        run = connection.execute(
            "SELECT post_pass_stage FROM execution_runs WHERE run_id=?",
            (self.manifest.run_id,),
        ).fetchone()
        if run is None:
            raise FileNotFoundError("execution run is not initialized")
        completed_stages = {
            str(row[0])
            for row in connection.execute(
                "SELECT stage FROM execution_post_pass WHERE run_id=?",
                (self.manifest.run_id,),
            )
        }
        rows = self.ledger.evaluation_rows(self.manifest.run_id)
        if not rows:
            raise RuntimeError("post-pass requires persisted base evaluations")
        evaluations = [
            {
                "task_id": str(row["manifest_task_id"]),
                "trial_id": str(row["trial_id"]),
                "family": str(row["family_name"]),
                "complete": row["result_json"] is not None
                and not json.loads(row["errors_json"]),
                "result": (
                    json.loads(row["result_json"])
                    if row["result_json"] is not None
                    else {}
                ),
                "portfolio": (
                    json.loads(row["portfolio_json"])
                    if row["portfolio_json"] is not None
                    else {}
                ),
                "non_overlap": (
                    json.loads(row["non_overlap_json"])
                    if row["non_overlap_json"] is not None
                    else {}
                ),
            }
            for row in rows
        ]

        if "FDR" not in completed_stages:
            raw = [
                item["result"].get("raw_p_value")
                for item in evaluations
            ]
            global_q = benjamini_hochberg(raw)
            families: dict[str, list[int]] = {}
            for index, item in enumerate(evaluations):
                families.setdefault(item["family"], []).append(index)
            local_q: list[float | None] = [None] * len(evaluations)
            for indices in families.values():
                adjusted = benjamini_hochberg([raw[index] for index in indices])
                for index, value in zip(indices, adjusted):
                    local_q[index] = value
            payload = {
                "family_members": {
                    family: [evaluations[index]["task_id"] for index in indices]
                    for family, indices in sorted(families.items())
                },
                "values": {
                    item["task_id"]: {
                        "raw_p_value": raw[index],
                        "local_fdr_q": local_q[index],
                        "global_fdr_q": global_q[index],
                    }
                    for index, item in enumerate(evaluations)
                },
            }
            self.ledger.save_post_pass(
                self.manifest.run_id, "FDR", payload, self.worker_config
            )
            if interrupt_after_stage == "FDR":
                raise ExecutionInterrupted("post-pass interrupted after FDR")

        if "CORRELATION_AND_EFFECTIVE_TRIALS" not in completed_stages:
            vectors = {}
            for item in evaluations:
                if not item["complete"]:
                    continue
                values = item["portfolio"].get(
                    "returns", item["non_overlap"].get("returns", [])
                )
                if len(values) >= 2:
                    vectors[item["task_id"]] = values
            membership, cluster_count = (
                correlation_clusters(vectors) if vectors else (0, {})
            )
            if not vectors:
                membership, cluster_count = {}, 0
            effective = max(1, int(cluster_count))
            payload = {
                "effective_trial_count": effective,
                "correlation_cluster_count": cluster_count,
                "membership": membership,
            }
            self.ledger.save_post_pass(
                self.manifest.run_id,
                "CORRELATION_AND_EFFECTIVE_TRIALS",
                payload,
                self.worker_config,
            )
            if interrupt_after_stage == "CORRELATION_AND_EFFECTIVE_TRIALS":
                raise ExecutionInterrupted(
                    "post-pass interrupted after correlation stage"
                )

        completed_stages = {
            str(row[0])
            for row in connection.execute(
                "SELECT stage FROM execution_post_pass WHERE run_id=?",
                (self.manifest.run_id,),
            )
        }
        if "PSR_DSR_PBO" not in completed_stages:
            effective_row = connection.execute(
                """SELECT payload_json FROM execution_post_pass
                   WHERE run_id=? AND stage='CORRELATION_AND_EFFECTIVE_TRIALS'""",
                (self.manifest.run_id,),
            ).fetchone()
            effective = int(json.loads(effective_row[0])["effective_trial_count"])
            attempts = int(
                connection.execute(
                    """SELECT COALESCE(SUM(attempt),0)
                       FROM execution_tasks WHERE run_id=?""",
                    (self.manifest.run_id,),
                ).fetchone()[0]
            )
            completed_evaluations = [
                item for item in evaluations if item["complete"]
            ]
            sharpes = [
                float(item["result"].get("sharpe", 0.0))
                for item in completed_evaluations
            ]
            diagnostics = {}
            for item, sharpe in zip(completed_evaluations, sharpes):
                returns = item["portfolio"].get("returns", [])
                observations = len(returns)
                skew = 0.0
                kurtosis = 3.0
                psr = probabilistic_sharpe_ratio(
                    sharpe, 0.0, max(2, observations), skew, kurtosis
                )
                dsr, benchmark = deflated_sharpe_ratio(
                    sharpe,
                    max(2, observations),
                    skew,
                    kurtosis,
                    effective_trials=max(effective, attempts),
                    trial_sharpes=sharpes,
                )
                diagnostics[item["task_id"]] = {
                    "psr": psr,
                    "dsr": dsr,
                    "dsr_benchmark": benchmark,
                }
            payload = {
                "attempt_count": attempts,
                "complete_evaluation_count": len(completed_evaluations),
                "effective_trial_count": effective,
                "diagnostics": diagnostics,
                "pbo": {
                    "available": False,
                    "reason": "ENGINE_REQUIRES_PRECOMPUTED_INDEPENDENT_BLOCKS",
                },
            }
            self.ledger.save_post_pass(
                self.manifest.run_id, "PSR_DSR_PBO", payload, self.worker_config
            )
            if interrupt_after_stage == "PSR_DSR_PBO":
                raise ExecutionInterrupted("post-pass interrupted after PSR/DSR/PBO")

        completed_stages = {
            str(row[0])
            for row in connection.execute(
                "SELECT stage FROM execution_post_pass WHERE run_id=?",
                (self.manifest.run_id,),
            )
        }
        if "CLASSIFICATION" not in completed_stages:
            fdr = json.loads(
                connection.execute(
                    """SELECT payload_json FROM execution_post_pass
                       WHERE run_id=? AND stage='FDR'""",
                    (self.manifest.run_id,),
                ).fetchone()[0]
            )
            classifications = {}
            for item in evaluations:
                q_value = fdr["values"][item["task_id"]]["global_fdr_q"]
                classifications[item["task_id"]] = (
                    "RETAIN_FACTOR_CANDIDATE"
                    if q_value is not None and q_value <= 0.05
                    else "RETIRE_MULTIPLE_TESTING"
                )
            self.ledger.save_post_pass(
                self.manifest.run_id,
                "CLASSIFICATION",
                {"classifications": classifications},
                self.worker_config,
            )
            if interrupt_after_stage == "CLASSIFICATION":
                raise ExecutionInterrupted("post-pass interrupted after classification")

        with connection:
            connection.execute(
                """UPDATE execution_tasks SET state='COMPLETE'
                   WHERE run_id=? AND state='POST_PASS_PENDING'""",
                (self.manifest.run_id,),
            )
            connection.execute(
                """UPDATE execution_runs SET post_pass_stage='COMPLETE',
                   status='COMPLETE',updated_at=? WHERE run_id=?""",
                (_utc_now(), self.manifest.run_id),
            )
        self.ledger.save_post_pass(
            self.manifest.run_id,
            "COMPLETE",
            {"completed_at": _utc_now()},
            self.worker_config,
        )
        return {
            "run_id": self.manifest.run_id,
            "stage": "COMPLETE",
            "evaluations": len(evaluations),
        }


__all__ = [
    "DEFAULT_CHECKPOINT_SECONDS",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_WORKERS",
    "ExecutionInterrupted",
    "ExecutionLedger",
    "ExecutionTask",
    "FACTOR_EXECUTION_ENGINE_VERSION",
    "FactorExecutionEngineV2",
    "FrozenExecutionManifest",
    "IdentityMismatch",
    "MAX_WORKERS",
    "MemoryBudgetExceeded",
    "POST_PASS_STAGES",
    "TaskState",
]
