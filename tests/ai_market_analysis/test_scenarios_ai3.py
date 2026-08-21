from __future__ import annotations

from copy import deepcopy
import json

from dashboard.ai_market_analysis.scenario_builder import build_scenario_tree


def level(ident,price,role,state="ACTIVE"):
    return {"level_id":ident,"representative_price":price,"zone_low":price-1,"zone_high":price+1,
            "role":role,"state":state,"strength":"STRONG"}


def phase(primary="SHORT_COVERING_DOMINANT",confidence="HIGH"):
    return {"phase_id":"p1","attribution":{"primary":primary,"confidence":confidence}}


LEVELS=[level("support",90,"SUPPORT","FLIPPED"),level("resistance",110,"RESISTANCE"),level("far-support",80,"SUPPORT")]


def tree(direction="UP",current="RETEST",levels=LEVELS,phases=None):
    return build_scenario_tree(direction,current,deepcopy(levels),phases or [phase()],["e1"])


def test_up_tree_has_three_semantic_branches():
    assert [s["type"] for s in tree()["scenarios"]]==["BULLISH_CONTINUATION","NORMAL_RETEST","FAILED_BREAKOUT"]


def test_down_tree_has_three_semantic_branches():
    assert [s["type"] for s in tree("DOWN")["scenarios"]]==["BEARISH_CONTINUATION","NORMAL_BEARISH_RETEST","FAILED_BREAKDOWN"]


def test_targets_reference_real_level_ids():
    ids={l["level_id"] for l in LEVELS}
    assert all(set(s["target_level_ids"])<=ids for s in tree()["scenarios"])


def test_invalidations_reference_real_level_ids():
    ids={l["level_id"] for l in LEVELS}
    assert all(s["invalidation"]["level_id"] in ids for s in tree()["scenarios"])


def test_sources_reference_real_phase(): assert all(s["source_phase_ids"]==["p1"] for s in tree()["scenarios"])
def test_sources_reference_real_event(): assert all(s["source_event_ids"]==["e1"] for s in tree()["scenarios"])


def test_covering_changes_continuation_oi_confirmation():
    text=tree()["scenarios"][0]["oi_confirmation"]
    assert "after covering" in text and "moderate OI recovery" in text


def test_partial_quality_caps_likelihood():
    assert all(s["likelihood"]!="HIGH" for s in tree(phases=[phase(confidence="LOW")])["scenarios"])


def test_missing_levels_degrades_to_not_implemented(): assert tree(levels=[])["status"]=="NOT_IMPLEMENTED"
def test_unconfirmed_breakout_keeps_conditional_auditable_paths():
    value = tree(current="BREAKOUT_ATTEMPT")
    assert value["status"] == "AVAILABLE"
    assert len(value["scenarios"]) == 3
    assert all(item["trigger"]["level_ids"] and item["invalidation"]["level_id"]
               for item in value["scenarios"])
def test_non_breakout_range_has_no_scenarios(): assert tree(direction="NONE",current="RANGE_BUILDING")["scenarios"]==[]


def test_scenario_identity_is_stable(): assert tree()==tree()


def test_level_input_order_does_not_change_scenario_identity():
    a=tree(); b=tree(levels=list(reversed(LEVELS)))
    assert [x["scenario_id"] for x in a["scenarios"]]==[x["scenario_id"] for x in b["scenarios"]]


def test_no_precise_probability():
    assert all(s["likelihood"] in {"LOW","MEDIUM","HIGH"} for s in tree()["scenarios"])


def test_no_order_instruction(): assert "create_order" not in json.dumps(tree())
def test_no_position_size(): assert "position size" not in json.dumps(tree()).lower()
def test_no_stop_order(): assert "stop order" not in json.dumps(tree()).lower()


def test_invalidation_is_explicit_close_timeframe_and_level():
    for s in tree()["scenarios"]:
        assert "confirmed" in s["invalidation"]["rule"] and s["invalidation"]["timeframe"] and s["invalidation"]["level_id"]


def test_bearish_cvd_semantics_are_negative(): assert "negative" in tree("DOWN")["scenarios"][0]["cvd_confirmation"]
def test_failed_breakdown_cvd_semantics_are_positive(): assert "positive" in tree("DOWN")["scenarios"][2]["cvd_confirmation"]


def test_future_unused_level_does_not_rewrite_replay_at_old_cutoff():
    before=tree(); future=deepcopy(LEVELS)+[level("future",120,"RESISTANCE")]
    replay=tree(levels=future[:-1])
    assert before==replay
