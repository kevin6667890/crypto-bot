"""Development-only bounded search for factor strategy programs.

This is intentionally independent from template discovery: it shares its
features, execution model and folds, but never changes template sampling.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import json
import os
from typing import Any, Mapping

from .approved_strategy_runtime import FrozenProgramEvaluator
from .backtest_engine import run_execution_backtest
from .discovery_features import build_features
from .factor_strategy_program import FactorStrategyProgram, generate, validate
from .strategy_rules import StrategyParameters

PROGRAM_DISCOVERY_ENABLED = os.getenv("PROGRAM_DISCOVERY_ENABLED", "false").lower() == "true"
PROGRAM_DISCOVERY_SEARCH_BUDGET = int(os.getenv("PROGRAM_DISCOVERY_SEARCH_BUDGET", "200"))
PROGRAM_DISCOVERY_POLICY_VERSION = "factor-program-discovery-v1"

def trigger_vector(program: FactorStrategyProgram, candles: list[Mapping[str, Any]], start: int, end: int) -> tuple[int, ...]:
    evaluator = FrozenProgramEvaluator(program)
    return tuple(int(row["ts"]) for index, row in enumerate(candles) if start <= int(row["ts"]) < end and evaluator.evaluate(candles, index)["action"] != "WAIT")

def screen(program: FactorStrategyProgram, candles: list[Mapping[str, Any]], folds: list[tuple[int, int]]) -> tuple[bool, list[str], tuple[int, ...]]:
    vector = trigger_vector(program, candles, min(x[0] for x in folds), max(x[1] for x in folds))
    counts = [sum(start <= x < end for x in vector) for start, end in folds]
    reasons = []
    if len(vector) < max(5, len(folds) * 2): reasons.append("INSUFFICIENT_SAMPLES")
    if sum(x > 0 for x in counts) < 2: reasons.append("SIGNALS_IN_ONLY_ONE_FOLD")
    if vector and max(counts) / len(vector) > .8: reasons.append("EXTREME_TRIGGER_CONCENTRATION")
    return not reasons, reasons, vector

def canonical_backtest(program: FactorStrategyProgram, candles: list[dict[str, Any]], instrument: str,
                       timeframe: str, start: int, end: int) -> dict[str, Any]:
    """Use the existing next-bar-open, fee/slippage/intrabar execution engine."""
    if validate(program): raise ValueError("PROGRAM_INVALID")
    if timeframe != program.timeframe: raise ValueError("PROGRAM_TIMEFRAME_MISMATCH")
    evaluator = FrozenProgramEvaluator(program)
    parameters = StrategyParameters(trading_fee=.0005, slippage=.0003, risk_per_trade=.01,
                                    max_notional_fraction=.25, enable_long=program.direction == "LONG",
                                    enable_short=program.direction == "SHORT", cooldown_bars=0)
    result = run_execution_backtest(candles, instrument, timeframe, parameters, start, end,
                                   signal_provider=lambda _row, index: evaluator.evaluate(candles, index))
    result["program_evidence"] = {"candidate_identity": program.identity, "configuration_hash": program.identity,
                                   "canonical_ast": program.canonical_ast(), "factor_versions": program.factor_versions,
                                   "policy_version": PROGRAM_DISCOVERY_POLICY_VERSION,
                                   "execution": "canonical-next-bar-open"}
    return result

def run(candles: list[dict[str, Any]], instrument: str, timeframe: str, folds: list[tuple[int, int]], *,
        seed: int = 20260820, budget: int = PROGRAM_DISCOVERY_SEARCH_BUDGET) -> dict[str, Any]:
    """Generate -> validate -> dedupe -> Development screening -> canonical backtest.

    ``folds`` must be development-only.  The caller owns all downstream
    walk-forward/holdout/OOT/cross-asset approval policy.
    """
    if not 1 <= budget <= 200: raise ValueError("program search budget must be 1..200")
    programs = generate(seed, budget)
    structurally_valid = [p for p in programs if not validate(p)]
    unique = {p.identity: p for p in structurally_valid}
    seen_vectors: set[tuple[int, ...]] = set(); screened = []; backtested = []
    for program in sorted(unique.values(), key=lambda p: p.identity):
        passed, reasons, vector = screen(program, candles, folds)
        if vector in seen_vectors: passed, reasons = False, ["DUPLICATE_TRIGGER_BEHAVIOR"]
        seen_vectors.add(vector)
        record = {"candidate_identity": program.identity, "program": program, "screen_reasons": reasons,
                  "trigger_count": len(vector), "complexity": program.complexity}
        screened.append(record)
        if passed:
            # Development-only canonical execution; no OOT access in this module.
            records = [canonical_backtest(program, candles, instrument, timeframe, start, end)
                       for start, end in folds]
            record["backtests"] = records; backtested.append(record)
    return {"discovery_mode": "PROGRAM", "enabled": PROGRAM_DISCOVERY_ENABLED,
            "policy_version": PROGRAM_DISCOVERY_POLICY_VERSION, "seed": seed, "budget": budget,
            "program_candidates": len(programs), "unique_programs": len(unique), "screened": len(screened),
            "backtested": len(backtested), "eligible": 0, "approved": 0,
            "items": [{k: v for k, v in item.items() if k != "program"} for item in screened]}

def persist_candidates(repository, discovery_run_id: int, programs: list[FactorStrategyProgram], *, seed: int) -> list[int]:
    """Persist immutable program provenance in the existing candidate table."""
    ids=[]
    with repository.connect() as c:
        offset=int(c.execute("SELECT COALESCE(MAX(candidate_number),0) FROM strategy_discovery_candidates WHERE discovery_run_id=?",(discovery_run_id,)).fetchone()[0])
        for number, program in enumerate(programs, 1):
            ast=program.canonical_ast(); identity=program.identity
            c.execute("""INSERT OR IGNORE INTO strategy_discovery_candidates(discovery_run_id,candidate_number,template,template_version,parameters,parameter_hash,feature_flags,complexity,status,created_at,program_ast,factor_versions,program_version,candidate_identity,configuration_hash,direction,program_timeframe)
                         VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?,?,?)""",(discovery_run_id,offset+number,"FACTOR_PROGRAM",program.grammar_version,"{}",identity,"{}",program.complexity,"GENERATED",json.dumps(ast,sort_keys=True),json.dumps(program.factor_versions,sort_keys=True),program.grammar_version,identity,identity,program.direction,program.timeframe))
            row=c.execute("SELECT id FROM strategy_discovery_candidates WHERE discovery_run_id=? AND candidate_number=?",(discovery_run_id,offset+number)).fetchone(); ids.append(int(row[0]))
    return ids
