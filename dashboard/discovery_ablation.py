"""Versioned, deterministic component-ablation semantics for Discovery.

Component audit (signal gates only): TREND_PULLBACK, VOLATILITY_BREAKOUT and
TREND_BREAKOUT combine a close/fast-MA direction gate, fast/slow-MA relation,
their template-specific distance or breakout gate, and optional volume.  MEAN_REVERSION
uses Bollinger-band extremes, RSI thresholds, and optional volume.  ATR warm-up,
fees, slippage, sizing, exits, cooldowns, and fill timing are execution controls,
not ablatable signal components.
"""
from __future__ import annotations

from typing import Any, Mapping

from .discovery_identity import (TEMPLATE_VERSION, build_parameter_identity,
    canonical_json_hash, normalize_template_parameters)
from .discovery_features import FEATURE_VERSION

DISCOVERY_ABLATION_VERSION = "discovery-component-ablation-v1"
DISCOVERY_ABLATION_IDENTITY_VERSION = "discovery-component-ablation-identity-v1"

# Each listed item is independently removable while retaining a coherent
# direction/entry rule.  The original strategy code contains no other signal gates.
_COMPONENTS = {
    "TREND_PULLBACK": (
        ("TREND_DIRECTION_FILTER", "Remove the close-versus-fast-MA direction filter."),
        ("FAST_SLOW_MA_RELATION", "Remove the fast-versus-slow-MA trend relation."),
        ("PULLBACK_DISTANCE_GATE", "Remove the pullback distance-to-fast-MA gate."),
    ),
    "VOLATILITY_BREAKOUT": (
        ("TREND_DIRECTION_FILTER", "Remove the close-versus-fast-MA direction filter."),
        ("FAST_SLOW_MA_RELATION", "Remove the fast-versus-slow-MA trend relation."),
        ("BREAKOUT_LEVEL_GATE", "Remove the Bollinger breakout-level gate."),
    ),
    "MEAN_REVERSION": (
        ("BOLLINGER_EXTREME_GATE", "Remove the Bollinger-band extreme gate."),
        ("RSI_ENTRY_GATE", "Remove the RSI entry-threshold gate."),
    ),
    "TREND_BREAKOUT": (
        ("TREND_DIRECTION_FILTER", "Remove the close-versus-fast-MA direction filter."),
        ("FAST_SLOW_MA_RELATION", "Remove the fast-versus-slow-MA trend relation."),
        ("BREAKOUT_LEVEL_GATE", "Remove the completed-candle breakout-level gate."),
    ),
}
_VOLUME = ("VOLUME_CONFIRMATION", "Remove the optional volume-ratio confirmation gate.")


def supported_ablation_components(template: str, parameters: Mapping[str, Any]) -> tuple[str, ...]:
    """Return audited, active, one-at-a-time removable components in policy order."""
    normalized = normalize_template_parameters(template, dict(parameters))
    components = [code for code, _ in _COMPONENTS[template]]
    if normalized["volume_enabled"]:
        components.append(_VOLUME[0])
    return tuple(components)


def _description(template: str, component: str) -> str:
    for code, description in (*_COMPONENTS[template], _VOLUME):
        if code == component:
            return description
    raise ValueError("DISCOVERY_ABLATION_COMPONENT_INAPPLICABLE")


def normalize_ablation_flags(template: str, parameters: Mapping[str, Any], flags: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the v1 single-component flag shape without mutating callers."""
    normalize_template_parameters(template, dict(parameters))
    if flags is None or flags == {}:
        return {"removed_component": None}
    if not isinstance(flags, Mapping) or set(flags) != {"removed_component"}:
        raise ValueError("DISCOVERY_ABLATION_FLAGS_INVALID")
    component = flags["removed_component"]
    if component is None:
        return {"removed_component": None}
    if isinstance(component, (list, tuple, set, dict)):
        raise ValueError("DISCOVERY_ABLATION_MULTIPLE_COMPONENTS_UNSUPPORTED")
    if not isinstance(component, str):
        raise ValueError("DISCOVERY_ABLATION_COMPONENT_UNKNOWN")
    all_codes = {code for values in _COMPONENTS.values() for code, _ in values} | {_VOLUME[0]}
    if component not in all_codes:
        raise ValueError("DISCOVERY_ABLATION_COMPONENT_UNKNOWN")
    if component not in supported_ablation_components(template, parameters):
        raise ValueError("DISCOVERY_ABLATION_COMPONENT_INAPPLICABLE")
    return {"removed_component": component}


def build_ablation_identity(*, template: str, parameters: Mapping[str, Any], flags: Mapping[str, Any] | None) -> str | None:
    normalized = normalize_template_parameters(template, dict(parameters))
    normalized_flags = normalize_ablation_flags(template, normalized, flags)
    component = normalized_flags["removed_component"]
    if component is None:
        return None
    return canonical_json_hash({"ablation_identity_version": DISCOVERY_ABLATION_IDENTITY_VERSION,
        "ablation_policy_version": DISCOVERY_ABLATION_VERSION, "template": template,
        "template_version": TEMPLATE_VERSION[template], "feature_version": FEATURE_VERSION,
        "source_parameter_hash": build_parameter_identity(template, normalized),
        "removed_component": component, "normalized_ablation_flags": normalized_flags})


def describe_ablation(template: str, parameters: Mapping[str, Any], flags: Mapping[str, Any] | None) -> str | None:
    component = normalize_ablation_flags(template, parameters, flags)["removed_component"]
    return _description(template, component) if component else None


def generate_ablation_scenarios(template: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Create one deterministic scenario per active audited component."""
    normalized = normalize_template_parameters(template, dict(parameters))
    source_parameter_hash = build_parameter_identity(template, normalized)
    scenarios = []
    for component in supported_ablation_components(template, normalized):
        flags = normalize_ablation_flags(template, normalized, {"removed_component": component})
        scenarios.append({"component_code": component, "description": describe_ablation(template, normalized, flags),
            "normalized_ablation_flags": flags, "source_parameter_hash": source_parameter_hash,
            "ablation_identity": build_ablation_identity(template=template, parameters=normalized, flags=flags),
            "ablation_policy_version": DISCOVERY_ABLATION_VERSION})
    return scenarios
