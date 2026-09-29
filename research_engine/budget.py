"""Configurable family/cycle budgets backed by immutable ledger events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ledger import ExperimentLedger, utc_now

BudgetResource = Literal["hypotheses", "candidates", "parameter_trials", "holdout_access"]


@dataclass(frozen=True)
class ResearchBudget:
    max_hypotheses: int = 8
    max_candidates: int = 32
    max_parameter_trials: int = 64
    max_holdout_access: int = 1

    def limit_for(self, resource: BudgetResource) -> int:
        return int(getattr(self, f"max_{resource}"))

    def __post_init__(self) -> None:
        if any(value < 0 for value in (self.max_hypotheses, self.max_candidates,
                                       self.max_parameter_trials, self.max_holdout_access)):
            raise ValueError("research budget limits must be non-negative")


@dataclass(frozen=True)
class BudgetDecision:
    status: str
    resource: str
    used: int
    limit: int
    requested: int

    @property
    def allowed(self) -> bool:
        return self.status == "ALLOWED"


class ResearchBudgetManager:
    """Atomically reserve budget by cycle/family; failed requests are auditable."""
    def __init__(self, ledger: ExperimentLedger, default_budget: ResearchBudget | None = None):
        self.ledger = ledger
        self.default_budget = default_budget or ResearchBudget()

    def consume(self, research_cycle_id: str, family: str, resource: BudgetResource,
                *, quantity: int = 1, budget: ResearchBudget | None = None) -> BudgetDecision:
        if quantity <= 0:
            raise ValueError("budget quantity must be positive")
        policy = budget or self.default_budget
        limit = policy.limit_for(resource)
        with self.ledger._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            used = int(con.execute("""SELECT COALESCE(SUM(quantity), 0) FROM budget_events
                WHERE research_cycle_id=? AND family=? AND resource=? AND status='ALLOWED'""",
                (research_cycle_id, family, resource)).fetchone()[0])
            allowed = used + quantity <= limit
            status = "ALLOWED" if allowed else "BUDGET_EXHAUSTED"
            con.execute("""INSERT INTO budget_events
                (research_cycle_id,family,resource,quantity,limit_value,status,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (research_cycle_id, family, resource, quantity, limit, status, utc_now()))
        return BudgetDecision(status=status, resource=resource, used=used, limit=limit, requested=quantity)

    def usage(self, research_cycle_id: str, family: str, *, budget: ResearchBudget | None = None) -> dict[str, dict[str, int]]:
        policy = budget or self.default_budget
        with self.ledger._connect() as con:
            rows = con.execute("""SELECT resource, COALESCE(SUM(quantity), 0) AS used
                FROM budget_events WHERE research_cycle_id=? AND family=? AND status='ALLOWED'
                GROUP BY resource""", (research_cycle_id, family)).fetchall()
        used = {row["resource"]: int(row["used"]) for row in rows}
        return {resource: {"used": used.get(resource, 0), "limit": policy.limit_for(resource)}
                for resource in ("hypotheses", "candidates", "parameter_trials", "holdout_access")}
