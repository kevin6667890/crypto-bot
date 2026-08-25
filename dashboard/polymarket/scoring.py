"""Pure scoring and explicitly counterfactual paper-execution primitives."""
from __future__ import annotations

import hashlib
import json
import math


SCORING_VERSION = "polymarket-score-v2"
EXECUTION_POLICY_VERSION = "polymarket-fixed-contracts-v1"
DEFAULT_MINIMUM_EDGE = 0.05


def execution_policy_hash(*, contracts: float, minimum_edge: float,
                          fee_model_version: str) -> str:
    payload = {
        "contracts": float(contracts),
        "fee_model_version": str(fee_model_version),
        "minimum_edge": float(minimum_edge),
        "version": EXECUTION_POLICY_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate(p: float, y: int) -> tuple[float, int]:
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    if y not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    return p, y


def brier_score(p: float, y: int) -> float:
    p, y = _validate(p, y)
    return (p - y) ** 2


def log_loss(p: float, y: int) -> float:
    p, y = _validate(p, y)
    return -math.log(p if y else 1.0 - p)


def market_baseline_score(midpoint: float, outcome: int) -> dict[str, float]:
    return {"probability": float(midpoint), "brier": brier_score(midpoint, outcome), "log_loss": log_loss(midpoint, outcome)}


def paired_scores(model_probability: float, market_midpoint: float, outcome: int) -> dict[str, float]:
    model_brier, market_brier = brier_score(model_probability, outcome), brier_score(market_midpoint, outcome)
    model_log, market_log = log_loss(model_probability, outcome), log_loss(market_midpoint, outcome)
    return {"forecast_brier": model_brier, "market_brier": market_brier,
            "forecast_log_loss": model_log, "market_log_loss": market_log,
            # Positive deltas consistently mean the model beat the market.
            "brier_delta": market_brier - model_brier, "log_loss_delta": market_log - model_log}


def executable_pnl_v1(model_probability: float, market_midpoint: float, yes_best_ask: float | None,
                      no_best_ask: float | None, outcome: int, *, contracts: float = 1.0,
                      minimum_edge: float = DEFAULT_MINIMUM_EDGE,
                      fee_model_version: str = "UNKNOWN",
                      estimated_fee: float | None = None) -> dict[str, float | str | None]:
    """Counterfactual one-contract settlement PnL; midpoint is never an entry price.

    Fees are deliberately UNKNOWN unless a separately versioned fee schedule is
    supplied.  In that state no net-PnL claim is returned.
    """
    _validate(model_probability, outcome); _validate(market_midpoint, outcome)
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    if not 0 <= float(minimum_edge) < 1:
        raise ValueError("minimum_edge must be in [0,1)")
    edge = abs(float(model_probability) - float(market_midpoint))
    common = {
        "contracts": float(contracts), "minimum_edge": float(minimum_edge),
        "edge": edge, "execution_policy_version": EXECUTION_POLICY_VERSION,
        "execution_policy_hash": execution_policy_hash(contracts=contracts, minimum_edge=minimum_edge,
                                                        fee_model_version=fee_model_version),
        "fee_model_version": fee_model_version,
    }
    if edge < float(minimum_edge):
        return {**common, "side": "NO_TRADE", "entry_ask": None, "gross_pnl": 0.0,
                "estimated_fee": None, "net_pnl": 0.0, "fee_status": "NOT_APPLICABLE"}
    side, ask = ("YES", yes_best_ask) if model_probability > market_midpoint else ("NO", no_best_ask)
    if ask is None or not 0 < float(ask) < 1:
        return {**common, "side": side, "entry_ask": None, "gross_pnl": None,
                "estimated_fee": None, "net_pnl": None, "fee_status": "UNAVAILABLE_ASK"}
    entry = float(ask); pays = outcome if side == "YES" else 1 - outcome
    gross = contracts * (pays - entry)
    fee_known = fee_model_version != "UNKNOWN" and estimated_fee is not None
    if estimated_fee is not None and float(estimated_fee) < 0:
        raise ValueError("estimated_fee must be non-negative")
    return {**common, "side": side, "entry_ask": entry, "gross_pnl": gross,
            "estimated_fee": float(estimated_fee) if fee_known else None,
            "net_pnl": gross - float(estimated_fee) if fee_known else None,
            "fee_status": "KNOWN" if fee_known else "UNKNOWN"}
