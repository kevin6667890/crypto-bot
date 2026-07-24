"""Bounded Development-only Phase 6B automatic discovery."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import statistics
import time
from typing import Any, Callable, Iterable

from .automatic_discovery import (
    CANONICAL_DATASET_FINGERPRINT, DevelopmentData, FOLD_SET_IDENTITY,
)
from .discovery_checkpoint import DiscoveryCheckpoint, checkpoint_key
from .discovery_execution import DiscoveryExecutionConfig
from .discovery_service import FOLDS, buy_and_hold
from .okx_history import TIMEFRAME_SECONDS
from .strategy_program_runtime import DEVELOPMENT_END_TS, summarize_backtests
from .strategy_program_v2 import (
    BENCHMARK_DIAGNOSTIC_VERSION, SEARCH_BUDGETS, STRATEGY_SEARCH_POLICY_VERSION,
    TEMPORAL_FAMILIES, TemporalStrategyProgram, generate_programs,
    geometry_aware_deduplicate, neighborhood_variants,
)
from .temporal_strategy_runtime import (
    evaluate_trigger_vector, run_program_backtest,
)

PHASE6B_RESULT_VERSION = "phase6b-temporal-automatic-discovery-result-v1"


def _fold_identity(number: int, start: int, end: int) -> dict[str, Any]:
    return {"fold": number, "validation_start": start, "validation_end_exclusive": end}


def _key(stage: str, program: TemporalStrategyProgram, instrument: str,
         fold: dict[str, Any], *, geometry: bool = True, variant: str = "") -> dict[str, str]:
    return checkpoint_key(
        stage=stage, program_identity=program.entry_identity,
        geometry_identity=program.geometry.identity if geometry else "",
        instrument=instrument, timeframe=program.timeframe, fold_identity=fold,
        dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
        policy_version=STRATEGY_SEARCH_POLICY_VERSION, variant_identity=variant,
    )


def _parallel_map(workers: int, tasks: list[Any], function: Callable[[Any], Any]) -> list[Any]:
    if workers == 1:
        return [function(task) for task in tasks]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # map preserves the deterministic input order.
        return list(executor.map(function, tasks))


def _event_labels(program: TemporalStrategyProgram, vector: Iterable[int],
                  candles: list[dict[str, Any]], features: list[dict[str, Any]],
                  start: int, end: int, transitions: list[dict[str, Any]]) -> dict[str, Any]:
    visible = [(index, row) for index, row in enumerate(candles) if start <= int(row["ts"]) < end]
    positions = {int(row["ts"]): position for position, (_, row) in enumerate(visible)}
    sign = 1 if program.direction == "LONG" else -1
    events = []
    for timestamp in sorted(vector):
        if timestamp not in positions:
            continue
        position = positions[timestamp]
        source_index, origin = visible[position]
        close, atr = float(origin["close"]), features[source_index].get("atr")
        if not atr:
            continue
        distance = float(atr) * max(program.geometry.stop_parameter, .25)
        horizons: dict[str, Any] = {}
        for horizon in (1, 2, 4, 8, 16):
            path = [row for _, row in visible[position + 1:position + horizon + 1]]
            if len(path) != horizon:
                continue
            returns = sign * (float(path[-1]["close"]) / close - 1) * 100
            mfe = max(sign * (float(row["high" if sign > 0 else "low"]) - close)
                      for row in path) / close * 100
            mae = max(-sign * (float(row["low" if sign > 0 else "high"]) - close)
                      for row in path) / close * 100
            stop, target = False, False
            outcome = "NEITHER"
            for row in path:
                stop = float(row["low"]) <= close - distance if sign > 0 else float(row["high"]) >= close + distance
                target = float(row["high"]) >= close + 1.5 * distance if sign > 0 else float(row["low"]) <= close - 1.5 * distance
                if stop or target:
                    outcome = "STOP_FIRST" if stop else "TARGET_FIRST"
                    break
            horizons[str(horizon)] = {
                "direction_adjusted_forward_return": returns, "mfe": mfe, "mae": mae,
                "stop_first": outcome == "STOP_FIRST", "target_first": outcome == "TARGET_FIRST",
                "neither": outcome == "NEITHER", "outcome_timestamp": int(path[-1]["ts"]),
            }
        events.append({"trigger_timestamp": timestamp, "labels": horizons})
    stage_durations = []
    activations: dict[str, int] = {}
    for transition in transitions:
        sequence_id = transition.get("sequence_id")
        if sequence_id and transition["resulting_state"].startswith("STAGE_"):
            prior = activations.get(sequence_id)
            if prior is not None:
                stage_durations.append(transition["timestamp"] - prior)
            activations[sequence_id] = transition["timestamp"]
    return {"events": events, "stage_durations_seconds": stage_durations}


def _combine_events(studies: list[dict[str, Any]]) -> dict[str, Any]:
    events = [event for study in studies for event in study["events"]]
    aggregate: dict[str, Any] = {}
    for horizon in (1, 2, 4, 8, 16):
        labels = [event["labels"][str(horizon)] for event in events if str(horizon) in event["labels"]]
        aggregate[str(horizon)] = {
            "event_count": len(labels),
            "median_forward_return": statistics.median(
                label["direction_adjusted_forward_return"] for label in labels) if labels else None,
            "median_mfe": statistics.median(label["mfe"] for label in labels) if labels else None,
            "median_mae": statistics.median(label["mae"] for label in labels) if labels else None,
            "stop_first_ratio": sum(label["stop_first"] for label in labels) / len(labels) if labels else None,
            "target_first_ratio": sum(label["target_first"] for label in labels) / len(labels) if labels else None,
            "neither_ratio": sum(label["neither"] for label in labels) / len(labels) if labels else None,
        }
    durations = [value for study in studies for value in study["stage_durations_seconds"]]
    return {
        "events": events, "aggregate": aggregate,
        "temporal_stage_duration_median_seconds": statistics.median(durations) if durations else None,
        "diagnostic_labels_only": True,
    }


def _event_reasons(item: dict[str, Any]) -> list[str]:
    minimum = {"15m": 20, "1H": 10, "4H": 5}[item["program"].timeframe]
    counts, aggregate = item["fold_event_counts"], item["event_study"]["aggregate"]
    total = sum(counts); reasons = []
    if total < minimum:
        reasons.append("INSUFFICIENT_SAMPLE")
    if sum(value > 0 for value in counts) < 2:
        reasons.append("SIGNALS_IN_ONLY_ONE_FOLD")
    medians = [aggregate[str(h)]["median_forward_return"] for h in (1, 2, 4, 8, 16)
               if aggregate[str(h)]["median_forward_return"] is not None]
    if medians and all(value <= 0 for value in medians):
        reasons.append("CONSISTENTLY_ADVERSE_FORWARD_OUTCOMES")
    if total and max(counts) / total > .8:
        reasons.append("EXTREME_TRIGGER_CONCENTRATION")
    return reasons


def _execution_payload(result: dict[str, Any], benchmark: dict[str, Any],
                       start: int, end: int) -> dict[str, Any]:
    trades = result["trades"]
    duration = max(end - start, 1)
    weighted_notional = sum(
        float(trade["entry_price"]) * float(trade["position_size"])
        * float(trade["holding_seconds"]) for trade in trades
    )
    average_notional = weighted_notional / duration
    maximum_notional = max((
        float(trade["entry_price"]) * float(trade["position_size"]) for trade in trades
    ), default=0.0)
    average_exposure = average_notional / 10_000
    raw_benchmark = (
        (float(benchmark["raw_exit_price"]) / float(benchmark["raw_entry_price"]) - 1) * 100
    )
    strategy_gross = float(result["metrics"]["total_return"]) + sum(
        float(trade["fees"]) / 10_000 * 100 for trade in trades
    )
    matched = raw_benchmark * average_exposure
    return {
        "metrics": result["metrics"], "trades": trades, "benchmark": benchmark,
        "maximum_accessed_timestamp": result["program_evidence"]["maximum_candle_timestamp_loaded"],
        "diagnostics": {
            "version": BENCHMARK_DIAGNOSTIC_VERSION, "formal_gate_unchanged": True,
            "average_notional_exposure": average_notional,
            "maximum_exposure": maximum_notional,
            "capital_utilization": average_exposure,
            "time_in_market": sum(float(t["holding_seconds"]) for t in trades) / duration,
            "formal_gross_benchmark_excess": strategy_gross - raw_benchmark,
            "formal_net_benchmark_excess": float(result["metrics"]["total_return"]) - float(benchmark["total_return"]),
            "matched_notional_benchmark_return": matched,
            "matched_exposure_excess": strategy_gross - matched,
            "strategy_return_per_average_capital_exposure": (
                strategy_gross / average_exposure if average_exposure else None),
            "benchmark_return_per_average_capital_exposure": (
                matched / average_exposure if average_exposure else None),
            "exposure_normalized_excess": (
                (strategy_gross - matched) / average_exposure if average_exposure else None),
            "diagnostic_only": True,
        },
    }


def _summarize_execution(program: TemporalStrategyProgram,
                         fold_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    fold_results = [{"metrics": value["metrics"], "trades": value["trades"]}
                    for value in fold_payloads]
    # summarize_backtests only needs metrics/trades and benchmark fields.
    metrics = summarize_backtests(
        fold_results, [value["benchmark"] for value in fold_payloads], program.timeframe
    )
    diagnostics = [value["diagnostics"] for value in fold_payloads]
    benchmark_diagnostics = {
        "version": BENCHMARK_DIAGNOSTIC_VERSION, "diagnostic_only": True,
    }
    benchmark_diagnostics.update({
        key: statistics.mean(value[key] for value in diagnostics)
        for key in (
            "average_notional_exposure", "maximum_exposure", "capital_utilization",
            "time_in_market", "formal_gross_benchmark_excess",
            "formal_net_benchmark_excess", "matched_notional_benchmark_return",
            "matched_exposure_excess",
        )
    })
    return {
        "metrics": metrics, "folds": fold_payloads,
        "benchmark_diagnostics": benchmark_diagnostics,
    }


def _pre_neighborhood(item: dict[str, Any]) -> bool:
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


def run_automatic_discovery_v2(
    database: Path, checkpoint_database: Path, *, workers: int = 2, resume: bool = True,
    max_programs: int = SEARCH_BUDGETS["raw_structurally_valid"],
    max_btc_backtests: int = SEARCH_BUDGETS["btc_backtest"],
    max_cross_asset_programs: int = SEARCH_BUDGETS["cross_asset"],
    cancel_file: Path | None = None,
) -> dict[str, Any]:
    if workers not in {1, 2}:
        raise ValueError("workers must be 1 or 2")
    started = time.perf_counter()
    data = DevelopmentData(database)
    generation = generate_programs(max_programs)
    programs = generation["programs"]
    with DiscoveryCheckpoint(checkpoint_database, cancel_file=cancel_file) as checkpoint:
        manifest_program = programs[0]
        manifest_key = _key("generation_manifest", manifest_program, "", {}, geometry=False)
        checkpoint.register([manifest_key])
        if not checkpoint.completed(manifest_key):
            checkpoint.complete(manifest_key, {
                "policy": generation["policy"], "program_identities":
                [program.semantic_identity for program in programs],
            })
        if checkpoint.cancelled:
            raise InterruptedError("Phase 6B cancellation requested after durable generation manifest")

        semantic_keys = [_key("semantic_validation", p, "", {}, geometry=True) for p in programs]
        checkpoint.register(semantic_keys)
        for program, key in zip(programs, semantic_keys):
            if not checkpoint.completed(key):
                checkpoint.mark_running(key)
                checkpoint.complete(key, {"canonical_ast": program.canonical_ast(), "valid": True})

        # Entry behavior is independent of geometry.  Evaluate one representative
        # for each entry AST, retaining all geometry aliases for Level 2.
        entries: dict[str, list[TemporalStrategyProgram]] = defaultdict(list)
        for program in programs:
            entries[program.entry_identity].append(program)
        entry_representatives = [sorted(group, key=lambda p: p.geometry.identity)[0]
                                 for _, group in sorted(entries.items())]
        trigger_tasks = []
        for program in entry_representatives:
            candles = data.candles("BTC-USDT", program.timeframe)
            features = data.features("BTC-USDT", program.timeframe)
            aligned = data.aligned("BTC-USDT", program)
            for number, (_, _, start, end) in enumerate(FOLDS, 1):
                fold = _fold_identity(number, start, end)
                key = _key("entry_trigger_vector", program, "BTC-USDT", fold, geometry=False)
                trigger_tasks.append((program, fold, key, candles, features, aligned))
        checkpoint.register(task[2] for task in trigger_tasks)
        prepared_trigger_tasks = []
        for task in trigger_tasks:
            existing = checkpoint.completed(task[2]) if resume else None
            if existing is None:
                checkpoint.mark_running(task[2])
            prepared_trigger_tasks.append((*task, existing))

        def trigger_work(task):
            program, fold, key, candles, features, aligned, existing = task
            if existing is not None:
                return program, fold, existing, True
            vector, evaluator = evaluate_trigger_vector(
                program, candles, features,
                fold["validation_start"], fold["validation_end_exclusive"],
                instrument="BTC-USDT", dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
                fold_identity=fold, aligned_context=aligned,
            )
            payload = {
                "trigger_vector": list(vector), "transitions": evaluator.transitions,
                "trigger_evidence": list(evaluator.trigger_evidence.values()),
                "maximum_accessed_timestamp": max(int(row["ts"]) for row in candles
                                                  if int(row["ts"]) < fold["validation_end_exclusive"]),
            }
            return program, fold, payload, False

        # Reads/evaluation may run concurrently; checkpoint writes remain serialized here.
        trigger_results = _parallel_map(workers, prepared_trigger_tasks, trigger_work)
        resumed_trigger_folds = 0
        for program, fold, payload, resumed in trigger_results:
            key = _key("entry_trigger_vector", program, "BTC-USDT", fold, geometry=False)
            if resumed:
                resumed_trigger_folds += 1
            else:
                checkpoint.complete(key, payload, payload["maximum_accessed_timestamp"])

        event_tasks = []
        for program, fold, trigger_payload, _ in trigger_results:
            key = _key("event_study", program, "BTC-USDT", fold, geometry=False)
            event_tasks.append((program, fold, trigger_payload, key))
        checkpoint.register(key for *_, key in event_tasks)
        prepared_event_tasks = []
        for task in event_tasks:
            existing = checkpoint.completed(task[3]) if resume else None
            if existing is None:
                checkpoint.mark_running(task[3])
            program = task[0]
            prepared_event_tasks.append((*task, data.candles("BTC-USDT", program.timeframe),
                                         data.features("BTC-USDT", program.timeframe), existing))

        def event_work(task):
            program, fold, trigger_payload, key, candles, features, existing = task
            if existing is not None:
                return program, fold, existing, True
            payload = _event_labels(
                program, trigger_payload["trigger_vector"], candles,
                features,
                fold["validation_start"], fold["validation_end_exclusive"],
                trigger_payload["transitions"],
            )
            payload["maximum_accessed_timestamp"] = trigger_payload["maximum_accessed_timestamp"]
            return program, fold, payload, False

        event_results = _parallel_map(workers, prepared_event_tasks, event_work)
        resumed_event_folds = 0
        for program, fold, payload, resumed in event_results:
            key = _key("event_study", program, "BTC-USDT", fold, geometry=False)
            if resumed:
                resumed_event_folds += 1
            else:
                checkpoint.complete(key, payload, payload["maximum_accessed_timestamp"])

        grouped_triggers, grouped_events = defaultdict(list), defaultdict(list)
        for program, fold, payload, _ in trigger_results:
            grouped_triggers[program.entry_identity].append(payload)
        for program, fold, payload, _ in event_results:
            grouped_events[program.entry_identity].append(payload)
        entry_items = []
        for entry_id, aliases in sorted(entries.items()):
            representative = sorted(aliases, key=lambda p: p.geometry.identity)[0]
            trigger_payloads = grouped_triggers[entry_id]
            vector = tuple(timestamp for payload in trigger_payloads for timestamp in payload["trigger_vector"])
            combined = _combine_events(grouped_events[entry_id])
            for program in aliases:
                entry_items.append({
                    "instrument": "BTC-USDT", "program": program, "trigger_vector": vector,
                    "fold_event_counts": [len(payload["trigger_vector"]) for payload in trigger_payloads],
                    "event_study": combined,
                })
        deduplicated, behavior_evidence = geometry_aware_deduplicate(entry_items)
        formal_event, diagnostic, event_rejections = [], [], []
        for item in deduplicated:
            reasons = _event_reasons(item)
            if not reasons:
                formal_event.append(item)
            elif set(reasons) <= {"INSUFFICIENT_SAMPLE", "SIGNALS_IN_ONLY_ONE_FOLD"}:
                medians = [value["median_forward_return"] for value in item["event_study"]["aggregate"].values()
                           if value["median_forward_return"] is not None]
                if medians and any(value > 0 for value in medians) and len(diagnostic) < SEARCH_BUDGETS["diagnostic_sparse_btc"]:
                    diagnostic.append(item)
                else:
                    event_rejections.append((item, reasons))
            else:
                event_rejections.append((item, reasons))
        formal_event = formal_event[:SEARCH_BUDGETS["event_study"]]
        btc_candidates = formal_event[:min(max_btc_backtests, SEARCH_BUDGETS["btc_backtest"])]

        def execute_stage(stage: str, candidates: list[dict[str, Any]], instrument: str,
                          variant_identity: str = "") -> list[tuple[dict[str, Any], dict[str, Any]]]:
            tasks = []
            for item in candidates:
                program = item["program"]
                candles = data.candles(instrument, program.timeframe)
                features = data.features(instrument, program.timeframe)
                aligned = data.aligned(instrument, program)
                for number, (_, _, start, end) in enumerate(FOLDS, 1):
                    fold = _fold_identity(number, start, end)
                    key = _key(stage, program, instrument, fold, variant=variant_identity)
                    tasks.append((item, program, fold, key, candles, features, aligned))
            checkpoint.register(task[3] for task in tasks)
            prepared_tasks = []
            for task in tasks:
                existing = checkpoint.completed(task[3]) if resume else None
                if existing is None:
                    checkpoint.mark_running(task[3])
                prepared_tasks.append((*task, existing))

            def work(task):
                item, program, fold, key, candles, features, aligned, existing = task
                if existing is not None:
                    return item, program, fold, existing, True
                result = run_program_backtest(
                    program, candles, features,
                    fold["validation_start"], fold["validation_end_exclusive"],
                    instrument=instrument, dataset_fingerprint=CANONICAL_DATASET_FINGERPRINT,
                    fold_identity=fold, aligned_context=aligned,
                )
                benchmark = buy_and_hold(
                    candles, fold["validation_start"],
                    fold["validation_end_exclusive"] - TIMEFRAME_SECONDS[program.timeframe],
                    DiscoveryExecutionConfig(
                        risk_per_trade=.01, trading_fee=.0005, slippage=.0003,
                        cooldown_bars=0, allow_long=program.direction == "LONG",
                        allow_short=program.direction == "SHORT",
                    ),
                )
                payload = _execution_payload(
                    result, benchmark, fold["validation_start"], fold["validation_end_exclusive"])
                return item, program, fold, payload, False

            results = _parallel_map(workers, prepared_tasks, work)
            grouped = defaultdict(list)
            for item, program, fold, payload, resumed in results:
                key = _key(stage, program, instrument, fold, variant=variant_identity)
                if not resumed:
                    checkpoint.complete(key, payload, payload["maximum_accessed_timestamp"])
                grouped[program.semantic_identity].append(payload)
            return [(item, _summarize_execution(item["program"],
                                                grouped[item["program"].semantic_identity]))
                    for item in candidates]

        btc_results = execute_stage("btc_canonical_execution", btc_candidates, "BTC-USDT")
        for item, result in btc_results:
            item["btc"] = result
        diagnostic_btc_results = execute_stage(
            "btc_canonical_execution", diagnostic, "BTC-USDT")
        for item, result in diagnostic_btc_results:
            item["btc"] = result

        neighborhood_candidates = [item for item in btc_candidates if _pre_neighborhood(item)]
        neighborhood_candidates = neighborhood_candidates[:SEARCH_BUDGETS["neighborhood_programs"]]
        neighborhood_survivors = []
        for item in neighborhood_candidates:
            variants = neighborhood_variants(item["program"])
            evidence = []
            for variant in variants:
                variant_item = {**item, "program": variant}
                result = execute_stage(
                    "neighborhood_variant_execution", [variant_item], "BTC-USDT",
                    variant_identity=variant.semantic_identity,
                )[0][1]
                metrics = result["metrics"]
                evidence.append({
                    "semantic_identity": variant.semantic_identity, "metrics": metrics,
                    "one_parameter_only": True,
                })
            base = item["btc"]["metrics"]
            stable = [value for value in evidence
                      if value["metrics"]["median_gross_excess_return"] is not None
                      and value["metrics"]["median_gross_excess_return"]
                      * base["median_gross_excess_return"] > 0
                      and value["metrics"]["gross_excess_positive_fold_ratio"] >= .4]
            item["neighborhood"] = {
                "variant_count": len(evidence), "variants": evidence,
                "stable_variant_count": len(stable),
                "stable": bool(evidence) and len(stable) >= (len(evidence) + 1) // 2,
            }
            if item["neighborhood"]["stable"]:
                neighborhood_survivors.append(item)

        confirmation = neighborhood_survivors[:min(
            max_cross_asset_programs, SEARCH_BUDGETS["cross_asset"])]
        for instrument, stage, field in (
            ("ETH-USDT", "eth_confirmation", "eth"),
            ("SOL-USDT", "sol_confirmation", "sol"),
        ):
            for item, result in execute_stage(stage, confirmation, instrument):
                item[field] = result

        all_items = btc_candidates + diagnostic
        classifications = Counter()
        program_results = []
        for item in all_items:
            if item in diagnostic:
                classification = "RETAIN_FOR_DIAGNOSTIC_ONLY"
            elif not _pre_neighborhood(item):
                metrics = item["btc"]["metrics"]
                classification = (
                    "RETIRED_NO_GROSS_EDGE" if metrics["median_gross_excess_return"] <= 0
                    else "RETIRED_UNSTABLE"
                )
            elif not item.get("neighborhood", {}).get("stable"):
                classification = "RETIRED_UNSTABLE"
            elif "eth" not in item or "sol" not in item:
                classification = "RETIRED_UNSTABLE"
            else:
                positive_assets = sum(
                    item[field]["metrics"]["median_gross_excess_return"] > 0
                    for field in ("btc", "eth", "sol")
                )
                classification = (
                    "RETAIN_FOR_FORMAL_SEARCH" if positive_assets >= 2
                    else "RETIRED_CROSS_ASSET_FAILURE"
                )
            classifications[classification] += 1
            program_results.append({
                "semantic_identity": item["program"].semantic_identity,
                "entry_identity": item["program"].entry_identity,
                "geometry_identity": item["program"].geometry.identity,
                "family": item["program"].sequence.family,
                "direction": item["program"].direction,
                "timeframe": item["program"].timeframe,
                "context": item["program"].higher_timeframe_context.canonical()
                if item["program"].higher_timeframe_context else None,
                "classification": classification, "event_study": item["event_study"],
                "fold_event_counts": item["fold_event_counts"], "btc": item.get("btc"),
                "neighborhood": item.get("neighborhood"), "eth": item.get("eth"),
                "sol": item.get("sol"),
            })
        final_key = _key("final_classification", manifest_program, "", {}, geometry=False)
        checkpoint.register([final_key])
        checkpoint.complete(final_key, {
            "classification_counts": dict(classifications),
            "program_identities": [item["semantic_identity"] for item in program_results],
        })
        progress = checkpoint.progress()

    maximum_accessed = {
        f"{instrument}:{timeframe}": timestamp
        for (instrument, timeframe), timestamp in sorted(data.maximum_accessed.items())
    }
    if maximum_accessed and max(maximum_accessed.values()) >= DEVELOPMENT_END_TS:
        raise AssertionError("Development boundary exceeded")
    family_results = {}
    for family in TEMPORAL_FAMILIES:
        rows = [item for item in program_results if item["family"] == family]
        generated_family = [program for program in programs if program.sequence.family == family]
        entry_family = [item for item in deduplicated if item["program"].sequence.family == family]
        formal_family = [item for item in formal_event if item["program"].sequence.family == family]
        diagnostic_family = [item for item in diagnostic if item["program"].sequence.family == family]
        family_results[family] = {
            "semantic_program_count": len(generated_family),
            "geometry_representative_count": len(entry_family),
            "formal_event_survivor_count": len(formal_family),
            "diagnostic_sparse_count": len(diagnostic_family),
            "program_count": len(rows),
            "classification_counts": dict(Counter(item["classification"] for item in rows)),
        }
    def directions(items):
        values = [item["program"] if isinstance(item, dict) else item for item in items]
        return {
            "long_only": sum(value.direction == "LONG" for value in values),
            "short_only": sum(value.direction == "SHORT" for value in values),
        }
    context_alias_families = list(behavior_evidence.values())
    collapsed_context_families = sum(
        any(value[1] is None for value in evidence["direction_context_evidence"])
        and any(value[1] is not None for value in evidence["direction_context_evidence"])
        for evidence in context_alias_families
    )
    failure_counts = Counter(
        reason for _, reasons in event_rejections for reason in reasons)
    failure_counts.update(classifications)
    return {
        "result_version": PHASE6B_RESULT_VERSION,
        "origin_dataset_fingerprint": CANONICAL_DATASET_FINGERPRINT,
        "generation_policy": generation["policy"],
        "proposal_count": generation["proposal_count"],
        "raw_program_count": generation["raw_program_count"],
        "structural_rejection_count": generation["structural_rejection_count"],
        "semantic_program_count": len(programs),
        "long_only_program_count": generation["long_only_count"],
        "short_only_program_count": generation["short_only_count"],
        "entry_behavior_family_count": len(behavior_evidence),
        "geometry_representative_count": len(deduplicated),
        "event_study_survivor_count": len(formal_event),
        "diagnostic_only_sparse_program_count": len(diagnostic),
        "btc_full_backtest_program_count": len(btc_candidates),
        "btc_diagnostic_backtest_program_count": len(diagnostic),
        "neighborhood_screen_program_count": len(neighborhood_candidates),
        "cross_asset_confirmation_count": len(confirmation),
        "final_retained_program_count": classifications["RETAIN_FOR_FORMAL_SEARCH"],
        "classification_counts": dict(classifications),
        "family_results": family_results, "programs": program_results,
        "direction_survival_counts": {
            "semantic": directions(programs),
            "geometry_representatives": directions(deduplicated),
            "formal_event": directions(formal_event),
            "diagnostic_sparse": directions(diagnostic),
            "btc_formal": directions(btc_candidates),
            "neighborhood": directions(neighborhood_candidates),
            "cross_asset": directions(confirmation),
        },
        "context_behavior": {
            "entry_behavior_family_count": len(context_alias_families),
            "families_collapsing_context_and_no_context": collapsed_context_families,
            "families_with_distinct_context_behavior":
                len(context_alias_families) - collapsed_context_families,
            "exact_context_timestamp_persisted_in_trigger_evidence": True,
        },
        "most_common_failure_reasons": failure_counts.most_common(),
        "event_rejections": [
            {"identity": item["program"].semantic_identity, "reasons": reasons}
            for item, reasons in event_rejections
        ],
        "behavior_families": behavior_evidence,
        "checkpoint_progress": progress,
        "resume_evidence": {
            "completed_trigger_folds_reused": resumed_trigger_folds,
            "completed_event_folds_reused": resumed_event_folds,
        },
        "maximum_accessed_timestamp": maximum_accessed,
        "development_end_exclusive": DEVELOPMENT_END_TS,
        "local_runtime_seconds": time.perf_counter() - started,
        "formal_thresholds_unchanged": True,
        "raw_ohlcv_unchanged": True,
        "retired_v2_families_widened": False,
        "holdout_or_oot_accessed": False,
        "historical_cvd_oi_requested": False,
        "strategy_activated": False, "paper_or_live_order_created": False,
        "deployment_performed": False, "cloud_run_performed": False,
    }
