from __future__ import annotations

import pytest

from dashboard.microstructure_factor_terminals import (
    MICROSTRUCTURE_FACTOR_TERMINALS_VERSION,
    TERMINALS,
    approved_terminal_manifest_entry,
)
from dashboard.research_readiness import READY_PENDING


EXPECTED = {
    "cvd_delta", "cvd_rolling_sum", "cvd_slope", "cvd_zscore",
    "cvd_volume_normalized", "cvd_price_divergence",
    "oi_absolute_change", "oi_percentage_change", "oi_zscore",
    "oi_acceleration", "oi_price_quadrant", "cvd_x_oi", "funding_x_oi",
    "basis_x_oi", "cvd_x_volatility_regime", "oi_x_volatility_regime",
}


def _readiness(identity="dataset-a"):
    return {
        "status": READY_PENDING,
        "feature_group": "CVD",
        "instrument": "BTC",
        "dataset_identity": identity,
    }


def _approval(identity="dataset-a"):
    return {
        "human_approved": True,
        "approved_by": "research-owner",
        "approved_at": "2026-07-29T00:00:00Z",
        "dataset_identity": identity,
    }


def test_terminal_catalog_is_complete_and_every_terminal_is_disabled():
    assert MICROSTRUCTURE_FACTOR_TERMINALS_VERSION == (
        "microstructure-factor-terminals-v1")
    assert set(TERMINALS) == EXPECTED
    assert all(item["enabled"] is False for item in TERMINALS.values())
    required = {
        "source", "native_update_frequency", "required_resolution",
        "causal_timestamp", "minimum_sample", "continuous_day_requirement",
        "independent_event_requirement", "missing_data_policy",
        "allowed_instruments", "readiness_dependency",
        "economic_explanation", "known_limitations", "enabled",
    }
    assert all(required <= set(item) for item in TERMINALS.values())


@pytest.mark.parametrize("approval", [None, {}, {"human_approved": False}])
def test_unapproved_terminal_cannot_enter_manifest(approval):
    with pytest.raises(PermissionError):
        approved_terminal_manifest_entry(
            "cvd_delta", "BTC", _readiness(), approval)


def test_non_ready_terminal_cannot_enter_manifest():
    readiness = _readiness()
    readiness["status"] = "APPROACHING_READINESS"
    with pytest.raises(PermissionError):
        approved_terminal_manifest_entry(
            "cvd_delta", "BTC", readiness, _approval())


def test_dataset_identity_change_invalidates_old_approval():
    with pytest.raises(PermissionError, match="identity"):
        approved_terminal_manifest_entry(
            "cvd_delta", "BTC", _readiness("dataset-b"),
            _approval("dataset-a"))


def test_matching_explicit_approval_only_creates_future_declaration():
    entry = approved_terminal_manifest_entry(
        "cvd_delta", "BTC", _readiness(), _approval())
    assert entry["research_only"] is True
    assert entry["generated_or_evaluated"] is False
    assert entry["definition"]["enabled"] is False


def test_missing_policy_forbids_interpolation_and_zero_fill():
    for terminal in TERMINALS.values():
        policy = terminal["missing_data_policy"].lower()
        assert "no interpolation" in policy
        assert "zero-fill" in policy
        assert "future-fill" in policy
