from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError

import pytest

from dashboard.ai_market_analysis.b3_acceptance import (
    acceptance_gate,
    evaluate_stub_fixture,
    validate_numeric_grounding,
    validate_reference_support,
)
from dashboard.ai_market_analysis.deepseek_report_provider import DeepSeekAIReportProvider
from dashboard.ai_market_analysis.live_attempt_guard import (
    B3ControlLedger,
    BudgetLimits,
    LiveRequestIdentity,
    retry_decision,
)
from dashboard.ai_market_analysis.live_provider_guard import trip
from dashboard.ai_market_analysis.provider_cost import (
    PRICE_VERSION,
    estimate_provider_cost,
    reconcile_provider_usage,
)
from dashboard.ai_market_analysis.provider_limits import (
    OUTPUT_TOKEN_LIMITS, PROVIDER_CONTEXT_TOKEN_MAX, PROVIDER_RESPONSE_BYTES_MAX,
    PROVIDER_TIMEOUT_SECONDS,
)
from dashboard.ai_market_analysis.report_provider import ProviderError
from dashboard.ai_market_analysis.secret_leak_scanner import scan_repository


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 11, 2, 2, 35, tzinfo=timezone.utc)


def identity(number: int = 1, instrument: str = "ETH-USDT-SWAP") -> LiveRequestIdentity:
    return LiveRequestIdentity(
        context_id=f"context-{number}",
        registry_snapshot_id=f"registry-{number}",
        prompt_identity=f"prompt-{number}",
        instrument=instrument,
        mode="QUICK",
        position_mode="NONE",
        request_id=f"request-{number}",
    )


def ledger(tmp_path: Path) -> B3ControlLedger:
    value = B3ControlLedger(tmp_path / "b3-control.db", kill_switch_path=tmp_path / "kill.json")
    value.initialize()
    return value


@pytest.mark.parametrize(
    ("model", "cache", "expected"),
    [
        ("deepseek-v4-flash", "MISS", "0.002800000000"),
        ("deepseek-v4-flash", "HIT", "0.001428000000"),
        ("deepseek-v4-pro", "MISS", "0.008700000000"),
        ("deepseek-v4-pro", "HIT", "0.004386250000"),
    ],
)
def test_cost_golden_exact_decimal(model, cache, expected):
    # 10k input + 5k output. Expected values include both components.
    value = estimate_provider_cost(
        model=model, input_tokens=10_000, output_tokens=5_000,
        cache_status=cache, official_price_version=PRICE_VERSION,
    )
    assert value.as_dict()["estimated_total_cost"] == expected
    assert isinstance(value.estimated_total_cost, Decimal)


def test_unknown_cache_is_conservative_and_price_version_is_pinned():
    unknown = estimate_provider_cost(
        model="deepseek-v4-flash", input_tokens=100_000, output_tokens=25_000,
        cache_status="UNKNOWN", official_price_version=PRICE_VERSION,
    )
    assert unknown.as_dict()["estimated_total_cost"] == "0.021000000000"
    with pytest.raises(ValueError, match="UNKNOWN_PRICE_VERSION"):
        estimate_provider_cost(model="deepseek-v4-flash", input_tokens=1, output_tokens=1,
                               cache_status="MISS", official_price_version="stale")


