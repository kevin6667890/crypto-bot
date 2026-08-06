"""Deterministic token-budgeted provider context compiler."""
from __future__ import annotations
from typing import Any
from .canonical import canonical_json, stable_hash
from .versions import AI_REPORT_CONTEXT_COMPILER_VERSION

MODE_INPUT_BUDGETS = {"QUICK": 5000, "FULL": 10000, "POSITION_AWARE": 12000}
CATEGORY_ORDER = {"WARNING":0,"TIMELINE":1,"ORDER_FLOW":2,"LEVEL":3,"SCENARIO":4,"TIMEFRAME":5,"POSITION":6,"MACRO":7}


def estimate_tokens(value: Any) -> int:
    # Conservative deterministic estimator for mixed Chinese/JSON input.
    return max(1, (len(canonical_json(value).encode("utf-8")) + 2) // 3)


def compile_report_context(registry: dict[str, Any], mode: str, max_tokens: int | None = None) -> dict[str, Any]:
    if mode not in MODE_INPUT_BUDGETS: raise ValueError("invalid report mode")
    budget = min(max_tokens or MODE_INPUT_BUDGETS[mode], MODE_INPUT_BUDGETS[mode], 12000)
    ordered = sorted(registry["facts"], key=lambda f:(CATEGORY_ORDER.get(f["category"],99),-f.get("priority",0),f["fact_id"]))
    kept, omitted = [], []
    envelope = {k:registry[k] for k in ("version","context_id","instrument","decision_time","allowed_directional_biases","max_confidence","allowed_market_phases")}
    for fact in ordered:
        candidate = {**envelope, "mode":mode, "facts":kept+[fact], "omitted_fact_ids":[]}
        if estimate_tokens(candidate) <= budget: kept.append(fact)
        else: omitted.append(fact["fact_id"])
    # Core warnings and invalidations are priority 100 and must fit; fail instead of cutting them.
    missing_core = [f["fact_id"] for f in ordered if f.get("priority") == 100 and f not in kept]
    if missing_core: raise ValueError(f"token budget cannot retain core facts: {missing_core}")
    compiled = {**envelope, "compiler_version": AI_REPORT_CONTEXT_COMPILER_VERSION, "mode": mode,
                "facts": kept, "numeric_registry": registry["numeric_registry"],
                "omitted_fact_ids": omitted, "context_warnings": (["CONTEXT_FACTS_OMITTED"] if omitted else [])}
    compiled["token_estimate"] = estimate_tokens(compiled)
    compiled["compiled_hash"] = stable_hash({k:v for k,v in compiled.items() if k != "token_estimate"})
    return compiled
