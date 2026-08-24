from __future__ import annotations

import copy
import pytest

from dashboard.approved_strategy_runtime import FrozenProgramEvaluator
from dashboard.factor_program_discovery import canonical_backtest, screen
from dashboard.factor_strategy_program import Condition, FactorStrategyProgram, generate, validate
from dashboard.strategy_router_v2 import StrategyRouterV2
from dashboard.approved_strategy_runtime import evaluate_frozen_candidate
from dashboard.factor_program_discovery import persist_candidates
from dashboard.research_repository import ResearchRepository
from dashboard.automatic_research import (
    FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE,
    factor_program_development_batches,
    release_factor_program_transients,
)

def rows(count=90):
    return [{"ts": i * 900, "open": 100 + i * .1, "high": 101 + i * .1, "low": 99 + i * .1,
             "close": 100 + i * .1, "volume": 100 + i} for i in range(count)]

def program():
    return FactorStrategyProgram("LONG", (Condition("ma_slope", ">", 0),),
                                 (Condition("rsi_14", "<", 101),),
                                 (Condition("close", "cross_above", "ema_20"),))

def test_generation_is_seeded_not_template_parameters():
    first, second = generate(17, 12), generate(17, 12)
    assert first and [x.identity for x in first] == [x.identity for x in second]
    assert all(not validate(x) and x.environment and x.setup and x.trigger for x in first)

def test_canonical_duplicate_and_garbage_screening():
    p = program(); assert p.identity == FactorStrategyProgram("LONG", p.environment, p.setup, p.trigger).identity
    passed, reasons, _ = screen(p, rows(), [(0, 27000), (27000, 54000)])
    assert not passed and "INSUFFICIENT_SAMPLES" in reasons

def test_frozen_registry_router_identity_is_identical(tmp_path):
    p = program(); registry_id = 7; evaluator = FrozenProgramEvaluator(p, registry_id=registry_id)
    signal = StrategyRouterV2().approved_program_signals(rows(), [evaluator])[0]
    assert signal["registry_id"] == registry_id
    assert signal["candidate_identity"] == p.identity == signal["config_hash"]
    assert signal["program_version"] == p.grammar_version

def test_canonical_backtest_accepts_frozen_program():
    p = program(); result = canonical_backtest(p, rows(100), "BTC-USDT", "15m", 0, 90 * 900)
    assert result["program_evidence"]["candidate_identity"] == p.identity

def frozen_program_registry():
    p=program(); definition={"program_ast":p.canonical_ast(),"factor_versions":p.factor_versions,"program_version":p.grammar_version,"parameters":{},"timeframe":"15m","runtime_adapter_version":"approved-strategy-runtime-v2.1-v1","validation_status":{"development":"PASS","walk_forward":"PASS","holdout":"PASS","oot":"PASS","cross_asset":"PASS","robustness":"PASS"},"execution_assumptions":{"initial_capital":10000.0,"risk_per_trade":.01,"trading_fee":.0005,"slippage":.0003,"stop_loss_atr_multiplier":1.0,"risk_reward_ratio":2.0,"cooldown_bars":16,"allow_long":True,"allow_short":True},"activation_scope":{"mode":"GLOBAL_CROSS_ASSET","instruments":["BTC-USDT","ETH-USDT","SOL-USDT"]}}
    return p,{"registry_id":"asr_program","candidate_identity":p.identity,"configuration_hash":__import__("dashboard.approved_strategy_runtime",fromlist=["canonical_hash"]).canonical_hash(definition),"serialized_definition":definition,"parameters":{},"strategy_version":p.schema_version,"instrument_scope":["BTC-USDT","ETH-USDT","SOL-USDT"],"timeframe":"15m"}


def test_canonical_registry_runtime_executes_frozen_program_ast():
    p,registry=frozen_program_registry()
    value=evaluate_frozen_candidate(registry,"BTC-USDT",rows(),snapshot_identity="synthetic-market-snapshot")
    assert value["candidate_identity"]==p.identity and value["configuration_hash"]==registry["configuration_hash"]


