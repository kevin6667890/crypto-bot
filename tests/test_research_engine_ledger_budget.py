import sqlite3

import pytest

from research_engine.budget import ResearchBudget, ResearchBudgetManager
from research_engine.ledger import ExperimentLedger, HoldoutAccessBlocked, candidate_id


def record(**extra):
    base = {"research_run_id": "run-1", "research_cycle_id": "cycle-1", "hypothesis_id": "hyp-1",
            "candidate_id": "cand-1", "family": "TREND", "stage": "STAGE_1", "status": "FAILED",
            "metrics": {"total_trades": 3}, "parameters": {"lookback": 20}}
    base.update(extra)
    return base


def test_candidate_identity_is_deterministic_and_uses_all_economic_inputs():
    base = dict(strategy_logic="trend-v1", parameters={"lookback": 20}, features=["OHLCV", "BOLLINGER"],
                asset="BTC-USDT", timeframe="15m", dataset_version="dataset-a",
                execution_assumptions={"fee": 0.0005, "fill": "next_open"})
    first = candidate_id(**base)
    assert first == candidate_id(**{**base, "parameters": {"lookback": 20}})
    assert first != candidate_id(**{**base, "dataset_version": "dataset-b"})
    assert first != candidate_id(**{**base, "execution_assumptions": {"fee": 0.001, "fill": "next_open"}})


def test_ledger_is_append_only_and_keeps_failed_duplicate_candidate_events(tmp_path):
    ledger = ExperimentLedger(tmp_path / "ledger.db")
    ledger.append_experiment(record(experiment_id="one"))
    ledger.append_experiment(record(experiment_id="two", stage="STAGE_2", rejection_reason="PARAMETER_INSTABILITY"))
    events = ledger.events(candidate_id="cand-1")
    assert [item["experiment_id"] for item in events] == ["one", "two"]
    assert ledger.candidate_test_count("cand-1") == 2
    with ledger._connect() as con:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM experiment_ledger")


def test_budget_exhaustion_is_scoped_to_cycle_and_family_and_audited(tmp_path):
    ledger = ExperimentLedger(tmp_path / "ledger.db")
    manager = ResearchBudgetManager(ledger, ResearchBudget(max_candidates=2, max_hypotheses=1,
                                                             max_parameter_trials=3, max_holdout_access=1))
    assert manager.consume("cycle", "TREND", "candidates", quantity=2).allowed
    exhausted = manager.consume("cycle", "TREND", "candidates")
    assert exhausted.status == "BUDGET_EXHAUSTED" and exhausted.used == 2
    assert manager.consume("cycle", "MOMENTUM", "candidates").allowed
    assert manager.consume("new-cycle", "TREND", "candidates").allowed
    assert manager.usage("cycle", "TREND")["candidates"] == {"used": 2, "limit": 2}


def test_holdout_access_is_single_reveal_by_default_and_block_is_preserved(tmp_path):
    ledger = ExperimentLedger(tmp_path / "ledger.db")
    first = ledger.holdout_access(research_run_id="run", research_cycle_id="cycle", hypothesis_id="hyp",
                                  candidate_id="cand", dataset_version="data-v1", reason="stage-six")
    assert first["status"] == "ALLOWED"
    with pytest.raises(HoldoutAccessBlocked):
        ledger.holdout_access(research_run_id="run", research_cycle_id="cycle", hypothesis_id="hyp",
                              candidate_id="cand", dataset_version="data-v1", reason="accidental-repeat")
    with ledger._connect() as con:
        statuses = [row[0] for row in con.execute("SELECT status FROM holdout_access_log ORDER BY event_id")]
    assert statuses == ["ALLOWED", "BLOCKED_REPEATED_HOLDOUT_ACCESS"]
    assert ledger.events(candidate_id="cand")[-1]["status"] == "BLOCKED_REPEATED_HOLDOUT_ACCESS"


def test_repeated_holdout_requires_explicit_override_reason(tmp_path):
    ledger = ExperimentLedger(tmp_path / "ledger.db")
    kwargs = dict(research_run_id="run", research_cycle_id="cycle", hypothesis_id="hyp", candidate_id="cand",
                  dataset_version="data-v1", reason="stage-six")
    ledger.holdout_access(**kwargs)
    with pytest.raises(ValueError, match="override_reason"):
        ledger.holdout_access(**kwargs, override=True)
    result = ledger.holdout_access(**kwargs, override=True, override_reason="independent data correction")
    assert result["status"] == "REPEATED_HOLDOUT_ACCESS"
