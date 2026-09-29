"""Append-only evidence ledger for the Research Engine.

This module intentionally has no dependency on the dashboard repository.  It
stores research *events*, rather than mutable candidate state, so a rejected
candidate and a repeated holdout request remain part of the audit trail.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    """Canonical JSON suitable for durable, cross-process identities."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str)


def deterministic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def candidate_id(*, strategy_logic: str, parameters: Mapping[str, Any],
                 features: Mapping[str, Any] | list[str] | tuple[str, ...], asset: str,
                 timeframe: str, dataset_version: str,
                 execution_assumptions: Mapping[str, Any]) -> str:
    """Identity only changes if an economically/evaluation-relevant input changes."""
    return deterministic_id("cand", {
        "identity_version": "research-engine-candidate-v1",
        "strategy_logic": strategy_logic,
        "parameters": dict(parameters), "features": features,
        "asset": asset, "timeframe": timeframe,
        "dataset_version": dataset_version,
        "execution_assumptions": dict(execution_assumptions),
    })


class HoldoutAccessBlocked(RuntimeError):
    """Raised when a protected reveal would silently repeat an earlier reveal."""


class ExperimentLedger:
    """Small SQLite event store with database-level append-only protection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialise(self) -> None:
        with self._connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS experiment_ledger (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              experiment_id TEXT NOT NULL UNIQUE,
              research_run_id TEXT NOT NULL,
              research_cycle_id TEXT NOT NULL,
              hypothesis_id TEXT,
              candidate_id TEXT,
              family TEXT,
              stage TEXT NOT NULL,
              status TEXT NOT NULL,
              gate_result TEXT,
              rejection_reason TEXT,
              asset TEXT,
              timeframe TEXT,
              dataset_version TEXT,
              code_version TEXT,
              holdout_access INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_ledger_candidate ON experiment_ledger(candidate_id, event_id);
            CREATE INDEX IF NOT EXISTS ix_ledger_hypothesis ON experiment_ledger(hypothesis_id, event_id);
            CREATE INDEX IF NOT EXISTS ix_ledger_run ON experiment_ledger(research_run_id, event_id);

            CREATE TABLE IF NOT EXISTS budget_events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              research_cycle_id TEXT NOT NULL,
              family TEXT NOT NULL,
              resource TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              limit_value INTEGER NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_budget_usage ON budget_events(research_cycle_id, family, resource, event_id);

            CREATE TABLE IF NOT EXISTS holdout_access_log (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              research_run_id TEXT NOT NULL,
              research_cycle_id TEXT NOT NULL,
              hypothesis_id TEXT,
              candidate_id TEXT NOT NULL,
              dataset_version TEXT NOT NULL,
              reason TEXT NOT NULL,
              status TEXT NOT NULL,
              override_reason TEXT,
              result_json TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_holdout_candidate ON holdout_access_log(research_cycle_id, candidate_id, event_id);

            CREATE TRIGGER IF NOT EXISTS no_update_experiment_ledger
            BEFORE UPDATE ON experiment_ledger BEGIN SELECT RAISE(ABORT, 'experiment_ledger is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_experiment_ledger
            BEFORE DELETE ON experiment_ledger BEGIN SELECT RAISE(ABORT, 'experiment_ledger is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS no_update_budget_events
            BEFORE UPDATE ON budget_events BEGIN SELECT RAISE(ABORT, 'budget_events is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_budget_events
            BEFORE DELETE ON budget_events BEGIN SELECT RAISE(ABORT, 'budget_events is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS no_update_holdout_log
            BEFORE UPDATE ON holdout_access_log BEGIN SELECT RAISE(ABORT, 'holdout_access_log is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_holdout_log
            BEFORE DELETE ON holdout_access_log BEGIN SELECT RAISE(ABORT, 'holdout_access_log is append-only'); END;
            """)

    def append_experiment(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Append an immutable event. Metrics and execution details stay in payload."""
        required = ("research_run_id", "research_cycle_id", "stage", "status")
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"experiment record missing required fields: {', '.join(missing)}")
        event = dict(record)
        experiment_id = str(event.pop("experiment_id", "") or f"exp_{uuid.uuid4().hex}")
        now = str(event.pop("created_at", "") or utc_now())
        columns = {
            "experiment_id": experiment_id, "research_run_id": event.get("research_run_id"),
            "research_cycle_id": event.get("research_cycle_id"), "hypothesis_id": event.get("hypothesis_id"),
            "candidate_id": event.get("candidate_id"), "family": event.get("family"),
            "stage": event.get("stage"), "status": event.get("status"),
            "gate_result": canonical_json(event.get("gate_result")) if event.get("gate_result") is not None else None, "rejection_reason": event.get("rejection_reason"),
            "asset": event.get("asset"), "timeframe": event.get("timeframe"),
            "dataset_version": event.get("dataset_version"), "code_version": event.get("code_version"),
            "holdout_access": int(bool(event.get("holdout_access", False))),
            "payload_json": canonical_json(event), "created_at": now,
        }
        names = ", ".join(columns)
        with self._connect() as con:
            con.execute(f"INSERT INTO experiment_ledger ({names}) VALUES ({', '.join('?' for _ in columns)})",
                        tuple(columns.values()))
        return {**columns, "payload": event}

    def events(self, *, candidate_id: str | None = None, hypothesis_id: str | None = None,
               research_run_id: str | None = None) -> list[dict[str, Any]]:
        clauses, values = [], []
        for name, value in (("candidate_id", candidate_id), ("hypothesis_id", hypothesis_id),
                            ("research_run_id", research_run_id)):
            if value is not None:
                clauses.append(f"{name} = ?"); values.append(value)
        query = "SELECT * FROM experiment_ledger" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY event_id"
        with self._connect() as con:
            rows = con.execute(query, values).fetchall()
        return [self._decode(row) for row in rows]

    def candidate_test_count(self, candidate: str) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM experiment_ledger WHERE candidate_id=?", (candidate,)).fetchone()[0])

    def summary(self) -> dict[str, Any]:
        with self._connect() as con:
            total=int(con.execute("SELECT COUNT(*) FROM experiment_ledger").fetchone()[0])
            candidates=int(con.execute("SELECT COUNT(DISTINCT candidate_id) FROM experiment_ledger WHERE candidate_id IS NOT NULL").fetchone()[0])
            hypotheses=int(con.execute("SELECT COUNT(DISTINCT hypothesis_id) FROM experiment_ledger WHERE hypothesis_id IS NOT NULL").fetchone()[0])
            holdout=int(con.execute("SELECT COUNT(*) FROM holdout_access_log WHERE status IN ('ALLOWED','REPEATED_HOLDOUT_ACCESS')").fetchone()[0])
            survivors=int(con.execute("SELECT COUNT(DISTINCT candidate_id) FROM experiment_ledger WHERE stage='STAGE_6_HOLDOUT' AND status='PASS'").fetchone()[0])
        return {"experiments":total,"hypotheses_tested":hypotheses,"unique_candidates_tested":candidates,"holdout_access_count":holdout,"survivor_count":survivors,"survivor_rate":survivors/candidates if candidates else 0}
    def query(self, kind: str, key: str) -> list[dict[str, Any]]:
        return self.events(**({"candidate_id":key} if kind=="candidate" else {"hypothesis_id":key}))

    def holdout_events(self, *, research_cycle_id: str | None = None,
                       candidate_id: str | None = None) -> list[dict[str, Any]]:
        """Read-only protected-access audit query for reports and CLI status."""
        clauses, values = [], []
        if research_cycle_id is not None:
            clauses.append("research_cycle_id=?"); values.append(research_cycle_id)
        if candidate_id is not None:
            clauses.append("candidate_id=?"); values.append(candidate_id)
        query = "SELECT * FROM holdout_access_log" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY event_id"
        with self._connect() as con:
            rows = con.execute(query, values).fetchall()
        decoded = []
        for row in rows:
            item = dict(row)
            if item["result_json"] is not None:
                item["result"] = json.loads(item.pop("result_json"))
            else:
                item.pop("result_json")
                item["result"] = None
            decoded.append(item)
        return decoded

    def holdout_access(self, *, research_run_id: str, research_cycle_id: str,
                       hypothesis_id: str | None, candidate_id: str, dataset_version: str,
                       reason: str, result: Mapping[str, Any] | None = None,
                       override: bool = False, override_reason: str | None = None) -> dict[str, Any]:
        """Record the reveal request; repeated reveals require an explicit reason."""
        blocked = False
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            previous = int(con.execute(
                "SELECT COUNT(*) FROM holdout_access_log WHERE research_cycle_id=? AND candidate_id=? AND status IN ('ALLOWED','REPEATED_HOLDOUT_ACCESS')",
                (research_cycle_id, candidate_id)).fetchone()[0])
            if previous and not override:
                status = "BLOCKED_REPEATED_HOLDOUT_ACCESS"
                con.execute("""INSERT INTO holdout_access_log
                    (research_run_id,research_cycle_id,hypothesis_id,candidate_id,dataset_version,reason,status,override_reason,result_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (research_run_id, research_cycle_id, hypothesis_id, candidate_id, dataset_version,
                     reason, status, None, canonical_json(result) if result is not None else None, utc_now()))
                blocked = True
            if blocked:
                # Leave the transaction normally so the prohibited access itself is immutable evidence.
                pass
            elif previous and (not override_reason):
                raise ValueError("repeated holdout access requires override_reason")
            elif not blocked:
                status = "REPEATED_HOLDOUT_ACCESS" if previous else "ALLOWED"
                con.execute("""INSERT INTO holdout_access_log
                (research_run_id,research_cycle_id,hypothesis_id,candidate_id,dataset_version,reason,status,override_reason,result_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (research_run_id, research_cycle_id, hypothesis_id, candidate_id, dataset_version,
                 reason, status, override_reason, canonical_json(result) if result is not None else None, utc_now()))
        # Separate ledger event makes protected access visible in normal reports.
        self.append_experiment({"research_run_id": research_run_id, "research_cycle_id": research_cycle_id,
                                "hypothesis_id": hypothesis_id, "candidate_id": candidate_id, "stage": "STAGE_6_HOLDOUT",
                                "status": status, "holdout_access": True, "dataset_version": dataset_version,
                                "rejection_reason": None if status != "REPEATED_HOLDOUT_ACCESS" else status,
                                "reason": reason, "override_reason": override_reason, "metrics": result or {}})
        if blocked:
            raise HoldoutAccessBlocked(
                f"holdout already revealed for {research_cycle_id}/{candidate_id}; explicit override required")
        return {"status": status, "previous_accesses": previous}

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item