@pytest.mark.parametrize("mutation",("hash","scope","timeframe"))
def test_frozen_program_wrong_hash_scope_or_timeframe_fails_closed(mutation):
    _,registry=frozen_program_registry(); registry=copy.deepcopy(registry)
    if mutation=="hash": registry["configuration_hash"]="0"*64
    elif mutation=="scope": registry["instrument_scope"]=["ETH-USDT","SOL-USDT"]
    else: registry["timeframe"]="1H"
    with pytest.raises(ValueError):
        evaluate_frozen_candidate(registry,"BTC-USDT",rows(),snapshot_identity="synthetic-market-snapshot")


def test_frozen_program_requires_external_market_snapshot_identity():
    _,registry=frozen_program_registry()
    with pytest.raises(ValueError,match="snapshot identity"):
        evaluate_frozen_candidate(registry,"BTC-USDT",rows())

def test_program_candidate_is_durable_in_existing_candidate_table(tmp_path):
    repo=ResearchRepository(tmp_path/"research.db")
    with repo.connect() as c:
        c.execute("INSERT INTO discovery_datasets(name,start_ts,end_ts,instruments,timeframes,source,status,manifest,created_at,updated_at) VALUES('d',0,1,'[]','[]','x','COMPLETE','{}','x','x')")
        dataset=c.execute("SELECT id FROM discovery_datasets").fetchone()[0]
        run=c.execute("INSERT INTO strategy_discovery_runs(dataset_id,status,request,search_policy,sampler,seed,maximum_trials,templates,feature_version,engine_version,scoring_version,progress,created_at,updated_at) VALUES(?, 'COMPLETED','{}','{}','program',1,1,'[\"FACTOR_PROGRAM\"]','x','x','x','{}','x','x')",(dataset,)).lastrowid
    persist_candidates(repo,run,[program()],seed=1)
    with repo.connect() as c: row=c.execute("SELECT program_ast,factor_versions,program_version,candidate_identity,configuration_hash,direction,program_timeframe,complexity FROM strategy_discovery_candidates").fetchone()
    assert row[0] and row[1] and row[3]==row[4]==program().identity and row[5]=="LONG"


def test_development_batches_bound_500_candidates_and_resume_without_replaying_completed_work():
    rows = [{"id": index, "status": "GENERATED"} for index in range(1, 501)]
    batches = factor_program_development_batches(rows)
    assert FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE == 25
    assert len(batches) == 20
    assert all(len(batch) <= FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE for batch in batches)
    assert [row["id"] for batch in batches for row in batch] == list(range(1, 501))

    # Candidate completion is durable before the batch checkpoint.  A restart
    # therefore selects only unfinished IDs and cannot repeat prior batches.
    resumed = [{"id": index, "status": "DEVELOPMENT_CANDIDATE" if index <= 50 else "GENERATED"}
               for index in range(1, 501)]
    resumed_batches = factor_program_development_batches(resumed)
    assert [row["id"] for batch in resumed_batches for row in batch] == list(range(51, 501))
    # Fixed ordering means batch evaluation has the same candidate order as an
    # equivalent single-pass evaluation.
    assert [row["id"] for batch in batches for row in batch] == [row["id"] for row in rows]


def test_development_batch_transient_release_is_safe_for_each_completed_candidate():
    # Linux workers trim large canonical-backtest allocator arenas; other
    # supported environments retain the same safe no-op fallback.
    release_factor_program_transients()


def test_240_bar_causal_fold_window_matches_full_development_backtest_metrics():
    all_rows = rows(600)
    start, end = 400 * 900, 550 * 900
    full = canonical_backtest(program(), all_rows, "BTC-USDT", "15m", start, end)
    bounded = canonical_backtest(program(), all_rows[160:550], "BTC-USDT", "15m", start, end)
    assert bounded["metrics"] == full["metrics"]
