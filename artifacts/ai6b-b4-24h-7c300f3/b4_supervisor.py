from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, "/app")

from dashboard.ai_market_analysis.live_provider_guard import trip
from dashboard.ai_market_analysis.presentation import build_report_presentation
from dashboard.ai_market_analysis.report_audit_jobs import queue_audit
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository, freeze_report_bundle
from dashboard.ai_market_analysis.report_repository import ReportRepository


DATABASE = "/var/lib/ai-report/ai_market_reports.db"
EVENTS = Path("/evidence/events.jsonl")
RESULT = Path("/evidence/result.json")
ADMIN_TOKEN = Path("/run/secrets/admin_token")
PAPER_API = "http://paper-api:8765"
POLL_SECONDS = 5
PIPELINE_TIMEOUT_SECONDS = 20 * 60
CASES = (
    (0, "ETH-USDT-SWAP", "QUICK", "NONE"),
    (4 * 3600, "BTC-USDT-SWAP", "FULL", "NONE"),
    (8 * 3600, "SOL-USDT-SWAP", "QUICK", "NONE"),
    (12 * 3600, "ETH-USDT-SWAP", "FULL", "PAPER"),
    (16 * 3600, "BTC-USDT-SWAP", "QUICK", "NONE"),
    (20 * 3600, "SOL-USDT-SWAP", "FULL", "PAPER"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit(event: str, **values: object) -> None:
    payload = {"utc": utc_now(), "event": event, **values}
    with EVENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def fail(code: str, **values: object) -> int:
    for path in ("/var/lib/ai-report/live-provider-disabled.json", "/var/lib/ai-report/ai6b-kill-switch.json"):
        trip(code, path=path, evidence_id=str(values.get("request_id") or "B4"))
    payload = {"status": "AI6B_B4_FAILED", "failure_code": code, "failed_at_utc": utc_now(), **values}
    RESULT.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    emit("B4_FAILED", **payload)
    return 1


def submit_once(instrument: str, mode: str, position_source: str) -> str:
    decision = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0)
    body = {
        "instrument": instrument,
        "decision_time": decision.isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "language": "zh-CN",
        "position_source": position_source,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
    }
    token = ADMIN_TOKEN.read_text(encoding="utf-8").strip()
    request = Request(
        PAPER_API + "/api/ai-market-analysis/v1/reports",
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=120) as response:
        value = json.loads(response.read())
        if response.status != 202 or not value.get("request_id"):
            raise RuntimeError("REPORT_SUBMISSION_NOT_ACCEPTED")
    request_id = str(value["request_id"])
    emit("REQUEST_SUBMITTED", request_id=request_id, instrument=instrument, mode=mode, position_source=position_source)
    return request_id


def complete_pipeline(request_id: str) -> dict[str, object]:
    reports = ReportRepository(DATABASE)
    audits = AuditRepository(DATABASE)
    deadline = time.monotonic() + PIPELINE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = reports.status(request_id)
        if status["status"] == "COMPLETED":
            break
        if status["status"] in {"FAILED_FINAL", "VALIDATION_FAILED", "BUDGET_BLOCKED", "CANCELLED"}:
            raise RuntimeError("REPORT_" + status["status"])
        time.sleep(POLL_SECONDS)
    else:
        raise RuntimeError("REPORT_PIPELINE_TIMEOUT")

    report = reports.get_report(request_id=request_id)
    if not report:
        raise RuntimeError("REPORT_MISSING_AFTER_COMPLETION")
    frozen_id = audits.freeze_input(freeze_report_bundle(reports, report["report_id"]))
    queued = queue_audit(audits, report["report_id"])
    emit("AUDIT_QUEUED", request_id=request_id, report_id=report["report_id"], audit_input_id=frozen_id, queue_status=queued["status"])

    while time.monotonic() < deadline:
        audit = audits.latest(report["report_id"])
        if audit and audit["status"] in {"PASSED", "FAILED"}:
            break
        time.sleep(POLL_SECONDS)
    else:
        raise RuntimeError("AUDIT_PIPELINE_TIMEOUT")
    if not audit or audit["status"] != "PASSED":
        raise RuntimeError("AUDIT_FAILED")

    started = time.perf_counter()
    request = reports.status(request_id)
    presentation = build_report_presentation(
        reports, report["report_id"], instrument=request["instrument"], mode=request["mode"]
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if presentation.get("report") is None or presentation.get("eligibility") != "AUDIT_PASSED_SHADOW_ONLY":
        raise RuntimeError("PRESENTATION_FAILED")
    with reports.connect() as connection:
        attempts = connection.execute(
            "SELECT COUNT(*) FROM ai_report_attempts WHERE request_id=?", (request_id,)
        ).fetchone()[0]
    if attempts != 1:
        raise RuntimeError("AUTOMATIC_RETRY_OR_DUPLICATE_ATTEMPT")
    return {
        "request_id": request_id,
        "report_id": report["report_id"],
        "audit_id": audit["audit_id"],
        "presentation_id": presentation["presentation_id"],
        "presentation_latency_ms": latency_ms,
        "audit_score": audit["overall_score"],
    }


def main() -> int:
    start_epoch = int(os.environ["B4_START_EPOCH"])
    end_epoch = int(os.environ["B4_END_EPOCH"])
    start_utc = os.environ["B4_START_UTC"]
    end_utc = os.environ["B4_END_EARLIEST_UTC"]
    emit("B4_STARTED", start_utc=start_utc, end_earliest_utc=end_utc, source_commit="7c300f37234b9081079f9bde354a068069c0ef6e")
    passed: list[dict[str, object]] = []
    for offset, instrument, mode, position_source in CASES:
        due = start_epoch + offset
        while time.time() < due:
            time.sleep(min(30, due - time.time()))
        try:
            request_id = submit_once(instrument, mode, position_source)
            result = complete_pipeline(request_id)
        except Exception as error:
            return fail(type(error).__name__ + ":" + str(error), instrument=instrument, mode=mode, position_source=position_source)
        passed.append({"instrument": instrument, "mode": mode, "position_source": position_source, **result})
        emit("CASE_PASSED", **passed[-1])
    while time.time() < end_epoch:
        time.sleep(min(30, end_epoch - time.time()))
    payload = {
        "status": "AI6B_B4_WINDOW_COMPLETED",
        "start_utc": start_utc,
        "end_utc": utc_now(),
        "end_earliest_utc": end_utc,
        "source_commit": "7c300f37234b9081079f9bde354a068069c0ef6e",
        "cases": passed,
    }
    RESULT.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    emit("B4_WINDOW_COMPLETED", case_count=len(passed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
