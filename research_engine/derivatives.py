"""Fail-closed registration and coverage audit for real derivative datasets."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

DERIVATIVE_FEATURES = {
    "FUNDING": ("funding_level", "funding_zscore", "funding_change", "extreme_funding", "price_funding_divergence"),
    "BASIS": ("annualized_basis", "basis_zscore", "basis_expansion_compression"),
    "OI": ("oi_change", "oi_zscore", "price_oi_interaction", "oi_breakout", "oi_divergence"),
    "LIQUIDATIONS": ("liquidation_spike", "liquidation_imbalance"),
    "TRUE_VPVA": ("true_vpva", "cvd"),
}

@dataclass(frozen=True)
class DatasetCoverage:
    dataset: str; source: str; symbol: str; exchange: str; first_ts: int | None; last_ts: int | None
    resolution: str; expected_rows: int; observed_rows: int; missing_ratio: float; validation_status: str
    dataset_version: str; reason: str | None = None
    def serialize(self) -> dict: return asdict(self)

def dataset_version(payload: dict) -> str:
    return "deriv_" + sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]

def evaluate_coverage(*, dataset: str, source: str, symbol: str, exchange: str, first_ts: int | None,
                      last_ts: int | None, resolution: str, expected_rows: int, observed_rows: int,
                      missing_ratio: float, reason: str | None = None, maximum_missing_ratio: float = .005) -> DatasetCoverage:
    status = "COMPLETE" if first_ts is not None and last_ts is not None and expected_rows > 0 and observed_rows > 0 and missing_ratio <= maximum_missing_ratio and reason is None else "UNAVAILABLE"
    payload = {"dataset":dataset,"source":source,"symbol":symbol,"exchange":exchange,"first_ts":first_ts,"last_ts":last_ts,"resolution":resolution,"expected_rows":expected_rows,"observed_rows":observed_rows,"missing_ratio":missing_ratio,"reason":reason,"validation_status":status}
    return DatasetCoverage(**payload, dataset_version=dataset_version(payload))
