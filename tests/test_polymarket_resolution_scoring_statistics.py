import math

import pytest

from dashboard.polymarket.resolution import classify_resolution
from dashboard.polymarket.scoring import executable_pnl_v1, paired_scores
from dashboard.polymarket.statistics import cohort_statistics


def test_resolution_classes_are_fail_closed():
    base = {"outcomes": '["Yes","No"]'}
    assert classify_resolution({**base, "closed": False})["classification"] == "UNRESOLVED"
    assert classify_resolution({**base, "closed": True, "outcomePrices": '["1","0"]'})["classification"] == "VALID_BINARY"
    assert classify_resolution({**base, "closed": True, "outcomePrices": '["0.5","0.5"]'})["classification"] == "AMBIGUOUS_50_50"
    assert classify_resolution({**base, "closed": True, "outcomePrices": '["0.7","0.3"]'})["classification"] == "AMBIGUOUS"
    assert classify_resolution({**base, "closed": True, "outcomePrices": "bad"})["classification"] == "INVALID"
    assert classify_resolution({**base, "closed": True, "cancelled": True})["classification"] == "CANCELLED"
    assert classify_resolution({**base, "closed": True, "resolution": "50/50"})["classification"] == "AMBIGUOUS_50_50"
    assert classify_resolution({**base, "closed": True, "resolutionStatus": "unknown"})["classification"] == "UNKNOWN"


def test_score_delta_is_market_minus_model():
    result = paired_scores(.8, .6, 1)
    assert result["brier_delta"] == pytest.approx(.12)
    assert result["log_loss_delta"] == pytest.approx(-math.log(.6) + math.log(.8))


def test_paper_execution_uses_ask_threshold_and_unknown_fee():
    no_trade = executable_pnl_v1(.54, .50, .56, .46, 1, minimum_edge=.05)
    assert no_trade["side"] == "NO_TRADE"
    assert no_trade["entry_ask"] is None

    yes = executable_pnl_v1(.70, .50, .56, .46, 1, contracts=2, minimum_edge=.05)
    assert yes["side"] == "YES"
    assert yes["entry_ask"] == .56  # never the .50 midpoint
    assert yes["gross_pnl"] == pytest.approx(.88)
    assert yes["fee_status"] == "UNKNOWN"
    assert yes["net_pnl"] is None
    assert len(yes["execution_policy_hash"]) == 64

    no = executable_pnl_v1(.30, .50, .56, .46, 0, minimum_edge=.05,
                            fee_model_version="fixture-fee-v1", estimated_fee=.02)
    assert no["side"] == "NO" and no["entry_ask"] == .46
    assert no["net_pnl"] == pytest.approx(.52)


def test_statistics_exclude_manual_and_report_cluster_counts():
    forecasts = [
        {"forecast_id": "a", "market_id": "m1", "producer_kind": "LLM", "statistical_cluster_id": "event-x"},
        {"forecast_id": "b", "market_id": "m2", "producer_kind": "LLM", "statistical_cluster_id": "event-x"},
        {"forecast_id": "c", "market_id": "m3", "producer_kind": "LLM", "statistical_cluster_id": "m3"},
        {"forecast_id": "manual", "market_id": "m4", "producer_kind": "MANUAL", "statistical_cluster_id": "m4"},
    ]
    def score(fid, p, y, delta, side="NO_TRADE"):
        return {"forecast_id": fid, "forecast_probability": p, "outcome_value": y,
                "forecast_brier": (p-y)**2, "market_brier": (p-y)**2 + delta,
                "forecast_log_loss": .5, "market_log_loss": .5 + delta,
                "brier_delta": delta, "log_loss_delta": delta,
                "executable_side": side, "executable_gross_pnl": .2 if side != "NO_TRADE" else 0,
                "executable_net_pnl": None, "fee_status": "UNKNOWN" if side != "NO_TRADE" else "NOT_APPLICABLE"}
    stats = cohort_statistics(
        forecasts=forecasts,
        scores=[score("a", .75, 1, .1, "YES"), score("b", .25, 0, -.1), score("manual", .9, 1, 1)],
        llm_attempts=[{"status": "SUCCEEDED"}, {"status": "FAILED"}],
        evidence_attempts=[{"status": "ACCEPTED"}, {"status": "REJECTED"}],
    )
    assert stats["forecast_count"] == 3
    assert stats["scored_count"] == 2
    assert stats["raw_resolved_forecasts"] == 2
    assert stats["unique_statistical_clusters"] == 1
    assert stats["wins_vs_market"] == 1 and stats["losses_vs_market"] == 1
    assert stats["LLM_attempt_success_rate"] == .5
    assert stats["evidence_admission_rate"] == .5
    assert stats["trade_count"] == 1 and stats["unknown_fee_count"] == 1
    assert stats["known_fee_net_pnl"] is None
    assert stats["calibration_bins"][7]["forecasts"] == 1
