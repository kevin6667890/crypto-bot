from __future__ import annotations

from copy import deepcopy

from dashboard.ai_market_analysis.key_level_candidates import psychological_levels
from dashboard.ai_market_analysis.key_level_zones import merge_level_zones
from dashboard.ai_market_analysis.report_fact_registry import select_relevant_levels
from dashboard.ai_market_analysis.report_fact_registry import current_level_relevance


def candidate(price,source="RANGE_HIGH",tf="15m",detected=0,dynamic=False,family=None,touches=1,low=None,high=None):
    return {"candidate_id":f"c-{source}-{price}-{tf}","price":price,"source":source,"timeframe":tf,
            "detected":detected,"dynamic":dynamic,"version":"ai-key-level-engine-v1","zone_low":low or price,
            "zone_high":high or price,"source_family":family or source,"slope":0 if dynamic else None,
            "valid_until":900 if dynamic else None,"touch_count":touches,"evidence_paths":["/facts"],"quality":"VALID"}


def bars(closes):
    return [{"ts":i*900,"close_time":i*900,"open":v,"high":v+1,"low":v-1,"close":v} for i,v in enumerate(closes)]


def zones(items,price=100,direction="NONE",history=None):
    return merge_level_zones(items,price,2,history or bars([price]),(len(history or bars([price]))-1)*900,direction)


def test_range_high_low_candidates_remain_provenanced():
    out=zones([candidate(105),candidate(95,"RANGE_LOW")])
    assert {c["source"] for z in out for c in z["source_candidates"]}=={"RANGE_HIGH","RANGE_LOW"}


def test_breakout_boundary_flips_to_support():
    out=zones([candidate(100,"BREAKOUT_BOUNDARY")],110,"UP",bars([99,101,105,110]))
    assert out[0]["state"]=="FLIPPED" and out[0]["role"]=="SUPPORT"


def test_retest_zone_preserves_bounds():
    out=zones([candidate(100,"RETEST_ZONE",low=98,high=102)],110)
    assert (out[0]["zone_low"],out[0]["zone_high"])==(98,102)


def test_impulse_high_is_resistance(): assert zones([candidate(110,"IMPULSE_HIGH")])[0]["role"]=="RESISTANCE"
def test_swing_low_is_support(): assert zones([candidate(90,"CONFIRMED_SWING_LOW")])[0]["role"]=="SUPPORT"
def test_dynamic_ma_remains_active_not_flipped(): assert zones([candidate(99,"MA20",dynamic=True,family="MOVING_AVERAGE")],110,"UP")[0]["state"]=="ACTIVE"


def test_vpvr_sources_merge():
    out=zones([candidate(99,"VPVR_POC",family="VPVR"),candidate(99.1,"VPVR_VAL",family="VPVR")])
    assert len(out)==1 and out[0]["confluences"]==["VPVR"]


def test_psychological_steps_are_scale_aware_and_bounded():
    assert len(psychological_levels(1900))==5
    assert psychological_levels(70)!=psychological_levels(1900)


def test_nearby_multisource_candidates_merge():
    out=zones([candidate(99,"RANGE_HIGH",family="RANGE"),candidate(99.2,"BREAKOUT_BOUNDARY",family="BREAKOUT")])
    assert len(out)==1 and len(out[0]["confluences"])==2


def test_same_ma_family_deduplicates_strength():
    one=zones([candidate(99,"MA20",dynamic=True,family="MOVING_AVERAGE")])[0]
    three=zones([candidate(99,"MA20",dynamic=True,family="MOVING_AVERAGE"),candidate(99.1,"MA30",dynamic=True,family="MOVING_AVERAGE"),candidate(99.2,"EMA20",dynamic=True,family="MOVING_AVERAGE")])[0]
    assert one["strength"]==three["strength"]


def test_wick_crossing_does_not_break():
    history=[{"ts":0,"close_time":0,"open":101,"high":103,"low":95,"close":101}]
    assert zones([candidate(100,"CONFIRMED_SWING_LOW")],110,history=history)[0]["state"]=="ACTIVE"


def test_two_confirmed_closes_break_static_support():
    history=bars([101,99,98])
    assert zones([candidate(100,"CONFIRMED_SWING_LOW")],110,history=history)[0]["state"]=="BROKEN"


