"""Transactional SQLite checkpoints for resumable Phase 6B discovery."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from .strategy_program import canonical_hash
from .strategy_program_v2 import CHECKPOINT_SCHEMA_VERSION

STAGES = (
    "generation_manifest", "semantic_validation", "entry_trigger_vector",
    "event_study", "btc_canonical_execution", "neighborhood_variant_execution",
    "eth_confirmation", "sol_confirmation", "final_classification",
)
STATUSES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")


def checkpoint_key(*, stage: str, program_identity: str, geometry_identity: str,
                   instrument: str, timeframe: str, fold_identity: Mapping[str, Any],
                   dataset_fingerprint: str, policy_version: str,
                   variant_identity: str = "") -> dict[str, str]:
    if stage not in STAGES:
        raise ValueError(f"unsupported checkpoint stage: {stage}")
    return {
        "stage": stage, "program_identity": program_identity,
        "geometry_identity": geometry_identity, "instrument": instrument,
        "timeframe": timeframe, "fold_identity": json.dumps(
            fold_identity, sort_keys=True, separators=(",", ":")),
        "dataset_fingerprint": dataset_fingerprint, "policy_version": policy_version,
        "variant_identity": variant_identity,
    }


class DiscoveryCheckpoint:
    """A single serialized writer; workers may only submit completed payloads."""

    def __init__(self, path: Path, *, maximum_retries: int = 2,
                 cancel_file: Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.maximum_retries = maximum_retries
        self.cancel_file = Path(cancel_file) if cancel_file else None
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS checkpoint_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkpoint_tasks(
          task_id TEXT PRIMARY KEY,
          stage TEXT NOT NULL,
          program_identity TEXT NOT NULL,
          geometry_identity TEXT NOT NULL,
          instrument TEXT NOT NULL,
          timeframe TEXT NOT NULL,
          fold_identity TEXT NOT NULL,
          dataset_fingerprint TEXT NOT NULL,
          policy_version TEXT NOT NULL,
          variant_identity TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,
          retry_count INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          evidence_json TEXT,
          maximum_accessed_timestamp INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(stage,program_identity,geometry_identity,instrument,timeframe,
                 fold_identity,dataset_fingerprint,policy_version,variant_identity)
        );
        CREATE INDEX IF NOT EXISTS checkpoint_stage_status
          ON checkpoint_tasks(stage,status,program_identity,geometry_identity,
                              instrument,timeframe,fold_identity,variant_identity);
        """)
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_meta(key,value) VALUES('schema_version',?)",
            (CHECKPOINT_SCHEMA_VERSION,),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DiscoveryCheckpoint":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    @staticmethod
    def _task_id(key: Mapping[str, str]) -> str:
        return canonical_hash({"checkpoint_schema": CHECKPOINT_SCHEMA_VERSION, **dict(key)})

    def register(self, keys: Iterable[Mapping[str, str]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction():
            for raw in keys:
                key = dict(raw); task_id = self._task_id(key)
                self.connection.execute("""
                  INSERT OR IGNORE INTO checkpoint_tasks(
                    task_id,stage,program_identity,geometry_identity,instrument,timeframe,
                    fold_identity,dataset_fingerprint,policy_version,variant_identity,
                    status,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)
                """, (task_id, key["stage"], key["program_identity"],
                      key["geometry_identity"], key["instrument"], key["timeframe"],
                      key["fold_identity"], key["dataset_fingerprint"],
                      key["policy_version"], key.get("variant_identity", ""), now, now))

    def completed(self, key: Mapping[str, str]) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT status,evidence_json FROM checkpoint_tasks WHERE task_id=?",
            (self._task_id(key),),
        ).fetchone()
        if not row or row["status"] != "COMPLETED":
            return None
        return json.loads(row["evidence_json"])

    def pending(self, stage: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
          SELECT * FROM checkpoint_tasks
          WHERE stage=? AND status!='COMPLETED' AND retry_count<=?
          ORDER BY program_identity,geometry_identity,instrument,timeframe,
                   fold_identity,variant_identity
        """, (stage, self.maximum_retries)).fetchall()
        return [dict(row) for row in rows]

    def mark_running(self, key: Mapping[str, str]) -> None:
        if self.cancelled:
            self.mark_cancelled(key)
            raise InterruptedError("Phase 6B cancellation requested")
        with self._transaction():
            self.connection.execute("""
              UPDATE checkpoint_tasks SET status='RUNNING',updated_at=?
              WHERE task_id=? AND status!='COMPLETED'
            """, (datetime.now(timezone.utc).isoformat(), self._task_id(key)))

    def complete(self, key: Mapping[str, str], evidence: Mapping[str, Any],
                 maximum_accessed_timestamp: int | None = None) -> None:
        """Persist evidence and completion atomically; duplicates are impossible."""
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        with self._transaction():
            self.connection.execute("""
              UPDATE checkpoint_tasks
              SET evidence_json=?,maximum_accessed_timestamp=?,status='COMPLETED',
                  error=NULL,updated_at=?
              WHERE task_id=?
            """, (payload, maximum_accessed_timestamp,
                  datetime.now(timezone.utc).isoformat(), self._task_id(key)))

    def fail(self, key: Mapping[str, str], error: BaseException | str) -> int:
        with self._transaction():
            self.connection.execute("""
              UPDATE checkpoint_tasks
              SET status='FAILED',retry_count=retry_count+1,error=?,updated_at=?
              WHERE task_id=? AND status!='COMPLETED'
            """, (str(error), datetime.now(timezone.utc).isoformat(), self._task_id(key)))
        row = self.connection.execute(
            "SELECT retry_count FROM checkpoint_tasks WHERE task_id=?",
            (self._task_id(key),),
        ).fetchone()
        return int(row["retry_count"])

    def mark_cancelled(self, key: Mapping[str, str]) -> None:
        with self._transaction():
            self.connection.execute("""
              UPDATE checkpoint_tasks SET status='CANCELLED',updated_at=?
              WHERE task_id=? AND status!='COMPLETED'
            """, (datetime.now(timezone.utc).isoformat(), self._task_id(key)))

    @property
    def cancelled(self) -> bool:
        return bool(self.cancel_file and self.cancel_file.exists())

    def progress(self) -> dict[str, Any]:
        rows = self.connection.execute("""
          SELECT stage,status,COUNT(*) AS count FROM checkpoint_tasks
          GROUP BY stage,status ORDER BY stage,status
        """).fetchall()
        by_stage: dict[str, dict[str, int]] = {}
        for row in rows:
            by_stage.setdefault(row["stage"], {})[row["status"]] = int(row["count"])
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "by_stage_status": by_stage,
            "total": sum(int(row["count"]) for row in rows),
            "completed": sum(int(row["count"]) for row in rows if row["status"] == "COMPLETED"),
            "failed": sum(int(row["count"]) for row in rows if row["status"] == "FAILED"),
            "cancelled": self.cancelled,
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
