from __future__ import annotations

from copy import deepcopy

from dashboard.market_state_v2 import MarketStateEngineV2
from dashboard.strategy_router_v2 import (
    StrategyRouterV2, level_continuity_identity,
)
from scripts.run_strategy_phase4a_state_transition_repair import (
    ORIGINAL_MANIFEST, full_chain_witnesses,
)
from dashboard.strategy_phase4a_router_repair import trials_from_original_manifest
from tests.test_strategy_router_v2 import advance, candidate, inputs, interaction, ma_setup


ROUTER = StrategyRouterV2()
TRIALS = trials_from_original_manifest(ORIGINAL_MANIFEST)


def _identity(level_type="EMA20", timeframe="1H", boundary=100.0, sources=(10,), **extra):
    level = {"level_type": level_type, "timeframe": timeframe, "boundary": boundary,
             "source_timestamps": list(sources), **extra}
    return level_continuity_identity("BTC-USDT-SWAP", level)


def _bump(value, state, seconds=900):
    value = advance(value, seconds=seconds)
    state = deepcopy(state); state["as_of"] = value["as_of"]
    for frame in state["timeframes"].values():
        frame["source_timestamps"] = [value["as_of"]]
    return value, state


def test_same_ema_pullback_across_three_bars_keeps_setup_anchor():
    row = next(item for item in full_chain_witnesses(TRIALS)
               if (item["family"], item["direction"]) == ("TREND_PULLBACK", "LONG"))
    assert [item["stage"] for item in row["trace"]] == ["WATCH", "ARMED", "TRIGGER_READY"]
    assert len({item["setup_anchor_identity"] for item in row["trace"]}) == 1


def test_moving_average_exact_identity_can_change_while_continuity_does_not():
    from dashboard.strategy_router_v2 import exact_level_identity
    a = {"level_type": "EMA20", "timeframe": "1H", "boundary": 100.0, "source_timestamps": [10]}
    b = {**a, "boundary": 100.25, "source_timestamps": [20]}
    assert exact_level_identity(a) != exact_level_identity(b)
    assert level_continuity_identity("BTC", a) == level_continuity_identity("BTC", b)


def test_new_confirmed_swing_changes_level_continuity_identity():
    assert _identity("SWING_LOW", sources=(10,)) != _identity("SWING_LOW", sources=(20,))


def test_ma200_one_hour_and_four_hour_are_distinct():
    assert _identity("MA200", "1H") != _identity("MA200", "4H")


def test_long_and_short_setup_anchors_are_distinct():
    rows = full_chain_witnesses(TRIALS)
    ids = {row["direction"]: row["trace"][0]["setup_anchor_identity"]
           for row in rows if row["family"] == "TREND_PULLBACK"}
    assert ids["LONG"] != ids["SHORT"]


def test_parameter_sets_are_distinct():
    value, state = ma_setup(touch=True)
    choices = [trial for trial in TRIALS if trial.family == "MA200_MEAN_REVERSION" and trial.direction == "LONG"]
    routes = [ROUTER.route(value, state, family=x.family, direction=x.direction,
                           parameter_set_id=x.parameter_set_id, parameter_set=x.parameters)
              for x in choices[:2]]
    assert routes[0]["candidates"][0]["identity"]["strategy_setup_anchor_id"] != routes[1]["candidates"][0]["identity"]["strategy_setup_anchor_id"]


def test_invalidated_setup_reforms_with_new_anchor():
    value, state = inputs({"1W": "up", "1D": "up", "4H": "up", "1H": "down", "15m": "down"})
    state["level_interactions"] = [interaction("EMA20", "FROM_ABOVE")]
    watch = ROUTER.route(value, state); old = candidate(watch, "TREND_PULLBACK", "LONG")["identity"]["strategy_setup_anchor_id"]
    broken = deepcopy(state); broken["overlays"].append("BREAKDOWN_CONFIRMED")
    invalid = ROUTER.route(value, broken, previous_route=watch)
    assert candidate(invalid, "TREND_PULLBACK", "LONG")["state"] == "INVALIDATED"
    next_value, next_state = _bump(value, state)
    reformed = ROUTER.route(next_value, next_state, previous_route=invalid)
    assert candidate(reformed, "TREND_PULLBACK", "LONG")["identity"]["strategy_setup_anchor_id"] != old


