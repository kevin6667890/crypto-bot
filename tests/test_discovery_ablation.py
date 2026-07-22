"""Pure policy contracts for versioned Discovery component ablation."""
from __future__ import annotations

import pytest

import dashboard.discovery_ablation as ablation
from dashboard.discovery_ablation import (DISCOVERY_ABLATION_IDENTITY_VERSION,
    DISCOVERY_ABLATION_VERSION, build_ablation_identity, generate_ablation_scenarios,
    normalize_ablation_flags, supported_ablation_components)

BASE={"fast_period":20,"slow_period":200,"fast_ma_type":"EMA","atr_period":14,"volume_enabled":False}


def test_audited_template_component_map_is_deterministic_and_non_mutating():
    cases={
        "TREND_PULLBACK": ("TREND_DIRECTION_FILTER","FAST_SLOW_MA_RELATION","PULLBACK_DISTANCE_GATE"),
        "VOLATILITY_BREAKOUT": ("TREND_DIRECTION_FILTER","FAST_SLOW_MA_RELATION","BREAKOUT_LEVEL_GATE"),
        "MEAN_REVERSION": ("BOLLINGER_EXTREME_GATE","RSI_ENTRY_GATE"),
        "TREND_BREAKOUT": ("TREND_DIRECTION_FILTER","FAST_SLOW_MA_RELATION","BREAKOUT_LEVEL_GATE"),
    }
    for template, expected in cases.items():
        params={**BASE, **({"maximum_distance":.004} if template=="TREND_PULLBACK" else {}), **({"rsi_lower":35,"rsi_upper":65} if template=="MEAN_REVERSION" else {})}
        before=dict(params)
        assert supported_ablation_components(template,params)==expected
        assert params==before


def test_scenarios_are_stable_key_ordered_unique_and_never_include_original():
    params={**BASE,"volume_enabled":True,"minimum_volume_ratio":1.1}
    first=generate_ablation_scenarios("TREND_BREAKOUT",params)
    second=generate_ablation_scenarios("TREND_BREAKOUT",dict(reversed(list(params.items()))))
    assert first==second and [x["component_code"] for x in first]==["TREND_DIRECTION_FILTER","FAST_SLOW_MA_RELATION","BREAKOUT_LEVEL_GATE","VOLUME_CONFIRMATION"]
    assert len({x["ablation_identity"] for x in first})==len(first)
    assert all(x["normalized_ablation_flags"]["removed_component"]==x["component_code"] for x in first)
    assert all(x["component_code"] is not None and "id" not in x for x in first)
    assert all(x["ablation_policy_version"]==DISCOVERY_ABLATION_VERSION for x in first)


def test_volume_is_only_supported_when_active_and_empty_registry_is_valid(monkeypatch):
    assert "VOLUME_CONFIRMATION" not in supported_ablation_components("TREND_BREAKOUT",BASE)
    active={**BASE,"volume_enabled":True,"minimum_volume_ratio":1.0}
    assert "VOLUME_CONFIRMATION" in supported_ablation_components("TREND_BREAKOUT",active)
    monkeypatch.setitem(ablation._COMPONENTS,"TREND_BREAKOUT",())
    assert generate_ablation_scenarios("TREND_BREAKOUT",BASE)==[]


def test_flags_reject_unknown_inapplicable_and_multiple_components():
    with pytest.raises(ValueError,match="UNKNOWN"):
        normalize_ablation_flags("TREND_BREAKOUT",BASE,{"removed_component":"NOT_A_COMPONENT"})
    with pytest.raises(ValueError,match="INAPPLICABLE"):
        normalize_ablation_flags("MEAN_REVERSION",{**BASE,"rsi_lower":35,"rsi_upper":65},{"removed_component":"TREND_DIRECTION_FILTER"})
    with pytest.raises(ValueError,match="INAPPLICABLE"):
        normalize_ablation_flags("TREND_BREAKOUT",BASE,{"removed_component":"VOLUME_CONFIRMATION"})
    with pytest.raises(ValueError,match="MULTIPLE"):
        normalize_ablation_flags("TREND_BREAKOUT",BASE,{"removed_component":["TREND_DIRECTION_FILTER","BREAKOUT_LEVEL_GATE"]})
    with pytest.raises(ValueError,match="Unknown discovery template"):
        supported_ablation_components("UNKNOWN",BASE)


def test_identity_is_component_sensitive_stable_and_versioned_without_database_ids():
    one={"removed_component":"TREND_DIRECTION_FILTER"}
    two={"removed_component":"BREAKOUT_LEVEL_GATE"}
    identity=build_ablation_identity(template="TREND_BREAKOUT",parameters=BASE,flags=one)
    assert identity==build_ablation_identity(template="TREND_BREAKOUT",parameters=dict(reversed(list(BASE.items()))),flags=dict(one))
    assert identity!=build_ablation_identity(template="TREND_BREAKOUT",parameters=BASE,flags=two)
    assert len(identity)==64 and build_ablation_identity(template="TREND_BREAKOUT",parameters=BASE,flags={}) is None
    assert DISCOVERY_ABLATION_IDENTITY_VERSION=="discovery-component-ablation-identity-v1"


def test_mandatory_execution_controls_are_not_exposed_as_components():
    all_codes={code for template in ("TREND_PULLBACK","VOLATILITY_BREAKOUT","MEAN_REVERSION","TREND_BREAKOUT") for code in supported_ablation_components(template,{**BASE, **({"maximum_distance":.004} if template=="TREND_PULLBACK" else {}), **({"rsi_lower":35,"rsi_upper":65} if template=="MEAN_REVERSION" else {})})}
    assert not {"TRADING_FEE","SLIPPAGE","RISK_SIZING","STOP_LOSS","PROFIT_TARGET","COOLDOWN","FILL_TIMING"}&all_codes