def test_usage_reconciliation_keeps_prediction_and_unknowns():
    predicted = estimate_provider_cost(
        model="deepseek-v4-flash", input_tokens=100, output_tokens=20,
        cache_status="UNKNOWN", official_price_version=PRICE_VERSION,
    )
    absent = reconcile_provider_usage(
        predicted_input_tokens=100, predicted_output_tokens=20, predicted_cost=predicted,
        model="deepseek-v4-flash", provider_usage=None,
    )
    assert absent["predicted_input_tokens"] == 100
    assert absent["provider_input_tokens"] == "UNKNOWN"
    assert absent["reconciled_cost"] == "UNKNOWN"
    reported = reconcile_provider_usage(
        predicted_input_tokens=100, predicted_output_tokens=20, predicted_cost=predicted,
        model="deepseek-v4-flash",
        provider_usage={"prompt_tokens": 90, "completion_tokens": 10,
                        "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 50},
    )
    assert reported["predicted_input_tokens"] == 100
    assert reported["provider_input_tokens"] == 90
    assert reported["reconciled_cost"] != "UNKNOWN"


def test_atomic_reservation_blocks_duplicate_and_concurrency(tmp_path):
    store = ledger(tmp_path)
    first = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                          maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert first["status"] == "LIVE_PROVIDER_ATTEMPT_RESERVED"
    duplicate = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                              maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert duplicate == {**duplicate, "provider_call_allowed": False}
    assert duplicate["code"] == "DUPLICATE_LIVE_PROVIDER_ATTEMPT"
    second = store.reserve(identity(2, "BTC-USDT-SWAP"), model="deepseek-v4-flash",
                           predicted_input_tokens=100, maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert second["code"] == "GLOBAL_CONCURRENCY_CAP"
    assert store.metrics(now=NOW)["duplicate_reservation_prevented"] == 1


def test_failed_before_charge_is_only_retryable_terminal(tmp_path):
    store = ledger(tmp_path)
    first = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                          maximum_output_tokens=50, queue_depth=0, now=NOW)
    store.finish(first["logical_request_id"], first["reservation_owner"], "FAILED_BEFORE_CHARGE")
    second = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                           maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert second["attempt_number"] == 2
    store.mark_request_sent(second["logical_request_id"], second["reservation_owner"])
    store.finish(second["logical_request_id"], second["reservation_owner"], "UNKNOWN_CHARGE_STATE")
    blocked = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                            maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert blocked["provider_call_allowed"] is False
    assert blocked["existing_state"] == "UNKNOWN_CHARGE_STATE"


def test_worker_restart_converts_sent_attempt_to_unknown_without_retry(tmp_path):
    store = ledger(tmp_path)
    reserved = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                             maximum_output_tokens=50, queue_depth=0, now=NOW)
    store.mark_request_sent(reserved["logical_request_id"], reserved["reservation_owner"])
    assert store.recover_uncertain_sent() == 1
    blocked = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                            maximum_output_tokens=50, queue_depth=0, now=NOW)
    assert blocked["code"] == "KILL_SWITCH_ACTIVE"
    assert blocked["provider_call_allowed"] is False


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"predicted_input_tokens": 500_001, "maximum_output_tokens": 1, "queue_depth": 0}, "REQUEST_INPUT_TOKEN_CAP"),
        ({"predicted_input_tokens": 1, "maximum_output_tokens": 200001, "queue_depth": 0}, "REQUEST_OUTPUT_TOKEN_CAP"),
        ({"predicted_input_tokens": 1, "maximum_output_tokens": 1, "queue_depth": 10}, "QUEUE_CAP"),
    ],
)
def test_pre_call_budget_blocks_structurally(tmp_path, kwargs, code):
    store = ledger(tmp_path)
    value = store.reserve(identity(), model="deepseek-v4-flash", now=NOW, **kwargs)
    assert value["status"] == "BUDGET_BLOCKED"
    assert value["code"] == code
    assert value["provider_call_allowed"] is False


def test_currency_cap_and_kill_switch_block_before_reservation(tmp_path):
    store = ledger(tmp_path)
    tiny = BudgetLimits(currency_cap_usd=Decimal("0.000001"))
    blocked = store.reserve(identity(), model="deepseek-v4-flash", predicted_input_tokens=100,
                            maximum_output_tokens=10, queue_depth=0, limits=tiny, now=NOW)
    assert blocked["code"] == "DAILY_CURRENCY_CAP"
    trip("WRONG_SYMBOL", path=tmp_path / "kill.json", evidence_id="fixture")
    killed = store.reserve(identity(2), model="deepseek-v4-flash", predicted_input_tokens=1,
                           maximum_output_tokens=1, queue_depth=0, now=NOW)
    assert killed["code"] == "KILL_SWITCH_ACTIVE"


def test_daily_technical_cap_reconciles_terminal_actual_usage(tmp_path):
    store = ledger(tmp_path)
    limits = BudgetLimits(daily_input_tokens=250, daily_output_tokens=250,
                          quick_output_tokens=200, currency_cap_usd=Decimal("2"))
    first = store.reserve(identity(1), model="deepseek-v4-flash", predicted_input_tokens=200,
                          maximum_output_tokens=200, queue_depth=0, limits=limits, now=NOW)
    store.mark_request_sent(first["logical_request_id"], first["reservation_owner"])
    store.finish(first["logical_request_id"], first["reservation_owner"], "SUCCEEDED", {
        "provider_input_tokens": 10, "provider_output_tokens": 10, "reconciled_cost": "0.00001",
    })
    second = store.reserve(identity(2), model="deepseek-v4-flash", predicted_input_tokens=200,
                           maximum_output_tokens=200, queue_depth=0, limits=limits, now=NOW)
    assert second["provider_call_allowed"] is True


def test_charge_safe_retry_state_machine():
    assert retry_decision(attempt_number=1, request_body_sent=False, provider_accepted=False,
                          failure_code="CONNECT_FAILED")["retry"] is True
    for code in ("TIMEOUT", "CONNECTION_RESET", "HTTP_429", "HTTP_500", "PARSE_FAILURE"):
        value = retry_decision(attempt_number=1, request_body_sent=True, provider_accepted=None,
                               failure_code=code)
        assert value["retry"] is False
        assert value["state"] == "UNKNOWN_CHARGE_STATE"


def test_all_19_provider_fixtures_fail_closed():
    manifest = json.loads((ROOT / "artifacts/ai6b/b3-prep/provider-negative-fixtures.json").read_text(encoding="utf-8"))
    assert len(manifest["fixtures"]) == 19
    for fixture in manifest["fixtures"]:
        result = evaluate_stub_fixture(fixture)
        assert result["status"] == "FAILED_CLOSED"
        assert result["failure_code"] == fixture["expected_failure"]
        assert result["audit_passed"] is False
        assert result["presentation_body_allowed"] is False
        assert result["automatic_retry_allowed"] is False


