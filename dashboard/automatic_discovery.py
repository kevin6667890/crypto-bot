"""Development-only orchestration for deterministic automatic program discovery."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable

from .discovery_execution import DiscoveryExecutionConfig
from .discovery_scoring import (
    assign_pareto_fronts, calculate_score, evaluate_eligibility, rank_eligible_candidates,
)
from .discovery_service import FOLDS, buy_and_hold
from .okx_history import TIMEFRAME_SECONDS
from .strategy_program import (
    ALLOWED_CONTEXT, PRIMARY_TIMEFRAMES, SEARCH_BUDGETS, STRATEGY_SEARCH_POLICY_VERSION,
    StrategyProgram, build_program_identity, generate_programs, neighborhood_variants,
)
from .strategy_program_runtime import (
    DEVELOPMENT_END_TS, align_higher_timeframe, behavior_deduplicate,
    build_program_features, evaluate_trigger_vector, event_study,
    run_program_backtest, summarize_backtests,
)

CANONICAL_DATASET_FINGERPRINT = "0bafcc5be02b513e5ea060d9e4d394c29915af8b67f99627eb9b4761c33683cd"
PHASE6A_RESULT_VERSION = "phase6a-automatic-discovery-result-v1"
FOLD_SET_IDENTITY = {
    "version": "development-five-fold-v1",
    "development_start": 1704067200,
    "development_end_exclusive": DEVELOPMENT_END_TS,
    "folds": [
        {"fold": number, "train_start": train_start, "train_end": train_end,
         "validation_start": validation_start, "validation_end_exclusive": validation_end}
        for number, (train_start, train_end, validation_start, validation_end) in enumerate(FOLDS, 1)
    ],
}


def _round_robin(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Fair deterministic budget allocation across all primary timeframes."""
    groups = {
        timeframe: [item for item in items if item["program"].timeframe == timeframe]
        for timeframe in PRIMARY_TIMEFRAMES
    }
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and any(groups.values()):
        for timeframe in PRIMARY_TIMEFRAMES:
            if groups[timeframe] and len(selected) < limit:
                selected.append(groups[timeframe].pop(0))
    return selected


class DevelopmentData:
    """Read-only, end-exclusive access to the working database copy."""

    def __init__(self, database: Path):
        self.database = Path(database)
        self._candles: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._features: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.maximum_accessed: dict[tuple[str, str], int] = {}

    def candles(self, instrument: str, timeframe: str) -> list[dict[str, Any]]:
        key = (instrument, timeframe)
        if key not in self._candles:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row) for row in connection.execute(
                    """SELECT ts,open,high,low,close,volume,confirmed,source
                       FROM historical_candles
                       WHERE instrument=? AND timeframe=? AND ts<?
                       ORDER BY ts""",
                    (instrument, timeframe, DEVELOPMENT_END_TS),
                )
            ]
            connection.close()
            if not rows or max(int(row["ts"]) for row in rows) >= DEVELOPMENT_END_TS:
                raise ValueError("Development loader boundary violation")
            if any(not int(row.get("confirmed", 1)) for row in rows):
                raise ValueError("unconfirmed candles are not permitted")
            self._candles[key] = rows
            self.maximum_accessed[key] = max(int(row["ts"]) for row in rows)
        return self._candles[key]

    def features(self, instrument: str, timeframe: str) -> list[dict[str, Any]]:
        key = (instrument, timeframe)
        if key not in self._features:
            self._features[key] = build_program_features(self.candles(instrument, timeframe))
        return self._features[key]

    def aligned(self, instrument: str, program: StrategyProgram) -> list[dict[str, Any] | None]:
        if program.higher_timeframe_context is None:
            return [None] * len(self.candles(instrument, program.timeframe))
        context_timeframe = str(program.higher_timeframe_context.params()["timeframe"])
        if context_timeframe not in ALLOWED_CONTEXT[program.timeframe]:
            raise ValueError("invalid higher-timeframe alignment")
        return align_higher_timeframe(
            self.candles(instrument, program.timeframe), program.timeframe,
            self.candles(instrument, context_timeframe), context_timeframe,
            self.features(instrument, context_timeframe),
        )


