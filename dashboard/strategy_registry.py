"""Approved Strategy Registry for immutable factor strategy programs."""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
from typing import Any
from .approved_strategy_runtime import FrozenProgramEvaluator, deserialize_program
from .factor_strategy_program import FactorStrategyProgram, validate

class ApprovedStrategyRegistry:
    def __init__(self, path: Path | str): self.path = Path(path); self.migrate()
    def connect(self):
        c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row; return c
    def migrate(self) -> None:
        with self.connect() as c: c.execute("CREATE TABLE IF NOT EXISTS approved_strategy_programs(id INTEGER PRIMARY KEY, status TEXT NOT NULL, candidate_identity TEXT NOT NULL UNIQUE, configuration_hash TEXT NOT NULL, program_ast TEXT NOT NULL, factor_versions TEXT NOT NULL, grammar_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    def approve(self, program: FactorStrategyProgram, configuration_hash: str | None = None) -> int:
        if validate(program): raise ValueError("REJECT_RUNTIME_NOT_EXECUTABLE")
        # Deserialize first: approval is impossible if restart recovery fails.
        ast = program.canonical_ast(); deserialize_program(ast); config = configuration_hash or program.identity
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO approved_strategy_programs(status,candidate_identity,configuration_hash,program_ast,factor_versions,grammar_version) VALUES('ACTIVE',?,?,?,?,?)", (program.identity, config, json.dumps(ast,sort_keys=True), json.dumps(program.factor_versions,sort_keys=True), program.grammar_version))
            return int(c.execute("SELECT id FROM approved_strategy_programs WHERE candidate_identity=?", (program.identity,)).fetchone()[0])
    def active(self) -> list[dict[str, Any]]:
        with self.connect() as c: return [dict(x) for x in c.execute("SELECT * FROM approved_strategy_programs WHERE status='ACTIVE' ORDER BY id")]
    def evaluators(self) -> list[FrozenProgramEvaluator]:
        return [FrozenProgramEvaluator(deserialize_program(json.loads(x["program_ast"])), registry_id=int(x["id"]), configuration_hash=str(x["configuration_hash"])) for x in self.active()]
