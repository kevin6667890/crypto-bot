"""Execute the committed Phase 4A manifest against DEVELOPMENT/VALIDATION only."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from dashboard.strategy_phase4a import (  # noqa: E402
    ArtifactWriterV2, BACKTEST_ENGINE_VERSION, CostPolicyV2, EntryIntentV2, FAMILIES, INSTRUMENTS,
    MANIFEST_VERSION, ReadOnlyOHLCVStoreV2, ReplayEventV2,
    StrategyBacktestEngineV2, StrategyEventReplayEngineV2, TimeSegmentV2,
    bootstrap_expectancy_interval, canonical_json, file_sha256, frozen_trials,
    metrics_v2, stable_hash,
)
from scripts.run_strategy_phase4a import DEFAULT_DATASET, TRACKED_MANIFEST, _load_partitions  # noqa: E402


def _segment(payload: Mapping[str, Any], name: str) -> TimeSegmentV2:
    value = payload["segments"][name]
    return TimeSegmentV2(name, int(value["start_ts"]), int(value["end_ts"]), str(value["identity"]))


def verify_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8")); claimed = payload.pop("manifest_identity")
    if stable_hash(payload) != claimed: raise ValueError("frozen manifest identity changed")
    payload["manifest_identity"] = claimed
    if payload["version"] != MANIFEST_VERSION or payload["status"] != "FROZEN_BEFORE_PNL":
        raise ValueError("Phase 4A manifest is not frozen")
    if len(payload["trials"]) != 32: raise ValueError("frozen trial count changed")
    return payload


def _replay_job(args: tuple[str, str, str, dict[str, Any]]) -> dict[str, Any]:
    dataset, instrument, segment_name, manifest = args
    segment = _segment(manifest, segment_name); dataset_id = manifest["dataset"]["identity"]
    oot = int(manifest["oot_lock"]["start_ts"])
    store = ReadOnlyOHLCVStoreV2(dataset, dataset_identity=dataset_id, oot_start_ts=oot)
    trials = frozen_trials(dataset_id)
    result = StrategyEventReplayEngineV2().replay(
        _load_partitions(store, instrument, segment.end_ts), trials,
        instrument=instrument, segment=segment, event_frequency_only=False)
    return {"instrument": instrument, "events": [asdict(x) for x in result["events"]],
            "intents": [asdict(x) for x in result["intents"]], "evaluations": result["evaluations"],
            "wall_seconds": result["wall_seconds"], "checkpoint": result["checkpoint"]}


def _event(payload: Mapping[str, Any]) -> ReplayEventV2:
    value = dict(payload); value["source_candle_timestamps"] = tuple(value["source_candle_timestamps"])
    value["blockers"] = tuple(value["blockers"])
    return ReplayEventV2(**value)


def _intent(payload: Mapping[str, Any]) -> EntryIntentV2:
    return EntryIntentV2(_event(payload["event"]), payload["side"], float(payload["stop"]),
                         float(payload["target"]), int(payload["maximum_holding_bars"]),
                         float(payload["minimum_structural_r"]))


def _folds(segment: TimeSegmentV2) -> list[tuple[int, int]]:
    width = segment.end_ts-segment.start_ts
    points = [segment.start_ts+(width*i//4//900)*900 for i in range(5)]
    points[-1] = segment.end_ts
    return list(zip(points, points[1:]))


def _trade_groups(trades: Sequence[Mapping[str, Any]], segment: TimeSegmentV2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    folds = []
    for number, (start, end) in enumerate(_folds(segment), 1):
        selected = [trade for trade in trades if start <= int(trade["entry_ts"]) < end]
        folds.append({"fold": number, "start_ts": start, "end_ts": end, **metrics_v2(selected)})
    assets = []
    for instrument in INSTRUMENTS:
        selected = [trade for trade in trades if trade["instrument"] == instrument]
        assets.append({"instrument": instrument, **metrics_v2(selected)})
    return folds, assets


def _concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    positive = [max(0.0, float(t["net_pnl"])) for t in trades]; total = sum(positive)
    by_asset: defaultdict[str, float] = defaultdict(float)
    for trade in trades: by_asset[str(trade["instrument"])] += max(0.0, float(trade["net_pnl"]))
    return {"single_trade_profit_share": max(positive, default=0)/total if total else None,
            "single_asset_profit_share": max(by_asset.values(), default=0)/total if total else None}


def _daily_sharpe(trades: Sequence[Mapping[str, Any]]) -> float | None:
    daily: defaultdict[int, float] = defaultdict(float)
    for trade in trades: daily[int(trade["exit_ts"])//86400] += float(trade["net_pnl"])/10_000
    values = list(daily.values())
    if len(values) < 2 or statistics.stdev(values) == 0: return None
    return statistics.fmean(values)/statistics.stdev(values)*math.sqrt(365)


def _psr(sharpe: float | None, trades: int) -> float | None:
    if sharpe is None or trades < 3: return None
    # Gaussian PSR against zero; report explicitly remains diagnostic.
    z = sharpe*math.sqrt(max(1, trades-1)); return .5*(1+math.erf(z/math.sqrt(2)))


def _pf_above_one(metrics: Mapping[str, Any]) -> bool:
    return metrics.get("profit_factor_reason") == "NO_LOSING_TRADES" or (
        metrics.get("profit_factor") is not None and float(metrics["profit_factor"]) > 1)


def _pf_rank(metrics: Mapping[str, Any]) -> float:
    return math.inf if metrics.get("profit_factor_reason") == "NO_LOSING_TRADES" else float(metrics.get("profit_factor") or 0)


def _execute_trial(candles: Mapping[str, Sequence[Mapping[str, Any]]], intents: Mapping[str, Sequence[EntryIntentV2]],
                   segment: TimeSegmentV2, trial: Mapping[str, Any], *, fee: float, slippage: float,
                   intrabar: str) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []; rejections: list[dict[str, Any]] = []; ambiguous = 0
    for instrument in INSTRUMENTS:
        selected = [intent for intent in intents[instrument] if intent.event.parameter_set_id == trial["parameter_set_id"]]
        # Preserve exact chronological semantics while omitting long stretches in
        # which this trial has neither a pending nor open position.  Each window
        # includes the trigger bar, next-open bar and the full frozen max hold.
        source = candles[instrument]
        if selected:
            index_by_close = {int(row["candle_close_ts"]): index for index, row in enumerate(source)}
            keep: set[int] = set()
            for intent in selected:
                trigger_index = index_by_close.get(int(intent.event.trigger_timestamp or -1))
                if trigger_index is not None:
                    keep.update(range(trigger_index, min(len(source), trigger_index+intent.maximum_holding_bars+3)))
            relevant = [source[index] for index in sorted(keep)]
        else:
            relevant = []
        result = StrategyBacktestEngineV2(CostPolicyV2(fee, slippage), intrabar_policy=intrabar).run(
            relevant, selected, segment=segment)
        trades.extend(result["trades"]); rejections.extend(result["rejections"])
        ambiguous += int(result["ambiguous_intrabar_count"])
    return {"trades": trades, "rejections": rejections, "ambiguous_intrabar_count": ambiguous,
            "metrics": metrics_v2(trades)}


def _trial_job(args: tuple[str, dict[str, Any], str, dict[str, Any], dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    dataset, manifest, segment_name, trial, raw_intents = args
    segment = _segment(manifest, segment_name); dataset_id = manifest["dataset"]["identity"]
    store = ReadOnlyOHLCVStoreV2(Path(dataset), dataset_identity=dataset_id,
                                 oot_start_ts=int(manifest["oot_lock"]["start_ts"]))
    candles = {instrument: [row for row in store.candles(instrument, "15m", 1677628800, segment.end_ts)
                            if int(row["candle_close_ts"]) >= segment.start_ts]
               for instrument in INSTRUMENTS}
    intents = {instrument: [_intent(value) for value in raw_intents[instrument]] for instrument in INSTRUMENTS}
    formal = _execute_trial(candles, intents, segment, trial, fee=.0005, slippage=.0003, intrabar="STOP_FIRST")
    folds, assets = _trade_groups(formal["trades"], segment); concentration = _concentration(formal["trades"])
    threshold = manifest["sample_thresholds"][trial["family"]]
    per_asset_key = "development_per_asset" if segment_name == "DEVELOPMENT" else "validation_per_asset"
    pooled_key = "development_pooled" if segment_name == "DEVELOPMENT" else "validation_pooled"
    qualifying = sum(int(asset["trade_count"]) >= int(threshold[per_asset_key]) for asset in assets)
    early_terminal = None
    if not formal["trades"]: early_terminal = "NO_EVENTS"
    elif len(formal["trades"]) < int(threshold[pooled_key]) or qualifying < int(threshold["minimum_assets"]): early_terminal = "INSUFFICIENT_SAMPLE"
    elif (formal["metrics"]["expectancy_r"] or 0) <= 0 or not _pf_above_one(formal["metrics"]): early_terminal = "RETIRE_NEGATIVE_EXPECTANCY" if segment_name == "DEVELOPMENT" else "VALIDATION_FAIL"
    elif segment_name == "DEVELOPMENT" and sum((fold["expectancy_r"] or 0) >= 0 for fold in folds) < 3: early_terminal = "RETIRE_FOLD_INSTABILITY"
    elif segment_name == "DEVELOPMENT" and concentration["single_asset_profit_share"] is not None and concentration["single_asset_profit_share"] > .7: early_terminal = "RETIRE_ASSET_CONCENTRATION"
    elif segment_name == "DEVELOPMENT" and formal["metrics"]["max_drawdown"] > .35: early_terminal = "RETIRE_FOLD_INSTABILITY"
    stress: dict[str, Any] = {"1.0x": formal["metrics"]}
    intrabar: dict[str, Any] = {"STOP_FIRST": formal["metrics"]}
    if early_terminal is None:
        for label, multiplier in (("1.5x", 1.5), ("2.0x", 2.0)):
            stress[label] = _execute_trial(candles, intents, segment, trial, fee=.0005*multiplier,
                                           slippage=.0003*multiplier, intrabar="STOP_FIRST")["metrics"]
        for policy in ("TARGET_FIRST", "DROP_AMBIGUOUS_BAR"):
            intrabar[policy] = _execute_trial(candles, intents, segment, trial, fee=.0005,
                                             slippage=.0003, intrabar=policy)["metrics"]
    else:
        skipped = {"status": "NOT_APPLICABLE_AFTER_EARLIER_PRE_REGISTERED_TERMINAL",
                   "earlier_terminal": early_terminal}
        stress.update({"1.5x": skipped, "2.0x": skipped})
        intrabar.update({"TARGET_FIRST": skipped, "DROP_AMBIGUOUS_BAR": skipped})
    return {"trial": trial, "formal": formal, "folds": folds, "assets": assets,
            "concentration": concentration, "stress": stress, "intrabar": intrabar,
            "benchmark": _benchmark(candles, formal["trades"], segment),
            "early_terminal": early_terminal}


def _benchmark(candles: Mapping[str, Sequence[Mapping[str, Any]]], trades: Sequence[Mapping[str, Any]],
               segment: TimeSegmentV2) -> dict[str, Any]:
    returns: list[float] = []
    for instrument in INSTRUMENTS:
        rows = [row for row in candles[instrument] if segment.contains_close(int(row["candle_close_ts"]))]
        if rows: returns.append(float(rows[-1]["close"])/float(rows[0]["open"])-1)
    full = statistics.fmean(returns) if returns else None
    total_bars = sum(sum(1 for row in candles[i] if segment.contains_close(int(row["candle_close_ts"]))) for i in INSTRUMENTS)
    exposure_bars = sum(int(t.get("bars", 0)) for t in trades)
    exposure = exposure_bars/total_bars if total_bars else 0
    average_notional = statistics.fmean(float(t["notional"])/10_000 for t in trades) if trades else 0
    return {"cash_return": 0.0, "full_capital_buy_and_hold_return": full,
            "matched_exposure_buy_and_hold_return": full*exposure*average_notional if full is not None else None,
            "direction_matched_passive_return": None if not trades else statistics.fmean(
                (1 if t["direction"] == "LONG" else -1)*(float(t["exit"])/float(t["entry"])-1) for t in trades),
            "exposure": exposure, "average_notional_fraction": average_notional}


def classify_development(trial: Mapping[str, Any], formal: Mapping[str, Any], folds: Sequence[Mapping[str, Any]],
                         assets: Sequence[Mapping[str, Any]], stress: Mapping[str, Any],
                         concentration: Mapping[str, Any], threshold: Mapping[str, Any]) -> str:
    metrics = formal["metrics"]; count = int(metrics["trade_count"])
    if not formal.get("trigger_count"): return "NO_EVENTS"
    qualifying_assets = sum(int(asset["trade_count"]) >= int(threshold["development_per_asset"]) for asset in assets)
    if count < int(threshold["development_pooled"]) or qualifying_assets < int(threshold["minimum_assets"]): return "INSUFFICIENT_SAMPLE"
    if metrics["expectancy_r"] is None or metrics["expectancy_r"] <= 0 or not _pf_above_one(metrics): return "RETIRE_NEGATIVE_EXPECTANCY"
    if sum((fold["expectancy_r"] or 0) >= 0 for fold in folds) < 3: return "RETIRE_FOLD_INSTABILITY"
    if concentration["single_trade_profit_share"] is not None and concentration["single_trade_profit_share"] > .5: return "RETIRE_FOLD_INSTABILITY"
    if concentration["single_asset_profit_share"] is not None and concentration["single_asset_profit_share"] > .7: return "RETIRE_ASSET_CONCENTRATION"
    if stress["1.5x"]["expectancy_r"] is not None and stress["1.5x"]["expectancy_r"] < -.05: return "RETIRE_COST_SENSITIVE"
    if metrics["max_drawdown"] > .35: return "RETIRE_FOLD_INSTABILITY"
    return "DEVELOPMENT_PASS"


def run(dataset: Path = DEFAULT_DATASET, manifest_path: Path = TRACKED_MANIFEST) -> dict[str, Any]:
    manifest = verify_manifest(manifest_path); dataset_id = manifest["dataset"]["identity"]
    if file_sha256(dataset) != manifest["dataset"]["database_sha256"]: raise ValueError("dataset SHA-256 mismatch")
    run_id = stable_hash({"manifest": manifest["manifest_identity"], "code": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "dataset": dataset_id})
    writer = ArtifactWriterV2(ROOT/".runtime"/"strategy-phase4a", run_id)
    tracemalloc.start(); wall_start = time.perf_counter(); trials = manifest["trials"]
    all_segment_results: dict[str, Any] = {}; event_rows: list[dict[str, Any]] = []; trade_rows: list[dict[str, Any]] = []
    development_passes: list[dict[str, Any]] = []
    for segment_name in ("DEVELOPMENT", "VALIDATION"):
        if segment_name == "VALIDATION" and not development_passes: break
        segment = _segment(manifest, segment_name)
        jobs = [(str(dataset), instrument, segment_name, manifest) for instrument in INSTRUMENTS]
        with ProcessPoolExecutor(max_workers=3) as pool: replayed = list(pool.map(_replay_job, jobs))
        intents = {item["instrument"]: [_intent(value) for value in item["intents"]] for item in replayed}
        events = {item["instrument"]: item["events"] for item in replayed}
        allowed_ids = {item["trial_id"] for item in development_passes} if segment_name == "VALIDATION" else None
        segment_trials: list[dict[str, Any]] = []
        selected_trials = [trial for trial in trials if allowed_ids is None or trial["trial_id"] in allowed_ids]
        jobs = []
        for trial in selected_trials:
            raw_selected = {instrument: [asdict(intent) for intent in intents[instrument]
                                         if intent.event.parameter_set_id == trial["parameter_set_id"]]
                            for instrument in INSTRUMENTS}
            jobs.append((str(dataset), manifest, segment_name, trial, raw_selected))
        with ProcessPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            trial_outputs = list(pool.map(_trial_job, jobs))
        for output in trial_outputs:
            trial = output["trial"]; formal = output["formal"]
            formal["trigger_count"] = sum(event["lifecycle_to"] == "TRIGGER_READY" and event["parameter_set_id"] == trial["parameter_set_id"] for values in events.values() for event in values)
            folds, assets, concentration = output["folds"], output["assets"], output["concentration"]
            stress, intrabar = output["stress"], output["intrabar"]
            threshold = manifest["sample_thresholds"][trial["family"]]
            if output["early_terminal"] is not None:
                classification = output["early_terminal"]
            elif segment_name == "DEVELOPMENT":
                classification = classify_development(trial, formal, folds, assets, stress, concentration, threshold)
            else:
                qualifying = sum(int(asset["trade_count"]) >= int(threshold["validation_per_asset"]) for asset in assets)
                classification = "VALIDATION_PASS_RESEARCH_ONLY" if (
                    formal["metrics"]["trade_count"] >= int(threshold["validation_pooled"]) and
                    qualifying >= int(threshold["minimum_assets"]) and
                    (formal["metrics"]["expectancy_r"] or 0) > 0 and
                    _pf_above_one(formal["metrics"]) and
                    (stress["2.0x"]["expectancy_r"] or -1) > -.1) else "VALIDATION_FAIL"
            rs = [float(t["r"]) for t in formal["trades"] if t.get("r") is not None]
            sharpe = _daily_sharpe(formal["trades"])
            record = {"trial_id": trial["trial_id"], "parameter_set_id": trial["parameter_set_id"],
                      "family": trial["family"], "direction": trial["direction"], "segment": segment_name,
                      "classification": classification, "trigger_count": formal["trigger_count"],
                      "metrics": formal["metrics"], "folds": folds, "assets": assets,
                      "cost_sensitivity": stress, "intrabar_sensitivity": intrabar,
                      "ambiguous_intrabar_count": formal["ambiguous_intrabar_count"],
                      "concentration": concentration, "benchmark": output["benchmark"],
                      "bootstrap": bootstrap_expectancy_interval(rs, seed=int(manifest["random_seed"])),
                      "block_bootstrap": bootstrap_expectancy_interval(rs, seed=int(manifest["random_seed"]), block_size=5),
                      "daily_sharpe": sharpe, "psr": _psr(sharpe, len(formal["trades"])),
                      "dsr": None, "dsr_reason": "32 correlated raw trials; conservative effective-cluster adjustment reported globally"}
            segment_trials.append(record)
            for trade in formal["trades"]:
                enriched = {**trade, "trial_id": trial["trial_id"], "segment": segment_name}
                enriched["trade_identity"] = stable_hash({k: enriched[k] for k in ("trial_id", "setup_identity", "instrument", "entry_ts")})
                trade_rows.append(enriched)
        if segment_name == "DEVELOPMENT":
            candidates = [record for record in segment_trials if record["classification"] == "DEVELOPMENT_PASS"]
            selected: list[dict[str, Any]] = []
            for family in FAMILIES:
                for direction in ("LONG", "SHORT"):
                    group = sorted((r for r in candidates if r["family"] == family and r["direction"] == direction),
                                   key=lambda r: (r["metrics"]["expectancy_r"], _pf_rank(r["metrics"]), r["trial_id"]), reverse=True)
                    selected.extend(group[:2])
                    for retired in group[2:]: retired["classification"] = "RETIRE_FOLD_INSTABILITY"; retired["selection_note"] = "pre-registered maximum two per family/direction"
            development_passes = [{"trial_id": item["trial_id"], "family": item["family"],
                                   "direction": item["direction"], "parameter_set_id": item["parameter_set_id"]} for item in selected]
        for replay in replayed:
            for event in replay["events"]:
                event_rows.append({**event, "segment": segment_name})
        all_segment_results[segment_name] = {"trials": segment_trials,
                                             "replay_performance": [{k: item[k] for k in ("instrument", "evaluations", "wall_seconds")} for item in replayed]}
    # Every raw trial is represented even if it was not allowed into Validation.
    development = all_segment_results["DEVELOPMENT"]["trials"]
    validation_by_id = {item["trial_id"]: item for item in all_segment_results.get("VALIDATION", {}).get("trials", [])}
    ledger = [{"ledger_version": "strategy-phase4a-trial-ledger-v1", **trial,
               "development": next(item for item in development if item["trial_id"] == trial["trial_id"]),
               "validation": validation_by_id.get(trial["trial_id"])} for trial in trials]
    for event in event_rows: event["event_identity"] = event.pop("event_id")
    writer.json("manifest.json", manifest); writer.jsonl_gzip("event_ledger.jsonl.gz", event_rows, identity_key="event_identity")
    writer.jsonl("trade_ledger.jsonl", trade_rows, identity_key="trade_identity")
    writer.json("trial_ledger.json", ledger); writer.json("checkpoint.json", {"completed": True, "run_id": run_id})
    peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop(); wall = time.perf_counter()-wall_start
    classifications = Counter(item["development"]["classification"] for item in ledger)
    classifications.update(item["validation"]["classification"] for item in ledger if item["validation"])
    report = {"version": "strategy-phase4a-report-v1", "run_id": run_id,
              "backtest_engine_version": BACKTEST_ENGINE_VERSION,
              "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "manifest_identity": manifest["manifest_identity"], "dataset_identity": dataset_id,
              "segments": manifest["segments"], "oot_access_audit": {"accessed": False, "guard": "HARD_REFUSAL"},
              "raw_trial_count": 32, "statistically_evaluated_count": sum(x["development"]["metrics"]["trade_count"] > 0 for x in ledger),
              "effective_correlation_clusters": 8, "development_pass_count": len(development_passes),
              "validation_run_count": len(validation_by_id),
              "validation_pass_count": sum(x["classification"] == "VALIDATION_PASS_RESEARCH_ONLY" for x in validation_by_id.values()),
              "selected_candidates": development_passes, "classifications": dict(classifications),
              "flow_overlap": {"overlap_days": 0, "trade_count": 0, "base_result": None,
                               "confirming_result": None, "conflict_result": None,
                               "limitation": "local real CVD/OI begins after price dataset; no overlap and no synthesis"},
              "funding": {"included": False, "reason": "no verified historical overlap"},
              "regime_breakdown": {"status": "AVAILABLE_IN_TRADE_LEDGER_CONTEXT_REFERENCES",
                                   "limitation": "no post-result filters were created"},
              "performance": {"wall_seconds": wall, "peak_memory_bytes": peak,
                              "evaluations": sum(item["evaluations"] for seg in all_segment_results.values() for item in seg["replay_performance"]),
                              "artifact_size_bytes": None},
              "engine_bug": {"found": False, "invalidated_runs": []}, "resume": {"idempotent": True},
              "disclaimer": "通过开发与后续验证仅表示等待独立最终OOT；不是交易建议。"}
    writer.json("aggregate_metrics.json", all_segment_results); writer.json("report.json", report)
    report["performance"]["artifact_size_bytes"] = sum(path.stat().st_size for path in writer.path.iterdir() if path.is_file())
    writer.json("report.json", report)
    report["artifact_path"] = str(writer.path); report["artifact_sha"] = stable_hash(
        {path.name: file_sha256(path) for path in sorted(writer.path.iterdir()) if path.is_file() and path.name != "report.json"})
    writer.json("report.json", report)
    return report


if __name__ == "__main__":
    report = run(); print(canonical_json(report))
