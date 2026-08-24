"""Bounded production scheduler for audited AI-6B QUICK reports."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .report_api import build_base_context_from_stores, submit_report
from .report_material_gate import (
    AI_REPORT_MATERIAL_FINGERPRINT_VERSION,
    material_fingerprint,
)
from .report_repository import ReportRepository


CONFIRMED_4H_SECONDS = 4 * 60 * 60
AUTOMATIC_SCHEDULER_MODE = "CONFIRMED_4H_CLOSE"


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").lower() == "true"


def _iso_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ReportScheduler:
    """Queue at most one due request per tick; workers own provider execution."""

    def __init__(self, repository: ReportRepository, paper_db: str | Path,
                 micro_db: str | Path | None) -> None:
        self.repository, self.paper_db, self.micro_db = repository, str(paper_db), micro_db
        self.last_tick: str | None = None
        self.last_queued: str | None = None
        self.last_error: str | None = None
        self.next_tick: str | None = None
        self.last_snapshot_identity: str | None = None
        self.last_material_fingerprint: str | None = None
        self.last_evaluation_outcome: str | None = None
        self.last_evaluation_at: str | None = None
        self.facts_as_of: str | None = None
        self._owner = f"scheduler-{uuid.uuid4()}"
        self._attempted_boundaries: set[tuple[str, str]] = set()
        self._retry_after: dict[tuple[str, str], datetime] = {}

    def _instruments(self) -> tuple[str, ...]:
        values = tuple(item.strip() for item in os.getenv(
            "AI_REPORT_SCHEDULER_INSTRUMENTS", "ETH-USDT-SWAP"
        ).split(",") if item.strip())
        return values or ("ETH-USDT-SWAP",)

    def _cadence(self) -> int:
        # Automatic generation is intentionally tied to the confirmed 4H
        # market boundary. A stale hourly deployment variable cannot silently
        # restore 24 LLM reports/day.
        return CONFIRMED_4H_SECONDS

    def _grace_seconds(self) -> int:
        return max(0, min(900, int(os.getenv(
            "AI_REPORT_SCHEDULER_CONFIRMATION_GRACE_SECONDS", "120"
        ))))

    def _eligible_close(self, now: datetime) -> datetime:
        effective = now - timedelta(seconds=self._grace_seconds())
        epoch = int(effective.timestamp())
        return datetime.fromtimestamp(
            epoch - epoch % CONFIRMED_4H_SECONDS, timezone.utc
        )

    def _boundary_submitted(self, instrument: str, decision_time: str) -> bool:
        with self.repository.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM ai_report_requests r "
                "JOIN ai_market_contexts c ON c.context_id=r.context_id "
                "WHERE r.instrument=? AND r.mode='QUICK' AND r.language='zh-CN' "
                "AND r.provider=? AND r.model=? AND c.decision_time=? LIMIT 1",
                (instrument, os.getenv("AI_REPORT_SCHEDULER_PROVIDER", "deepseek"),
                 os.getenv("AI_REPORT_SCHEDULER_MODEL", "deepseek-v4-flash"),
                 decision_time),
            ).fetchone()
        return row is not None

    def _staleness(self, now: datetime) -> list[dict[str, Any]]:
        """Expose cadence-relative display freshness through the health plane.

        A failed report remains fail-closed, but it must not leave an old valid
        snapshot silently presented forever.  This intentionally reads only the
        current scheduler instruments and their latest promoted audit.
        """
        cadence = self._cadence()
        warning_after = cadence * 2
        critical_after = cadence * 4
        values: list[dict[str, Any]] = []
        with self.repository.connect() as conn:
            for instrument in self._instruments():
                row = conn.execute(
                    "SELECT MAX(p.created_at) FROM ai_market_reports p "
                    "JOIN ai_report_requests r ON r.request_id=p.request_id "
                    "JOIN ai_report_audits a ON a.report_id=p.report_id "
                    "WHERE r.instrument=? AND r.mode='QUICK' AND r.language='zh-CN' "
                    "AND a.status='PASSED' AND a.promotion_eligible=1",
                    (instrument,),
                ).fetchone()
                latest = _parse(row[0] if row else None)
                age = int((now - latest).total_seconds()) if latest else None
                status = ("AI_REPORT_STALE_CRITICAL" if age is None or age > critical_after
                          else "AI_REPORT_STALE_WARNING" if age > warning_after
                          else "OK")
                values.append({"instrument": instrument, "last_display_eligible_report": row[0] if row else None,
                               "age_seconds": age, "expected_refresh_interval_seconds": cadence,
                               "warning_after_seconds": warning_after, "critical_after_seconds": critical_after,
                               "status": status})
        return values

    def state(self) -> dict[str, Any]:
        telemetry = {
            "last_queue_attempt": None, "last_successful_queue": None,
            "last_provider_request": None, "last_report_created": None,
            "last_audit_completed": None,
        }
        try:
            with self.repository.connect() as conn:
                telemetry["last_queue_attempt"] = conn.execute(
                    "SELECT MAX(created_at) FROM ai_report_requests"
                ).fetchone()[0]
                telemetry["last_successful_queue"] = conn.execute(
                    "SELECT MAX(created_at) FROM ai_report_request_events WHERE event_type='QUEUED'"
                ).fetchone()[0]
                telemetry["last_provider_request"] = conn.execute(
                    "SELECT MAX(started_at) FROM ai_report_attempts"
                ).fetchone()[0]
                telemetry["last_report_created"] = conn.execute(
                    "SELECT MAX(created_at) FROM ai_market_reports"
                ).fetchone()[0]
                telemetry["last_audit_completed"] = conn.execute(
                    "SELECT MAX(created_at) FROM ai_report_audits"
                ).fetchone()[0]
        except Exception:
            # Scheduler liveness must not depend on optional diagnostics.
            pass
        now = _iso_now()
        try:
            staleness = self._staleness(now)
        except Exception:
            staleness = []
        latest = None
        try:
            latest = self.repository.latest_generation_decision()
        except Exception:
            pass
        last_outcome = self.last_evaluation_outcome or (latest or {}).get("outcome")
        last_fingerprint = self.last_material_fingerprint or (latest or {}).get("material_fingerprint")
        facts_as_of = self.facts_as_of or (latest or {}).get("facts_as_of")
        last_evaluation_at = self.last_evaluation_at or (latest or {}).get("updated_at")
        return {
            "enabled": _enabled("AI_REPORT_SCHEDULER_ENABLED"),
            "scheduler_mode": AUTOMATIC_SCHEDULER_MODE,
            "confirmed_timeframe": "4H",
            "cadence_seconds": self._cadence(),
            "confirmation_grace_seconds": self._grace_seconds(),
            "estimated_automatic_reports_per_day": 6,
            "material_transition_trigger": "DETERMINISTIC_GATE_V1",
            "event_trigger": "DISABLED",
            "material_gate_enabled": True,
            "material_fingerprint_version": AI_REPORT_MATERIAL_FINGERPRINT_VERSION,
            "last_material_fingerprint": last_fingerprint,
            "last_evaluation_outcome": last_outcome,
            "last_evaluation_at": last_evaluation_at,
            "facts_as_of": facts_as_of,
            "instruments": list(self._instruments()),
            "last_tick": self.last_tick,
            "next_tick": self.next_tick,
            "next_evaluation": self.next_tick,
            "last_queued": self.last_queued or telemetry["last_successful_queue"],
            "last_error": self.last_error,
            "last_snapshot_identity": self.last_snapshot_identity,
            "last_scheduler_error": self.last_error,
            "lease_required": False,
            "report_staleness": staleness,
            **telemetry,
        }

    def tick(self) -> dict[str, Any]:
        now = _iso_now()
        self.last_tick = now.isoformat().replace("+00:00", "Z")
        self.last_error = None
        if not _enabled("AI_REPORT_SCHEDULER_ENABLED"):
            self.next_tick = None
            return self.state()
        if not _enabled("AI_MARKET_REPORTS_ENABLED") or not _enabled("AI_REPORT_LIVE_PROVIDER_ENABLED"):
            self.last_error = "REPORTS_OR_LIVE_PROVIDER_DISABLED"
            self.next_tick = (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
            return self.state()
        cadence = self._cadence()
        boundary = self._eligible_close(now)
        decision_time = boundary.isoformat().replace("+00:00", "Z")
        next_due = boundary + timedelta(seconds=cadence + self._grace_seconds())
        for instrument in self._instruments():
            decision = None
            key = (instrument, decision_time)
            if key in self._attempted_boundaries:
                continue
            retry_after = self._retry_after.get(key)
            if retry_after is not None and now < retry_after:
                next_due = min(next_due, retry_after)
                continue
            try:
                payload = {
                    "instrument": instrument,
                    "decision_time": decision_time,
                    "mode": "QUICK", "language": "zh-CN", "position_source": "NONE",
                    "provider": os.getenv("AI_REPORT_SCHEDULER_PROVIDER", "deepseek"),
                    "model": os.getenv("AI_REPORT_SCHEDULER_MODEL", "deepseek-v4-flash"),
                }
                base = build_base_context_from_stores(payload, self.paper_db, self.micro_db)
                material = material_fingerprint(base)
                canonical = base.get("canonical_market_snapshot") or {}
                facts_as_of = str(base.get("latest_confirmed_market_time") or decision_time)
                decision = self.repository.claim_generation_decision(
                    instrument=instrument, confirmed_4h_close=decision_time,
                    material_fingerprint=material["fingerprint"],
                    fingerprint_version=material["version"],
                    canonical_snapshot_identity=canonical.get("snapshot_identity"),
                    facts_as_of=facts_as_of, projection=material["projection"], owner=self._owner,
                    lease_seconds=max(120, int(os.getenv("AI_REPORT_SCHEDULER_CLAIM_SECONDS", "600"))),
                )
                # ``created=False`` is also terminal for this boundary: the
                # immutable request already exists and must not be re-queued.
                self._retry_after.pop(key, None)
                self.last_snapshot_identity = canonical.get("snapshot_identity")
                self.last_material_fingerprint = material["fingerprint"]
                self.last_evaluation_outcome = decision["outcome"]
                self.last_evaluation_at = decision.get("updated_at")
                self.facts_as_of = facts_as_of
                if not decision.get("claimed") or not decision.get("should_generate"):
                    if decision.get("outcome") in {"QUEUED","SKIPPED_NO_MATERIAL_CHANGE"}:
                        self._attempted_boundaries.add(key)
                    continue
                result = submit_report(
                    payload, self.repository, self.paper_db, self.micro_db, base_context=base,
                )
                detail = {"created": bool(result.get("created")),
                          "canonical_snapshot_identity": result.get("canonical_snapshot_identity")}
                if not self.repository.complete_generation_decision(
                    decision["decision_id"], self._owner, outcome="QUEUED",
                    request_id=result["request_id"], detail=detail,
                ):
                    raise RuntimeError("GENERATION_DECISION_CLAIM_LOST")
                self.last_evaluation_outcome = "QUEUED"
                self.last_evaluation_at = self.last_tick
                self._attempted_boundaries.add(key)
                if result.get("created"):
                    self.last_queued = self.last_tick
                break
            except Exception as error:  # Sanitized runtime state only.
                self.last_error = type(error).__name__
                cooldown = max(60, int(os.getenv(
                    "AI_REPORT_SCHEDULER_FAILURE_COOLDOWN_SECONDS", "900"
                )))
                self._retry_after[key] = now + timedelta(seconds=cooldown)
                if decision and decision.get("claimed"):
                    try:
                        self.repository.complete_generation_decision(
                            decision["decision_id"], self._owner, outcome="ERROR",
                            detail={"error_type":type(error).__name__}, retry_seconds=cooldown,
                        )
                    except Exception:
                        pass
                next_due = min(next_due, self._retry_after[key])
                break
        self.next_tick = next_due.isoformat().replace("+00:00", "Z")
        return self.state()
