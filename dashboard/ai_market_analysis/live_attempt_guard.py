"""Durable pre-call budget and single-flight controls for AI-6B B3.

This module owns only an isolated B3 control ledger.  It does not open network
connections and it never reads provider secrets.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .canonical import canonical_json, stable_hash
from .live_provider_guard import status as kill_switch_status, trip as trip_kill_switch
from .provider_cost import PRICE_VERSION, estimate_provider_cost
from .provider_limits import (
    B3_MAX_PAID_ATTEMPTS_TOTAL,
    DAILY_INPUT_TOKEN_SAFETY_CAP,
    DAILY_OUTPUT_TOKEN_SAFETY_CAP,
    OUTPUT_TOKEN_LIMITS,
    REQUEST_INPUT_TOKEN_MAX,
)


ACTIVE_STATES = frozenset({"LIVE_PROVIDER_ATTEMPT_RESERVED", "REQUEST_SENT"})
TERMINAL_STATES = frozenset({
    "SUCCEEDED",
    "FAILED_BEFORE_CHARGE",
    "FAILED_AFTER_REQUEST_SENT",
    "UNKNOWN_CHARGE_STATE",
})
NO_RETRY_STATES = frozenset({"SUCCEEDED", "FAILED_AFTER_REQUEST_SENT", "UNKNOWN_CHARGE_STATE"})


def _technical_token_usage(reservations: list[sqlite3.Row], terminals: list[sqlite3.Row]) -> tuple[int, int]:
    """Count actual terminal usage; reserve ceilings only for unresolved/unknown calls."""
    outcomes: dict[tuple[str, int], tuple[str, dict[str, Any]]] = {}
    for row in terminals:
        outcomes[(str(row[0]), int(row[1]))] = (str(row[2]), json.loads(row[3]))
    input_tokens = output_tokens = 0
    for row in reservations:
        key = (str(row[0]), int(row[1])); predicted = json.loads(row[2])
        terminal = outcomes.get(key)
        if terminal and terminal[0] == "SUCCEEDED":
            input_tokens += int(terminal[1].get("provider_input_tokens", predicted["predicted_input_tokens"]))
            output_tokens += int(terminal[1].get("provider_output_tokens", predicted["predicted_output_tokens"]))
        elif terminal and terminal[0] == "FAILED_BEFORE_CHARGE":
            continue
        else:
            input_tokens += int(predicted["predicted_input_tokens"])
            output_tokens += int(predicted["predicted_output_tokens"])
    return input_tokens, output_tokens


@dataclass(frozen=True)
class BudgetLimits:
    calls_24h: int = B3_MAX_PAID_ATTEMPTS_TOTAL
    global_concurrency: int = 1
    per_instrument_concurrency: int = 1
    queue_max: int = 10
    request_input_tokens: int = REQUEST_INPUT_TOKEN_MAX
    quick_output_tokens: int = OUTPUT_TOKEN_LIMITS["QUICK"]
    full_output_tokens: int = OUTPUT_TOKEN_LIMITS["FULL"]
    position_output_tokens: int = OUTPUT_TOKEN_LIMITS["POSITION_AWARE"]
    daily_input_tokens: int = DAILY_INPUT_TOKEN_SAFETY_CAP
    daily_output_tokens: int = DAILY_OUTPUT_TOKEN_SAFETY_CAP
    currency_cap_usd: Decimal = Decimal("2")
    max_attempts: int = 3


@dataclass(frozen=True)
class LiveRequestIdentity:
    context_id: str
    registry_snapshot_id: str
    prompt_identity: str
    instrument: str
    mode: str
    position_mode: str
    request_id: str

    @property
    def logical_request_id(self) -> str:
        return stable_hash({
            "context_id": self.context_id,
            "registry_snapshot_id": self.registry_snapshot_id,
            "prompt_identity": self.prompt_identity,
            "instrument": self.instrument,
            "mode": self.mode,
            "position_mode": self.position_mode,
            "request_id": self.request_id,
        })


class B3ControlLedger:
    """SQLite-backed, process-safe reservation ledger.

    ``initialize`` is explicit so importing this module cannot write anywhere.
    Formal B3 must point it at the isolated AI report control database.
    """

    def __init__(self, path: str | Path, *, kill_switch_path: str | Path):
        self.path = Path(path)
        self.kill_switch_path = Path(kill_switch_path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ai6b_b3_live_requests(
                  logical_request_id TEXT PRIMARY KEY,
                  identity_json TEXT NOT NULL,
                  request_id TEXT NOT NULL UNIQUE,
                  instrument TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  position_mode TEXT NOT NULL,
                  state TEXT NOT NULL,
                  attempt_number INTEGER NOT NULL,
                  reservation_owner TEXT,
                  predicted_input_tokens INTEGER NOT NULL,
                  predicted_output_tokens INTEGER NOT NULL,
                  predicted_cost_usd TEXT NOT NULL,
                  pricing_source_version TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai6b_b3_attempt_events(
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  logical_request_id TEXT,
                  request_id TEXT NOT NULL,
                  instrument TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  attempt_number INTEGER,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai6b_b3_events_time
                  ON ai6b_b3_attempt_events(created_at,event_type);
                """
            )

    def reserve(
        self,
        identity: LiveRequestIdentity,
        *,
        model: str,
        predicted_input_tokens: int,
        maximum_output_tokens: int,
        queue_depth: int,
        cache_status: str = "UNKNOWN",
        limits: BudgetLimits = BudgetLimits(),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = _as_utc(now)
        estimated = estimate_provider_cost(
            model=model,
            input_tokens=predicted_input_tokens,
            output_tokens=maximum_output_tokens,
            cache_status=cache_status,
            official_price_version=PRICE_VERSION,
        )
        requested_cost = estimated.estimated_total_cost
        selected_mode = identity.mode.upper()
        mode_output_cap = {
            "QUICK": limits.quick_output_tokens,
            "FULL": limits.full_output_tokens,
            "POSITION_AWARE": limits.position_output_tokens,
        }.get(selected_mode)
        static_checks = [
            (predicted_input_tokens > limits.request_input_tokens, "REQUEST_INPUT_TOKEN_CAP"),
            (mode_output_cap is None, "UNAPPROVED_MODE"),
            (mode_output_cap is not None and maximum_output_tokens > mode_output_cap, "REQUEST_OUTPUT_TOKEN_CAP"),
            (queue_depth >= limits.queue_max, "QUEUE_CAP"),
            (kill_switch_status(self.kill_switch_path)["live_provider_disabled"], "KILL_SWITCH_ACTIVE"),
        ]
        for blocked, code in static_checks:
            if blocked:
                return self._budget_blocked(identity, code, now, estimated.as_dict())

        cutoff = _stamp(now - timedelta(hours=24))
        stamp = _stamp(now)
        owner = secrets.token_urlsafe(32)
        logical_id = identity.logical_request_id
        identity_json = canonical_json(identity.__dict__)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM ai6b_b3_live_requests WHERE logical_request_id=? OR request_id=?",
                (logical_id, identity.request_id),
            ).fetchone()
            if existing:
                row = dict(existing)
                if row["logical_request_id"] != logical_id or row["identity_json"] != identity_json:
                    return self._blocked_in_transaction(
                        connection, identity, "REQUEST_IDENTITY_CONFLICT", now, estimated.as_dict()
                    )
                if row["state"] in ACTIVE_STATES or row["state"] in NO_RETRY_STATES:
                    self._event(connection, identity, "DUPLICATE_RESERVATION_PREVENTED", row["attempt_number"],
                                {"state": row["state"]}, stamp)
                    connection.commit()
                    return {
                        "status": "BUDGET_BLOCKED",
                        "code": "DUPLICATE_LIVE_PROVIDER_ATTEMPT",
                        "logical_request_id": logical_id,
                        "existing_state": row["state"],
                        "provider_call_allowed": False,
                    }
                attempt_number = int(row["attempt_number"]) + 1
                if attempt_number > limits.max_attempts:
                    return self._blocked_in_transaction(
                        connection, identity, "MAX_ATTEMPTS", now, estimated.as_dict()
                    )
            else:
                attempt_number = 1

            reservation_rows = connection.execute(
                """SELECT logical_request_id,attempt_number,payload_json FROM ai6b_b3_attempt_events
                   WHERE created_at>=? AND event_type='LIVE_PROVIDER_ATTEMPT_RESERVED'""",
                (cutoff,),
            ).fetchall()
            terminal_rows = connection.execute(
                """SELECT logical_request_id,attempt_number,event_type,payload_json
                   FROM ai6b_b3_attempt_events WHERE created_at>=?
                   AND event_type IN ('SUCCEEDED','FAILED_BEFORE_CHARGE','FAILED_AFTER_REQUEST_SENT','UNKNOWN_CHARGE_STATE')
                   ORDER BY event_id""", (cutoff,),
            ).fetchall()
            reservation_payloads = [json.loads(row[2]) for row in reservation_rows]
            active_global = connection.execute(
                "SELECT COUNT(*) FROM ai6b_b3_live_requests WHERE state IN (?,?)",
                tuple(ACTIVE_STATES),
            ).fetchone()[0]
            active_instrument = connection.execute(
                "SELECT COUNT(*) FROM ai6b_b3_live_requests WHERE instrument=? AND state IN (?,?)",
                (identity.instrument, *tuple(ACTIVE_STATES)),
            ).fetchone()[0]
            daily_cost = sum(
                (Decimal(payload["predicted_cost_usd"]) for payload in reservation_payloads),
                Decimal("0"),
            )
            daily_input, daily_output = _technical_token_usage(reservation_rows, terminal_rows)
            dynamic_checks = [
                (limits.calls_24h > 0 and len(reservation_payloads) + 1 > limits.calls_24h, "DAILY_CALL_CAP"),
                (daily_input + predicted_input_tokens > limits.daily_input_tokens, "DAILY_INPUT_TOKEN_CAP"),
                (daily_output + maximum_output_tokens > limits.daily_output_tokens, "DAILY_OUTPUT_TOKEN_CAP"),
                (daily_cost + requested_cost > limits.currency_cap_usd, "DAILY_CURRENCY_CAP"),
                (int(active_global) >= limits.global_concurrency, "GLOBAL_CONCURRENCY_CAP"),
                (int(active_instrument) >= limits.per_instrument_concurrency, "INSTRUMENT_CONCURRENCY_CAP"),
            ]
            for blocked, code in dynamic_checks:
                if blocked:
                    return self._blocked_in_transaction(connection, identity, code, now, estimated.as_dict())

            values = (
                logical_id, identity_json, identity.request_id, identity.instrument, selected_mode,
                identity.position_mode, "LIVE_PROVIDER_ATTEMPT_RESERVED", attempt_number, owner,
                predicted_input_tokens, maximum_output_tokens, estimated.as_dict()["estimated_total_cost"],
                PRICE_VERSION, stamp, stamp,
            )
            if existing:
                connection.execute(
                    """UPDATE ai6b_b3_live_requests SET state=?,attempt_number=?,reservation_owner=?,
                       predicted_input_tokens=?,predicted_output_tokens=?,predicted_cost_usd=?,
                       pricing_source_version=?,updated_at=? WHERE logical_request_id=?""",
                    ("LIVE_PROVIDER_ATTEMPT_RESERVED", attempt_number, owner, predicted_input_tokens,
                     maximum_output_tokens, estimated.as_dict()["estimated_total_cost"], PRICE_VERSION,
                     stamp, logical_id),
                )
            else:
                connection.execute(
                    "INSERT INTO ai6b_b3_live_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
                )
            self._event(connection, identity, "LIVE_PROVIDER_ATTEMPT_RESERVED", attempt_number, {
                "predicted_input_tokens": predicted_input_tokens,
                "predicted_output_tokens": maximum_output_tokens,
                "predicted_cost_usd": estimated.as_dict()["estimated_total_cost"],
                "pricing_source_version": PRICE_VERSION,
            }, stamp)
            connection.commit()
        return {
            "status": "LIVE_PROVIDER_ATTEMPT_RESERVED",
            "logical_request_id": logical_id,
            "attempt_number": attempt_number,
            "reservation_owner": owner,
            "predicted_cost": estimated.as_dict(),
            "provider_call_allowed": True,
        }

    def mark_request_sent(self, logical_request_id: str, owner: str) -> None:
        self._transition(logical_request_id, owner, "REQUEST_SENT", {"request_body_sent": True})

    def finish(self, logical_request_id: str, owner: str, outcome: str, payload: dict[str, Any] | None = None) -> None:
        if outcome not in TERMINAL_STATES:
            raise ValueError("INVALID_ATTEMPT_OUTCOME")
        self._transition(logical_request_id, owner, outcome, payload or {})

    def record_observation(self, logical_request_id: str, event: str,
                           payload: dict[str, Any] | None = None) -> None:
        if event not in {"AUDIT_PASS", "AUDIT_FAIL", "PRESENTATION_PASS", "PRESENTATION_FAIL", "KILL_SWITCH_EVENT"}:
            raise ValueError("INVALID_OBSERVATION_EVENT")
        stamp = _stamp(_as_utc(None))
        with self._connect() as connection:
            row = connection.execute(
                "SELECT identity_json,attempt_number FROM ai6b_b3_live_requests WHERE logical_request_id=?",
                (logical_request_id,),
            ).fetchone()
            if not row:
                raise KeyError("LIVE_REQUEST_NOT_FOUND")
            self._event(connection, LiveRequestIdentity(**json.loads(row[0])), event,
                        int(row[1]), payload or {}, stamp)

    def recover_uncertain_sent(self) -> int:
        """On process restart, sent-but-unfinished requests become non-retryable."""
        stamp = _stamp(_as_utc(None))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM ai6b_b3_live_requests WHERE state='REQUEST_SENT'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE ai6b_b3_live_requests SET state='UNKNOWN_CHARGE_STATE',updated_at=? WHERE logical_request_id=?",
                    (stamp, row["logical_request_id"]),
                )
                identity = LiveRequestIdentity(**json.loads(row["identity_json"]))
                self._event(connection, identity, "UNKNOWN_CHARGE_STATE", int(row["attempt_number"]),
                            {"failure_code": "WORKER_RESTART_AFTER_REQUEST_SENT", "automatic_retry": False}, stamp)
                self._event(connection, identity, "KILL_SWITCH_EVENT", int(row["attempt_number"]),
                            {"event": "DUPLICATE_PROVIDER_CHARGE"}, stamp)
            connection.commit()
        if rows:
            trip_kill_switch("DUPLICATE_PROVIDER_CHARGE", path=self.kill_switch_path,
                             evidence_id=str(rows[0]["logical_request_id"]))
        return len(rows)

    def metrics(self, *, now: datetime | None = None) -> dict[str, Any]:
        cutoff = _stamp(_as_utc(now) - timedelta(hours=24))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type,COUNT(*) FROM ai6b_b3_attempt_events WHERE created_at>=? GROUP BY event_type",
                (cutoff,),
            ).fetchall()
            counts = {row[0]: int(row[1]) for row in rows}
            reservation_rows = connection.execute(
                """SELECT payload_json FROM ai6b_b3_attempt_events
                   WHERE created_at>=? AND event_type='LIVE_PROVIDER_ATTEMPT_RESERVED'""", (cutoff,)
            ).fetchall()
            retry_count = connection.execute(
                """SELECT COUNT(*) FROM ai6b_b3_attempt_events
                   WHERE created_at>=? AND event_type='LIVE_PROVIDER_ATTEMPT_RESERVED' AND attempt_number>1""",
                (cutoff,),
            ).fetchone()[0]
            succeeded_rows = connection.execute(
                """SELECT payload_json FROM ai6b_b3_attempt_events
                   WHERE created_at>=? AND event_type='SUCCEEDED'""", (cutoff,)
            ).fetchall()
            failed_rows = connection.execute(
                """SELECT payload_json FROM ai6b_b3_attempt_events
                   WHERE created_at>=? AND event_type IN ('FAILED_BEFORE_CHARGE','FAILED_AFTER_REQUEST_SENT','UNKNOWN_CHARGE_STATE')""",
                (cutoff,),
            ).fetchall()
        reservations = [json.loads(row[0]) for row in reservation_rows]
        successes = [json.loads(row[0]) for row in succeeded_rows]
        failures = [json.loads(row[0]) for row in failed_rows]
        provider_inputs = [row.get("provider_input_tokens", "UNKNOWN") for row in successes]
        provider_outputs = [row.get("provider_output_tokens", "UNKNOWN") for row in successes]
        reconciled = [row.get("reconciled_cost", "UNKNOWN") for row in successes]
        failure_codes = [str(row.get("failure_code", "")) for row in failures]
        return {
            "metrics_schema_version": "ai6b-b3-provider-metrics-v1",
            "provider_requests": counts.get("REQUEST_SENT", 0),
            "successful_calls": counts.get("SUCCEEDED", 0),
            "failed_calls": counts.get("FAILED_BEFORE_CHARGE", 0) + counts.get("FAILED_AFTER_REQUEST_SENT", 0) + counts.get("UNKNOWN_CHARGE_STATE", 0),
            "reserved_attempts": counts.get("LIVE_PROVIDER_ATTEMPT_RESERVED", 0),
            "duplicate_reservation_prevented": counts.get("DUPLICATE_RESERVATION_PREVENTED", 0),
            "unknown_charge_states": counts.get("UNKNOWN_CHARGE_STATE", 0),
            "input_tokens": sum(provider_inputs) if all(isinstance(value, int) for value in provider_inputs) else "UNKNOWN",
            "output_tokens": sum(provider_outputs) if all(isinstance(value, int) for value in provider_outputs) else "UNKNOWN",
            "predicted_input_tokens": sum(int(row["predicted_input_tokens"]) for row in reservations),
            "predicted_output_tokens": sum(int(row["predicted_output_tokens"]) for row in reservations),
            "predicted_cost": format(sum((Decimal(row["predicted_cost_usd"]) for row in reservations), Decimal("0")), "f"),
            "reconciled_cost": format(sum((Decimal(value) for value in reconciled), Decimal("0")), "f") if all(value != "UNKNOWN" for value in reconciled) else "UNKNOWN",
            "401": failure_codes.count("HTTP_401"),
            "403": failure_codes.count("HTTP_403"),
            "429": failure_codes.count("HTTP_429"),
            "5xx": sum(code.startswith("HTTP_5") for code in failure_codes),
            "timeout": sum(code in {"TIMEOUT", "CONNECTION_OR_TIMEOUT"} for code in failure_codes),
            "retry": int(retry_count),
            "queue_depth": "COLLECTED_AT_RUNTIME",
            "audit_pass": counts.get("AUDIT_PASS", 0),
            "audit_fail": counts.get("AUDIT_FAIL", 0),
            "presentation_pass": counts.get("PRESENTATION_PASS", 0),
            "presentation_fail": counts.get("PRESENTATION_FAIL", 0),
            "budget_blocked": counts.get("BUDGET_BLOCKED", 0),
            "kill_switch_events": counts.get("KILL_SWITCH_EVENT", 0),
        }

    def _transition(self, logical_id: str, owner: str, outcome: str, payload: dict[str, Any]) -> None:
        stamp = _stamp(_as_utc(None))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM ai6b_b3_live_requests WHERE logical_request_id=?", (logical_id,)
            ).fetchone()
            if not row or row["reservation_owner"] != owner:
                connection.rollback()
                raise PermissionError("RESERVATION_OWNER_MISMATCH")
            current = row["state"]
            allowed = (
                current == "LIVE_PROVIDER_ATTEMPT_RESERVED" and outcome in {"REQUEST_SENT", "FAILED_BEFORE_CHARGE"}
            ) or (
                current == "REQUEST_SENT" and outcome in {
                    "SUCCEEDED", "FAILED_AFTER_REQUEST_SENT", "UNKNOWN_CHARGE_STATE"
                }
            )
            if not allowed:
                connection.rollback()
                raise ValueError("INVALID_ATTEMPT_STATE_TRANSITION")
            connection.execute(
                "UPDATE ai6b_b3_live_requests SET state=?,updated_at=? WHERE logical_request_id=?",
                (outcome, stamp, logical_id),
            )
            identity = LiveRequestIdentity(**json.loads(row["identity_json"]))
            self._event(connection, identity, outcome, int(row["attempt_number"]), payload, stamp)
            connection.commit()

    def _budget_blocked(self, identity: LiveRequestIdentity, code: str, now: datetime,
                        estimate: dict[str, str]) -> dict[str, Any]:
        if self.path.exists():
            with self._connect() as connection:
                self._event(connection, identity, "BUDGET_BLOCKED", None, {"code": code}, _stamp(now))
        return {
            "status": "BUDGET_BLOCKED",
            "code": code,
            "logical_request_id": identity.logical_request_id,
            "predicted_cost": estimate,
            "provider_call_allowed": False,
        }

    def _blocked_in_transaction(self, connection: sqlite3.Connection, identity: LiveRequestIdentity,
                                code: str, now: datetime, estimate: dict[str, str]) -> dict[str, Any]:
        self._event(connection, identity, "BUDGET_BLOCKED", None, {"code": code}, _stamp(now))
        connection.commit()
        return {
            "status": "BUDGET_BLOCKED",
            "code": code,
            "logical_request_id": identity.logical_request_id,
            "predicted_cost": estimate,
            "provider_call_allowed": False,
        }

    @staticmethod
    def _event(connection: sqlite3.Connection, identity: LiveRequestIdentity, event: str,
               attempt: int | None, payload: dict[str, Any], stamp: str) -> None:
        connection.execute(
            """INSERT INTO ai6b_b3_attempt_events(
                 logical_request_id,request_id,instrument,event_type,attempt_number,payload_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (identity.logical_request_id, identity.request_id, identity.instrument, event, attempt,
             canonical_json(payload), stamp),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection


def _as_utc(value: datetime | None) -> datetime:
    selected = value or datetime.now(timezone.utc)
    if selected.tzinfo is None:
        raise ValueError("NAIVE_DATETIME_FORBIDDEN")
    return selected.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def retry_decision(
    *,
    attempt_number: int,
    request_body_sent: bool,
    provider_accepted: bool | None,
    failure_code: str,
    evidence_provider_did_not_accept: bool = False,
) -> dict[str, Any]:
    """Charge-safe retry state machine; uncertainty always wins over availability."""
    if attempt_number >= 3:
        return {"retry": False, "state": "FAILED_BEFORE_CHARGE" if not request_body_sent else "UNKNOWN_CHARGE_STATE",
                "code": "MAX_ATTEMPTS"}
    if not request_body_sent:
        return {"retry": True, "state": "FAILED_BEFORE_CHARGE", "code": failure_code}
    if evidence_provider_did_not_accept and provider_accepted is False:
        return {"retry": True, "state": "FAILED_BEFORE_CHARGE", "code": failure_code}
    if failure_code in {"HTTP_401", "HTTP_403"}:
        return {"retry": False, "state": "FAILED_AFTER_REQUEST_SENT", "code": failure_code}
    return {"retry": False, "state": "UNKNOWN_CHARGE_STATE", "code": failure_code}
