"""Thin, local-only orchestration layer for falsification-first research."""

from .schema import Candidate, Hypothesis, ResearchCycle, candidate_id

__all__ = ["Candidate", "Hypothesis", "ResearchCycle", "candidate_id"]
"""Thin, auditable orchestration primitives for quant research."""

from .budget import BudgetDecision, ResearchBudget, ResearchBudgetManager
from .ledger import ExperimentLedger, HoldoutAccessBlocked, candidate_id, deterministic_id

__all__ = ["BudgetDecision", "ExperimentLedger", "HoldoutAccessBlocked", "ResearchBudget",
           "ResearchBudgetManager", "candidate_id", "deterministic_id"]