def test_machine_readable_grounding_and_reference_support():
    numeric = [{"source_fact_id": "n1", "canonical_value": "100.25", "unit": "USDT"}]
    passed = validate_numeric_grounding(
        [{"claim_id": "c1", "numeric_type": "price", "source_fact_id": "n1",
          "canonical_value": "100.25", "unit": "USDT"}], numeric,
    )
    assert passed["status"] == "PASSED"
    failed = validate_numeric_grounding(
        [{"claim_id": "c2", "numeric_type": "target", "source_fact_id": "made-up",
          "canonical_value": "999", "unit": "USDT"}], numeric,
    )
    assert failed["failures"] == [{"claim_id": "c2", "code": "UNSUPPORTED_NUMERIC_CLAIM"}]
    refs = validate_reference_support(
        [{"claim_id": "f1", "support_refs": []}], fact_ids={"FACT_1"}, numeric_ids={"n1"}, macro_ids=set()
    )
    assert refs["status"] == "FAILED"


def test_presentation_body_requires_every_acceptance_check():
    names = json.loads((ROOT / "artifacts/ai6b/b3-prep/acceptance-matrix.json").read_text(encoding="utf-8"))["required_checks"]
    checks = {name: "PASSED" for name in names}
    assert acceptance_gate(checks)["presentation_body_allowed"] is True
    checks["reference_support"] = "FAILED"
    assert acceptance_gate(checks)["presentation_body_allowed"] is False


def test_secret_scanner_never_emits_matched_text(tmp_path):
    (tmp_path / "clean.py").write_text("KEY_FILE = '/run/secrets/provider'\n", encoding="utf-8")
    secret = "sk-" + "A" * 24
    (tmp_path / "leak.txt").write_text(secret, encoding="utf-8")
    result = scan_repository(tmp_path)
    assert result["secret_leak_count"] == 1
    assert result["secret_values_emitted"] is False
    assert secret not in json.dumps(result)


def test_deepseek_stub_usage_and_http_429_are_no_retry(monkeypatch):
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE", os.devnull + ".not-present")
    payload = {"id": "fixture-id", "model": "deepseek-v4-flash",
               "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                         "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6}}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, _): return json.dumps(payload).encode()

    monkeypatch.setattr("dashboard.ai_market_analysis.deepseek_report_provider.urlopen", lambda *_args, **_kwargs: Response())
    provider = DeepSeekAIReportProvider("deepseek-v4-flash", api_key="fixture")
    result = provider.generate({"messages": [{"role": "user", "content": "fixture"}], "max_output_tokens": 10})
    assert result.usage["prompt_cache_hit_tokens"] == 4

    def rate_limited(*_args, **_kwargs):
        raise HTTPError("https://api.deepseek.com/chat/completions", 429, "rate", {}, None)
    monkeypatch.setattr("dashboard.ai_market_analysis.deepseek_report_provider.urlopen", rate_limited)
    with pytest.raises(ProviderError) as raised:
        provider.generate({"messages": [{"role": "user", "content": "fixture"}], "max_output_tokens": 10})
    assert raised.value.retryable is False
    assert raised.value.charge_state == "UNKNOWN_CHARGE_STATE"


def test_provider_200k_output_and_context_window_guard(monkeypatch):
    monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE", os.devnull + ".not-present")
    captured = {}
    payload = {"id":"fixture","model":"deepseek-v4-flash",
               "choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],
               "usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,limit):captured["read_limit"]=limit;return json.dumps(payload).encode()
    def fake_urlopen(request,timeout):
        captured["body"]=json.loads(request.data);captured["timeout"]=timeout;return Response()
    monkeypatch.setattr("dashboard.ai_market_analysis.deepseek_report_provider.urlopen",fake_urlopen)
    provider=DeepSeekAIReportProvider("deepseek-v4-flash",api_key="fixture")
    provider.generate({"messages":[{"role":"user","content":"json"}],"token_estimate":12000,
                       "max_output_tokens":OUTPUT_TOKEN_LIMITS["QUICK"]})
    assert captured["body"]["max_tokens"] == 200000
    assert captured["read_limit"] == PROVIDER_RESPONSE_BYTES_MAX + 1
    assert captured["timeout"] == PROVIDER_TIMEOUT_SECONDS
    with pytest.raises(ProviderError) as raised:
        provider.generate({"messages":[],"token_estimate":PROVIDER_CONTEXT_TOKEN_MAX,
                           "max_output_tokens":1})
    assert raised.value.code == "PROVIDER_CONTEXT_WINDOW_EXCEEDED"


def test_smoke_script_defaults_to_no_call_and_requires_approval():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_ai6b_b3_smoke.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "LIVE_PROVIDER_APPROVAL_REQUIRED"
    assert payload["provider_call_attempted"] is False


def test_final_closure_technical_envelope_and_paid_attempt_allowance():
    limits = BudgetLimits()
    assert limits.quick_output_tokens == 200000
    assert limits.full_output_tokens == 200000
    assert limits.position_output_tokens == 200000
    assert limits.daily_input_tokens == 500000
    assert limits.daily_output_tokens == 1000000
    assert limits.calls_24h == 24
