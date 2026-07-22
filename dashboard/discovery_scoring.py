"""Deterministic development-only Discovery eligibility, scoring and Pareto policy."""
from __future__ import annotations

import math
from collections.abc import Mapping

DISCOVERY_ELIGIBILITY_VERSION = "discovery-development-eligibility-v1"
DISCOVERY_SCORING_VERSION = "discovery-development-scoring-v1"
DISCOVERY_PARETO_VERSION = "discovery-development-pareto-v1"
PARETO_EPSILON = 1e-12
REASONS = ("CANDIDATE_NOT_DEVELOPMENT_COMPLETE", "INCOMPLETE_FOLD_SET", "FAILED_DEVELOPMENT_FOLD", "INSUFFICIENT_FOLDS_WITH_TRADES", "INSUFFICIENT_TOTAL_TRADES", "INSUFFICIENT_MEDIAN_TRADES", "INSUFFICIENT_PROFITABLE_FOLDS", "INSUFFICIENT_BENCHMARK_BEATING_FOLDS", "NONPOSITIVE_MEDIAN_EXCESS_RETURN", "WORST_FOLD_RETURN_TOO_LOW", "WORST_EXCESS_RETURN_TOO_LOW", "MAXIMUM_DRAWDOWN_TOO_HIGH", "REQUIRED_METRIC_UNDEFINED", "REQUIRED_METRIC_NONFINITE")
REQUIRED_ELIGIBILITY_METRICS = ("completed_fold_count", "failed_fold_count", "folds_with_trades", "total_trades", "median_trades_per_fold", "profitable_fold_ratio", "benchmark_beating_fold_ratio", "median_excess_return", "worst_validation_return", "worst_excess_return", "worst_maximum_drawdown", "validation_return_standard_deviation")
SCORE_SPECS = (("median_excess_return", 25, "linear", 0, 8), ("worst_excess_return", 15, "linear", -10, 3), ("profitable_fold_ratio", 15, "linear", .6, 1), ("benchmark_beating_fold_ratio", 15, "linear", .6, 1), ("worst_maximum_drawdown", 15, "inverse", 5, 20), ("validation_return_standard_deviation", 10, "inverse", 2, 12), ("structural_complexity", 5, "inverse", 5, 8))

def candidate_complexity(template, p):
    return 5 + int(bool(p.get("volume_enabled"))) + int(template == "TREND_PULLBACK") + (2 if template == "MEAN_REVERSION" else 0)

def eligibility_policy(timeframe):
    try:
        return {"15m": (40, 8), "1H": (20, 4), "4H": (10, 2), "1D": (5, 1)}[timeframe]
    except KeyError:
        raise ValueError("Unsupported Discovery timeframe: %s" % timeframe) from None

def _classification(value):
    if value is None:
        return "undefined"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "invalid"
    return "valid" if math.isfinite(float(value)) else "invalid"

def _metrics(aggregate, keys):
    aggregate = aggregate if isinstance(aggregate, Mapping) else {}
    return {key: _classification(aggregate.get(key)) if key in aggregate else "undefined" for key in keys}

def evaluate_eligibility(aggregate, timeframe, status="DEVELOPMENT_CANDIDATE"):
    # Validate timeframe before inspecting arbitrary persisted evidence.
    trades_threshold, median_threshold = eligibility_policy(timeframe)
    aggregate = aggregate if isinstance(aggregate, Mapping) else {}
    kinds = _metrics(aggregate, REQUIRED_ELIGIBILITY_METRICS)
    valid = lambda key: kinds[key] == "valid"
    reasons = []
    if status != "DEVELOPMENT_CANDIDATE": reasons.append(REASONS[0])
    if valid("completed_fold_count") and aggregate["completed_fold_count"] != 5: reasons.append(REASONS[1])
    if valid("failed_fold_count") and aggregate["failed_fold_count"] != 0: reasons.append(REASONS[2])
    if valid("folds_with_trades") and aggregate["folds_with_trades"] < 4: reasons.append(REASONS[3])
    if valid("total_trades") and aggregate["total_trades"] < trades_threshold: reasons.append(REASONS[4])
    if valid("median_trades_per_fold") and aggregate["median_trades_per_fold"] < median_threshold: reasons.append(REASONS[5])
    if valid("profitable_fold_ratio") and aggregate["profitable_fold_ratio"] < .6: reasons.append(REASONS[6])
    if valid("benchmark_beating_fold_ratio") and aggregate["benchmark_beating_fold_ratio"] < .6: reasons.append(REASONS[7])
    if valid("median_excess_return") and aggregate["median_excess_return"] <= 0: reasons.append(REASONS[8])
    if valid("worst_validation_return") and aggregate["worst_validation_return"] < -10: reasons.append(REASONS[9])
    if valid("worst_excess_return") and aggregate["worst_excess_return"] < -10: reasons.append(REASONS[10])
    if valid("worst_maximum_drawdown") and aggregate["worst_maximum_drawdown"] > 20: reasons.append(REASONS[11])
    if any(kind == "undefined" for kind in kinds.values()): reasons.append(REASONS[12])
    if any(kind == "invalid" for kind in kinds.values()): reasons.append(REASONS[13])
    return {"eligible": not reasons, "reasons": reasons}

