"""Contract tests for the thin Research Engine primitives.

These tests deliberately exercise pure policy/data objects only.  They do not
open market data, run a backtest, or reveal protected data.
"""
from __future__ import annotations

from research_engine.features import FeatureRegistry
from research_engine.gates import GateConfig, evaluate_gates
from research_engine.schema import Hypothesis, candidate_id


def _hypothesis(required_features: tuple[str, ...] = ("OHLCV", "BOLLINGER")) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="boll-proxy-v1",
        research_cycle_id="research_cycle_v1",
        family="VPVA_PROXY_BOLL",
        description="Causal OHLCV proxy integration fixture",
        market_intuition="Volume acceptance may condition mean reversion.",
        required_features=required_features,
        assets=("BTC-USDT",),
        timeframes=("15m",),
        parameter_space={"lookback": [18, 20, 22], "band": [2.0]},
        entry_logic_version="vpva-proxy-boll-entry-v1",
        exit_logic_version="vpva-proxy-boll-exit-v1",
        execution_model="NEXT_OPEN;fee_bps=5;slippage_bps=3",
        data_requirements=("confirmed_only",),
        dataset_version="canonical-ohlcv-v1",
        code_version="unit-test",
        created_at="2026-09-29T00:00:00Z",
    )


def test_hypothesis_serialization_and_candidate_identity_are_deterministic() -> None:
    hypothesis = _hypothesis()
    reordered = Hypothesis(**{
        **hypothesis.__dict__,
        "parameter_space": {"band": [2.0], "lookback": [18, 20, 22]},
    })
    assert hypothesis.serialize() == reordered.serialize()
    args = {"feature_set": ("OHLCV", "BOLLINGER"),
            "execution_assumptions": {"fill": "NEXT_OPEN", "fee_bps": 5, "slippage_bps": 3}}
    left = candidate_id(hypothesis=hypothesis, parameters={"lookback": 20, "band": 2.0}, asset="BTC-USDT", timeframe="15m", **args)
    right = candidate_id(hypothesis=reordered, parameters={"band": 2.0, "lookback": 20}, asset="BTC-USDT", timeframe="15m", **args)
    assert left == right
    assert left != candidate_id(hypothesis=hypothesis, parameters={"lookback": 22, "band": 2.0}, asset="BTC-USDT", timeframe="15m", **args)
    assert left != candidate_id(hypothesis=hypothesis, parameters={"lookback": 20, "band": 2.0}, asset="ETH-USDT", timeframe="15m", **args)


def test_unavailable_true_order_flow_is_explicit_and_never_silently_proxied() -> None:
    results = FeatureRegistry().check(("OHLCV", "TRUE_VPVA", "CVD", "FUNDING"))
    unavailable = {item.feature for item in results if item.status == "UNAVAILABLE"}
    available = {item.feature for item in results if item.status == "AVAILABLE"}
    assert unavailable == {"TRUE_VPVA", "CVD", "FUNDING"}
    assert "VPVA_PROXY" not in available


def test_hard_gates_reject_without_a_composite_score() -> None:
    config = GateConfig(
        minimum_trades=30,
        maximum_drawdown=0.20,
        minimum_oos_sharpe=0.25,
        maximum_cost_ratio=0.50,
        minimum_profitable_fold_ratio=0.60,
        minimum_parameter_stability=0.60,
        minimum_cross_asset_consistency=0.34,
        minimum_cost_stress_survival=1.0,
    )
    outcome = evaluate_gates(
        {
            "total_trades": 12,
            "maximum_drawdown": 0.10,
            "oos_sharpe": 3.0,
            "cost_ratio": 0.10,
            "profitable_fold_ratio": 1.0,
            "parameter_stability": 1.0,
            "cross_asset_consistency": 1.0,
            "cost_stress_survival": 1.0,
        },
        config,
    )
    assert any(item["gate"] == "MINIMUM_TRADES" and item["status"] == "FAIL" for item in outcome)
    assert all("score" not in item for item in outcome)


def test_parameter_instability_is_a_named_hard_rejection() -> None:
    config = GateConfig(minimum_parameter_stability=0.60)
    outcome = evaluate_gates({"parameter_stability": 0.20}, config)
    failure = next(item for item in outcome if item["gate"] == "PARAMETER_STABILITY")
    assert failure["status"] == "FAIL"
    # Keep this domain reason distinct from a generic gate name so reports can
    # identify isolated parameter spikes without interpretation.
    assert failure["rejection_reason"] == "PARAMETER_INSTABILITY"


def test_missing_metrics_are_not_passed_by_default() -> None:
    outcome = evaluate_gates({}, GateConfig(minimum_trades=1))
    assert outcome[0]["status"] == "UNAVAILABLE"