def test_down_breakout_flips_to_resistance():
    out=zones([candidate(100,"BREAKOUT_BOUNDARY")],90,"DOWN",bars([101,99,95,90]))
    assert out[0]["state"]=="FLIPPED" and out[0]["role"]=="RESISTANCE"


def test_far_weekly_level_is_ranked_after_near_level():
    out=zones([candidate(101,"MA20",dynamic=True),candidate(200,"PREVIOUS_WEEK_HIGH","1W")])
    assert out[0]["representative_price"]==101


def test_near_strong_level_is_first():
    out=zones([candidate(101,"BREAKOUT_BOUNDARY",touches=5),candidate(150,"CONFIRMED_SWING_HIGH","1D",touches=5)])
    assert out[0]["representative_price"]==101


def test_strength_has_major_ceiling():
    out=zones([candidate(100+i*.01,"BREAKOUT_BOUNDARY","1W",touches=20,family=f"f{i}") for i in range(20)],110,"UP",bars([99,110]))
    assert out[0]["strength"]=="MAJOR"


def test_psychological_alone_never_major(): assert zones([candidate(100,"PSYCHOLOGICAL_LEVEL",touches=99)])[0]["strength"]=="WEAK"


def test_total_level_limit():
    items=[candidate(50+i*5,"ROLLING_LOW",tf="1H") for i in range(30)]
    assert len(zones(items))<=12


def test_future_bar_does_not_rewrite_old_decision_state():
    item=[candidate(100,"CONFIRMED_SWING_LOW")]
    before=zones(item,110,history=bars([101,102]))
    after=zones(item,110,history=bars([101,102,90,89]))
    replay=merge_level_zones(item,110,2,bars([101,102,90,89])[:2],900,"NONE")
    assert before==replay and after[0]["state"]=="BROKEN"


def test_dynamic_identity_differs_from_static():
    a=zones([candidate(99,"MA20",dynamic=True)])[0]["level_id"]
    b=zones([candidate(99,"RANGE_HIGH",dynamic=False)])[0]["level_id"]
    assert a!=b


def test_source_family_confluence_not_raw_source_count():
    out=zones([candidate(99,"MA20",dynamic=True,family="MOVING_AVERAGE"),candidate(99.1,"EMA20",dynamic=True,family="MOVING_AVERAGE"),candidate(99.2,"VPVR_POC",family="VPVR")])[0]
    assert out["confluences"]==["MOVING_AVERAGE","VPVR"]


def test_only_active_or_valid_flipped_levels_are_current_candidates():
    def level(level_id, state, role, price):
        return {"level_id": level_id, "state": state, "role": role, "representative_price": price,
                "strength": "STRONG", "timeframes": ["1H"], "source_candidates": [],
                "confluences": [], "evidence_paths": [], "first_detected": 0}
    base = {"decision_time": "2026-08-21T00:00:00Z", "timeframe_structures": [
        {"timeframe": "15m", "last_confirmed_close": {"value": 100}}
    ], "scenario_tree": {"scenarios": []}, "key_levels": [
        level("active", "ACTIVE", "SUPPORT", 95), level("flipped", "FLIPPED", "RESISTANCE", 105),
        level("broken", "BROKEN", "RESISTANCE", 101), level("expired", "EXPIRED", "SUPPORT", 99),
    ]}
    assert {item["level_id"] for item in select_relevant_levels(base)} == {"active", "flipped"}


def test_current_relevance_excludes_remote_weekly_reference_without_deleting_it():
    base = {"decision_time": "2026-08-21T00:00:00Z", "timeframe_structures": [{
        "timeframe": "15m", "last_confirmed_close": {"value": 2390}, "atr": {"value": 18},
    }]}
    nearby = {"representative_price": 2400, "role": "RESISTANCE", "state": "ACTIVE", "timeframes": ["15m"]}
    remote = {"representative_price": 3000, "role": "RESISTANCE", "state": "ACTIVE", "timeframes": ["1W"]}
    broken = {"representative_price": 2400, "role": "RESISTANCE", "state": "BROKEN", "timeframes": ["15m"]}
    assert current_level_relevance(base, nearby)["current_eligible"] is True
    assert current_level_relevance(base, remote)["current_eligible"] is False
    assert current_level_relevance(base, remote)["reference_tier"] == "LONG_TERM_REFERENCE"
    assert current_level_relevance(base, broken)["current_eligible"] is False
