from __future__ import annotations

from dashboard.approved_strategy_runtime import FrozenProgramEvaluator
from dashboard.factor_program_discovery import canonical_backtest, screen
from dashboard.factor_strategy_program import Condition, FactorStrategyProgram, generate, validate
from dashboard.strategy_router_v2 import StrategyRouterV2
from dashboard.approved_strategy_runtime import evaluate_frozen_candidate

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

def test_canonical_registry_runtime_executes_frozen_program_ast():
    p=program(); definition={"program_ast":p.canonical_ast(),"factor_versions":p.factor_versions,"program_version":p.grammar_version,"parameters":{},"timeframe":"15m"}
    registry={"registry_id":"asr_program","candidate_identity":p.identity,"configuration_hash":__import__("dashboard.approved_strategy_runtime",fromlist=["canonical_hash"]).canonical_hash(definition),"serialized_definition":definition,"parameters":{},"strategy_version":p.schema_version,"instrument_scope":["BTC-USDT"]}
    value=evaluate_frozen_candidate(registry,"BTC-USDT",rows())
    assert value["candidate_identity"]==p.identity and value["configuration_hash"]==registry["configuration_hash"]
