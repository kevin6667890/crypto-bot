"""One-shot AI-6B B3 smoke runner. Default mode is non-networking dry-run."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.live_attempt_guard import B3ControlLedger, LiveRequestIdentity
from dashboard.ai_market_analysis.live_provider_guard import status as kill_switch_status, trip
from dashboard.ai_market_analysis.presentation import build_report_presentation
from dashboard.ai_market_analysis.provider_cost import PRICE_VERSION, estimate_provider_cost, reconcile_provider_usage
from dashboard.ai_market_analysis.report_audit_jobs import AuditWorker, queue_audit
from dashboard.ai_market_analysis.report_audit_repository import AuditRepository
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import ProviderError
from dashboard.ai_market_analysis.report_repository import ReportRepository


SMOKES = {
    1: ("ETH-USDT-SWAP", "QUICK", "NONE"),
    2: ("ETH-USDT-SWAP", "FULL", "NONE"),
    3: ("BTC-USDT-SWAP", "QUICK", "NONE"),
    4: ("SOL-USDT-SWAP", "QUICK", "NONE"),
    5: ("ETH-USDT-SWAP", "FULL", "PAPER"),
}
REQUIRED_PRECONDITIONS = (
    "B1_PASSED", "B2_PASSED", "ROLLBACK_REHEARSAL_PASSED",
    "OFFICIAL_PROVIDER_AUDIT_FRESH", "BUDGET_VALID", "SECRET_ISOLATION_VALID",
)


class GuardedLiveProvider:
    def __init__(self, request: dict[str, Any], ledger: B3ControlLedger, secret_file: Path,
                 repository: ReportRepository):
        self.request = request
        self.ledger = ledger
        self.repository = repository
        self.inner = DeepSeekAIReportProvider(request["model"], api_key_file=secret_file)
        self.model = self.inner.model
        self.provider_name = self.inner.provider_name
        self.supports_structured_output = self.inner.supports_structured_output
        self.timeout = self.inner.timeout

    def generate(self, provider_request: dict[str, Any]):
        identity = LiveRequestIdentity(
            context_id=self.request["context_id"],
            registry_snapshot_id=self.request["registry_snapshot_id"],
            prompt_identity=self.request["generation_prompt_hash"],
            instrument=self.request["instrument"],
            mode=self.request["mode"],
            position_mode=provider_request["position_source"],
            request_id=self.request["request_id"],
        )
        decision = self.ledger.reserve(
            identity,
            model=self.request["model"],
            predicted_input_tokens=int(provider_request["token_estimate"]),
            maximum_output_tokens=int(provider_request["max_output_tokens"]),
            queue_depth=len(self.repository.queued(11)),
        )
        if not decision["provider_call_allowed"]:
            raise ProviderError(decision["code"], retryable=False, request_body_sent=False,
                                provider_accepted=False, charge_state="FAILED_BEFORE_CHARGE")
        logical_id = decision["logical_request_id"]
        owner = decision["reservation_owner"]
        predicted_cost = estimate_provider_cost(
            model=self.request["model"], input_tokens=int(provider_request["token_estimate"]),
            output_tokens=int(provider_request["max_output_tokens"]), cache_status="UNKNOWN",
            official_price_version=PRICE_VERSION,
        )
        # Conservative evidence boundary: once handed to the HTTP adapter, an
        # interrupted process must assume the request may have left the host.
        self.ledger.mark_request_sent(logical_id, owner)
        try:
            result = self.inner.generate(provider_request)
        except ProviderError as error:
            outcome = error.charge_state
            if outcome not in {"FAILED_AFTER_REQUEST_SENT", "UNKNOWN_CHARGE_STATE"}:
                outcome = "UNKNOWN_CHARGE_STATE"
            self.ledger.finish(logical_id, owner, outcome, {
                "failure_code": error.code,
                "http_status": error.http_status,
                "automatic_retry": False,
            })
            if outcome == "UNKNOWN_CHARGE_STATE":
                trip("DUPLICATE_PROVIDER_CHARGE", path=self.ledger.kill_switch_path,
                     evidence_id=logical_id)
                self.ledger.record_observation(logical_id, "KILL_SWITCH_EVENT",
                                               {"event": "DUPLICATE_PROVIDER_CHARGE"})
            raise
        usage = reconcile_provider_usage(
            predicted_input_tokens=int(provider_request["token_estimate"]),
            predicted_output_tokens=int(provider_request["max_output_tokens"]),
            predicted_cost=predicted_cost, model=self.request["model"], provider_usage=result.usage,
        )
        self.ledger.finish(logical_id, owner, "SUCCEEDED", {
            "provider_request_id": result.provider_request_id or "UNKNOWN",
            "http_status": result.http_status if result.http_status is not None else "UNKNOWN",
            **usage,
        })
        return result


def _emit(status: str, **extra: Any) -> None:
    print(json.dumps({"status": status, **extra}, sort_keys=True, separators=(",", ":")))


def _preconditions(path: str | None) -> tuple[bool, list[str]]:
    if not path:
        return False, list(REQUIRED_PRECONDITIONS)
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [name for name in REQUIRED_PRECONDITIONS if value.get(name) != "PASSED"]
    return not missing, missing


def _trip_for_failure(code: str, path: str, evidence_id: str) -> None:
    mapping = {
        "DUPLICATE_LIVE_PROVIDER_ATTEMPT": "DUPLICATE_PROVIDER_CHARGE",
        "NUMERIC_NOT_IN_REGISTRY": "UNSUPPORTED_NUMERIC_CLAIM",
        "UNSUPPORTED_NUMERIC_CLAIM": "UNSUPPORTED_NUMERIC_CLAIM",
        "UNKNOWN_FACT_REF": "REFERENCE_SUPPORT_FAILURE",
        "UNKNOWN_LEVEL_REF": "REFERENCE_SUPPORT_FAILURE",
        "UNKNOWN_SCENARIO_REF": "REFERENCE_SUPPORT_FAILURE",
        "UNKNOWN_MACRO_REF": "REFERENCE_SUPPORT_FAILURE",
        "REFERENCE_SUPPORT_FAILURE": "REFERENCE_SUPPORT_FAILURE",
        "CONTEXT_ID_MISMATCH": "CONTEXT_MISMATCH",
        "REGISTRY_PROMPT_HASH_MISMATCH": "REGISTRY_MISMATCH",
        "REGISTRY_SNAPSHOT_NOT_FOUND": "REGISTRY_MISMATCH",
        "SCHEMA_FAILURE_NO_PROVIDER_RETRY": "SCHEMA_CORRUPTION",
    }
    event = mapping.get(code)
    if event:
        trip(event, path=path, evidence_id=evidence_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute-live", action="store_false", dest="dry_run")
    parser.add_argument("--allow-live-provider", action="store_true")
    parser.add_argument("--approval-id")
    parser.add_argument("--preconditions")
    parser.add_argument("--smoke-number", type=int, choices=SMOKES)
    parser.add_argument("--database")
    parser.add_argument("--request-id")
    parser.add_argument("--control-ledger")
    parser.add_argument("--kill-switch-file")
    parser.add_argument("--secret-file")
    args = parser.parse_args()

    if not args.approval_id:
        _emit("LIVE_PROVIDER_APPROVAL_REQUIRED", provider_call_attempted=False)
        return 2
    if args.dry_run:
        _emit("DRY_RUN", provider_call_attempted=False, approval_present=True,
              live_provider_allowed=False, smoke_sequence=SMOKES)
        return 0

    gates = {
        "runtime_live_flag": os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "false").lower() == "true",
        "cli_allow_live_provider": args.allow_live_provider,
        "human_approval_id": bool(args.approval_id),
        "budget_guard_configured": bool(args.control_ledger),
        "kill_switch_clear": bool(args.kill_switch_file) and not kill_switch_status(args.kill_switch_file)["live_provider_disabled"],
    }
    failed_gates = [name for name, passed in gates.items() if not passed]
    if failed_gates:
        _emit("LIVE_PROVIDER_GATES_BLOCKED", failed_gates=failed_gates, provider_call_attempted=False)
        return 3
    preconditions_ok, missing = _preconditions(args.preconditions)
    if not preconditions_ok:
        _emit("B3_PRECONDITIONS_BLOCKED", missing=missing, provider_call_attempted=False)
        return 4
    if not all((args.database, args.request_id, args.secret_file, args.smoke_number)):
        _emit("LIVE_PROVIDER_INPUT_REQUIRED", provider_call_attempted=False)
        return 5
    secret_file = Path(args.secret_file)
    if not secret_file.is_file():
        _emit("SECRET_PRESENT=false", provider_call_attempted=False)
        return 6
    secret_exposed = "AI_REPORT_API_KEY" in os.environ
    secret_in_repo = secret_file.resolve().is_relative_to(ROOT.resolve())
    if secret_exposed or secret_in_repo:
        trip("SECRET_EXPOSURE", path=args.kill_switch_file, evidence_id=args.request_id)
        _emit("SECRET_ISOLATION_FAILED", SECRET_PRESENT=True,
              PLAINTEXT_ENV_SECRET_PRESENT=secret_exposed,
              SECRET_PATH_INSIDE_REPOSITORY=secret_in_repo,
              provider_call_attempted=False)
        return 6

    repository = ReportRepository(args.database)
    request = repository.status(args.request_id)
    context = repository.load_context(request["context_id"])
    snapshot = repository.load_registry_snapshot(registry_snapshot_id=request["registry_snapshot_id"])
    smoke_identity = LiveRequestIdentity(
        request["context_id"], request["registry_snapshot_id"], snapshot["prompt_hash"],
        request["instrument"], request["mode"], context["position_context"]["source"], request["request_id"]
    )
    expected = SMOKES[args.smoke_number]
    actual = (request["instrument"], request["mode"], context["position_context"]["source"])
    if actual != expected or actual[2] == "USER_DECLARED":
        if actual[0] != expected[0]:
            trip("WRONG_SYMBOL", path=args.kill_switch_file, evidence_id=args.request_id)
        elif actual[1] != expected[1]:
            trip("WRONG_MODE", path=args.kill_switch_file, evidence_id=args.request_id)
        else:
            trip("UNEXPECTED_POSITION_DATA", path=args.kill_switch_file, evidence_id=args.request_id)
        _emit("SMOKE_IDENTITY_MISMATCH", expected=expected, actual=actual, provider_call_attempted=False)
        return 7
    if args.smoke_number > 2:
        # Later smokes are released only after both gate smokes have immutable,
        # passed audits. Formal precondition evidence must state this explicitly.
        values = json.loads(Path(args.preconditions).read_text(encoding="utf-8"))
        if values.get("SMOKE_1_PASSED") != "PASSED" or values.get("SMOKE_2_PASSED") != "PASSED":
            _emit("PRIOR_SMOKES_REQUIRED", provider_call_attempted=False)
            return 8

    os.environ["AI_REPORT_COST_STATUS"] = "B3_CONTROL_LEDGER"
    os.environ["AI_REPORT_PRICE_SCHEDULE_VERSION"] = PRICE_VERSION
    os.environ["AI_REPORT_INPUT_USD_PER_MILLION"] = "0.14"
    os.environ["AI_REPORT_OUTPUT_USD_PER_MILLION"] = "0.28"
    ledger = B3ControlLedger(args.control_ledger, kill_switch_path=args.kill_switch_file)
    ledger.initialize()
    worker = ReportWorker(repository, lambda value: GuardedLiveProvider(value, ledger, secret_file, repository))
    recovered_unknown = ledger.recover_uncertain_sent()
    worker.recover()
    if recovered_unknown:
        _emit("SMOKE_FAILED_CLOSED", stage="UNKNOWN_CHARGE_STATE_RECOVERY",
              unknown_charge_states=recovered_unknown, presentation_body_allowed=False,
              automatic_retry=False)
        return 9
    worker.run_once()
    request_status = repository.status(args.request_id)
    if request_status["status"] != "COMPLETED":
        latest_payload = json.loads(request_status["events"][-1]["payload_json"]) if request_status["events"] else {}
        _trip_for_failure(str(latest_payload.get("code", "")), args.kill_switch_file, args.request_id)
        _emit("SMOKE_FAILED_CLOSED", stage="PROVIDER_OR_REPORT", request_status=request_status["status"],
              presentation_body_allowed=False, automatic_retry=False)
        return 9
    report = repository.get_report(request_id=args.request_id)
    assert report is not None
    audit_repository = AuditRepository(args.database)
    queue_audit(audit_repository, report["report_id"])
    AuditWorker(audit_repository).run_once()
    audit = audit_repository.latest(report["report_id"])
    if not audit or audit["status"] != "PASSED":
        ledger.record_observation(smoke_identity.logical_request_id, "AUDIT_FAIL")
        trip("AUDIT_MISMATCH", path=args.kill_switch_file, evidence_id=report["report_id"])
        _emit("SMOKE_FAILED_CLOSED", stage="AUDIT", presentation_body_allowed=False)
        return 10
    logical_id = smoke_identity.logical_request_id
    ledger.record_observation(logical_id, "AUDIT_PASS")
    presentation = build_report_presentation(
        repository, report["report_id"], instrument=request["instrument"], mode=request["mode"]
    )
    if presentation.get("report") is None or presentation.get("eligibility") != "AUDIT_PASSED_SHADOW_ONLY":
        ledger.record_observation(logical_id, "PRESENTATION_FAIL")
        trip("AUDIT_MISMATCH", path=args.kill_switch_file, evidence_id=report["report_id"])
        _emit("SMOKE_FAILED_CLOSED", stage="PRESENTATION", presentation_body_allowed=False)
        return 11
    ledger.record_observation(logical_id, "PRESENTATION_PASS")
    _emit("SMOKE_PASSED", smoke_number=args.smoke_number, report_id=report["report_id"],
          audit_id=audit["audit_id"], presentation_id=presentation["presentation_id"],
          metrics=ledger.metrics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