def test_expired_setup_reforms_with_new_anchor():
    value, state = ma_setup(touch=True); armed = ROUTER.route(value, state)
    old = candidate(armed, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]
    later, later_state = _bump(value, state, 20_000)
    expired = ROUTER.route(later, later_state, previous_route=armed)
    assert candidate(expired, "MA200_MEAN_REVERSION", "LONG")["state"] == "EXPIRED"
    again, again_state = _bump(later, state)
    reformed = ROUTER.route(again, again_state, previous_route=expired)
    assert candidate(reformed, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"] != old


def test_segment_boundary_never_reuses_anchor():
    value, state = ma_setup(touch=True)
    a = ROUTER.route(value, state, segment_identity="segment-a")
    b = ROUTER.route(value, state, segment_identity="segment-b")
    assert candidate(a, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"] != candidate(b, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]


def test_different_confluence_members_are_distinct_and_member_order_is_canonical():
    base = {"level_type": "CONFLUENCE_ZONE", "timeframe": "MULTI", "boundary": 100,
            "source_timestamps": [10]}
    a = {**base, "level_continuity_sources": ["ema", "swing-a"]}
    reordered = {**base, "level_continuity_sources": ["swing-a", "ema"]}
    b = {**base, "level_continuity_sources": ["ema", "swing-b"]}
    assert level_continuity_identity("BTC", a) == level_continuity_identity("BTC", reordered)
    assert level_continuity_identity("BTC", a) != level_continuity_identity("BTC", b)


def test_evidence_order_does_not_change_continuity():
    value, state = ma_setup(touch=True); first = ROUTER.route(value, state)
    changed = deepcopy(first); item = candidate(changed, "MA200_MEAN_REVERSION", "LONG")
    item["supporting_evidence"] = tuple(reversed(item["supporting_evidence"]))
    second = ROUTER.route(value, state, previous_route=changed)
    assert candidate(second, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"] == candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]


def test_current_score_does_not_change_continuity():
    value, state = ma_setup(touch=True); first = ROUTER.route(value, state)
    changed = deepcopy(first); candidate(changed, "MA200_MEAN_REVERSION", "LONG")["score"] += 1
    second = ROUTER.route(value, state, previous_route=changed)
    assert candidate(second, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"] == candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]


def test_evaluation_identity_changes_each_snapshot():
    value, state = ma_setup(touch=True); first = ROUTER.route(value, state)
    next_value, next_state = _bump(value, state)
    second = ROUTER.route(next_value, next_state, previous_route=first)
    a = candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity"]
    b = candidate(second, "MA200_MEAN_REVERSION", "LONG")["identity"]
    assert a["strategy_setup_anchor_id"] == b["strategy_setup_anchor_id"]
    assert a["strategy_evaluation_id"] != b["strategy_evaluation_id"]


def test_checkpoint_shaped_route_resume_keeps_anchor():
    value, state = ma_setup(touch=True); first = ROUTER.route(value, state, segment_identity="segment")
    checkpoint = deepcopy(first)
    next_value, next_state = _bump(value, state)
    resumed = ROUTER.route(next_value, next_state, previous_route=checkpoint, segment_identity="segment")
    assert candidate(first, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"] == candidate(resumed, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]


def test_trigger_ready_is_not_emitted_twice_for_same_setup():
    value, state = ma_setup(touch=True, reclaim=True)
    value["timeframes"]["15m"]["volume"]["lower_wick_percentage"]["value"] = 60
    state["timeframes"]["15m"]["momentum_state"] = "RECOVERING_FROM_OVERSOLD"
    state["level_interactions"].append(interaction("SWING_HIGH", "FROM_BELOW", boundary=104))
    ready = ROUTER.route(value, state)
    assert candidate(ready, "MA200_MEAN_REVERSION", "LONG")["state"] == "TRIGGER_READY"
    next_value, next_state = _bump(value, state)
    next_route = ROUTER.route(next_value, next_state, previous_route=ready)
    stages = [item["to_state"] for item in ready["transitions"] + next_route["transitions"]
              if item["strategy_setup_id"] == candidate(ready, "MA200_MEAN_REVERSION", "LONG")["identity"]["strategy_setup_anchor_id"]]
    assert stages.count("TRIGGER_READY") == 1
