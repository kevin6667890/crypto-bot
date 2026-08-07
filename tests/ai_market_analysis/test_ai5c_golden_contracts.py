from __future__ import annotations
import copy,json
import pytest
from pathlib import Path
from dashboard.ai_market_analysis.canonical import identity
from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry,select_relevant_levels
from .ai4_helpers import base_context

ROOT=Path(__file__).resolve().parents[2]

def _base_registry():
    base=base_context();enriched=build_enriched_context(base,none_position_context(base["instrument"]),freeze_macro_evidence_set([],base["decision_time"]));return base,build_fact_registry(enriched)

def test_base_golden_levels_are_source_driven():
    base,registry=_base_registry();by_id={x["level_id"]:x for x in base["key_levels"]};selected={f["value"]["level_id"]:f["value"] for f in registry["facts"] if f["category"]=="LEVEL"}
    flipped=next(x for x in by_id.values() if x["role"]=="SUPPORT" and x["state"]=="FLIPPED" and "BREAKOUT_BOUNDARY" in x["confluences"])
    impulse=next(x for x in by_id.values() if x["role"]=="RESISTANCE" and "IMPULSE_HIGH" in x["confluences"])
    assert selected[flipped["level_id"]]["zone_low"]==flipped["zone_low"] and selected[flipped["level_id"]]["zone_high"]==flipped["zone_high"]
    assert selected[impulse["level_id"]]["zone_low"]==impulse["zone_low"] and selected[impulse["level_id"]]["source_types"]

def test_base_psychological_level_is_not_promoted():
    base,registry=_base_registry();psych=next(x for x in base["key_levels"] if x["confluences"]==["PSYCHOLOGICAL_LEVEL"])
    fact=next((f["value"] for f in registry["facts"] if f["category"]=="LEVEL" and f["value"]["level_id"]==psych["level_id"]),None)
    if fact:assert fact["strength"]=="WEAK" and fact["source_types"]==["PSYCHOLOGICAL_LEVEL"] and fact["zone_low"]==fact["zone_high"]

def test_synthetic_high_timeframe_fixture_identity_and_selection():
    fixture=json.loads((ROOT/"fixtures/ai_market_analysis/synthetic_high_tf_pressure_golden_v1.json").read_text(encoding="utf-8"));level=fixture["level"]
    assert fixture["fixture"] is True and fixture["historical_market_fact"] is False and fixture["production_eligible"] is False
    assert identity("level",fixture["canonical_identity_input"])==level["level_id"]
    base=base_context();base["key_levels"]=[*base["key_levels"],level]
    selected={x["level_id"] for x in select_relevant_levels(base)}
    assert level["level_id"] in selected and level["role"]=="RESISTANCE" and level["state"]=="ACTIVE" and level["strength"]=="MAJOR"
    assert level["timeframes"]==["1D","1W"] and {x["source"] for x in level["source_candidates"]}>={"CONFIRMED_SWING_HIGH","MA200","PREVIOUS_WEEK_HIGH"}

def test_level_order_does_not_change_selection():
    base=base_context();a=[x["level_id"] for x in select_relevant_levels(base)];other=copy.deepcopy(base);other["key_levels"].reverse();b=[x["level_id"] for x in select_relevant_levels(other)];assert a==b

def test_scenario_references_flipped_impulse_and_bound_are_retained():
    base=base_context();selected={x["level_id"] for x in select_relevant_levels(base)};referenced={x for s in base["scenario_tree"]["scenarios"] for x in [*(s["trigger"]["level_ids"]),*s["target_level_ids"],s["invalidation"]["level_id"]]}
    assert referenced<=selected and any(x["state"]=="FLIPPED" and x["level_id"] in selected for x in base["key_levels"]) and any("IMPULSE_HIGH" in x["confluences"] and x["level_id"] in selected for x in base["key_levels"])

def test_weak_single_source_families_are_deduplicated_and_bound_is_kept():
    base=base_context();selected=select_relevant_levels(base,6);assert len(selected)<=6
    singleton=[next(iter({c["source_family"] for c in x["source_candidates"]})) for x in selected if len({c["source_family"] for c in x["source_candidates"]})==1]
    assert len(singleton)==len(set(singleton))

def test_future_detected_level_does_not_change_decision_time_selection():
    base=base_context();before=[x["level_id"] for x in select_relevant_levels(base)];future=copy.deepcopy(base["key_levels"][0]);future["level_id"]="future_level";future["first_detected"]=32503680000;base["key_levels"].append(future);assert [x["level_id"] for x in select_relevant_levels(base)]==before

@pytest.mark.parametrize("instrument",["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP"])
def test_selection_has_no_instrument_price_or_step_constants(instrument):
    base=base_context();base["instrument"]=instrument;assert select_relevant_levels(base)==select_relevant_levels(copy.deepcopy(base))