def _event_rank(item: dict[str, Any]) -> tuple[Any, ...]:
    aggregates = item["event_study"]["aggregate"]
    medians = [
        aggregates[str(horizon)]["median_forward_return"]
        for horizon in (1, 2, 4, 8, 16)
        if aggregates[str(horizon)]["median_forward_return"] is not None
    ]
    positives = sum(value > 0 for value in medians)
    median_sum = sum(medians)
    return (-positives, -median_sum, item["program"].complexity, item["program"].semantic_identity)


def _screen_events(item: dict[str, Any]) -> list[str]:
    timeframe = item["program"].timeframe
    minimum = {"15m": 20, "1H": 10, "4H": 5}[timeframe]
    counts = item["fold_event_counts"]
    total = sum(counts)
    reasons: list[str] = []
    if total < minimum:
        reasons.append("INSUFFICIENT_SAMPLE")
    if sum(count > 0 for count in counts) < 2:
        reasons.append("SIGNALS_IN_ONLY_ONE_FOLD")
    aggregates = item["event_study"]["aggregate"]
    medians = [
        aggregates[str(horizon)]["median_forward_return"]
        for horizon in (1, 2, 4, 8, 16)
        if aggregates[str(horizon)]["median_forward_return"] is not None
    ]
    if medians and all(value <= 0 for value in medians):
        reasons.append("CONSISTENTLY_ADVERSE_FORWARD_OUTCOMES")
    if total and max(counts) / total > .8:
        reasons.append("EXTREME_TRIGGER_CONCENTRATION")
    if item["invalid_geometry_count"]:
        reasons.append("INVALID_GEOMETRY")
    return reasons


