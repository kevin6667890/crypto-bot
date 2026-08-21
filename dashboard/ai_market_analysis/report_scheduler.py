"""Bounded production scheduler for audited AI-6B QUICK reports."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .report_api import submit_report
from .report_repository import ReportRepository


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

    def _instruments(self) -> tuple[str, ...]:
        values = tuple(item.strip() for item in os.getenv(
            "AI_REPORT_SCHEDULER_INSTRUMENTS", "ETH-USDT-SWAP"
        ).split(",") if item.strip())
        return values or ("ETH-USDT-SWAP",)

    def _cadence(self) -> int:
        return max(60, int(os.getenv("AI_REPORT_SCHEDULER_CADENCE_SECONDS", "3600")))

    def _last_submission(self, instrument: str) -> datetime | None:
        with self.repository.connect() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) FROM ai_report_requests "
                "WHERE instrument=? AND mode='QUICK' AND language='zh-CN' "
                "AND provider=? AND model=?",
                (instrument, os.getenv("AI_REPORT_SCHEDULER_PROVIDER", "deepseek"),
                 os.getenv("AI_REPORT_SCHEDULER_MODEL", "deepseek-v4-flash")),
            ).fetchone()
        return _parse(row[0] if row else None)

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
        return {
            "enabled": _enabled("AI_REPORT_SCHEDULER_ENABLED"),
            "cadence_seconds": self._cadence(),
            "instruments": list(self._instruments()),
            "last_tick": self.last_tick,
            "next_tick": self.next_tick,
            "last_queued": self.last_queued or telemetry["last_successful_queue"],
            "last_error": self.last_error,
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
        due_at: list[datetime] = []
        for instrument in self._instruments():
            previous = self._last_submission(instrument)
            due = previous is None or now >= previous + timedelta(seconds=cadence)
            due_at.append((previous + timedelta(seconds=cadence)) if previous else now)
            if not due:
                continue
            try:
                result = submit_report({
                    "instrument": instrument,
                    "decision_time": now.isoformat().replace("+00:00", "Z"),
                    "mode": "QUICK", "language": "zh-CN", "position_source": "NONE",
                    "provider": os.getenv("AI_REPORT_SCHEDULER_PROVIDER", "deepseek"),
                    "model": os.getenv("AI_REPORT_SCHEDULER_MODEL", "deepseek-v4-flash"),
                }, self.repository, self.paper_db, self.micro_db)
                if result.get("created"):
                    self.last_queued = self.last_tick
                    due_at[-1] = now + timedelta(seconds=cadence)
                break
            except Exception as error:  # Sanitized runtime state only.
                self.last_error = type(error).__name__
                break
        self.next_tick = min(due_at, default=now + timedelta(seconds=cadence)).isoformat().replace("+00:00", "Z")
        return self.state()
