"""Disabled future CVD/OI factor terminal declarations and approval gate."""

from __future__ import annotations

from typing import Any, Mapping

from dashboard.research_readiness import READY_PENDING


MICROSTRUCTURE_FACTOR_TERMINALS_VERSION = "microstructure-factor-terminals-v1"
ALLOWED_INSTRUMENTS = ("BTC", "ETH", "SOL")


def _terminal(
    name: str, source: str, frequency: str, resolution: str,
    minimum_sample: int, continuous_days: int, independent_events: int,
    explanation: str, limitation: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "native_update_frequency": frequency,
        "required_resolution": resolution,
        "causal_timestamp": "confirmed source timestamp at or before decision time",
        "minimum_sample": minimum_sample,
        "continuous_day_requirement": continuous_days,
        "independent_event_requirement": independent_events,
        "missing_data_policy": "leave missing; no interpolation, zero-fill, or future-fill",
        "allowed_instruments": list(ALLOWED_INSTRUMENTS),
        "readiness_dependency": "matching feature_group × instrument readiness",
        "economic_explanation": explanation,
        "known_limitations": limitation,
        "enabled": False,
    }


TERMINALS = {
    terminal["name"]: terminal for terminal in (
        _terminal("cvd_delta", "confirmed CVD buckets", "1m", "1m", 2000, 30, 2000,
                  "Net aggressive flow in the bucket.", "Trade classification and venue scope."),
        _terminal("cvd_rolling_sum", "confirmed CVD buckets", "1m", "1m", 2000, 30, 2000,
                  "Persistent signed flow.", "Window choice and flow clustering."),
        _terminal("cvd_slope", "confirmed CVD buckets", "1m", "1m", 2000, 30, 2000,
                  "Rate of change in cumulative flow.", "Sensitive to window and outliers."),
        _terminal("cvd_zscore", "confirmed CVD buckets", "1m", "1m", 2000, 30, 2000,
                  "Standardized flow imbalance.", "Distribution is non-stationary."),
        _terminal("cvd_volume_normalized", "confirmed trades/CVD", "1m", "1m", 2000, 30, 2000,
                  "Flow imbalance scaled by traded volume.", "Low-volume buckets are unstable."),
        _terminal("cvd_price_divergence", "confirmed CVD + mark price", "1m", "1m", 2000, 30, 2000,
                  "Disagreement between aggressive flow and price.", "Divergence has horizon dependence."),
        _terminal("oi_absolute_change", "confirmed OI changes", "5m", "5m", 1000, 30, 1000,
                  "Absolute positioning expansion or contraction.", "Contract units vary by instrument."),
        _terminal("oi_percentage_change", "confirmed OI changes", "5m", "5m", 1000, 30, 1000,
                  "Scale-neutral positioning change.", "Unstable near small denominators."),
        _terminal("oi_zscore", "confirmed OI changes", "5m", "5m", 1000, 30, 1000,
                  "Unusual positioning relative to history.", "Regime shifts distort normalization."),
        _terminal("oi_acceleration", "confirmed OI changes", "5m", "5m", 1000, 30, 1000,
                  "Second difference of positioning.", "Amplifies observation noise."),
        _terminal("oi_price_quadrant", "confirmed OI + mark price", "5m", "5m", 1000, 30, 1000,
                  "Classifies price/OI expansion and contraction.", "Quadrants do not identify leverage side."),
        _terminal("cvd_x_oi", "confirmed CVD + OI", "mixed native", "5m", 1000, 30, 1000,
                  "Combines aggressive flow with positioning.", "Limited by the smaller native event set."),
        _terminal("funding_x_oi", "settled funding + confirmed OI", "8h/5m", "5m", 1000, 30, 1000,
                  "Relates carry pressure to positioning.", "Funding is sparse and forward-filled rows are not events."),
        _terminal("basis_x_oi", "confirmed basis + OI", "1h/5m", "1H", 1000, 30, 1000,
                  "Relates carry dislocation to positioning.", "Basis depends on mark/index quality."),
        _terminal("cvd_x_volatility_regime", "confirmed CVD + causal volatility", "1m", "1m", 2000, 30, 2000,
                  "Conditions flow on volatility regime.", "Regime definition introduces model risk."),
        _terminal("oi_x_volatility_regime", "confirmed OI + causal volatility", "5m", "5m", 1000, 30, 1000,
                  "Conditions positioning change on volatility.", "Regime shifts may be detected late."),
    )
}


TERMINAL_FEATURE_GROUP = {
    **{name: "CVD" for name in (
        "cvd_delta", "cvd_rolling_sum", "cvd_slope", "cvd_zscore",
        "cvd_volume_normalized", "cvd_price_divergence",
        "cvd_x_volatility_regime")},
    **{name: "OI" for name in (
        "oi_absolute_change", "oi_percentage_change", "oi_zscore",
        "oi_acceleration", "oi_price_quadrant", "oi_x_volatility_regime")},
    "cvd_x_oi": "CVD + OI",
    "funding_x_oi": "funding + OI",
    "basis_x_oi": "basis + OI",
}


def approved_terminal_manifest_entry(
    terminal_name: str, instrument: str, readiness: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a future manifest entry only after all three explicit gates."""
    if terminal_name not in TERMINALS:
        raise ValueError(f"unknown terminal: {terminal_name}")
    if instrument not in ALLOWED_INSTRUMENTS:
        raise ValueError(f"unsupported instrument: {instrument}")
    expected_group = TERMINAL_FEATURE_GROUP[terminal_name]
    if (
        readiness.get("status") != READY_PENDING
        or readiness.get("feature_group") != expected_group
        or readiness.get("instrument") != instrument
    ):
        raise PermissionError("matching readiness is not pending human approval")
    if not approval or approval.get("human_approved") is not True:
        raise PermissionError("explicit human approval is absent")
    identity = readiness.get("dataset_identity")
    if not identity or approval.get("dataset_identity") != identity:
        raise PermissionError("approval dataset identity does not match")
    approver = approval.get("approved_by")
    approved_at = approval.get("approved_at")
    if not approver or not approved_at:
        raise PermissionError("approval record is incomplete")
    return {
        "version": MICROSTRUCTURE_FACTOR_TERMINALS_VERSION,
        "terminal": terminal_name,
        "instrument": instrument,
        "dataset_identity": identity,
        "approval": {
            "human_approved": True,
            "approved_by": approver,
            "approved_at": approved_at,
        },
        "definition": dict(TERMINALS[terminal_name]),
        "research_only": True,
        "generated_or_evaluated": False,
    }
