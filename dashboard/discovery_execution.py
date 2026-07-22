"""Discovery adapter for the canonical backtest execution core.

This module deliberately contains no fill or position-management logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .backtest_engine import SHARED_EXECUTION_ENGINE_VERSION, run_execution_backtest
from .discovery_features import FEATURE_VERSION, build_features
from .discovery_templates import TEMPLATE_VERSION, signal, validate
from .discovery_identity import canonical_json_hash, normalize_template_parameters, build_parameter_identity, build_candidate_identity, build_evaluation_identity, DISCOVERY_PARAMETER_IDENTITY_VERSION, DISCOVERY_CANDIDATE_IDENTITY_VERSION, DISCOVERY_EVALUATION_IDENTITY_VERSION
from .strategy_rules import StrategyParameters
from .discovery_ablation import (DISCOVERY_ABLATION_VERSION,
    DISCOVERY_ABLATION_IDENTITY_VERSION, build_ablation_identity, normalize_ablation_flags)

DISCOVERY_EXECUTION_POLICY_VERSION = "discovery-execution-v1"


@dataclass(frozen=True)
class DiscoveryExecutionConfig:
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01
    trading_fee: float = 0.0005
    slippage: float = 0.0003
    stop_loss_atr_multiplier: float = 1.0
    risk_reward_ratio: float = 2.0
    cooldown_bars: int = 16
    allow_long: bool = True
    allow_short: bool = True

    def validate(self) -> "DiscoveryExecutionConfig":
        if self.initial_capital <= 0 or not 0 < self.risk_per_trade <= 0.1:
            raise ValueError("capital must be positive and risk_per_trade must be in (0, 0.1].")
        if self.trading_fee < 0 or self.slippage < 0 or self.stop_loss_atr_multiplier <= 0 or self.risk_reward_ratio <= 0 or self.cooldown_bars < 0:
            raise ValueError("Invalid Discovery execution assumptions.")
        if not self.allow_long and not self.allow_short:
            raise ValueError("At least one Discovery direction must be enabled.")
        return self

    def execution_hash(self) -> str:
        self.validate()
        return canonical_json_hash({"execution_policy_version": DISCOVERY_EXECUTION_POLICY_VERSION,
                      "initial_capital": self.initial_capital, "risk_per_trade": self.risk_per_trade,
                      "trading_fee": self.trading_fee, "slippage": self.slippage,
                      "stop_loss_atr_multiplier": self.stop_loss_atr_multiplier,
                      "risk_reward_ratio": self.risk_reward_ratio, "cooldown_bars": self.cooldown_bars,
                      "allow_long": self.allow_long, "allow_short": self.allow_short})


def run_discovery_candidate_backtest(candles: list[dict[str, Any]], instrument: str, timeframe: str,
                                     template: str, template_parameters: dict[str, Any], start_ts: int,
                                     end_ts: int, execution: DiscoveryExecutionConfig | None = None,
                                     dataset_fingerprint: str | None = None,
                                     ablation_flags: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate one causal Discovery template over an inclusive execution interval.

    For end-exclusive folds callers must pass ``validation_end_ts - timeframe_seconds``.
    """
    execution = (execution or DiscoveryExecutionConfig()).validate()
    normalized_parameters = normalize_template_parameters(template, template_parameters)
    config = validate({"template": template, "parameters": normalized_parameters})
    normalized_ablation_flags = normalize_ablation_flags(template, normalized_parameters, ablation_flags)
    ablation_identity = build_ablation_identity(template=template, parameters=normalized_parameters, flags=normalized_ablation_flags)
    features = build_features(candles, {"ma_periods": sorted({6, 20, 60, 200, int(normalized_parameters["fast_period"]), int(normalized_parameters["slow_period"])}), "atr_period": int(normalized_parameters["atr_period"])})
    signal_parameter_hash = build_parameter_identity(template, normalized_parameters)
    execution_hash = execution.execution_hash()
    base_candidate_config_hash = build_candidate_identity(template, normalized_parameters, execution_hash)
    candidate_config_hash = base_candidate_config_hash if ablation_identity is None else canonical_json_hash({"ablation_candidate_identity_version": DISCOVERY_ABLATION_IDENTITY_VERSION, "base_candidate_config_hash": base_candidate_config_hash, "execution_hash": execution_hash, "ablation_identity": ablation_identity})
    evaluation_hash = build_evaluation_identity(candidate_config_hash, instrument, timeframe, start_ts, end_ts, dataset_fingerprint)

    def provider(candle: dict[str, Any], index: int) -> dict[str, Any]:
        feature = features[index]
        action = signal(template, config["parameters"], candle, feature, normalized_ablation_flags)
        timestamp = int(candle["ts"])
        return {"action": action, "atr": feature.get("atr"), "score": 0.0,
                "signal_ts": timestamp, "signal_id": f"discovery:{candidate_config_hash[:16]}:{timestamp}",
                "strategy_version": TEMPLATE_VERSION[template], "config_hash": candidate_config_hash,
                "warmed": bool(feature.get("warm"))}

    parameters = StrategyParameters(initial_capital=execution.initial_capital, risk_per_trade=execution.risk_per_trade,
        trading_fee=execution.trading_fee, slippage=execution.slippage,
        stop_loss_atr_multiplier=execution.stop_loss_atr_multiplier, risk_reward_ratio=execution.risk_reward_ratio,
        cooldown_bars=execution.cooldown_bars, enable_long=execution.allow_long, enable_short=execution.allow_short)
    result = run_execution_backtest(candles, instrument, timeframe, parameters, start_ts, end_ts, signal_provider=provider)
    result["discovery_evidence"] = {"parameter_hash": signal_parameter_hash, "execution_hash": execution_hash,
        "candidate_config_hash": candidate_config_hash, "evaluation_hash": evaluation_hash, "template": template,
        "template_version": TEMPLATE_VERSION[template], "feature_version": FEATURE_VERSION, "parameters": normalized_parameters,
        "parameter_identity_version": DISCOVERY_PARAMETER_IDENTITY_VERSION, "candidate_identity_version": DISCOVERY_CANDIDATE_IDENTITY_VERSION, "evaluation_identity_version": DISCOVERY_EVALUATION_IDENTITY_VERSION,
        "execution_policy_version": DISCOVERY_EXECUTION_POLICY_VERSION,
        "execution_engine_version": SHARED_EXECUTION_ENGINE_VERSION, "instrument": instrument,
        "timeframe": timeframe, "start_ts": start_ts, "end_ts": end_ts, "execution": execution.__dict__,
        "ablation_version": DISCOVERY_ABLATION_VERSION, "ablation_identity": ablation_identity,
        "ablation_candidate_identity_version": DISCOVERY_ABLATION_IDENTITY_VERSION if ablation_identity else None,
        "removed_component": normalized_ablation_flags["removed_component"], "normalized_ablation_flags": normalized_ablation_flags}
    return result
