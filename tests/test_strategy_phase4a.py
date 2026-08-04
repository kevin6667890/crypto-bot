from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sqlite3

import pytest

from dashboard.strategy_phase4a import (
    AccountPolicyV2, ArtifactWriterV2, CostPolicyV2, EntryIntentV2,
    OOTAccessError, ReadOnlyOHLCVStoreV2, ReplayEventV2,
    StrategyBacktestEngineV2, TimeSegmentV2, bootstrap_expectancy_interval,
    chronological_segments, frozen_trials, stable_hash, structural_r,
)


def event(side: str = "LONG", trigger: int = 900, setup: str = "setup") -> ReplayEventV2:
    return ReplayEventV2(
        stable_hash([side, trigger, setup]), "BTC-USDT", "TREND_PULLBACK", side, "p1",
        "ARMED", "TRIGGER_READY", trigger, trigger, 0, trigger, (trigger,), "level", setup,
        "evaluation", "route", "PRICE_CONFIRMED_FLOW_MISSING", (), {"valid": True},
    )


def candle(ts: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"ts": ts, "candle_close_ts": ts+900, "open": open_, "high": high,
            "low": low, "close": close, "volume": 1, "confirmed": 1}


def run(side="LONG", stop=90, target=120, rows=None, policy="STOP_FIRST", cost=None):
    rows = rows or [candle(900, 100, 101, 99, 100), candle(1800, 100, 121, 89, 110)]
    if not any(row["candle_close_ts"] == 900 for row in rows):
        rows = [candle(0, 100, 101, 99, 100), *rows]
    intent = EntryIntentV2(event(side), side, stop, target, 10, 1)
    segment = TimeSegmentV2("D", 0, 10_000, "segment")
    return StrategyBacktestEngineV2(cost or CostPolicyV2(0, 0), intrabar_policy=policy).run(rows, [intent], segment=segment)


def test_trial_space_is_exactly_32_and_eight_each_direction():
    trials = frozen_trials("dataset")
    assert len(trials) == len({trial.trial_id for trial in trials}) == 32
    for family in ("TREND_PULLBACK", "MA200_MEAN_REVERSION"):
        for direction in ("LONG", "SHORT"):
            assert sum(t.family == family and t.direction == direction for t in trials) == 8


def test_parameter_identity_is_stable_and_isolated():
    one = frozen_trials("dataset"); two = frozen_trials("dataset")
    assert one == two
    assert one[0].trial_id != frozen_trials("another-dataset")[0].trial_id
    assert len({(t.direction, t.config_hash) for t in one}) == 32


def test_chronological_split_is_60_20_20_after_warmup():
    segments = chronological_segments(900, 100_000_000, 9_000)
    assert segments["DEVELOPMENT"].start_ts == 9_900
    assert segments["DEVELOPMENT"].end_ts == segments["VALIDATION"].start_ts
    assert segments["VALIDATION"].end_ts == segments["LOCKED_FINAL_OOT"].start_ts
    assert segments["LOCKED_FINAL_OOT"].end_ts == 100_000_000


def test_read_only_store_refuses_oot_and_unbounded_coverage(tmp_path: Path):
    db = tmp_path/"data.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE historical_candles(instrument,timeframe,ts,open,high,low,close,volume,confirmed,source)")
        connection.execute("INSERT INTO historical_candles VALUES('BTC-USDT','15m',0,1,1,1,1,1,1,'fixture')")
    store = ReadOnlyOHLCVStoreV2(db, dataset_identity="x", oot_start_ts=100)
    assert len(store.candles("BTC-USDT", "15m", 0, 100)) == 1
    with pytest.raises(OOTAccessError): store.candles("BTC-USDT", "15m", 0, 101)
    with pytest.raises(OOTAccessError): store.candles("BTC-USDT", "15m", 100, 100)
    with pytest.raises(OOTAccessError): store.coverage()


def test_entry_is_next_open_not_trigger_close():
    result = run(rows=[candle(0, 80, 81, 79, 80), candle(900, 100, 105, 95, 101),
                           candle(1800, 102, 121, 101, 120)])
    assert result["trades"][0]["entry_ts"] == 900
    assert result["trades"][0]["entry"] == 100


@pytest.mark.parametrize(("side", "expected"), [("LONG", 100.03), ("SHORT", 99.97)])
def test_entry_slippage_is_always_adverse(side, expected):
    stop, target = ((90, 120) if side == "LONG" else (110, 80))
    result = run(side, stop, target, rows=[candle(900, 100, 101, 99, 100),
                                           candle(1800, 100, 105, 95, 100),
                                           candle(2700, 100, 121, 79, 100)],
                 cost=CostPolicyV2(0, .0003))
    assert result["trades"][0]["entry"] == pytest.approx(expected)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_gap_invalidated_before_entry(side):
    stop, target, gap = ((90, 120, 89) if side == "LONG" else (110, 80, 111))
    result = run(side, stop, target, [candle(900, gap, gap+1, gap-1, gap)])
    assert result["trades"] == []
    assert result["rejections"][0]["classification"] == "GAP_INVALIDATED_BEFORE_ENTRY"