def _linear(value, low, high): return 100 * max(0, min(1, (value - low) / (high - low)))
def _inverse(value, best, worst): return 100 * max(0, min(1, (worst - value) / (worst - best)))

def calculate_score(aggregate, complexity, timeframe=None):
    # timeframe is retained for the established public API; score policy is timeframe-neutral.
    if sum(spec[1] for spec in SCORE_SPECS) != 100: raise ValueError("Discovery score weights must sum to exactly 100")
    if isinstance(complexity, bool) or not isinstance(complexity, int) or not 5 <= complexity <= 8:
        raise ValueError("Discovery structural complexity must be an integer from 5 to 8")
    aggregate = aggregate if isinstance(aggregate, Mapping) else {}
    components, total = {}, 0.0
    for key, weight, transform, lower, upper in SCORE_SPECS:
        raw = complexity if key == "structural_complexity" else aggregate.get(key)
        if _classification(raw) != "valid": raise ValueError("Invalid Discovery score metric: %s" % key)
        normalized = round((_linear if transform == "linear" else _inverse)(float(raw), lower, upper), 6)
        contribution = round(normalized * weight / 100, 6)
        total += contribution
        components[key] = {"raw_metric_value": raw, "normalized_component_score": normalized, "weight": weight, "weighted_contribution": contribution}
    total = round(total, 6)
    if not math.isfinite(total) or not 0 <= total <= 100: raise ValueError("Discovery score is outside 0..100")
    return total, {"policy_version": DISCOVERY_SCORING_VERSION, "components": components, "final_score": total,
                   "warnings": ["Development folds only; primary holdout and final OOT excluded.", "Score is not proof of future profitability."]}

def _objectives(candidate):
    aggregate = candidate.get("aggregate", candidate)
    return (aggregate["median_excess_return"], aggregate["worst_excess_return"], -aggregate["worst_maximum_drawdown"], -aggregate["validation_return_standard_deviation"])

def _dominates(a, b):
    av, bv = _objectives(a), _objectives(b)
    return all(x >= y - PARETO_EPSILON for x, y in zip(av, bv)) and any(x > y + PARETO_EPSILON for x, y in zip(av, bv))

def _identity(candidate):
    return (candidate["parameter_hash"], candidate["candidate_number"])

def assign_pareto_fronts(candidates):
    """Return a pure candidate-identity -> positive front-rank mapping."""
    left = list(candidates); fronts = {}; rank = 1
    while left:
        front = [item for item in left if not any(_dominates(other, item) for other in left if other is not item)]
        for item in front: fronts[_identity(item)] = rank
        left = [item for item in left if item not in front]; rank += 1
    return fronts

def rank_eligible_candidates(candidates, pareto_ranks=None):
    """Return a pure candidate-identity -> contiguous eligible-rank mapping."""
    pareto_ranks = pareto_ranks if pareto_ranks is not None else assign_pareto_fronts(candidates)
    ordered = sorted(candidates, key=lambda item: (-item["development_score"], pareto_ranks[_identity(item)], -item["aggregate"]["median_excess_return"], -item["aggregate"]["worst_excess_return"], item["aggregate"]["worst_maximum_drawdown"], item["aggregate"]["validation_return_standard_deviation"], item["complexity"], item["parameter_hash"], item["candidate_number"]))
    return {_identity(item): index for index, item in enumerate(ordered, 1)}
