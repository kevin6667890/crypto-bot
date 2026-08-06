from __future__ import annotations

from copy import deepcopy

import pytest

from dashboard.strategy_router_v2 import (
    ACTIVE_SETUP_STAGES, LIFECYCLE_IDENTITY_CONTRACT_VERSION,
    StrategyRouterV2, canonical_lifecycle_setup_key,
)
from tests.test_strategy_router_v2 import advance, candidate, inputs, interaction, ma_setup


ROUTER = StrategyRouterV2()
FAMILY = "MA200_MEAN_REVERSION"
DIRECTION = "LONG"


def _ineligible(previous=None, *, segment=None):
    value, state = inputs()
    state["level_interactions"] = []
    return ROUTER.route(value, state, previous_route=previous, segment_identity=segment)


def _watch(previous=None, *, segment="segment-a", parameter_set_id=None, parameter_set=None):
    value, state = ma_setup(touch=False)
    return ROUTER.route(value, state, previous_route=previous, segment_identity=segment,
                        **({"family": FAMILY, "direction": DIRECTION,
                            "parameter_set_id": parameter_set_id, "parameter_set": parameter_set}
                           if parameter_set_id else {}))


def _item(route, family=FAMILY, direction=DIRECTION):
    return candidate(route, family, direction)


def test_consecutive_ineligible_does_not_create_anchor_or_key():
    first = _ineligible(); second = _ineligible(first)
    for route in (first, second):
        identity = _item(route)["identity"]
        assert identity["strategy_setup_anchor_id"] is None
        assert identity["lifecycle_setup_key"] is None


@pytest.mark.parametrize("fallback_field", [
    "strategy_setup_id", "level_identity", "strategy_evaluation_id",
])
def test_ineligible_never_promotes_fallback_identity(fallback_field):
    first = _ineligible()
    _item(first)["identity"][fallback_field] = "legacy-fallback"
    identity = _item(_ineligible(first))["identity"]
    assert identity["strategy_setup_anchor_id"] is None
    assert identity["lifecycle_setup_key"] is None


def test_watch_first_creates_anchor_and_versioned_key():
    identity = _item(_watch())["identity"]
    assert identity["strategy_setup_anchor_id"]
    assert identity["lifecycle_setup_key"].startswith(
        LIFECYCLE_IDENTITY_CONTRACT_VERSION + ":" + identity["strategy_setup_anchor_id"] + ":")


def test_watch_armed_trigger_ready_keep_anchor():
    value, state = ma_setup(touch=False)
    watch = ROUTER.route(value, state, segment_identity="segment")
    state["level_interactions"][0]["interaction_type"] = "TOUCHING"
    state["timeframes"]["15m"]["momentum_state"] = "OVERSOLD"
    armed = ROUTER.route(value, state, previous_route=watch, segment_identity="segment")
    state["level_interactions"][0]["interaction_type"] = "RECLAIMED"
    state["level_interactions"][0]["reclaim_status"] = "RECLAIMED_ABOVE"
    state["timeframes"]["15m"]["momentum_state"] = "RECOVERING_FROM_OVERSOLD"
    value["timeframes"]["15m"]["volume"]["lower_wick_percentage"]["value"] = 60
    state["level_interactions"].append(interaction("SWING_HIGH", "FROM_BELOW", boundary=104))
    ready = ROUTER.route(value, state, previous_route=armed, segment_identity="segment")
    rows = [_item(route) for route in (watch, armed, ready)]
    assert [row["state"] for row in rows] == ["WATCH", "ARMED", "TRIGGER_READY"]
    assert len({row["identity"]["strategy_setup_anchor_id"] for row in rows}) == 1
    assert len({row["identity"]["lifecycle_setup_key"] for row in rows}) == 1


def test_invalidated_event_retains_anchor_then_ineligible_clears_it():
    value, state = ma_setup(touch=True); active = ROUTER.route(value, state)
    old = _item(active)["identity"]["strategy_setup_anchor_id"]
    broken = deepcopy(state); broken["level_interactions"][0]["current_stage"] = "MA200_BREAKDOWN_CONFIRMED"
    terminal = ROUTER.route(value, broken, previous_route=active)
    assert _item(terminal)["state"] == "INVALIDATED"
    assert _item(terminal)["identity"]["strategy_setup_anchor_id"] == old
    cleared = _ineligible(terminal)
    assert _item(cleared)["identity"]["strategy_setup_anchor_id"] is None
    assert _item(cleared)["identity"]["lifecycle_setup_key"] is None


def test_expired_event_retains_anchor_then_ineligible_clears_it():
    value, state = ma_setup(touch=True); active = ROUTER.route(value, state)
    old = _item(active)["identity"]["strategy_setup_anchor_id"]
    later = advance(value, seconds=20_000); later_state = deepcopy(state); later_state["as_of"] = later["as_of"]
    for frame in later_state["timeframes"].values(): frame["source_timestamps"] = [later["as_of"]]
    terminal = ROUTER.route(later, later_state, previous_route=active)
    assert _item(terminal)["state"] == "EXPIRED"
    assert _item(terminal)["identity"]["strategy_setup_anchor_id"] == old
    cleared = _ineligible(terminal)
    assert _item(cleared)["identity"]["strategy_setup_anchor_id"] is None


def test_new_setup_after_ineligible_has_new_anchor():
    first = _watch(); cleared = _ineligible(first, segment="segment-a")
    value, state = ma_setup(touch=False); value = advance(value, seconds=900)
    state = deepcopy(state); state["as_of"] = value["as_of"]
    for frame in state["timeframes"].values(): frame["source_timestamps"] = [value["as_of"]]
    second = ROUTER.route(value, state, previous_route=cleared, segment_identity="segment-a")
    assert _item(first)["identity"]["strategy_setup_anchor_id"] != _item(second)["identity"]["strategy_setup_anchor_id"]


def test_raw_lifecycle_key_is_rejected():
    previous = _watch(); _item(previous)["identity"]["lifecycle_setup_key"] = "raw-key"
    with pytest.raises(ValueError, match="RAW_LIFECYCLE_KEY_REJECTED"):
        _watch(previous)


def test_legacy_identity_fields_are_unavailable_not_guessed():
    previous = _ineligible(); del _item(previous)["identity"]["strategy_setup_anchor_id"]
    with pytest.raises(ValueError, match="LEGACY_IDENTITY_UNAVAILABLE"):
        _watch(previous)


@pytest.mark.parametrize(("field", "value"), [
    ("parameter_set_id", "p2"), ("direction", "SHORT"),
    ("family", "MA200_MEAN_REVERSION"), ("instrument", "ETH"),
    ("segment_identity", "s2"),
])
def test_canonical_key_isolated_by_all_contract_dimensions(field, value):
    base = dict(strategy_setup_anchor_id="anchor", instrument="BTC", family="TREND_PULLBACK",
                direction="LONG", parameter_set_id="p1", strategy_version="trend-pullback-v2",
                segment_identity="s1")
    key = canonical_lifecycle_setup_key(**base)
    variant = {**base, field: value}
    if field == "family":
        variant["strategy_version"] = "ma200-mean-reversion-v2"
    assert canonical_lifecycle_setup_key(**variant) != key


@pytest.mark.parametrize("stage", ["WATCH", "ARMED", "TRIGGER_READY",
                                    "TRIGGERED_RESEARCH_ONLY", "COOLDOWN_RESEARCH_ONLY"])
def test_active_stage_contract_is_exact(stage):
    assert stage in ACTIVE_SETUP_STAGES and len(ACTIVE_SETUP_STAGES) == 5