def test_geometry_rechecked_at_entry():
    result = run(stop=90, target=101, rows=[candle(900, 100, 100, 99, 100)])
    assert result["rejections"][0]["classification"] == "GEOMETRY_INVALID_AT_ENTRY"


@pytest.mark.parametrize(("high", "low", "reason"), [(105, 89, "STOP"), (121, 95, "TARGET")])
def test_single_intrabar_exit(high, low, reason):
    assert run(rows=[candle(900, 100, 101, 99, 100), candle(1800, 100, high, low, 100)])["trades"][0]["exit_reason"] == reason


def test_formal_intrabar_is_stop_first_and_diagnostics_differ():
    assert run()["trades"][0]["exit_reason"] == "STOP_FIRST"
    assert run(policy="TARGET_FIRST")["trades"][0]["exit_reason"] == "TARGET_FIRST"
    assert run(policy="DROP_AMBIGUOUS_BAR")["trades"] == []
    assert run()["ambiguous_intrabar_count"] == 1


@pytest.mark.parametrize(("side", "stop", "target", "gap", "reason"), [
    ("LONG", 90, 120, 85, "GAP_STOP"), ("SHORT", 110, 80, 115, "GAP_STOP"),
    ("LONG", 90, 120, 125, "GAP_TARGET"), ("SHORT", 110, 80, 75, "GAP_TARGET")])
def test_gap_policy(side, stop, target, gap, reason):
    rows = [candle(900, 100, 101, 99, 100), candle(1800, 100, 105, 95, 100),
            candle(2700, gap, gap+1, gap-1, gap)]
    trade = run(side, stop, target, rows)["trades"][0]
    assert trade["exit_reason"] == reason
    if reason == "GAP_TARGET": assert trade["exit"] == target
    else: assert trade["exit"] == gap


def test_timeout_and_maximum_holding_bars():
    intent = EntryIntentV2(event(), "LONG", 90, 120, 1, 1)
    rows = [candle(0, 100, 101, 99, 100), candle(900, 100, 101, 99, 100)]
    result = StrategyBacktestEngineV2(CostPolicyV2(0, 0)).run(rows, [intent], segment=TimeSegmentV2("D", 0, 10_000, "s"))
    assert result["trades"][0]["exit_reason"] == "TIMEOUT"


def test_fee_is_charged_both_sides():
    trade = run(cost=CostPolicyV2(.001, 0))["trades"][0]
    assert trade["fees"] == pytest.approx((trade["entry"]+trade["exit"])*trade["units"]*.001)


def test_risk_and_notional_cap_reduce_actual_risk():
    trade = run(stop=99, target=103)["trades"][0]
    assert trade["requested_risk"] == 100
    assert trade["notional"] <= 2500+1e-9
    assert trade["actual_risk"] < trade["requested_risk"]


def test_duplicate_setup_does_not_open_twice():
    e = event(); intent = EntryIntentV2(e, "LONG", 90, 120, 1, 1)
    rows = [candle(0, 100, 101, 99, 100), candle(900, 100, 121, 99, 110), candle(1800, 100, 121, 99, 110)]
    result = StrategyBacktestEngineV2(CostPolicyV2(0, 0)).run(rows, [intent, intent], segment=TimeSegmentV2("D", 0, 10_000, "s"))
    assert len(result["trades"]) == 1


def test_structural_r_rejects_wrong_geometry():
    assert structural_r(100, 90, 120, "LONG") == 2
    assert structural_r(100, 110, 80, "SHORT") == 2
    assert structural_r(100, 101, 120, "LONG") is None


def test_bootstrap_is_deterministic_and_null_when_insufficient():
    assert bootstrap_expectancy_interval([1, -1, 2], seed=7, repetitions=100) == bootstrap_expectancy_interval([1, -1, 2], seed=7, repetitions=100)
    assert bootstrap_expectancy_interval([], seed=7)["lower"] is None


def test_artifact_resume_is_idempotent_and_collision_safe(tmp_path: Path):
    writer = ArtifactWriterV2(tmp_path, "run")
    first = writer.jsonl("events.jsonl", [{"id": "a", "value": 1}], identity_key="id")
    before = first.read_bytes(); writer.jsonl("events.jsonl", [{"id": "a", "value": 1}], identity_key="id")
    assert first.read_bytes() == before
    with pytest.raises(ValueError): writer.jsonl("events.jsonl", [{"id": "a", "value": 2}], identity_key="id")