def _combine_event_studies(studies: list[dict[str, Any]]) -> dict[str, Any]:
    events = [event for study in studies for event in study["events"]]
    aggregate: dict[str, Any] = {}
    for horizon in (1, 2, 4, 8, 16):
        labels = [event["labels"][str(horizon)] for event in events if str(horizon) in event["labels"]]
        returns = sorted(label["direction_adjusted_forward_return"] for label in labels)
        median = (
            returns[len(returns) // 2] if len(returns) % 2
            else (returns[len(returns) // 2 - 1] + returns[len(returns) // 2]) / 2
        ) if returns else None
        aggregate[str(horizon)] = {
            "event_count": len(labels),
            "median_forward_return": median,
            "profitable_event_ratio": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "median_mfe": None if not labels else sorted(label["mfe"] for label in labels)[len(labels) // 2],
            "median_mae": None if not labels else sorted(label["mae"] for label in labels)[len(labels) // 2],
            "stop_first_ratio": sum(label["stop_first"] for label in labels) / len(labels) if labels else None,
            "target_first_ratio": sum(label["target_first"] for label in labels) / len(labels) if labels else None,
        }
    fold_distribution = {
        str(study["fold_start"]): len(study["events"]) for study in studies
    }
    total = len(events)
    return {
        "events": events,
        "aggregate": aggregate,
        "fold_distribution": fold_distribution,
        "trigger_concentration": (
            max(fold_distribution.values()) / total if total else None
        ),
        "diagnostic_labels_only": True,
    }


def stage_a(data: DevelopmentData, programs: list[StrategyProgram]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for program in programs:
        candles = data.candles("BTC-USDT", program.timeframe)
        features = data.features("BTC-USDT", program.timeframe)
        aligned = data.aligned("BTC-USDT", program)
        all_triggers: list[int] = []
        fold_counts: list[int] = []
        studies: list[dict[str, Any]] = []
        invalid_geometry = 0
        lifecycle: list[dict[str, Any]] = []
        for fold_number, (_, _, validation_start, validation_end) in enumerate(FOLDS, 1):
            identity = {"fold": fold_number, "validation_start": validation_start,
                        "validation_end_exclusive": validation_end}
            vector, evaluator = evaluate_trigger_vector(
                program, candles, features, validation_start, validation_end,
                instrument="BTC-USDT", dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
                fold_identity=identity, aligned_context=aligned,
            )
            all_triggers.extend(vector)
            fold_counts.append(len(vector))
            invalid_geometry += sum(
                not evidence["geometry_valid"] for evidence in evaluator.trigger_evidence.values()
            )
            studies.append(event_study(
                program, vector, candles, features, validation_start, validation_end
            ))
            lifecycle.append({
                "fold": fold_number, "transition_count": len(evaluator.transitions),
                "trigger_count": len(vector),
                "maximum_triggers_per_setup": max(
                    Counter(
                        evidence["setup_id"] for evidence in evaluator.trigger_evidence.values()
                        if evidence["setup_id"]
                    ).values(),
                    default=0,
                ),
                "trigger_evidence": list(evaluator.trigger_evidence.values()),
            })
        candidates.append({
            "instrument": "BTC-USDT", "program": program,
            "trigger_vector": tuple(all_triggers), "fold_event_counts": fold_counts,
            "invalid_geometry_count": invalid_geometry,
            "event_study": _combine_event_studies(studies),
            "lifecycle_evidence": lifecycle,
        })
    deduplicated, aliases = behavior_deduplicate(candidates)
    behavior_duplicate_count = len(candidates) - len(deduplicated)
    rejected: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []
    for item in deduplicated:
        reasons = _screen_events(item)
        if reasons:
            rejected.append({"semantic_identity": item["program"].semantic_identity, "reasons": reasons})
        else:
            survivors.append(item)
    survivors.sort(key=_event_rank)
    survivors = _round_robin(survivors, SEARCH_BUDGETS["event_study"])
    return survivors, {
        "behavior_duplicate_count": behavior_duplicate_count,
        "behavior_aliases": aliases,
        "event_rejections": rejected,
        "event_study_survivor_count": len(survivors),
    }


def _backtest_asset(data: DevelopmentData, program: StrategyProgram, instrument: str) -> dict[str, Any]:
    candles = data.candles(instrument, program.timeframe)
    features = data.features(instrument, program.timeframe)
    aligned = data.aligned(instrument, program)
    fold_results: list[dict[str, Any]] = []
    benchmarks: list[dict[str, Any]] = []
    fold_evidence: list[dict[str, Any]] = []
    execution = DiscoveryExecutionConfig(
        risk_per_trade=.01, trading_fee=.0005, slippage=.0003,
        cooldown_bars=0, allow_long=program.direction == "LONG",
        allow_short=program.direction == "SHORT",
    )
    for fold_number, (_, _, validation_start, validation_end) in enumerate(FOLDS, 1):
        identity = {"fold": fold_number, "validation_start": validation_start,
                    "validation_end_exclusive": validation_end}
        result = run_program_backtest(
            program, candles, features, validation_start, validation_end,
            instrument=instrument, dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
            fold_identity=identity, aligned_context=aligned,
        )
        fold_results.append(result)
        benchmark = buy_and_hold(
            candles, validation_start,
            validation_end - TIMEFRAME_SECONDS[program.timeframe], execution,
        )
        benchmarks.append(benchmark)
        fold_evidence.append({
            "fold": fold_number,
            "maximum_accessed_timestamp": max(
                int(row["ts"]) for row in candles if int(row["ts"]) < validation_end
            ),
            "program_identity": result["program_evidence"]["program_identity"],
            "lifecycle": result["lifecycle_evidence"],
            "trade_count": len(result["trades"]),
        })
    return {
        "instrument": instrument,
        "metrics": summarize_backtests(fold_results, benchmarks, program.timeframe),
        "fold_evidence": fold_evidence,
    }


def stage_b(data: DevelopmentData, stage_a_survivors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = _round_robin(sorted(stage_a_survivors, key=_event_rank), SEARCH_BUDGETS["btc_backtest"])
    output: list[dict[str, Any]] = []
    for item in selected:
        value = {**item, "btc": _backtest_asset(data, item["program"], "BTC-USDT")}
        value["development_evidence"] = _development_evidence(value)
        output.append(value)
    eligible = [item for item in output if item["development_evidence"]["eligibility"]["eligible"]]
    if eligible:
        scoring_items = []
        for number, item in enumerate(eligible, 1):
            evidence = item["development_evidence"]
            scoring_items.append({
                "aggregate": evidence["aggregate"],
                "parameter_hash": item["program"].semantic_identity,
                "candidate_number": number,
                "development_score": evidence["score"],
                "complexity": item["program"].complexity,
            })
        fronts = assign_pareto_fronts(scoring_items)
        ranks = rank_eligible_candidates(scoring_items, fronts)
        for item, scoring_item in zip(eligible, scoring_items):
            key = (scoring_item["parameter_hash"], scoring_item["candidate_number"])
            item["development_evidence"]["pareto_rank"] = fronts[key]
            item["development_evidence"]["eligible_rank"] = ranks[key]
    return output


def _development_evidence(item: dict[str, Any]) -> dict[str, Any]:
    metrics = item["btc"]["metrics"]
    fold_returns = metrics["fold_net_returns"]
    fold_excess = metrics["fold_net_excess_returns"]
    fold_trades = [fold["trade_count"] for fold in item["btc"]["fold_evidence"]]
    aggregate = {
        "completed_fold_count": 5,
        "failed_fold_count": 0,
        "folds_with_trades": sum(value > 0 for value in fold_trades),
        "total_trades": metrics["trade_count"],
        "median_trades_per_fold": statistics.median(fold_trades),
        "profitable_fold_ratio": metrics["profitable_fold_ratio"],
        "benchmark_beating_fold_ratio": metrics["benchmark_beating_fold_ratio"],
        "median_excess_return": metrics["median_net_excess_return"],
        "worst_validation_return": min(fold_returns),
        "worst_excess_return": min(fold_excess),
        "worst_maximum_drawdown": metrics["maximum_drawdown"],
        "validation_return_standard_deviation": (
            statistics.pstdev(fold_returns) if len(fold_returns) > 1 else 0.0
        ),
    }
    eligibility = evaluate_eligibility(aggregate, item["program"].timeframe)
    score = score_components = None
    if eligibility["eligible"]:
        score, score_components = calculate_score(
            aggregate, item["program"].complexity, item["program"].timeframe
        )
    return {
        "aggregate": aggregate,
        "eligibility": eligibility,
        "score": score,
        "score_components": score_components,
        "pareto_rank": None,
        "eligible_rank": None,
        "development_only": True,
    }


def _btc_pre_neighborhood(item: dict[str, Any]) -> bool:
    metrics = item["btc"]["metrics"]
    threshold = {"15m": 40, "1H": 20, "4H": 10}[item["program"].timeframe]
    return (
        metrics["median_gross_excess_return"] is not None
        and metrics["median_gross_excess_return"] > 0
        and metrics["gross_excess_positive_fold_ratio"] >= .6
        and metrics["trade_count"] >= threshold
        and metrics["worst_fold_return"] > -10
        and (metrics["return_concentration"] is None or metrics["return_concentration"] <= .75)
    )


def neighborhood_screen(data: DevelopmentData, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [item for item in candidates if _btc_pre_neighborhood(item)]
    eligible.sort(key=lambda item: (
        -item["btc"]["metrics"]["median_gross_excess_return"],
        item["program"].complexity, item["program"].semantic_identity,
    ))
    eligible = eligible[:SEARCH_BUDGETS["cross_asset"]]
    output: list[dict[str, Any]] = []
    for item in eligible:
        base = item["btc"]["metrics"]
        variants = neighborhood_variants(item["program"])
        evidence: list[dict[str, Any]] = []
        base_vector = set(item["trigger_vector"])
        for variant in variants:
            result = _backtest_asset(data, variant, "BTC-USDT")
            candles = data.candles("BTC-USDT", variant.timeframe)
            features = data.features("BTC-USDT", variant.timeframe)
            aligned = data.aligned("BTC-USDT", variant)
            vector: set[int] = set()
            for fold_number, (_, _, start, end) in enumerate(FOLDS, 1):
                triggers, _ = evaluate_trigger_vector(
                    variant, candles, features, start, end, instrument="BTC-USDT",
                    dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
                    fold_identity={"fold": fold_number}, aligned_context=aligned,
                )
                vector.update(triggers)
            union = base_vector | vector
            overlap = len(base_vector & vector) / len(union) if union else 1.0
            changed = _changed_parameters(item["program"], variant)
            evidence.append({
                "semantic_identity": variant.semantic_identity,
                "changed_parameters": changed,
                "one_parameter_only": len(changed) == 1,
                "metrics": result["metrics"],
                "trigger_jaccard": overlap,
            })
        stable_variants = [
            value for value in evidence
            if value["one_parameter_only"]
            and value["metrics"]["trade_count"] > 0
            and value["metrics"]["median_gross_excess_return"] is not None
            and value["metrics"]["median_gross_excess_return"] * base["median_gross_excess_return"] > 0
            and value["metrics"]["maximum_drawdown"] <= max(base["maximum_drawdown"] * 2, base["maximum_drawdown"] + 5)
            and value["metrics"]["gross_excess_positive_fold_ratio"] >= .4
            and value["trigger_jaccard"] >= .05
        ]
        item["neighborhood"] = {
            "variant_count": len(evidence), "variants": evidence,
            "stable_variant_count": len(stable_variants),
            "stable": bool(evidence) and len(stable_variants) >= (len(evidence) + 1) // 2,
        }
        if item["neighborhood"]["stable"]:
            output.append(item)
    return output


def _changed_parameters(base: StrategyProgram, variant: StrategyProgram) -> list[str]:
    def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key in sorted(value):
                result.update(flatten(value[key], f"{prefix}.{key}" if prefix else key))
            return result
        if isinstance(value, list):
            result = {}
            for index, item in enumerate(value):
                result.update(flatten(item, f"{prefix}[{index}]"))
            return result
        return {prefix: value}
    first, second = flatten(base.canonical_ast()), flatten(variant.canonical_ast())
    return [key for key in sorted(set(first) | set(second)) if first.get(key) != second.get(key)]


def stage_c(data: DevelopmentData, survivors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = survivors[:SEARCH_BUDGETS["cross_asset"]]
    for item in selected:
        item["eth"] = _backtest_asset(data, item["program"], "ETH-USDT")
        item["sol"] = _backtest_asset(data, item["program"], "SOL-USDT")
    return selected


def _classification(item: dict[str, Any], reached_neighborhood: bool, reached_cross_asset: bool) -> str:
    btc = item["btc"]["metrics"]
    threshold = {"15m": 40, "1H": 20, "4H": 10}[item["program"].timeframe]
    if btc["trade_count"] < threshold:
        return "INSUFFICIENT_SAMPLE"
    if btc["median_gross_excess_return"] is None or btc["median_gross_excess_return"] <= 0:
        return "RETIRE_NO_GROSS_EDGE"
    if not reached_neighborhood or not item.get("neighborhood", {}).get("stable"):
        return "RETIRE_UNSTABLE"
    if not reached_cross_asset:
        return "RETAIN_FOR_DIAGNOSTIC_ONLY"
    assets = [btc, item["eth"]["metrics"], item["sol"]["metrics"]]
    positive = sum(
        metrics["median_gross_excess_return"] is not None
        and metrics["median_gross_excess_return"] > 0 for metrics in assets
    )
    catastrophic = any(metrics["worst_fold_return"] <= -10 for metrics in assets)
    if positive < 2 or catastrophic:
        return "RETIRE_CROSS_ASSET_FAILURE"
    if btc["return_concentration"] is not None and btc["return_concentration"] > .75:
        return "RETAIN_FOR_DIAGNOSTIC_ONLY"
    return "RETAIN_FOR_FORMAL_SEARCH"


def run_automatic_discovery(database: Path) -> dict[str, Any]:
    generation = generate_programs()
    if len(generation["programs"]) > SEARCH_BUDGETS["semantic"]:
        raise AssertionError("semantic generation budget exceeded")
    data = DevelopmentData(database)
    event_survivors, stage_a_evidence = stage_a(data, generation["programs"])
    btc = stage_b(data, event_survivors)
    neighborhood = neighborhood_screen(data, btc)
    cross_asset = stage_c(data, neighborhood)
    neighborhood_ids = {item["program"].semantic_identity for item in neighborhood}
    cross_ids = {item["program"].semantic_identity for item in cross_asset}
    program_results: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    for item in btc:
        semantic_identity = item["program"].semantic_identity
        classification = _classification(
            item, semantic_identity in neighborhood_ids, semantic_identity in cross_ids
        )
        classification_counts[classification] += 1
        program_results.append({
            "semantic_identity": semantic_identity,
            "development_identity": build_program_identity(
                item["program"], dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
                instrument="BTC-USDT", fold_identity=FOLD_SET_IDENTITY,
            ),
            "canonical_ast": item["program"].canonical_ast(),
            "complexity": item["program"].complexity,
            "classification": classification,
            "event_study": item["event_study"],
            "fold_event_counts": item["fold_event_counts"],
            "lifecycle_evidence": item["lifecycle_evidence"],
            "btc": item["btc"],
            "development_evidence": item["development_evidence"],
            "neighborhood": item.get("neighborhood"),
            "eth": item.get("eth"),
            "sol": item.get("sol"),
        })
    retained = [
        item for item in program_results
        if item["classification"] == "RETAIN_FOR_FORMAL_SEARCH"
    ]
    failures = Counter(
        reason
        for rejection in stage_a_evidence["event_rejections"]
        for reason in rejection["reasons"]
    )
    failures.update(classification_counts)
    maximum_accessed = {
        f"{instrument}:{timeframe}": timestamp
        for (instrument, timeframe), timestamp in sorted(data.maximum_accessed.items())
    }
    if maximum_accessed and max(maximum_accessed.values()) >= DEVELOPMENT_END_TS:
        raise AssertionError("Development boundary exceeded")
    return {
        "result_version": PHASE6A_RESULT_VERSION,
        "search_policy_version": STRATEGY_SEARCH_POLICY_VERSION,
        "dataset_fingerprint": CANONICAL_DATASET_FINGERPRINT,
        "generation_policy": generation["policy"],
        "proposal_count": generation["proposal_count"],
        "raw_program_count": generation["raw_program_count"],
        "structural_rejection_count": generation["structural_rejection_count"],
        "structural_rejection_code_counts": dict(Counter(
            code for item in generation["structural_rejections"]
            for code in item["rejection_codes"]
        )),
        "semantic_duplicate_count": generation["semantic_duplicate_count"],
        "semantic_program_count": len(generation["programs"]),
        "behavior_duplicate_count": stage_a_evidence["behavior_duplicate_count"],
        "behavior_aliases": stage_a_evidence["behavior_aliases"],
        "event_study_survivor_count": len(event_survivors),
        "event_rejections": stage_a_evidence["event_rejections"],
        "btc_full_backtest_program_count": len(btc),
        "neighborhood_screen_survivor_count": len(neighborhood),
        "cross_asset_confirmation_count": len(cross_asset),
        "final_retained_program_count": len(retained),
        "retained_formal_search_scope": [
            {"semantic_identity": item["semantic_identity"],
             "timeframe": item["canonical_ast"]["timeframe"]}
            for item in retained
        ],
        "classification_counts": dict(classification_counts),
        "programs": program_results,
        "most_common_failure_reasons": failures.most_common(),
        "maximum_accessed_timestamp": maximum_accessed,
        "development_end_exclusive": DEVELOPMENT_END_TS,
        "raw_ohlcv_unchanged": True,
        "retired_v2_families_widened": False,
        "formal_robustness_or_ablation_run": False,
        "holdout_or_oot_accessed_by_discovery": False,
        "historical_cvd_oi_requested": False,
        "strategy_activated": False,
        "paper_or_live_order_created": False,
        "deployment_performed": False,
        "warnings": [
            "Development evidence only; no production strategy or winner is declared.",
            "Event labels are diagnostic and were computed after immutable trigger vectors.",
        ],
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")), encoding="utf-8")
