from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_ai6b_b2_fake_canary import FIXTURE_PATH, assert_local_safety, deterministic_context, run_matrix
from scripts.verify_ai6b_b2_rollback import rehearse
from dashboard.ai_market_analysis.presentation import PresentationError, build_latest_presentation
from tests.ai_market_analysis.test_ai6a_presentation import seeded


ROOT = Path(__file__).resolve().parents[2]


def test_b2_fixture_and_acceptance_matrix_are_bounded_and_disabled():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    matrix = json.loads((ROOT / "config/ai6b_b2_acceptance_matrix.json").read_text(encoding="utf-8"))
    assert fixture["enabled_by_default"] is False
    assert fixture["provider"] == "fake"
    assert fixture["instruments"] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    assert fixture["modes"] == ["QUICK", "FULL"]
    assert fixture["position_sources"] == ["NONE", "PAPER"]
    assert matrix["positive_cartesian"]["expected_case_count"] == 12
    assert [batch["maximum_reserved_output_tokens"] for batch in matrix["production_batches"]] == [17400, 12000]
    assert all(batch["maximum_reserved_output_tokens"] <= 25000 for batch in matrix["production_batches"])
    assert matrix["execution_authorized"] is False and matrix["default_enabled"] is False
    assert matrix["thresholds"]["live_provider_calls"] == 0
    assert matrix["thresholds"]["paper_orders_created"] == 0
    assert {item["id"] for item in fixture["negative_scenarios"]} >= {
        "audit_failure", "registry_mismatch", "wrong_instrument", "wrong_mode",
        "pending", "failed", "error", "legacy_schema",
    }


def test_b2_context_variants_are_deterministic_and_explicit():
    first = deterministic_context("BTC-USDT-SWAP", "complete")
    second = deterministic_context("BTC-USDT-SWAP", "complete")
    assert first == second
    warning = deterministic_context("ETH-USDT-SWAP", "warning")
    assert warning["data_quality"]["overall"] == "PARTIAL"
    assert "AI6B_B2_SYNTHETIC_WARNING" in warning["data_quality"]["missing_sources"]
    missing = deterministic_context("SOL-USDT-SWAP", "missing_orderflow")
    assert {"cvd", "oi"} <= set(missing["data_quality"]["missing_sources"])
    stale = deterministic_context("BTC-USDT-SWAP", "stale")
    assert stale["provenance"]["expected_presentation_freshness"] == "STALE_AFTER_NEWER_CONTEXT"


def test_b2_fake_cartesian_traverses_complete_pipeline(tmp_path):
    result = run_matrix(tmp_path / "canary")
    assert result["passed"] is True
    assert result["case_count"] == result["pass_count"] == 12
    assert result["fake_provider_calls"] == 12
    assert result["live_provider_calls"] == 0
    assert result["paper_orders_created"] == 0
    assert all(case["audit_status"] == "PASSED" for case in result["cases"])
    assert all(case["eligibility"] == "AUDIT_PASSED_SHADOW_ONLY" for case in result["cases"])
    assert len({(case["instrument"], case["mode"], case["position_source"]) for case in result["cases"]}) == 12
    assert all(case["context_id"].startswith("enriched_") for case in result["cases"])
    assert all(case["registry_snapshot_id"].startswith("registry_snapshot_") for case in result["cases"])


@pytest.mark.parametrize("source", ["USER_DECLARED", "LIVE", ""])
def test_b2_harness_rejects_forbidden_position_sources(source):
    with pytest.raises(RuntimeError, match="B2_POSITION_SOURCE_FORBIDDEN"):
        assert_local_safety(source)


@pytest.mark.parametrize("provider", ["deepseek", "live", "DeepSeek"])
def test_b2_harness_rejects_non_fake_provider(provider):
    with pytest.raises(RuntimeError, match="B2_FAKE_PROVIDER_ONLY"):
        assert_local_safety("NONE", provider)


def test_b2_harness_rejects_provider_secret(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-must-not-be-used")
    with pytest.raises(RuntimeError, match="B2_PROVIDER_SECRET_PRESENT"):
        assert_local_safety("NONE")


def test_report_worker_does_not_read_provider_secret_for_fake_only_b2():
    source = (ROOT / "scripts/run_ai_report_worker.py").read_text(encoding="utf-8")
    assert 'if os.getenv("AI_REPORT_LIVE_PROVIDER_ENABLED","false").lower()=="true":load_provider_secret()' in source
    assert 'if request["provider"]=="fake":return FakeAIReportProvider' in source
    assert "assert_live_provider_allowed()" in source


def test_b2_privacy_scope_makes_existing_user_declared_report_inaccessible(tmp_path, monkeypatch):
    reports, _, _ = seeded(tmp_path, mode="POSITION_AWARE", position=True)
    monkeypatch.setenv("AI6B_PRIVACY_SCOPE_ENFORCED", "true")
    with pytest.raises(PresentationError, match="POSITION_SOURCE_OUTSIDE_CANARY_SCOPE"):
        build_latest_presentation(reports, "ETH-USDT-SWAP", "POSITION_AWARE")


def test_b2_local_rollback_preserves_immutable_evidence(tmp_path):
    result = rehearse(tmp_path / "rollback")
    assert result["passed"] is True
    assert result["sequence"] == ["SHADOW_ON", "FAKE_CANARY", "SHADOW_OFF"]
    assert all(result["checks"].values())
    assert result["production_connections"] == result["production_writes"] == 0
