"""Phase 4A research CLI.  It never connects to a production database or API."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from dashboard.strategy_phase4a import (  # noqa: E402
    AccountPolicyV2, ArtifactWriterV2, BACKTEST_ENGINE_VERSION,
    CONTEXT_VERSION, CostPolicyV2, DEFINITIONS_VERSION, DISCLAIMER, FAMILIES,
    INSTRUMENTS, MANIFEST_VERSION, REPLAY_ENGINE_VERSION, REPORT_VERSION,
    ROUTER_VERSION, STATE_VERSION, StrategyBacktestEngineV2,
    StrategyEventReplayEngineV2, TRIAL_LEDGER_VERSION, bootstrap_expectancy_interval,
    canonical_json, chronological_segments, file_sha256, frozen_trials, metrics_v2,
    ReadOnlyOHLCVStoreV2, stable_hash, utc_iso,
)

DEFAULT_DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
DEFAULT_SOURCE_MANIFEST = DEFAULT_DATASET.with_suffix(".manifest.json")
TRACKED_MANIFEST = ROOT / "research" / "phase4a_research_manifest_v1.json"
ARTIFACT_ROOT = ROOT / ".runtime" / "strategy-phase4a"
WARMUP_SECONDS = 240 * 86400
RANDOM_SEED = 20260804


def source_metadata(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    first_open = 1677628800; end_exclusive = 1767225600
    segments = chronological_segments(first_open+900, end_exclusive, WARMUP_SECONDS)
    return {"source_manifest": manifest, "first_open_ts": first_open,
            "first_close_ts": first_open+900, "end_exclusive_ts": end_exclusive,
            "segments": segments}


def _load_partitions(store: ReadOnlyOHLCVStoreV2, instrument: str, end_ts: int) -> dict[str, list[dict[str, Any]]]:
    return {timeframe: store.candles(instrument, timeframe, 1677628800, end_ts)
            for timeframe in ("15m", "1H", "4H", "1D")}


def _audit_asset(arguments: tuple[str, str, int, dict[str, tuple[int, int, str]]]) -> dict[str, Any]:
    dataset_path, instrument, oot_start, raw_segments = arguments
    dataset_id = json.loads(DEFAULT_SOURCE_MANIFEST.read_text(encoding="utf-8"))["dataset_fingerprint"]
    store = ReadOnlyOHLCVStoreV2(Path(dataset_path), dataset_identity=dataset_id, oot_start_ts=oot_start)
    trials = frozen_trials(dataset_id); engine = StrategyEventReplayEngineV2(); output: dict[str, Any] = {}
    for name in ("DEVELOPMENT", "VALIDATION"):
        start, end, identity = raw_segments[name]
        from dashboard.strategy_phase4a import TimeSegmentV2
        segment = TimeSegmentV2(name, start, end, identity)
        result = engine.replay(_load_partitions(store, instrument, end), trials,
                               instrument=instrument, segment=segment, event_frequency_only=True)
        output[name] = {
            "events": [(event.family, event.direction, event.lifecycle_to) for event in result["events"]],
            "evaluations": result["evaluations"], "wall_seconds": result["wall_seconds"],
        }
    return {"instrument": instrument, "segments": output}


def event_audit(dataset: Path = DEFAULT_DATASET) -> dict[str, Any]:
    metadata = source_metadata(DEFAULT_SOURCE_MANIFEST)
    dataset_id = metadata["source_manifest"]["dataset_fingerprint"]
    oot_start = metadata["segments"]["LOCKED_FINAL_OOT"].start_ts
    store = ReadOnlyOHLCVStoreV2(dataset, dataset_identity=dataset_id, oot_start_ts=oot_start)
    trials = frozen_trials(dataset_id); engine = StrategyEventReplayEngineV2()
    audit: dict[str, Any] = {"mode": "EVENT_FREQUENCY_ONLY_NO_PNL", "dataset_identity": dataset_id,
                             "segments": {}, "counts": {}, "performance": {}}
    raw_segments = {name: (segment.start_ts, segment.end_ts, segment.identity)
                    for name, segment in metadata["segments"].items()}
    jobs = [(str(dataset), instrument, oot_start, raw_segments) for instrument in INSTRUMENTS]
    with ProcessPoolExecutor(max_workers=min(3, len(jobs))) as pool:
        asset_results = list(pool.map(_audit_asset, jobs))
    for segment_name in ("DEVELOPMENT", "VALIDATION"):
        segment = metadata["segments"][segment_name]
        counts: Counter[tuple[str, str, str]] = Counter(); per_asset: Counter[tuple[str, str, str, str]] = Counter()
        evaluations = 0; wall = 0.0
        for asset_result in asset_results:
            instrument = asset_result["instrument"]; result = asset_result["segments"][segment_name]
            evaluations += result["evaluations"]; wall += result["wall_seconds"]
            for family, direction, stage in result["events"]:
                counts[(family, direction, stage)] += 1
                per_asset[(instrument, family, direction, stage)] += 1
        audit["segments"][segment_name] = asdict(segment)
        audit["counts"][segment_name] = [
            {"family": key[0], "direction": key[1], "stage": key[2], "count": value}
            for key, value in sorted(counts.items())]
        audit["counts"][segment_name+"_PER_ASSET"] = [
            {"instrument": key[0], "family": key[1], "direction": key[2], "stage": key[3], "count": value}
            for key, value in sorted(per_asset.items())]
        audit["performance"][segment_name] = {"evaluations": evaluations, "wall_seconds": wall,
                                                "evaluations_per_second": evaluations/wall if wall else None}
    return audit


def build_manifest(audit: dict[str, Any], dataset: Path = DEFAULT_DATASET) -> dict[str, Any]:
    metadata = source_metadata(DEFAULT_SOURCE_MANIFEST); source = metadata["source_manifest"]
    trials = frozen_trials(source["dataset_fingerprint"])
    thresholds = {
        "TREND_PULLBACK": {"development_pooled": 90, "validation_pooled": 30,
                           "development_per_asset": 20, "validation_per_asset": 8, "minimum_assets": 2},
        "MA200_MEAN_REVERSION": {"development_pooled": 30, "validation_pooled": 10,
                                  "development_per_asset": 8, "validation_per_asset": 3, "minimum_assets": 2},
    }
    origin_sha = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=ROOT, text=True).strip()
    payload: dict[str, Any] = {
        "version": MANIFEST_VERSION, "status": "FROZEN_BEFORE_PNL", "origin_main_sha": origin_sha,
        "versions": {"router": ROUTER_VERSION, "definitions": DEFINITIONS_VERSION,
                     "context": CONTEXT_VERSION, "state": STATE_VERSION,
                     "replay_engine": REPLAY_ENGINE_VERSION, "backtest_engine": BACKTEST_ENGINE_VERSION},
        "scope": {"families": list(FAMILIES), "directions": ["LONG", "SHORT"],
                  "excluded_families": ["BREAKOUT_CONTINUATION", "FAILED_BREAKOUT_REVERSAL"],
                  "assets": list(INSTRUMENTS), "execution_timeframe": "15m",
                  "required_timeframes": ["15m", "1H", "4H", "1D", "1W"]},
        "dataset": {"path_policy": "caller-supplied verified offline immutable SQLite; path excluded from identity",
                    "identity": source["dataset_fingerprint"], "database_sha256": source["database_sha256"],
                    "raw_range_utc": source["raw_range_utc"], "partition_fingerprints": source["partition_fingerprints"]},
        "warmup": {"seconds": WARMUP_SECONDS, "days": 240,
                   "rule": "200 completed 1D candles plus causal slope/weekly formation buffer; no pre-segment position"},
        "segments": {name: {**asdict(segment), "start_utc": utc_iso(segment.start_ts), "end_utc": utc_iso(segment.end_ts)}
                     for name, segment in metadata["segments"].items()},
        "development_folds": 4,
        "sample_thresholds": thresholds,
        "execution_policy": {"trigger": "confirmed 15m close", "entry": "next 15m open only",
                             "missing_or_bad_next_open": "NO_TRADE", "entry_gap": "reject if invalidated",
                             "entry_geometry": "recompute structural R; reject below frozen minimum"},
        "intrabar_policy": {"formal": "STOP_FIRST", "diagnostics": ["TARGET_FIRST", "DROP_AMBIGUOUS_BAR"]},
        "gap_policy": {"stop": "worse next open", "target": "open or target whichever is conservative"},
        "cost_policy": {"fee_rate_each_side": .0005, "adverse_slippage_each_side": .0003,
                        "stress_multipliers": [1.0, 1.5, 2.0],
                        "funding": "real overlap diagnostic only; never synthesized"},
        "account_policy": asdict(AccountPolicyV2()),
        "position_policy": {"compound": True, "one_open_per_instrument_candidate": True,
                            "pyramiding": False, "averaging": False},
        "benchmark_policy": ["CASH", "FULL_CAPITAL_BUY_AND_HOLD", "MATCHED_EXPOSURE_BUY_AND_HOLD",
                             "DIRECTION_MATCHED_PASSIVE"],
        "candidate_classification": [
            "INVALID_ENGINE_OR_DATA", "NO_EVENTS", "INSUFFICIENT_SAMPLE", "RETIRE_NEGATIVE_EXPECTANCY",
            "RETIRE_COST_SENSITIVE", "RETIRE_FOLD_INSTABILITY", "RETIRE_ASSET_CONCENTRATION",
            "RETIRE_INTRABAR_SENSITIVE", "DEVELOPMENT_PASS", "VALIDATION_FAIL",
            "VALIDATION_PASS_RESEARCH_ONLY"],
        "development_selection": {"maximum_per_family_direction": 2, "maximum_total": 8,
                                  "minimum_nonnegative_folds": 3, "maximum_single_trade_profit_share": .5,
                                  "maximum_single_asset_profit_share": .7, "maximum_drawdown": .35,
                                  "requires_positive_expectancy": True, "requires_pf_above_one": True,
                                  "cost_1_5x_must_not_be_materially_negative": True},
        "validation_policy": {"no_retuning": True, "requires_positive_expectancy": True,
                              "requires_pf_above_one": True, "two_x_cost_not_catastrophic": True},
        "robustness_tests": ["FROZEN_PARAMETER_NEIGHBORS", "COST_1.5X", "COST_2X",
                             "TARGET_FIRST", "DROP_AMBIGUOUS_BAR", "BLOCK_BOOTSTRAP"],
        "statistics": {"bootstrap_repetitions": 2000, "block_bootstrap_size": 5,
                       "psr": True, "dsr": True, "multiple_testing_caveat": True},
        "random_seed": RANDOM_SEED, "raw_trial_count": 32,
        "trials": [asdict(trial) for trial in trials],
        "oot_lock": {"boundary_metadata_only": True, "read_forbidden": True,
                     "start_ts": metadata["segments"]["LOCKED_FINAL_OOT"].start_ts},
        "event_frequency_audit": audit,
        "governance": {"results_cannot_change_manifest": True,
                       "engine_bug": "invalidate old run, bump engine, rerun all 32",
                       "validation_cannot_select_or_tune": True},
        "disclaimer": DISCLAIMER,
    }
    payload["manifest_identity"] = stable_hash(payload)
    return payload


def freeze(audit_path: Path, output: Path = TRACKED_MANIFEST) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("mode") != "EVENT_FREQUENCY_ONLY_NO_PNL": raise ValueError("audit includes forbidden outcome data")
    manifest = build_manifest(audit)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(manifest)+"\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "freeze"))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--audit", type=Path, default=ARTIFACT_ROOT/"pre_manifest_event_audit.json")
    parser.add_argument("--output", type=Path, default=TRACKED_MANIFEST)
    args = parser.parse_args()
    if args.mode == "audit":
        result = event_audit(args.dataset); args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(canonical_json(result)+"\n", encoding="utf-8")
        print(canonical_json({"audit": str(args.audit), "identity": stable_hash(result), "performance": result["performance"]}))
    else:
        result = freeze(args.audit, args.output)
        print(canonical_json({"manifest": str(args.output), "identity": result["manifest_identity"]}))


if __name__ == "__main__": main()
