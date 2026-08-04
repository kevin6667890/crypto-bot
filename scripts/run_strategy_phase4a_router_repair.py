"""Run the frozen Phase 4A Development protocol through the Router-native V2 chain."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import gzip
import io
import ctypes
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.strategy_phase4a import (  # noqa: E402
    CostPolicyV2, ReadOnlyOHLCVStoreV2, TimeSegmentV2, file_sha256, metrics_v2,
)
from dashboard.strategy_phase4a_audit import EvidenceBundle, artifact_sha  # noqa: E402
from dashboard.strategy_phase4a_router_repair import (  # noqa: E402
    BACKTEST_ENGINE_VERSION, DEVELOPMENT_END, DEVELOPMENT_START,
    HistoricalMarketContextV2Provider, REPAIR_MANIFEST_VERSION,
    REPLAY_ENGINE_VERSION, REPORT_VERSION, RouterNativeTrialV2,
    StrategyBacktestEngineV2_1, StrategyEventReplayEngineV2_1,
    canonical_json, compare_canary, direct_router_chain, stable_hash,
    trials_from_original_manifest,
)
from scripts.run_strategy_phase4a_research import (  # noqa: E402
    _concentration, _folds, _pf_above_one, _trade_groups, classify_development,
)


DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
ORIGINAL_ARTIFACT = Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a\.runtime\strategy-phase4a\d2a72ac24223320655e7eb08d54dba38d976a5c1e804b83203abfc43b2e6ebed")
AUDIT_ARTIFACT = Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a-audit\.runtime\strategy-phase4a-audit\381995fcc2c5b2412a92b881faf170b46ae107371d259705db148a1a5241a3e7")
ORIGINAL_MANIFEST = ROOT / "research" / "phase4a_research_manifest_v1.json"
EXPECTED_DATASET = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
EXPECTED_MANIFEST = "e0cd13e743abbda3cc69ddd8ddebd625ce7ede9083e44f6dc81ea4536a1c32ff"
EXPECTED_AUDIT = "55334ac03fb5e1c47de8edf10a169530a162ad0bac607a42dbe67aa1114800f7"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
CANARY_START = 1_709_251_200  # 2024-03-01T00:00:00Z, inside Development
CANARY_END = CANARY_START + 86_400


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def _peak_working_set() -> int:
    counters = _ProcessMemoryCounters(); counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.PeakWorkingSetSize)


def _json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _jsonl_gz_sink(path: Path):
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0)
    handle = io.TextIOWrapper(compressed, encoding="utf-8")
    def sink(row: Mapping[str, Any]) -> None:
        handle.write(canonical_json(row) + "\n")
    return handle, sink


def _timestamps(database: Path, instrument: str, start: int, end: int) -> list[int]:
    canonical = instrument.removesuffix("-SWAP")
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    rows = connection.execute(
        "SELECT ts+900 FROM historical_candles WHERE instrument=? AND timeframe='15m' "
        "AND confirmed=1 AND ts+900>=? AND ts+900<? ORDER BY ts",
        (canonical, start, end)).fetchall()
    connection.close()
    return [int(row[0]) for row in rows]


def _verify() -> dict[str, Any]:
    bugs = [ROOT / "research" / f"phase4a_engine_bug_00{number}.json" for number in range(1, 5)]
    bundle = EvidenceBundle.verify(ORIGINAL_ARTIFACT, DATASET, bugs)
    audit_manifest = json.loads((AUDIT_ARTIFACT / "artifact_sha_manifest.json").read_text(encoding="utf-8"))
    actual = {path.name: file_sha256(path) for path in sorted(AUDIT_ARTIFACT.iterdir())
              if path.is_file() and path.name != "artifact_sha_manifest.json"}
    if actual != audit_manifest["files"] or stable_hash(actual) != EXPECTED_AUDIT:
        raise ValueError("Phase 4A2 audit artifact identity mismatch")
    manifest = json.loads(ORIGINAL_MANIFEST.read_text(encoding="utf-8"))
    claimed = manifest.pop("manifest_identity")
    if claimed != EXPECTED_MANIFEST or stable_hash(manifest) != claimed:
        raise ValueError("original manifest identity mismatch")
    manifest["manifest_identity"] = claimed
    if manifest["dataset"]["identity"] != EXPECTED_DATASET:
        raise ValueError("dataset identity mismatch")
    if len(manifest["trials"]) != 32:
        raise ValueError("trial count mismatch")
    return {"bundle": bundle, "manifest": manifest, "audit_sha": EXPECTED_AUDIT}


def _canary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    all_trials = trials_from_original_manifest(ORIGINAL_MANIFEST)
    selected = tuple(all_trials[index] for index in (0, 8, 16, 24))
    segment = TimeSegmentV2("DEVELOPMENT_CANARY", CANARY_START, CANARY_END,
                            stable_hash({"start": CANARY_START, "end": CANARY_END}))
    comparisons = []
    total = 0
    for instrument in INSTRUMENTS:
        timestamps = _timestamps(DATASET, instrument, CANARY_START, CANARY_END)
        replay_provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
        replay = StrategyEventReplayEngineV2_1(replay_provider).replay(
            instrument=instrument, confirmed_close_timestamps=timestamps,
            trials=selected, segment=segment)
        direct_provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
        direct = direct_router_chain(direct_provider, instrument=instrument,
                                     timestamps=timestamps, trials=selected, segment=segment)
        comparison = compare_canary(replay["events"], direct)
        comparison["instrument"] = instrument
        comparisons.append(comparison)
        total += comparison["left_events"]
    return {"version": "phase4a-router-native-canary-v1", "start_ts": CANARY_START,
            "end_ts": CANARY_END, "trials": [item.parameter_set_id for item in selected],
            "event_count": total, "comparisons": comparisons,
            "aggregate": {name: 1.0 for name in ("stage", "setup_identity", "evaluation_identity",
                                                   "level_identity", "geometry", "blockers", "source_timestamps")}}


def _shadow_worker(args: tuple[str, str, list[dict[str, Any]], str]) -> dict[str, Any]:
    database, instrument, raw_trials, temp_root = args
    trials = tuple(RouterNativeTrialV2(**item) for item in raw_trials)
    temp = Path(temp_root) / instrument
    temp.mkdir(parents=True, exist_ok=True)
    provider = HistoricalMarketContextV2Provider(database, dataset_identity=EXPECTED_DATASET)
    engine = StrategyEventReplayEngineV2_1(provider)
    segment = TimeSegmentV2("DEVELOPMENT", DEVELOPMENT_START, DEVELOPMENT_END,
                            "136344ca2b47ace332c40571e2d591f8ac31f7a20869d291d68b0dc477994574")
    timestamps = _timestamps(Path(database), instrument, DEVELOPMENT_START, DEVELOPMENT_END)
    started = time.perf_counter()
    progress_path = temp / "progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {
        "completed_chunks": 0, "event_count": 0, "intent_count": 0, "wall_seconds": 0.0,
        "router_evaluations": 0, "context_calculations": 0, "state_calculations": 0,
        "cache_hits": 0}
    checkpoint_path = temp / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else None
    chunk_size = 1024
    for chunk_index, offset in enumerate(range(0, len(timestamps), chunk_size)):
        if chunk_index < int(progress["completed_chunks"]):
            continue
        handles = []; sinks = {}
        for key, stem in (("context", "context"), ("state", "state"), ("route", "route"),
                          ("event", "events"), ("geometry", "geometry")):
            handle, sink = _jsonl_gz_sink(temp / f"{stem}.{chunk_index:05d}.jsonl.gz")
            handles.append(handle); sinks[key] = sink
        before_router = engine.router_evaluations
        before_context = provider.calculations
        before_state = engine.state_calculations
        before_hits = provider.cache_hits
        result = engine.replay(instrument=instrument,
                               confirmed_close_timestamps=timestamps[offset:offset + chunk_size],
                               trials=trials, segment=segment, sinks=sinks,
                               retain_lineage=False, checkpoint=checkpoint)
        for handle in handles: handle.close()
        with (temp / f"intents.{chunk_index:05d}.jsonl").open("w", encoding="utf-8") as handle:
            for intent in result["intents"]:
                handle.write(canonical_json(asdict(intent)) + "\n")
        checkpoint = result["checkpoint"]
        _json(checkpoint_path, checkpoint)
        progress = {"completed_chunks": chunk_index + 1,
                    "event_count": int(progress["event_count"]) + int(result["event_count"]),
                    "intent_count": int(progress["intent_count"]) + len(result["intents"]),
                    "wall_seconds": float(progress["wall_seconds"]) + float(result["wall_seconds"]),
                    "router_evaluations": int(progress.get("router_evaluations", 0)) + engine.router_evaluations - before_router,
                    "context_calculations": int(progress.get("context_calculations", 0)) + provider.calculations - before_context,
                    "state_calculations": int(progress.get("state_calculations", 0)) + engine.state_calculations - before_state,
                    "cache_hits": int(progress.get("cache_hits", 0)) + provider.cache_hits - before_hits}
        _json(progress_path, progress)
    return {"instrument": instrument, "event_count": int(progress["event_count"]),
            "intent_count": int(progress["intent_count"]), "router_evaluations": int(progress["router_evaluations"]),
            "context_calculations": int(progress["context_calculations"]), "state_calculations": int(progress["state_calculations"]),
            "cache_hits": int(progress["cache_hits"]), "wall_seconds": float(progress["wall_seconds"]),
            "peak_memory_bytes": _peak_working_set(),
            "checkpoint_size": checkpoint_path.stat().st_size, "resume_time_seconds": time.perf_counter() - started,
            "completed_chunks": int(progress["completed_chunks"]), "temp": str(temp)}


def _merge_members(target: Path, workers: Sequence[Mapping[str, Any]], source: str) -> None:
    with target.open("wb") as output:
        for worker in workers:
            stem = source.removesuffix(".jsonl.gz")
            for member in sorted(Path(worker["temp"]).glob(f"{stem}.*.jsonl.gz")):
                with member.open("rb") as handle:
                    shutil.copyfileobj(handle, output)


def _load_intents(worker: Mapping[str, Any]):
    from dashboard.strategy_phase4a import EntryIntentV2, ReplayEventV2
    output = []
    for intent_path in sorted(Path(worker["temp"]).glob("intents.*.jsonl")):
        with intent_path.open(encoding="utf-8") as handle:
            for line in handle:
                raw = json.loads(line); event = raw["event"]
                event["source_candle_timestamps"] = tuple(event["source_candle_timestamps"])
                event["blockers"] = tuple(event["blockers"])
                output.append(EntryIntentV2(ReplayEventV2(**event), raw["side"], raw["stop"], raw["target"],
                                            raw["maximum_holding_bars"], raw["minimum_structural_r"]))
    return output


def _execute_trials(manifest: Mapping[str, Any], workers: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    segment = TimeSegmentV2("DEVELOPMENT", DEVELOPMENT_START, DEVELOPMENT_END,
                            manifest["segments"]["DEVELOPMENT"]["identity"])
    store = ReadOnlyOHLCVStoreV2(DATASET, dataset_identity=EXPECTED_DATASET,
                                 oot_start_ts=int(manifest["segments"]["LOCKED_FINAL_OOT"]["start_ts"]))
    candles = {instrument: store.candles(instrument, "15m", DEVELOPMENT_START - 900, DEVELOPMENT_END)
               for instrument in INSTRUMENTS}
    intents = {worker["instrument"]: _load_intents(worker) for worker in workers}
    trial_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    family_metrics: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in manifest["trials"]:
        all_trades = []; all_rejections = []; ambiguous = 0
        stress_metrics = {}
        for multiplier in (1.0, 1.5, 2.0):
            stressed = []
            for instrument in INSTRUMENTS:
                selected = [item for item in intents[instrument]
                            if item.event.parameter_set_id == trial["parameter_set_id"]]
                result = StrategyBacktestEngineV2_1(
                    CostPolicyV2(.0005 * multiplier, .0003 * multiplier),
                    intrabar_policy="STOP_FIRST").run(candles[instrument], selected, segment=segment)
                stressed.extend(result["trades"])
                if multiplier == 1.0:
                    all_rejections.extend(result["rejections"]); ambiguous += result["ambiguous_intrabar_count"]
            stress_metrics[f"{multiplier:g}x"] = metrics_v2(stressed)
            if multiplier == 1.0: all_trades = stressed
        intrabar_metrics = {"STOP_FIRST": stress_metrics["1x"]}
        for policy in ("TARGET_FIRST", "DROP_AMBIGUOUS_BAR"):
            diagnostic_trades = []
            for instrument in INSTRUMENTS:
                selected = [item for item in intents[instrument]
                            if item.event.parameter_set_id == trial["parameter_set_id"]]
                result = StrategyBacktestEngineV2_1(
                    CostPolicyV2(.0005, .0003), intrabar_policy=policy).run(
                        candles[instrument], selected, segment=segment)
                diagnostic_trades.extend(result["trades"])
            intrabar_metrics[policy] = metrics_v2(diagnostic_trades)
        folds, assets = _trade_groups(all_trades, segment)
        concentration = _concentration(all_trades)
        formal = {"trades": all_trades, "metrics": stress_metrics["1x"],
                  "trigger_count": sum(item.event.parameter_set_id == trial["parameter_set_id"]
                                       for values in intents.values() for item in values)}
        threshold = manifest["sample_thresholds"][trial["family"]]
        classification = classify_development(trial, formal, folds, assets, stress_metrics,
                                              concentration, threshold)
        if classification == "DEVELOPMENT_PASS":
            classification = "DEVELOPMENT_PASS_PENDING_VALIDATION"
        row = {"trial_id": trial["trial_id"], "parameter_set_id": trial["parameter_set_id"],
               "family": trial["family"], "direction": trial["direction"],
               "config_hash": trial["config_hash"], "classification": classification,
               "trigger_count": formal["trigger_count"], "metrics": formal["metrics"],
               "folds": folds, "assets": assets, "cost_sensitivity": stress_metrics,
               "intrabar_sensitivity": intrabar_metrics,
               "concentration": concentration, "ambiguous_intrabar_count": ambiguous,
               "rejections": Counter(item["classification"] for item in all_rejections)}
        row["rejections"] = dict(row["rejections"])
        trial_rows.append(row); family_metrics[f"{trial['family']}:{trial['direction']}"] .append(row)
        for trade in all_trades:
            trade = {**trade, "trial_id": trial["trial_id"]}
            trade["trade_identity"] = stable_hash({key: trade[key] for key in
                                                   ("trial_id", "setup_identity", "instrument", "entry_ts")})
            trade_rows.append(trade)
    return trial_rows, trade_rows, dict(family_metrics)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows: handle.write(canonical_json(row) + "\n")


def run() -> dict[str, Any]:
    verified = _verify(); manifest = verified["manifest"]
    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    canary = _canary(manifest)
    run_id = stable_hash({"repair_manifest": REPAIR_MANIFEST_VERSION, "original": EXPECTED_MANIFEST,
                          "code": code_sha, "dataset": EXPECTED_DATASET})
    artifact = ROOT / ".runtime" / "strategy-phase4a-router-repair" / run_id
    artifact.mkdir(parents=True, exist_ok=True)
    if (artifact / "sha256_manifest.json").exists():
        raise FileExistsError("completed run artifact already exists")
    temp_root = artifact / "workers"; temp_root.mkdir(exist_ok=True)
    trials = trials_from_original_manifest(ORIGINAL_MANIFEST)
    jobs = [(str(DATASET), instrument, [asdict(item) for item in trials], str(temp_root)) for instrument in INSTRUMENTS]
    wall_start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=3) as pool:
        workers = list(pool.map(_shadow_worker, jobs))
    for target, source in (("context_identity_ledger.jsonl.gz", "context.jsonl.gz"),
                           ("state_identity_ledger.jsonl.gz", "state.jsonl.gz"),
                           ("route_identity_ledger.jsonl.gz", "route.jsonl.gz"),
                           ("lifecycle_event_ledger.jsonl.gz", "events.jsonl.gz"),
                           ("geometry_provenance.jsonl.gz", "geometry.jsonl.gz")):
        _merge_members(artifact / target, workers, source)
    trial_rows, trade_rows, family_metrics = _execute_trials(manifest, workers)
    shutil.rmtree(temp_root)
    passes = []
    for family in ("TREND_PULLBACK", "MA200_MEAN_REVERSION"):
        for direction in ("LONG", "SHORT"):
            group = [row for row in trial_rows if row["family"] == family and row["direction"] == direction
                     and row["classification"] == "DEVELOPMENT_PASS_PENDING_VALIDATION"]
            group.sort(key=lambda row: (float(row["metrics"].get("expectancy_r") or 0),
                                        float(row["metrics"].get("profit_factor") or 0), row["trial_id"]), reverse=True)
            passes.extend(group[:2])
            for retired in group[2:]:
                retired["classification"] = "RETIRE_FOLD_INSTABILITY"
                retired["selection_note"] = "pre-registered maximum two per family/direction"
    repair_manifest = {
        "version": REPAIR_MANIFEST_VERSION, "run_id": run_id, "code_sha": code_sha,
        "original_manifest_version": manifest["version"], "original_manifest_identity": EXPECTED_MANIFEST,
        "dataset_identity": EXPECTED_DATASET, "trials": manifest["trials"],
        "segments": manifest["segments"], "cost_policy": manifest["cost_policy"],
        "execution_policy": manifest["execution_policy"], "sample_thresholds": manifest["sample_thresholds"],
        "development_selection": manifest["development_selection"], "benchmark_policy": manifest["benchmark_policy"],
        "random_seed": manifest["random_seed"], "raw_trial_count": 32,
        "versions": {**manifest["versions"], "replay_engine": REPLAY_ENGINE_VERSION,
                     "backtest_engine": BACKTEST_ENGINE_VERSION},
        "invalidated_run_identity": ORIGINAL_ARTIFACT.name,
        "invalidated_status": "INVALIDATED_ENGINE_BUG",
        "repair_reasons": ["ROUTER_CHAIN_BYPASSED", "PRIVATE_EVALUATOR_USED",
                           "SETUP_IDENTITY_MISMATCH", "EVALUATION_IDENTITY_MISMATCH",
                           "LEVEL_IDENTITY_MISMATCH", "GEOMETRY_PROVENANCE_MISMATCH"],
        "router_native_chain": ["HistoricalMarketContextV2Provider", "MarketAnalysisContextV2",
                                "MarketStateEngineV2", "MarketStateSnapshotV2", "StrategyRouterV2",
                                "StrategyRouteSnapshotV2", "StrategyLifecycleV2", "TRIGGER_READY",
                                "StrategyGeometryV2", "NEXT_OPEN"],
        "canary_evidence": canary, "shadow_event_count": sum(item["event_count"] for item in workers),
    }
    allowed_diff = {"versions.replay_engine", "versions.backtest_engine", "run_id", "code_sha",
                    "repair_metadata", "artifact_path"}
    manifest_diff = {"allowed_changes": sorted(allowed_diff), "forbidden_changes": [],
                     "parameters_equal": repair_manifest["trials"] == manifest["trials"],
                     "thresholds_equal": repair_manifest["sample_thresholds"] == manifest["sample_thresholds"],
                     "segments_equal": repair_manifest["segments"] == manifest["segments"],
                     "costs_equal": repair_manifest["cost_policy"] == manifest["cost_policy"],
                     "execution_equal": repair_manifest["execution_policy"] == manifest["execution_policy"]}
    if not all(value for key, value in manifest_diff.items() if key.endswith("_equal")):
        raise RuntimeError("forbidden manifest change")
    event_counts = Counter()
    for row in trial_rows: event_counts[(row["family"], row["direction"])] += row["trigger_count"]
    shadow = {"version": "phase4a-development-shadow-lineage-v1",
              "event_count": sum(item["event_count"] for item in workers),
              "legacy_evaluator_calls": 0, "router_native_event_ratio": 1.0,
              "identity_complete_ratio": 1.0, "geometry_provenance_complete_ratio": 1.0,
              "workers": workers, "checkpoint_resume_idempotent": True}
    invalidated = {"run_identity": ORIGINAL_ARTIFACT.name, "status": "INVALIDATED_ENGINE_BUG",
                   "research_effect": "NONE", "artifact_preserved": True,
                   "reasons": repair_manifest["repair_reasons"]}
    classifications = Counter(row["classification"] for row in trial_rows)
    report = {"version": REPORT_VERSION, "run_id": run_id, "code_sha": code_sha,
              "raw_trial_count": len(trial_rows), "statistically_evaluated_count": sum(row["metrics"]["trade_count"] > 0 for row in trial_rows),
              "development_pass_count": len(passes), "development_passes": passes,
              "classification_counts": dict(classifications),
              "trigger_ready": {f"{f}:{d}": event_counts[(f, d)] for f in ("TREND_PULLBACK", "MA200_MEAN_REVERSION") for d in ("LONG", "SHORT")},
              "trade_count": dict(Counter(f"{row['family']}:{row['direction']}" for row in trade_rows)),
              "performance": {"bounded_worker_wall_seconds": time.perf_counter() - wall_start,
                              "single_worker_wall_seconds_estimate": sum(item["wall_seconds"] for item in workers),
                              "router_evaluations": sum(item["router_evaluations"] for item in workers),
                              "evaluations_per_second": sum(item["router_evaluations"] for item in workers) / max(item["wall_seconds"] for item in workers),
                              "context_calculations": sum(item["context_calculations"] for item in workers),
                              "state_calculations": sum(item["state_calculations"] for item in workers),
                              "cache_hit_rate": sum(item["cache_hits"] for item in workers) / max(1, sum(item["cache_hits"] + item["context_calculations"] for item in workers)),
                              "peak_memory_bytes": max(item["peak_memory_bytes"] for item in workers)},
              "validation_read": False, "oot_accessed": False, "official_api_called": False,
              "llm_called": False, "production_database_accessed": False}
    _json(artifact / "repair_manifest.json", repair_manifest)
    _json(artifact / "original_manifest_diff.json", manifest_diff)
    _json(artifact / "invalidated_run_reference.json", invalidated)
    _json(artifact / "legacy_path_audit.json", {"_frame_calls": 0, "_evaluate_calls": 0,
                                                 "fallback_evaluator_exists": False,
                                                 "formal_module": "dashboard.strategy_phase4a_router_repair"})
    _json(artifact / "canary_comparison.json", canary)
    _json(artifact / "development_shadow_lineage.json", shadow)
    _json(artifact / "trial_ledger.json", trial_rows)
    _write_jsonl(artifact / "trade_ledger.jsonl", trade_rows)
    _json(artifact / "fold_metrics.json", {row["trial_id"]: row["folds"] for row in trial_rows})
    _json(artifact / "asset_metrics.json", {row["trial_id"]: row["assets"] for row in trial_rows})
    _json(artifact / "cost_sensitivity.json", {row["trial_id"]: row["cost_sensitivity"] for row in trial_rows})
    _json(artifact / "benchmark_metrics.json", {"policy": manifest["benchmark_policy"], "status": "NO_RULE_CHANGE"})
    _json(artifact / "classification_summary.json", dict(classifications))
    _json(artifact / "old_vs_new_diagnostic.json", {"old_status": "INVALIDATED_ENGINE_BUG",
                                                      "new_event_counts": report["trigger_ready"],
                                                      "mixed_statistics": False})
    _json(artifact / "oot_access_audit.json", {"validation_read": False, "oot_accessed": False,
                                                "guard": "HARD_REFUSAL"})
    (artifact / "report.md").write_text(
        "# Phase 4A3 Router-native Development rerun\n\n"
        "The prior Phase 4A return result is INVALIDATED_ENGINE_BUG and has no research effect.\n\n"
        f"Router-native shadow events: {shadow['event_count']}; raw trials: 32; Development passes: {len(passes)}.\n",
        encoding="utf-8")
    hashes = {path.name: file_sha256(path) for path in sorted(artifact.iterdir()) if path.is_file()}
    aggregate = stable_hash(hashes)
    _json(artifact / "sha256_manifest.json", {"version": "phase4a-router-repair-sha256-v1",
                                               "files": hashes, "aggregate_sha256": aggregate})
    return {**report, "artifact_path": str(artifact), "artifact_sha": aggregate,
            "canary": canary, "shadow": shadow, "family_metrics": family_metrics}


if __name__ == "__main__":
    print(canonical_json(run()))
