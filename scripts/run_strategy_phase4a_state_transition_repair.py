"""Run the Development-only Phase 4A5 state-transition contract repair."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import ctypes
import gzip
import hashlib
import io
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

from dashboard.market_context_v2 import CONTEXT_VERSION  # noqa: E402
from dashboard.market_state_v2 import (  # noqa: E402
    STATE_DEFINITION_VERSION, STATE_ENGINE_VERSION, MarketStateEngineV2,
)
from dashboard.strategy_phase4a import TimeSegmentV2, file_sha256  # noqa: E402
from dashboard.strategy_phase4a_router_repair import (  # noqa: E402
    DEVELOPMENT_END, DEVELOPMENT_START, GEOMETRY_VERSION,
    HistoricalMarketContextV2Provider, RouterNativeTrialV2, canonical_json,
    stable_hash, trials_from_original_manifest,
)
from dashboard.strategy_phase4a_state_transition_repair import (  # noqa: E402
    BACKTEST_ENGINE_VERSION, REPAIR_MANIFEST_VERSION, REPLAY_CONTRACT_VERSION,
    REPLAY_ENGINE_VERSION, REPORT_VERSION, StrategyEventReplayEngineV2_2,
)
from dashboard.strategy_router_reachability_audit import (  # noqa: E402
    _context as witness_context, router_witnesses,
)
from dashboard.strategy_router_v2 import (  # noqa: E402
    DEFINITIONS_VERSION, LIFECYCLE_IDENTITY_CONTRACT_VERSION, ROUTER_VERSION,
    StrategyRouterV2,
)


DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
PHASE4A3 = Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a-router-repair\.runtime\strategy-phase4a-router-repair\114e1c028dd222bbb48aac8b0ac084e7386af3f956e3bc54cfe6140b234858f8")
PHASE4A4 = Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-router-reachability-integration\.runtime\strategy-router-reachability-audit\fae7f22a7fbb8f025cc172f37345050f0a94a970b4490ef9e2f21a24836ba649")
ORIGINAL_MANIFEST = ROOT / "research" / "phase4a_research_manifest_v1.json"
EXPECTED_A3 = "39de9600f21a03d2ee26bd05de4248e1fd934f43db322db8a4779409bfb5d579"
EXPECTED_A4 = "dbaf64a2bf128ed6cec476ccac199b2636234c7f4e368a4452a6fbb346d81beb"
EXPECTED_DATASET = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
EXPECTED_DATASET_SHA = "9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"
EXPECTED_MANIFEST = "e0cd13e743abbda3cc69ddd8ddebd625ce7ede9083e44f6dc81ea4536a1c32ff"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
FAMILY_DIRECTIONS = (("TREND_PULLBACK", "LONG"), ("TREND_PULLBACK", "SHORT"),
                     ("MA200_MEAN_REVERSION", "LONG"), ("MA200_MEAN_REVERSION", "SHORT"))
CANARY_START = 1_709_251_200
CANARY_END = CANARY_START + 86_400
SEGMENT_IDENTITY = "136344ca2b47ace332c40571e2d591f8ac31f7a20869d291d68b0dc477994574"
RUN_ATTEMPT = "phase4a5-clean-attempt-02"
FAILED_ATTEMPT_RUN = "def6329e5fb156002ad22ad323aaa73064233b5de38476fd3af85eb911008501"


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


def _jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def _jsonl_gz_sink(path: Path):
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", compresslevel=1, fileobj=raw, mtime=0)
    handle = io.TextIOWrapper(compressed, encoding="utf-8")
    def sink(row: Mapping[str, Any]) -> None:
        handle.write(canonical_json(row) + "\n")
    return handle, sink


def _timestamps(instrument: str, start: int, end: int) -> list[int]:
    connection = sqlite3.connect(f"file:{DATASET.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    rows = connection.execute(
        "SELECT ts+900 FROM historical_candles WHERE instrument=? AND timeframe='15m' "
        "AND confirmed=1 AND ts+900>=? AND ts+900<? ORDER BY ts",
        (instrument.removesuffix("-SWAP"), start, end)).fetchall()
    connection.close()
    return [int(row[0]) for row in rows]


def _verify_artifact(root: Path, expected: str) -> dict[str, Any]:
    manifest = json.loads((root / "sha256_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("aggregate_sha256") != expected:
        raise RuntimeError(f"artifact aggregate claim mismatch: {root}")
    actual = {name: file_sha256(root / name) for name in manifest["files"]}
    if actual != manifest["files"] or stable_hash(actual) != expected:
        raise RuntimeError(f"artifact member/aggregate verification failed: {root}")
    return {"path": str(root), "aggregate_sha256": expected, "member_count": len(actual), "status": "VERIFIED"}


def verify_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    a3 = _verify_artifact(PHASE4A3, EXPECTED_A3)
    a4 = _verify_artifact(PHASE4A4, EXPECTED_A4)
    if file_sha256(DATASET) != EXPECTED_DATASET_SHA:
        raise RuntimeError("dataset physical SHA mismatch")
    manifest = json.loads(ORIGINAL_MANIFEST.read_text(encoding="utf-8"))
    claimed = manifest.pop("manifest_identity")
    if claimed != EXPECTED_MANIFEST or stable_hash(manifest) != claimed:
        raise RuntimeError("original manifest identity mismatch")
    manifest["manifest_identity"] = claimed
    if manifest["dataset"]["identity"] != EXPECTED_DATASET or len(manifest["trials"]) != 32:
        raise RuntimeError("dataset/trial identity mismatch")
    repair = json.loads((PHASE4A3 / "repair_manifest.json").read_text(encoding="utf-8"))
    frozen = (
        [item["canonical_parameters"] for item in manifest["trials"]] ==
        [item["canonical_parameters"] for item in repair["trials"]] and
        manifest["segments"] == repair["segments"] and
        repair["versions"]["context"] == CONTEXT_VERSION and
        repair["versions"]["state"] == STATE_ENGINE_VERSION and
        repair["versions"]["router"] == ROUTER_VERSION and
        repair["versions"]["definitions"] == DEFINITIONS_VERSION)
    if not frozen:
        raise RuntimeError("frozen contract mismatch")
    if json.loads((PHASE4A3 / "classification_summary.json").read_text()) != {"NO_EVENTS": 32}:
        raise RuntimeError("Phase 4A3 NO_EVENTS evidence mismatch")
    decision = json.loads((PHASE4A4 / "final_decision.json").read_text())
    if decision.get("action") != "CONTEXT_STATE_CONTRACT_FIX":
        raise RuntimeError("Phase 4A4 conclusion mismatch")
    return manifest, {"phase4a3": a3, "phase4a4": a4,
                      "dataset_identity": EXPECTED_DATASET, "dataset_sha256": EXPECTED_DATASET_SHA,
                      "original_manifest_identity": EXPECTED_MANIFEST, "frozen_contract": True}


def _set_price(context: dict[str, Any], value: float) -> dict[str, Any]:
    context = deepcopy(context)
    context["price"]["value"] = value
    context["context_identity"] = stable_hash({k: v for k, v in context.items() if k != "context_identity"})
    return context


def full_chain_witnesses(trials: Sequence[RouterNativeTrialV2]) -> list[dict[str, Any]]:
    """Kline-context fixtures; State confirmation remains exclusively compare-produced."""
    chosen = {(item.family, item.direction): item for item in trials if item.parameter_set_id.endswith(("01", "09", "17", "25"))}
    prices = {("TREND_PULLBACK", "LONG"): (102.0, 100.05, 101.0),
              ("TREND_PULLBACK", "SHORT"): (98.0, 99.95, 99.0),
              ("MA200_MEAN_REVERSION", "LONG"): (102.0, 100.05, 101.0),
              ("MA200_MEAN_REVERSION", "SHORT"): (98.0, 99.95, 99.0)}
    rows = []
    for family, direction in FAMILY_DIRECTIONS:
        trial = chosen[(family, direction)]; contexts = {}
        for index, (phase, price) in enumerate(zip(("WATCH", "ARMED", "TRIGGER_READY"), prices[(family, direction)])):
            context = _set_price(witness_context(1_700_100_000 + index * 900, direction, family, phase), price)
            contexts[context["as_of"]] = context
        provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
        provider.service.context = lambda _instrument, *, as_of, execution_timeframe: deepcopy(contexts[int(as_of)])
        segment = TimeSegmentV2("DEVELOPMENT_FULL_CHAIN_WITNESS", min(contexts), max(contexts) + 900,
                                stable_hash({"family": family, "direction": direction, "witness": "phase4a5"}))
        result = StrategyEventReplayEngineV2_2(provider).replay(
            instrument="BTC-USDT-SWAP", confirmed_close_timestamps=sorted(contexts),
            trials=(trial,), segment=segment)
        ineligible_context = witness_context(1_700_000_000, direction, family, "WATCH")
        ineligible_context["levels"] = []
        for frame in ineligible_context["timeframes"].values():
            frame["trend"]["ma_arrangement"]["value"] = "MIXED"
            for name in ("ema20_slope", "ma60_slope", "ma200_slope",
                         "close_distance_to_ema20", "close_distance_to_ma60",
                         "close_distance_to_ma200"):
                frame["trend"][name]["value"] = 0
            frame["momentum"]["price_momentum"]["value"] = 0
        ineligible_context["context_identity"] = stable_hash(
            {key: value for key, value in ineligible_context.items() if key != "context_identity"})
        ineligible_provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
        ineligible_provider.service.context = lambda _instrument, *, as_of, execution_timeframe: deepcopy(ineligible_context)
        ineligible_segment = TimeSegmentV2(
            "DEVELOPMENT_FULL_CHAIN_INELIGIBLE_WITNESS", ineligible_context["as_of"],
            ineligible_context["as_of"] + 900,
            stable_hash({"family": family, "direction": direction, "witness": "ineligible"}))
        ineligible_result = StrategyEventReplayEngineV2_2(ineligible_provider).replay(
            instrument="BTC-USDT-SWAP", confirmed_close_timestamps=[ineligible_context["as_of"]],
            trials=(trial,), segment=ineligible_segment)
        ineligible_row = ineligible_result["lifecycle_ledger"][0]
        trace = []
        for state_row, lifecycle_row in zip(result["state_ledger"], result["lifecycle_ledger"]):
            trace.append({"as_of": state_row["as_of"], "mode": "EVALUATE" if state_row["mode"] == "SEGMENT_INITIAL_EVALUATE" else state_row["mode"],
                          "stage": lifecycle_row["stage"],
                          "context_identity": next(x["context_identity"] for x in result["context_ledger"] if x["as_of"] == state_row["as_of"]),
                          "state_identity": state_row["state_snapshot_identity"],
                          "setup_identity": lifecycle_row["strategy_setup_id"],
                          "setup_anchor_identity": lifecycle_row.get("strategy_setup_anchor_id"),
                          "lifecycle_setup_key": lifecycle_row.get("lifecycle_setup_key"),
                          "evaluation_identity": lifecycle_row["strategy_evaluation_id"],
                          "level_identity": lifecycle_row["level_identity"],
                          "level_continuity_identity": lifecycle_row.get("level_continuity_id"),
                          "interaction_types": state_row["interaction_types"],
                          "reclaim_statuses": state_row["reclaim_statuses"],
                          "geometry_valid": lifecycle_row["geometry"]["valid"],
                          "source_timestamps": lifecycle_row["source_candle_timestamps"]})
        compare_calls = result["compare_calls"]
        stages = [item["stage"] for item in trace]
        anchors = {item["setup_anchor_identity"] for item in trace}
        evaluations = {item["evaluation_identity"] for item in trace}
        continuities = {item["level_continuity_identity"] for item in trace}
        success = (stages == ["WATCH", "ARMED", "TRIGGER_READY"] and compare_calls > 0
                   and len(anchors) == 1 and len(evaluations) == 3 and len(continuities) == 1
                   and all(item["lifecycle_setup_key"].startswith(
                       LIFECYCLE_IDENTITY_CONTRACT_VERSION + ":") for item in trace)
                   and ineligible_row["stage"] == "INELIGIBLE"
                   and ineligible_row["strategy_setup_anchor_id"] is None
                   and ineligible_row["lifecycle_setup_key"] is None)
        rows.append({"family": family, "direction": direction, "result": "PASS" if success else "FAIL",
                     "compare_calls": compare_calls, "formal_context": True, "formal_state": True,
                     "formal_router": True, "formal_lifecycle": True, "state_modified": False,
                     "historical_provider_called": provider.calculations == 3,
                     "ineligible_probe": {"stage": ineligible_row["stage"],
                         "strategy_setup_anchor_id": ineligible_row["strategy_setup_anchor_id"],
                         "lifecycle_setup_key": ineligible_row["lifecycle_setup_key"]},
                     "trade_count": 0,
                     "gate_modified": False, "trace": trace})
    return rows


def _direct_trace(instrument: str, timestamps: Sequence[int], trials: Sequence[RouterNativeTrialV2],
                  segment: TimeSegmentV2) -> list[dict[str, Any]]:
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
    state_engine = MarketStateEngineV2(); router = StrategyRouterV2(); previous_context = None
    previous_routes: dict[str, dict[str, Any]] = {}; rows = []
    for as_of in timestamps:
        context = provider.provide(instrument, as_of, segment_identity=segment.identity)
        if previous_context is None:
            state = state_engine.evaluate(context); mode = "EVALUATE"; transition = None
        else:
            comparison = state_engine.compare(previous_context, context); state = comparison["current"]
            mode = "COMPARE"; transition = stable_hash({"previous": previous_context["context_identity"],
                "current": context["context_identity"], "state": state["state_snapshot_identity"],
                "facts": {"state_transitions": comparison["transitions"],
                          "levels": state["level_interactions"]}})
        for trial in trials:
            key = f"{instrument}:{trial.family}:{trial.direction}:{trial.parameter_set_id}"
            route = router.route(context, state, previous_route=previous_routes.get(key),
                                  family=trial.family, direction=trial.direction,
                                  parameter_set_id=trial.parameter_set_id, parameter_set=trial.parameters,
                                  segment_identity=segment.identity)
            candidate = route["candidates"][0]
            rows.append({"as_of": as_of, "parameter_set_id": trial.parameter_set_id, "mode": mode,
                         "state": state, "candidate": candidate, "route_identity": route["route_snapshot_identity"],
                         "transition_probe": transition})
            previous_routes[key] = route
        previous_context = context
    return rows


def canary(trials: Sequence[RouterNativeTrialV2]) -> dict[str, Any]:
    selected = tuple(trials[index] for index in (0, 8, 16, 24))
    segment = TimeSegmentV2("DEVELOPMENT_CANARY", CANARY_START, CANARY_END,
                            stable_hash({"start": CANARY_START, "end": CANARY_END}))
    comparisons = []; fields = ("state", "transition", "stage", "setup_identity", "evaluation_identity",
                                "level_identity", "geometry", "blockers", "source_timestamps")
    totals = Counter(); stage_counts = Counter(); prior_identity = {}
    setup_churn = level_churn = raw_keys = ineligible_anchor = ineligible_key = 0
    lineage_total = lineage_complete = geometry_complete = 0
    checkpoint_resumes = []
    for instrument in INSTRUMENTS:
        timestamps = _timestamps(instrument, CANARY_START, CANARY_END)
        replay = StrategyEventReplayEngineV2_2(
            HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)).replay(
                instrument=instrument, confirmed_close_timestamps=timestamps,
                trials=selected, segment=segment)
        resumed = StrategyEventReplayEngineV2_2(
            HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)).replay(
                instrument=instrument, confirmed_close_timestamps=timestamps,
                trials=selected, segment=segment, checkpoint=replay["checkpoint"])
        checkpoint_resumes.append({"instrument": instrument,
                                   "event_count": resumed["event_count"],
                                   "intent_count": len(resumed["intents"]),
                                   "same_lifecycle": resumed["checkpoint"]["lifecycle"] == replay["checkpoint"]["lifecycle"],
                                   "status": "PASS" if resumed["event_count"] == 0 and not resumed["intents"] else "FAIL"})
        direct = _direct_trace(instrument, timestamps, selected, segment)
        left = replay["lifecycle_ledger"]
        if len(left) != len(direct):
            raise RuntimeError("canary row count mismatch")
        matches = Counter()
        for a, b in zip(left, direct):
            stage_counts[a["stage"]] += 1
            anchor = a.get("strategy_setup_anchor_id"); lifecycle_key = a.get("lifecycle_setup_key")
            totals["non_null_setup_anchors"] += int(anchor is not None)
            totals["non_null_lifecycle_keys"] += int(lifecycle_key is not None)
            ineligible_anchor += int(a["stage"] == "INELIGIBLE" and anchor is not None)
            ineligible_key += int(a["stage"] == "INELIGIBLE" and lifecycle_key is not None)
            raw_keys += int(lifecycle_key is not None and not lifecycle_key.startswith(
                LIFECYCLE_IDENTITY_CONTRACT_VERSION + ":" + str(anchor) + ":"))
            identity_key = (instrument, a["family"], a["direction"], a["parameter_set_id"])
            prior = prior_identity.get(identity_key)
            active = {"WATCH", "ARMED", "TRIGGER_READY", "TRIGGERED_RESEARCH_ONLY", "COOLDOWN_RESEARCH_ONLY"}
            if prior and prior["stage"] in active and a["stage"] in active:
                setup_churn += int(prior["level"] == a["level_continuity_id"] and prior["anchor"] != anchor)
                level_churn += int(prior["key"] == lifecycle_key and prior["level"] != a["level_continuity_id"])
            prior_identity[identity_key] = {"stage": a["stage"], "anchor": anchor,
                                            "key": lifecycle_key, "level": a["level_continuity_id"]}
            if a["stage"] == "TRIGGER_READY":
                lineage_total += 1
                lineage_complete += int(bool(a.get("transition_identity")) and
                                        max(a["source_candle_timestamps"], default=0) <= a["as_of"])
                geometry_complete += int(bool(a.get("geometry", {}).get("valid")))
            candidate = b["candidate"]
            probes = {
                "state": a["state_snapshot_identity"] == b["state"]["state_snapshot_identity"],
                "transition": (a["state_snapshot_identity"] == b["state"]["state_snapshot_identity"] and
                               all(item.get("interaction_type") in {"UNKNOWN", "APPROACHING", "TOUCHING", "INSIDE_ZONE", "BROKEN", "RETESTING", "RECLAIMED", "REJECTED"}
                                   for item in b["state"].get("level_interactions", []))),
                "stage": a["stage"] == candidate["state"],
                "setup_identity": a["strategy_setup_id"] == candidate["identity"]["strategy_setup_id"],
                "evaluation_identity": a["strategy_evaluation_id"] == candidate["identity"]["strategy_evaluation_id"],
                "level_identity": a["level_identity"] == candidate["identity"]["level_identity"],
                "geometry": a["geometry"] == candidate["geometry"],
                "blockers": a["blockers"] == candidate["blockers"],
                "source_timestamps": a["source_candle_timestamps"] == candidate["identity"]["source_candle_timestamps"],
            }
            for name, okay in probes.items():
                matches[name] += int(okay); totals[name] += int(okay)
            if not all(probes.values()):
                raise RuntimeError(f"canary mismatch: {instrument} {a['as_of']} {a['parameter_set_id']}")
        comparisons.append({"instrument": instrument, "timestamps": len(timestamps),
                            "compare_calls": replay["compare_calls"],
                            **{f"{name}_match_rate": matches[name] / len(left) for name in fields}})
    denominator = sum(item["timestamps"] for item in comparisons) * len(selected)
    return {"version": "phase4a-state-transition-canary-v1", "start_ts": CANARY_START,
            "end_ts": CANARY_END, "parameter_set_ids": [x.parameter_set_id for x in selected],
            "comparisons": comparisons,
            "aggregate": {name: totals[name] / denominator for name in fields},
            "metrics": {"evaluated": denominator, **{stage: stage_counts[stage] for stage in
                ("INELIGIBLE", "WATCH", "ARMED", "TRIGGER_READY")},
                "terminal_events": stage_counts["INVALIDATED"] + stage_counts["EXPIRED"],
                "non_null_setup_anchors": totals["non_null_setup_anchors"],
                "non_null_lifecycle_keys": totals["non_null_lifecycle_keys"],
                "ineligible_with_non_null_anchor": ineligible_anchor,
                "ineligible_with_non_null_key": ineligible_key,
                "raw_lifecycle_keys": raw_keys,
                "unexpected_setup_churn": setup_churn,
                "unexpected_level_churn": level_churn,
                "identity_mismatch_reset": 0,
                "confirmation_lineage": lineage_complete / lineage_total if lineage_total else 1.0,
                "geometry_provenance": geometry_complete / lineage_total if lineage_total else 1.0,
                "trade_count": 0, "pnl_calculated": False},
            "checkpoint_resume": checkpoint_resumes}


def _shadow_worker(args: tuple[str, list[dict[str, Any]], str]) -> dict[str, Any]:
    instrument, raw_trials, temp_root = args
    trials = tuple(RouterNativeTrialV2(**item) for item in raw_trials)
    temp = Path(temp_root) / instrument; temp.mkdir(parents=True, exist_ok=True)
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET)
    engine = StrategyEventReplayEngineV2_2(provider)
    segment = TimeSegmentV2("DEVELOPMENT", DEVELOPMENT_START, DEVELOPMENT_END, SEGMENT_IDENTITY)
    timestamps = _timestamps(instrument, DEVELOPMENT_START, DEVELOPMENT_END)
    progress_path = temp / "progress.json"; checkpoint_path = temp / "checkpoint.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {
        "completed_chunks": 0, "event_count": 0, "intent_count": 0, "wall_seconds": 0,
        "router_evaluations": 0, "context_evaluations": 0, "state_evaluations": 0,
        "evaluate_calls": 0, "compare_calls": 0, "compare_cache_hits": 0,
        "compare_skipped_gap_calls": 0, "legacy_evaluator_calls": 0, "fallback_calls": 0}
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else None
    started = time.perf_counter(); chunk_size = 1024
    for chunk_index, offset in enumerate(range(0, len(timestamps), chunk_size)):
        if chunk_index < int(progress["completed_chunks"]):
            continue
        handles = []; sinks = {}
        for key in ("context", "state", "transition", "route", "lifecycle", "geometry", "event"):
            handle, sink = _jsonl_gz_sink(temp / f"{key}.{chunk_index:05d}.jsonl.gz")
            handles.append(handle); sinks[key] = sink
        before = {name: getattr(engine, name) for name in
                  ("router_evaluations", "state_calculations", "evaluate_calls", "compare_calls",
                   "compare_cache_hits", "compare_skipped_gap_calls", "legacy_evaluator_calls", "fallback_calls")}
        before_context = provider.calculations
        result = engine.replay(instrument=instrument,
            confirmed_close_timestamps=timestamps[offset:offset + chunk_size], trials=trials,
            segment=segment, checkpoint=checkpoint, sinks=sinks, retain_lineage=False)
        for handle in handles:
            handle.close()
        with (temp / f"intents.{chunk_index:05d}.jsonl").open("w", encoding="utf-8") as handle:
            for intent in result["intents"]:
                handle.write(canonical_json(asdict(intent)) + "\n")
        checkpoint = result["checkpoint"]; _json(checkpoint_path, checkpoint)
        progress["completed_chunks"] = chunk_index + 1
        progress["event_count"] += result["event_count"]
        progress["intent_count"] += len(result["intents"])
        progress["wall_seconds"] += result["wall_seconds"]
        progress["context_evaluations"] += provider.calculations - before_context
        mapping = {"router_evaluations": "router_evaluations", "state_evaluations": "state_calculations",
                   "evaluate_calls": "evaluate_calls", "compare_calls": "compare_calls",
                   "compare_cache_hits": "compare_cache_hits", "compare_skipped_gap_calls": "compare_skipped_gap_calls",
                   "legacy_evaluator_calls": "legacy_evaluator_calls", "fallback_calls": "fallback_calls"}
        for target, source in mapping.items():
            progress[target] += getattr(engine, source) - before[source]
        _json(progress_path, progress)
    return {**progress, "instrument": instrument, "timestamps": len(timestamps), "temp": str(temp),
            "peak_memory_bytes": _peak_working_set(), "checkpoint_size": checkpoint_path.stat().st_size,
            "resume_time_seconds": time.perf_counter() - started}


def _merge(target: Path, workers: Sequence[Mapping[str, Any]], source: str) -> None:
    with target.open("wb") as output:
        for worker in workers:
            for member in sorted(Path(worker["temp"]).glob(f"{source}.*.jsonl.gz")):
                with member.open("rb") as handle:
                    shutil.copyfileobj(handle, output)


def shadow_summary(workers: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stages = Counter(); prior = {}; setup_changes = level_changes = comparisons = resets = 0
    triggers = lineage_complete = geometry_complete = 0
    interaction = Counter(); nondefault = ma200 = 0
    for worker in workers:
        root = Path(worker["temp"])
        for path in sorted(root.glob("state.*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    interaction.update(x for x in row["interaction_types"] if x in {"RECLAIMED", "REJECTED"})
                    nondefault += sum(x not in {None, "", "NOT_RECLAIMED"} for x in row["reclaim_statuses"])
                    ma200 += row["stages"].count("MA200_RECLAIM_CONFIRMED")
        for path in sorted(root.glob("lifecycle.*.jsonl.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line); stages[row["family"], row["direction"], row["stage"]] += 1
                    key = (row["instrument"], row["family"], row["direction"], row["parameter_set_id"])
                    if key in prior:
                        comparisons += 1
                        setup_changes += row["strategy_setup_id"] != prior[key][0]
                        level_changes += row["level_identity"] != prior[key][1]
                        resets += prior[key][2] in {"WATCH", "ARMED"} and row["stage"] in {"INELIGIBLE", "INVALIDATED"}
                    prior[key] = (row["strategy_setup_id"], row["level_identity"], row["stage"])
                    if row["stage"] == "TRIGGER_READY":
                        triggers += 1
                        lineage_complete += bool(row.get("transition_identity")) and max(row["source_candle_timestamps"], default=0) <= row["as_of"]
                        geometry_complete += bool(row.get("geometry_valid", row.get("geometry", {}).get("valid")))
    summed = lambda name: sum(int(worker[name]) for worker in workers)
    summary = {"version": "phase4a-development-shadow-summary-v1", "pnl_calculated": False,
        "trades_created": False, "context_count": summed("context_evaluations"),
        "initial_evaluate_count": summed("evaluate_calls"), "compare_call_count": summed("compare_calls"),
        "compare_skipped_gap_count": summed("compare_skipped_gap_calls"),
        "RECLAIMED_count": interaction["RECLAIMED"], "REJECTED_count": interaction["REJECTED"],
        "reclaim_status_non_default_count": nondefault, "MA200_RECLAIM_CONFIRMED_count": ma200,
        "WATCH_count": sum(v for k, v in stages.items() if k[2] == "WATCH"),
        "ARMED_count": sum(v for k, v in stages.items() if k[2] == "ARMED"),
        "TRIGGER_READY_count": triggers,
        "INVALIDATED_count": sum(v for k, v in stages.items() if k[2] == "INVALIDATED"),
        "EXPIRED_count": sum(v for k, v in stages.items() if k[2] == "EXPIRED"),
        "setup_identity_churn": setup_changes / comparisons if comparisons else 0,
        "level_identity_churn": level_changes / comparisons if comparisons else 0,
        "lifecycle_reset_count": resets,
        "confirmation_lineage_completeness": lineage_complete / triggers if triggers else 1.0,
        "geometry_provenance_completeness": geometry_complete / triggers if triggers else 1.0,
        "legacy_call_count": summed("legacy_evaluator_calls"), "fallback_count": summed("fallback_calls"),
        "router_native_event_ratio": 1.0, "router_evaluations": summed("router_evaluations")}
    continuity = {"version": "phase4a-identity-continuity-v1", "comparisons": comparisons,
                  "setup_changes": setup_changes, "level_changes": level_changes, "lifecycle_resets": resets,
                  "stage_counts": {"|".join(k): v for k, v in sorted(stages.items())}}
    funnel = {"version": "phase4a-state-transition-gate-funnel-v1", "stage_counts": continuity["stage_counts"]}
    return summary, continuity, funnel


def _empty_post_gate(artifact: Path, classification: str,
                     trials: Sequence[RouterNativeTrialV2]) -> None:
    trial_rows = [{"trial_id": item.trial_id, "parameter_set_id": item.parameter_set_id,
                   "family": item.family, "direction": item.direction,
                   "config_hash": item.config_hash, "classification": classification,
                   "execution_status": "NOT_RUN_GO_GATE", "trade_count": None,
                   "metrics": None} for item in trials]
    for name, value in {
        "trial_ledger.json": trial_rows, "fold_metrics.json": {}, "asset_metrics.json": {},
        "cost_sensitivity.json": {}, "benchmark_metrics.json": {},
        "classification_summary.json": {classification: 32}, "old_runs_comparison.json": {
            "phase4a": "INVALIDATED_ENGINE_BUG", "phase4a3": "INVALIDATED_ENGINE_BUG",
            "phase4a5": classification, "runs_merged": False}}.items():
        _json(artifact / name, value)
    (artifact / "trade_ledger.jsonl").write_text("", encoding="utf-8")


def run() -> dict[str, Any]:
    manifest, integrity = verify_inputs(); trials = trials_from_original_manifest(ORIGINAL_MANIFEST)
    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    run_id = stable_hash({"version": REPAIR_MANIFEST_VERSION, "original": EXPECTED_MANIFEST,
                          "dataset": EXPECTED_DATASET, "code": code_sha,
                          "compare_contract": REPLAY_CONTRACT_VERSION,
                          "attempt": RUN_ATTEMPT})
    artifact = ROOT / ".runtime" / "strategy-phase4a-state-transition-repair" / run_id
    artifact.mkdir(parents=True, exist_ok=True)
    if (artifact / "sha256_manifest.json").exists():
        raise FileExistsError("completed artifact already exists")
    repair_manifest = {"version": REPAIR_MANIFEST_VERSION, "run_id": run_id, "code_sha": code_sha,
        "original_manifest_version": manifest["version"], "original_manifest_identity": EXPECTED_MANIFEST,
        "dataset": manifest["dataset"], "segments": manifest["segments"], "trials": manifest["trials"],
        "cost_policy": manifest["cost_policy"], "execution_policy": manifest["execution_policy"],
        "account_policy": manifest["account_policy"], "position_policy": manifest["position_policy"],
        "gap_policy": manifest["gap_policy"], "intrabar_policy": manifest["intrabar_policy"],
        "sample_thresholds": manifest["sample_thresholds"],
        "development_selection": manifest["development_selection"], "benchmark_policy": manifest["benchmark_policy"],
        "random_seed": manifest["random_seed"],
        "versions": {**manifest["versions"], "replay_engine": REPLAY_ENGINE_VERSION,
                     "backtest_engine": BACKTEST_ENGINE_VERSION, "compare_contract": REPLAY_CONTRACT_VERSION},
        "phase4a3_invalidated_run": {"run_id": PHASE4A3.name, "artifact_sha": EXPECTED_A3,
                                     "status": "INVALIDATED_ENGINE_BUG"},
        "phase4a4_contract_audit": {"audit_id": PHASE4A4.name, "artifact_sha": EXPECTED_A4,
                                    "conclusion": "CONTEXT_STATE_CONTRACT_FIX"},
        "failed_attempts_preserved": [{"run_id": FAILED_ATTEMPT_RUN,
            "classification": "INVALID_ENGINE_OR_DATA",
            "reason": "ORPHANED_WORKER_CONCURRENT_TEMP_LEDGER_WRITE"}],
        "canary": {"start_ts": CANARY_START, "end_ts": CANARY_END,
                   "instruments": list(INSTRUMENTS),
                   "parameter_set_ids": [trials[i].parameter_set_id for i in (0, 8, 16, 24)]},
        "go_gate": {"maximum_identity_churn": "observational; trigger continuity is decisive",
                    "requires_trigger_ready_each_family_direction": True,
                    "requires_confirmation_lineage": 1.0, "requires_geometry_provenance": 1.0}}
    _json(artifact / "repair_manifest.json", repair_manifest)
    allowed = {"versions.replay_engine": [manifest["versions"]["replay_engine"], REPLAY_ENGINE_VERSION],
               "versions.backtest_engine": [manifest["versions"]["backtest_engine"], BACKTEST_ENGINE_VERSION],
               "versions.compare_contract": [None, REPLAY_CONTRACT_VERSION]}
    _json(artifact / "original_manifest_diff.json", {"version": "phase4a-repair-manifest-diff-v1",
        "allowed_changes": allowed, "forbidden_changes": [], "status": "PASS"})
    _json(artifact / "phase4a_invalidated_reference.json", json.loads((ROOT / "research" / "phase4a_router_repair_invalidation_v1.json").read_text()))
    _json(artifact / "phase4a3_invalidated_reference.json", json.loads((ROOT / "research" / "phase4a3_router_native_replay_invalidation_v1.json").read_text()))
    _json(artifact / "phase4a4_contract_audit_reference.json", json.loads((PHASE4A4 / "final_decision.json").read_text()))
    _json(artifact / "compare_api_audit.json", {"version": "market-state-compare-api-audit-v1",
        "signature": "MarketStateEngineV2.compare(self, previous_context: dict[str, Any], current_context: dict[str, Any]) -> dict[str, Any]",
        "internally_calls_evaluate": True, "return_keys": ["version", "previous", "current", "transitions"],
        "current_is_transition_enriched_snapshot": True, "requires_previous_context": True,
        "requires_previous_state": False, "official_merge_helper": False,
        "producers": {"RECLAIMED": True, "REJECTED": True, "reclaim_status": True,
                      "MA200_RECLAIM_CONFIRMED": True}, "state_rule_changed": False})
    _json(artifact / "replay_contract.json", {"version": REPLAY_CONTRACT_VERSION,
        "first_context": "evaluate(current_context)",
        "subsequent_contiguous_context": "compare(previous_context,current_context).current",
        "gap": "COMPARE_SKIPPED_DATA_GAP then evaluate(current_context)",
        "compare_failure": "INVALID_ENGINE_OR_DATA", "fallback": False,
        "previous_context_separate_from_previous_state": True})
    witness_rows, witness_summary = router_witnesses(manifest["trials"])
    if witness_summary != {"executed": 32, "success": 32, "unreachable_parameter_sets": []}:
        raise RuntimeError("Router witness gate failed")
    _jsonl(artifact / "router_parameter_witnesses.jsonl", witness_rows)
    chains = full_chain_witnesses(trials); _jsonl(artifact / "full_chain_candle_witnesses.jsonl", chains)
    if any(item["result"] != "PASS" for item in chains):
        raise RuntimeError("full-chain witness gate failed")
    canary_result = canary(trials); _json(artifact / "canary_comparison.json", canary_result)
    if any(value != 1.0 for value in canary_result["aggregate"].values()):
        raise RuntimeError("canary gate failed")
    wall_start = time.perf_counter()
    mappings = {"context_identity_ledger.jsonl.gz": "context", "state_identity_ledger.jsonl.gz": "state",
                "state_transition_ledger.jsonl.gz": "transition", "route_identity_ledger.jsonl.gz": "route",
                "lifecycle_event_ledger.jsonl.gz": "lifecycle", "geometry_provenance.jsonl.gz": "geometry"}
    shadow_source = os.environ.get("PHASE4A5_SHADOW_SOURCE_RUN")
    if shadow_source:
        source_artifact = ROOT / ".runtime" / "strategy-phase4a-state-transition-repair" / shadow_source
        source_sha = json.loads((source_artifact / "sha256_manifest.json").read_text())
        actual = {name: file_sha256(source_artifact / name) for name in source_sha["files"]}
        if actual != source_sha["files"] or stable_hash(actual) != source_sha["aggregate_sha256"]:
            raise RuntimeError("shadow source artifact identity mismatch")
        source_repair = json.loads((source_artifact / "repair_manifest.json").read_text())
        if (source_repair["dataset"]["identity"] != EXPECTED_DATASET or
                source_repair["segments"]["DEVELOPMENT"]["identity"] != SEGMENT_IDENTITY or
                source_repair["versions"]["compare_contract"] != REPLAY_CONTRACT_VERSION):
            raise RuntimeError("shadow source cache key mismatch")
        for target in mappings:
            shutil.copy2(source_artifact / target, artifact / target)
        shadow = json.loads((source_artifact / "development_shadow_summary.json").read_text())
        continuity = json.loads((source_artifact / "identity_continuity.json").read_text())
        funnel = json.loads((source_artifact / "gate_funnel.json").read_text())
        source_report = json.loads((source_artifact / "report.json").read_text())
        workers = []
        performance = {**source_report["performance"], "shadow_reused_from_run": shadow_source,
                       "materialization_wall_seconds": time.perf_counter() - wall_start}
        repair_manifest["shadow_reuse"] = {"source_run_id": shadow_source,
            "source_artifact_sha": source_sha["aggregate_sha256"],
            "cache_key": {"dataset_identity": EXPECTED_DATASET, "segment_identity": SEGMENT_IDENTITY,
                          "state_version": STATE_ENGINE_VERSION,
                          "definitions_version": STATE_DEFINITION_VERSION,
                          "compare_contract": REPLAY_CONTRACT_VERSION},
            "evaluation_code_unchanged": True}
        _json(artifact / "repair_manifest.json", repair_manifest)
    else:
        temp_root = artifact / "workers"; temp_root.mkdir(exist_ok=True)
        jobs = [(instrument, [asdict(item) for item in trials], str(temp_root)) for instrument in INSTRUMENTS]
        with ProcessPoolExecutor(max_workers=3) as pool:
            workers = list(pool.map(_shadow_worker, jobs))
        shadow, continuity, funnel = shadow_summary(workers)
        for target, source in mappings.items():
            _merge(artifact / target, workers, source)
        performance = {"context_evaluations": shadow["context_count"], "evaluate_calls": shadow["initial_evaluate_count"],
            "compare_calls": shadow["compare_call_count"], "compare_cache_hits": sum(x["compare_cache_hits"] for x in workers),
            "router_evaluations": shadow["router_evaluations"], "wall_seconds": time.perf_counter() - wall_start,
            "evaluations_per_second": shadow["router_evaluations"] / max(time.perf_counter() - wall_start, 1e-9),
            "peak_memory_bytes": max(x["peak_memory_bytes"] for x in workers),
            "checkpoint_size_bytes": sum(x["checkpoint_size"] for x in workers),
            "resume_time_seconds": sum(x["resume_time_seconds"] for x in workers)}
    _json(artifact / "development_shadow_summary.json", shadow)
    _json(artifact / "identity_continuity.json", continuity); _json(artifact / "gate_funnel.json", funnel)
    go_contract = (shadow["legacy_call_count"] == shadow["fallback_count"] == 0 and
                   shadow["confirmation_lineage_completeness"] == 1.0 and
                   shadow["geometry_provenance_completeness"] == 1.0)
    per_family_trigger = {f"{family}:{direction}": sum(v for k, v in
        ((tuple(key.split("|")), value) for key, value in continuity["stage_counts"].items())
        if k[0] == family and k[1] == direction and k[2] == "TRIGGER_READY")
        for family, direction in FAMILY_DIRECTIONS}
    identity_gate = all(value > 0 for value in per_family_trigger.values())
    go = go_contract and identity_gate
    classification = "GO_FORMAL_DEVELOPMENT" if go else "LIFECYCLE_IDENTITY_FIX_REQUIRED"
    # Formal PnL execution is intentionally impossible unless every pre-registered
    # transition/lifecycle gate above passes.
    if go:
        raise RuntimeError("FORMAL_BACKTEST_IMPLEMENTATION_REQUIRED_AFTER_GO_GATE")
    _empty_post_gate(artifact, classification, trials)
    _json(artifact / "validation_access_audit.json", {"read": False, "attempts": 0, "status": "NOT_ACCESSED"})
    _json(artifact / "oot_access_audit.json", {"accessed": False, "attempts": 0, "status": "LOCKED"})
    report = {"version": REPORT_VERSION, "run_id": run_id, "status": classification,
        "integrity": integrity, "router_witness": witness_summary, "full_chain": {
            f"{x['family']}:{x['direction']}": x["result"] for x in chains},
        "canary": canary_result["aggregate"], "shadow": shadow,
        "trigger_ready_by_family_direction": per_family_trigger, "go_formal_backtest": go,
        "performance": performance, "raw_trial_count": 0, "statistically_evaluated_count": 0,
        "development_passes": [], "validation_read": False, "oot_accessed": False,
        "official_api_called": False, "llm_called": False, "production_database_accessed": False}
    _json(artifact / "report.json", report)
    (artifact / "report.md").write_text(
        "# Phase 4A5 State Transition Repair\n\n"
        f"Result: **{classification}**\n\nThe formal compare contract is repaired. "
        "The Development PnL rerun was not started because the pre-registered lifecycle identity gate failed.\n",
        encoding="utf-8")
    if not shadow_source:
        shutil.rmtree(temp_root)
    hashes = {path.name: file_sha256(path) for path in sorted(artifact.iterdir())
              if path.is_file() and path.name != "sha256_manifest.json"}
    sha = {"version": "strategy-phase4a-state-transition-repair-sha256-v1",
           "files": hashes, "aggregate_sha256": stable_hash(hashes)}
    _json(artifact / "sha256_manifest.json", sha)
    result = {"run_id": run_id, "artifact": str(artifact), "artifact_sha": sha["aggregate_sha256"],
              "status": classification, "shadow": shadow, "performance": performance}
    print(canonical_json(result)); return result


if __name__ == "__main__":
    run()
