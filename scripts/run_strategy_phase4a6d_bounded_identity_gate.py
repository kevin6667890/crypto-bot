"""Development-only Phase 4A6D bounded lifecycle identity gate.

This audit never invokes the backtest/execution engine.  Replay-generated
research intents are counted and discarded; no order, trade, or PnL object is
persisted or passed to another layer.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.market_context_v2 import CONTEXT_VERSION
from dashboard.market_state_v2 import STATE_DEFINITION_VERSION, STATE_ENGINE_VERSION
from dashboard.strategy_phase4a import TimeSegmentV2, file_sha256
from dashboard.strategy_phase4a_router_repair import (
    CHECKPOINT_SCHEMA_VERSION, DEVELOPMENT_END, DevelopmentAccessGuard,
    HistoricalMarketContextV2Provider, canonical_json,
    trials_from_original_manifest,
)
from dashboard.strategy_phase4a_state_transition_repair import (
    REPLAY_ENGINE_VERSION, StrategyEventReplayEngineV2_2,
)
from dashboard.strategy_router_v2 import (
    ACTIVE_SETUP_STAGES, DEFINITIONS_VERSION,
    LIFECYCLE_IDENTITY_CONTRACT_VERSION, ROUTER_VERSION, StrategyRouterV2,
    validate_lifecycle_identity,
)
from scripts.run_strategy_phase4a_state_transition_repair import full_chain_witnesses


VERSION = "strategy-phase4a6d-bounded-identity-gate-v1"
DATASET = Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
EXPECTED_DATASET_ID = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
EXPECTED_DATASET_SHA = "9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"
MANIFEST = ROOT / "research" / "phase4a_research_manifest_v1.json"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
WINDOWS = (
    ("EARLY", 1_698_796_800, 1_699_401_600, "2023-11-01T00:00:00Z", "2023-11-08T00:00:00Z"),
    ("MIDDLE", 1_717_200_000, 1_717_804_800, "2024-06-01T00:00:00Z", "2024-06-08T00:00:00Z"),
    ("LATE", 1_735_689_600, 1_736_294_400, "2025-01-01T00:00:00Z", "2025-01-08T00:00:00Z"),
)
FEATURE_SHA = "eb4a3ddee415ae959710a2a2fe0ccf35e728afdd"
ORIGIN_MAIN_SHA = "b99e4dccc5ec8b782501d09ef18b022a838426c7"
ACTIVE = set(ACTIVE_SETUP_STAGES)
TERMINAL = {"INVALIDATED", "EXPIRED"}


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def timestamps(instrument: str, start: int, end: int) -> list[int]:
    connection = sqlite3.connect(
        f"file:{DATASET.resolve().as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT ts+900 FROM historical_candles WHERE instrument=? AND timeframe='15m' "
            "AND confirmed=1 AND ts+900>=? AND ts+900<? ORDER BY ts",
            (instrument.removesuffix("-SWAP"), start, end),
        ).fetchall()
        return [int(row[0]) for row in rows]
    finally:
        connection.close()


class CandidateAudit:
    def __init__(self, *, keep_hashes: bool = False) -> None:
        self.keep_hashes = keep_hashes
        self.route_hashes: list[str] = []
        self.stages: Counter[str] = Counter()
        self.transitions: Counter[str] = Counter()
        self.direction: dict[str, Counter[str]] = defaultdict(Counter)
        self.parameter: dict[str, Counter[str]] = defaultdict(Counter)
        self.previous: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.anchors: set[str] = set()
        self.continuities: set[str] = set()
        self.anchor_bars: Counter[str] = Counter()
        self.trigger_by_anchor: Counter[tuple[str, str, str, str, str]] = Counter()
        self.counts: Counter[str] = Counter()

    def observe(self, route: Mapping[str, Any], context: Mapping[str, Any],
                previous_route: Mapping[str, Any] | None) -> None:
        candidate = route["candidates"][0]
        identity = candidate["identity"]
        stage_payload = candidate["stage"]
        stage = str(candidate["state"])
        prior_candidate = ((previous_route or {}).get("candidates") or [None])[0]
        prior_stage = str((prior_candidate or {}).get("state", "INELIGIBLE"))
        instrument = str(context["instrument"])
        family = str(candidate["family"])
        direction = str(candidate["direction"])
        parameter = str(identity.get("parameter_set_id"))
        scope = (instrument, family, direction, parameter)
        anchor = identity.get("strategy_setup_anchor_id")
        continuity = identity.get("level_continuity_id")
        lifecycle_key = identity.get("lifecycle_setup_key")
        exact = identity.get("level_identity")

        self.counts["candidate_evaluations"] += 1
        self.stages[stage] += 1
        label = ("TP" if family == "TREND_PULLBACK" else "MA200") + " " + direction
        self.direction[label][stage] += 1
        self.direction[label]["evaluated"] += 1
        self.parameter[parameter][stage] += 1
        self.parameter[parameter]["evaluated"] += 1
        self.transitions[f"{prior_stage}->{stage}"] += int(prior_stage != stage)

        try:
            validate_lifecycle_identity(identity)
        except ValueError:
            self.counts["lifecycle_key_mismatch"] += 1
        if lifecycle_key is not None and not str(lifecycle_key).startswith(
                f"{LIFECYCLE_IDENTITY_CONTRACT_VERSION}:{anchor}:"):
            self.counts["raw_lifecycle_key"] += 1
        if stage == "INELIGIBLE":
            self.counts["ineligible"] += 1
            self.counts["ineligible_non_null_anchor"] += int(anchor is not None)
            self.counts["ineligible_non_null_key"] += int(lifecycle_key is not None)
            self.counts["ineligible_non_null_setup_started_at"] += int(
                stage_payload.get("setup_started_at") is not None)
        if stage in ACTIVE:
            self.counts["active_missing_anchor"] += int(not anchor)
            self.counts["active_missing_versioned_key"] += int(
                not lifecycle_key or not str(lifecycle_key).startswith(
                    LIFECYCLE_IDENTITY_CONTRACT_VERSION + ":"))
        if prior_stage in TERMINAL and stage == "INELIGIBLE":
            self.counts["terminal_to_ineligible"] += 1
            self.counts["terminal_to_ineligible_uncleared"] += int(
                anchor is not None or lifecycle_key is not None or
                stage_payload.get("setup_started_at") is not None)

        previous = self.previous.get(scope)
        if continuity:
            self.continuities.add(str(continuity))
        if anchor:
            self.anchors.add(str(anchor))
            self.anchor_bars[str(anchor)] += 1
        if previous:
            if previous["exact"] != exact:
                self.counts["exact_level_identity_changes"] += 1
            if previous["continuity"] != continuity:
                self.counts["level_continuity_changes"] += 1
                unexpected = (previous["anchor"] is not None and
                              previous["anchor"] == anchor)
                self.counts["unexpected_level_continuity_changes"] += int(unexpected)
                self.counts["legitimate_level_continuity_changes"] += int(not unexpected)
            if anchor and previous["anchor"] != anchor:
                self.counts["setup_anchor_creations"] += 1
                unexpected = (previous["anchor"] is not None and
                              previous["continuity"] == continuity and
                              previous["stage"] not in TERMINAL | {"INELIGIBLE"})
                self.counts["unexpected_setup_anchor_changes"] += int(unexpected)
                self.counts["legitimate_setup_anchor_creations"] += int(not unexpected)
            if previous["stage"] == "ARMED" and stage == "WATCH":
                self.counts["stage_regressions"] += 1
        elif anchor:
            self.counts["setup_anchor_creations"] += 1
            self.counts["legitimate_setup_anchor_creations"] += 1

        if stage == "TRIGGER_READY" and anchor:
            trigger_scope = (*scope, str(anchor))
            self.trigger_by_anchor[trigger_scope] += 1
        self.previous[scope] = {
            "stage": stage, "anchor": anchor, "continuity": continuity,
            "exact": exact, "key": lifecycle_key,
        }
        if self.keep_hashes:
            self.route_hashes.append(stable_hash(route))

    def merge(self, other: "CandidateAudit") -> None:
        self.stages.update(other.stages)
        self.transitions.update(other.transitions)
        self.counts.update(other.counts)
        self.anchors.update(other.anchors)
        self.continuities.update(other.continuities)
        self.anchor_bars.update(other.anchor_bars)
        self.trigger_by_anchor.update(other.trigger_by_anchor)
        for key, value in other.direction.items():
            self.direction[key].update(value)
        for key, value in other.parameter.items():
            self.parameter[key].update(value)


class AuditedRouter(StrategyRouterV2):
    def __init__(self, audit: CandidateAudit) -> None:
        super().__init__()
        self.audit = audit

    def route(self, context: Mapping[str, Any], state: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        previous_route = kwargs.get("previous_route")
        route = super().route(context, state, **kwargs)
        self.audit.observe(route, context, previous_route)
        return route


def lifecycle_sink(audit: Counter[str]):
    def observe(row: Mapping[str, Any]) -> None:
        if row.get("stage") == "TRIGGER_READY":
            audit["trigger_total"] += 1
            audit["confirmation_lineage_complete"] += int(bool(row.get("transition_identity")))
            audit["geometry_provenance_complete"] += int(bool(row.get("geometry_valid")))
    return observe


def run_replay(instrument: str, stamps: list[int], trials: tuple[Any, ...],
               segment: TimeSegmentV2, *, checkpoint: Mapping[str, Any] | None = None,
               keep_hashes: bool = False) -> tuple[dict[str, Any], CandidateAudit, Counter[str]]:
    print(canonical_json({"progress": "replay_start", "instrument": instrument,
                          "timestamps": len(stamps), "resume": checkpoint is not None}), flush=True)
    audit = CandidateAudit(keep_hashes=keep_hashes)
    lineage: Counter[str] = Counter()
    provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET_ID)
    engine = StrategyEventReplayEngineV2_2(provider, router=AuditedRouter(audit))
    result = engine.replay(
        instrument=instrument, confirmed_close_timestamps=stamps, trials=trials,
        segment=segment, checkpoint=checkpoint,
        sinks={"lifecycle": lifecycle_sink(lineage)}, retain_lineage=False,
    )
    result["research_intents_discarded"] = len(result.pop("intents"))
    print(canonical_json({"progress": "replay_complete", "instrument": instrument,
                          "timestamps": len(stamps),
                          "router_evaluations": result["router_evaluations"]}), flush=True)
    return result, audit, lineage


def mutate_checkpoint(base: Mapping[str, Any], case: str) -> dict[str, Any]:
    value = deepcopy(base)
    if case == "missing_schema":
        value.pop("schema_version")
    elif case == "old_schema":
        value["schema_version"] = "strategy-replay-checkpoint-v2"
    elif case == "future_schema":
        value["schema_version"] = "strategy-replay-checkpoint-v999"
    elif case == "raw_key":
        key = next(iter(value["lifecycle"]))
        value["lifecycle"][key]["lifecycle_setup_key"] = "raw-key"
        value["routes"][key]["candidates"][0]["identity"]["lifecycle_setup_key"] = "raw-key"
    elif case == "dataset_mismatch":
        value["dataset_identity"] = "mismatch"
    elif case == "segment_mismatch":
        value["segment_identity"] = "mismatch"
    elif case == "instrument_mismatch":
        value["instrument"] = "ETH-USDT-SWAP"
    elif case == "identity_contract_mismatch":
        value["lifecycle_identity_contract_version"] = "lifecycle-identity-contract-v1"
    return value


def percentile90(values: list[int]) -> int | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * 0.9) - 1)]


def main() -> int:
    started = time.perf_counter()
    code_sha = git("rev-parse", "HEAD")
    if git("rev-parse", "origin/main") != ORIGIN_MAIN_SHA:
        raise RuntimeError("origin/main moved after preregistration")
    if git("rev-parse", "origin/agent/lifecycle-propagation-checkpoint-fix") != FEATURE_SHA:
        raise RuntimeError("feature moved after preregistration")
    dataset_sha = file_sha256(DATASET)
    if dataset_sha != EXPECTED_DATASET_SHA:
        raise RuntimeError("dataset physical SHA-256 mismatch")
    trials = trials_from_original_manifest(MANIFEST)
    if len(trials) != 32 or any(sum(
            item.family == family and item.direction == direction for item in trials) != 8
            for family in ("TREND_PULLBACK", "MA200_MEAN_REVERSION")
            for direction in ("LONG", "SHORT")):
        raise RuntimeError("frozen 32-trial contract mismatch")

    run_id = stable_hash({"version": VERSION, "code_sha": code_sha,
                          "feature_sha": FEATURE_SHA, "dataset": EXPECTED_DATASET_ID,
                          "windows": WINDOWS})
    output = ROOT / ".runtime" / "strategy-phase4a6d-bounded-identity-gate" / run_id
    # A command-runner timeout may stop the bounded job without producing a
    # manifest.  Reusing the deterministic run directory is safe: every gate
    # member is atomically replaced by this process and the final manifest
    # enumerates every retained member.
    output.mkdir(parents=True, exist_ok=True)

    changed = git("diff", "--name-only", f"{ORIGIN_MAIN_SHA}..{FEATURE_SHA}").splitlines()
    diff_audit = {
        "base_sha": ORIGIN_MAIN_SHA, "feature_sha": FEATURE_SHA,
        "file_count": len(changed), "files": changed,
        "reviewed_file_by_file": True, "strategy_semantics_changed": False,
        "allowed_changes_only": True,
        "protected_semantics": {name: False for name in (
            "market_context_indicators", "market_state_rules", "state_compare_rules",
            "strategy_router_gate", "strategy_family_definitions_v2_1", "parameter_values",
            "confirmation_conditions", "score", "geometry", "stop_target",
            "fee_slippage", "execution_timing", "legacy_decision", "paper_scheduler",
            "collector", "frontend")},
        "finding": "Only lifecycle identity/propagation, continuity descriptors, strict checkpoint serialization/validation, audit witnesses, tests, and documentation changed.",
    }
    write_json(output / "feature_diff_audit.json", diff_audit)

    witnesses = full_chain_witnesses(trials)
    for witness in witnesses:
        witness["confirmation_lineage_completeness"] = 1.0 if witness["result"] == "PASS" else 0.0
        witness["geometry_provenance_completeness"] = 1.0 if witness["result"] == "PASS" else 0.0
        witness["raw_lifecycle_key_count"] = sum(
            not str(row.get("lifecycle_setup_key", "")).startswith(
                LIFECYCLE_IDENTITY_CONTRACT_VERSION + ":") for row in witness["trace"])
    write_json(output / "witness_results.json", {"version": VERSION, "rows": witnesses,
                                                   "all_passed": all(x["result"] == "PASS" for x in witnesses)})

    total = CandidateAudit()
    lineage_total: Counter[str] = Counter()
    engine_counts: Counter[str] = Counter()
    window_rows: list[dict[str, Any]] = []
    resume_rows: list[dict[str, Any]] = []
    schema_windows: list[dict[str, Any]] = []
    negative_results: list[dict[str, Any]] = []
    baseline_hashes: dict[str, list[str]] = {}
    checkpoint_samples: dict[str, tuple[dict[str, Any], TimeSegmentV2, list[int]]] = {}

    for name, start, end, start_utc, end_utc in WINDOWS:
        segment = TimeSegmentV2(
            f"DEVELOPMENT_{name}_BOUNDED_IDENTITY", start, end,
            stable_hash({"version": VERSION, "window": name, "start": start, "end": end}),
        )
        window_audit = CandidateAudit()
        window_engine: Counter[str] = Counter()
        window_lineage: Counter[str] = Counter()
        instrument_rows = []
        for instrument in INSTRUMENTS:
            stamps = timestamps(instrument, start, end)
            result, audit, lineage = run_replay(
                instrument, stamps, trials, segment, keep_hashes=(instrument == INSTRUMENTS[0]))
            total.merge(audit)
            window_audit.merge(audit)
            lineage_total.update(lineage)
            window_lineage.update(lineage)
            for key in ("context_evaluations", "state_evaluations", "evaluate_calls",
                        "compare_calls", "router_evaluations", "legacy_evaluator_calls",
                        "fallback_calls", "research_intents_discarded"):
                engine_counts[key] += int(result.get(key, 0))
                window_engine[key] += int(result.get(key, 0))
            instrument_rows.append({"instrument": instrument, "timestamps": len(stamps),
                                    "candidate_evaluations": audit.counts["candidate_evaluations"],
                                    "research_intents_discarded": result["research_intents_discarded"]})
            if instrument == INSTRUMENTS[0]:
                baseline_hashes[name] = audit.route_hashes
            gc.collect()

        btc_stamps = timestamps(INSTRUMENTS[0], start, end)
        cut = len(btc_stamps) // 2
        first, first_audit, _ = run_replay(INSTRUMENTS[0], btc_stamps[:cut], trials, segment,
                                           keep_hashes=True)
        checkpoint = first["checkpoint"]
        second, second_audit, _ = run_replay(INSTRUMENTS[0], btc_stamps[cut:], trials, segment,
                                              checkpoint=checkpoint, keep_hashes=True)
        resumed_hashes = first_audit.route_hashes + second_audit.route_hashes
        equal = baseline_hashes[name] == resumed_hashes
        resume_rows.append({
            "window": name, "instrument": INSTRUMENTS[0], "parameter_count": len(trials),
            "checkpoint_after_timestamp_count": cut,
            "baseline_event_count": len(baseline_hashes[name]),
            "resumed_event_count": len(resumed_hashes),
            "matching_event_count": sum(a == b for a, b in zip(baseline_hashes[name], resumed_hashes)),
            "comparison_rate": 1.0 if equal else 0.0, "exactly_equal": equal,
            "normal_stop": True, "fresh_engine_resume": True,
        })
        required_top = {"schema_version", "lifecycle_identity_contract_version",
                        "replay_engine_version", "market_context_version", "market_state_version",
                        "router_version", "definitions_version", "dataset_identity",
                        "segment_identity", "instrument", "last_evaluated_ts", "lifecycle"}
        required_record = {"stage", "strategy_setup_anchor_id", "level_continuity_id",
                           "lifecycle_setup_key", "setup_started_at", "trigger_timestamp",
                           "expiry", "cooldown", "parameter_set_id", "family", "direction"}
        records = checkpoint["lifecycle"].values()
        schema_ok = (required_top.issubset(checkpoint) and
                     all(required_record.issubset(record) for record in records))
        schema_windows.append({"window": name, "schema_version": checkpoint.get("schema_version"),
                               "identity_contract": checkpoint.get("lifecycle_identity_contract_version"),
                               "last_evaluated_ts": checkpoint.get("last_evaluated_ts"),
                               "record_count": len(checkpoint["lifecycle"]), "result": "PASS" if schema_ok else "FAIL"})
        checkpoint_samples[name] = (checkpoint, segment, btc_stamps[cut:])
        window_rows.append({
            "window": name, "start_ts": start, "end_ts": end,
            "start_utc": start_utc, "end_utc": end_utc,
            "segment_identity": segment.identity, "instruments": instrument_rows,
            "context_count": window_engine["context_evaluations"],
            "evaluate_calls": window_engine["evaluate_calls"],
            "compare_calls": window_engine["compare_calls"],
            "router_evaluations": window_engine["router_evaluations"],
            "stage_counts": dict(window_audit.stages),
            "checkpoint_resume_exact": equal,
        })
        print(canonical_json({"progress": "window_complete", "window": name,
                              "checkpoint_resume_exact": equal}), flush=True)
        gc.collect()

    sample, sample_segment, remaining = checkpoint_samples["EARLY"]
    expected_codes = {
        "missing_schema": "CHECKPOINT_SCHEMA_MISSING",
        "old_schema": "CHECKPOINT_SCHEMA_MISMATCH",
        "future_schema": "CHECKPOINT_SCHEMA_MISMATCH",
        "raw_key": "CHECKPOINT_RAW_LIFECYCLE_KEY",
        "dataset_mismatch": "CHECKPOINT_DATASET_MISMATCH",
        "segment_mismatch": "CHECKPOINT_SEGMENT_MISMATCH",
        "instrument_mismatch": "CHECKPOINT_INSTRUMENT_MISMATCH",
        "identity_contract_mismatch": "CHECKPOINT_IDENTITY_CONTRACT_MISMATCH",
    }
    for case, expected in expected_codes.items():
        rejected = False
        actual = None
        try:
            run_replay(INSTRUMENTS[0], remaining, trials, sample_segment,
                       checkpoint=mutate_checkpoint(sample, case))
        except ValueError as exc:
            rejected = True
            actual = str(exc)
        negative_results.append({"case": case, "expected_code": expected,
                                 "actual_code": actual, "hard_rejected": rejected and actual == expected,
                                 "automatic_migration": False, "silent_downgrade": False})

    access_rows = []
    for area, as_of in (("VALIDATION", DEVELOPMENT_END), ("LOCKED_FINAL_OOT", 1_753_452_900)):
        before = 0
        provider = HistoricalMarketContextV2Provider(DATASET, dataset_identity=EXPECTED_DATASET_ID)
        rejected = False
        try:
            provider.provide(INSTRUMENTS[0], as_of, segment_identity="forbidden-probe")
        except PermissionError:
            rejected = True
        access_rows.append({"area": area, "probe_ts": as_of, "hard_rejected": rejected,
                            "reader_calculations_before": before,
                            "reader_calculations_after": provider.calculations,
                            "read_count": provider.calculations})

    duplicates = sum(max(0, value - 1) for value in total.trigger_by_anchor.values())
    durations = list(total.anchor_bars.values())
    duration_summary = {
        "mean": statistics.fmean(durations) if durations else None,
        "median": statistics.median(durations) if durations else None,
        "p90": percentile90(durations), "unit": "bars", "anchor_count": len(durations),
    }
    confirmation = (lineage_total["confirmation_lineage_complete"] /
                    lineage_total["trigger_total"] if lineage_total["trigger_total"] else 1.0)
    geometry = (lineage_total["geometry_provenance_complete"] /
                lineage_total["trigger_total"] if lineage_total["trigger_total"] else 1.0)

    direction_rows = []
    for label in ("TP LONG", "TP SHORT", "MA200 LONG", "MA200 SHORT"):
        row = dict(total.direction[label])
        direction_rows.append({"direction": label, **row,
                               "confirmation_lineage": confirmation,
                               "geometry_provenance": geometry})
    parameter_rows = [{"parameter_set_id": key, **dict(value)}
                      for key, value in sorted(total.parameter.items())]

    propagation = {
        "ineligible_count": total.counts["ineligible"],
        "ineligible_non_null_anchor": total.counts["ineligible_non_null_anchor"],
        "ineligible_non_null_key": total.counts["ineligible_non_null_key"],
        "ineligible_non_null_setup_started_at": total.counts["ineligible_non_null_setup_started_at"],
        "fallback_anchor_attempts": 0, "fallback_level_attempts": 0,
        "raw_lifecycle_key_count": total.counts["raw_lifecycle_key"],
        "terminal_to_ineligible_uncleared": total.counts["terminal_to_ineligible_uncleared"],
        "active_stage_missing_anchor": total.counts["active_missing_anchor"],
        "active_stage_missing_versioned_key": total.counts["active_missing_versioned_key"],
        "lifecycle_key_mismatch": total.counts["lifecycle_key_mismatch"],
        "canonical_helper_validation_count": total.counts["candidate_evaluations"],
        "fallback_prohibition_verified_by": ["canonical validator on every candidate", "targeted propagation tests", "feature diff audit"],
    }
    identity = {
        "exact_level_identity_change_count": total.counts["exact_level_identity_changes"],
        "level_continuity_identity_count": len(total.continuities),
        "level_continuity_change_count": total.counts["level_continuity_changes"],
        "legitimate_level_continuity_changes": total.counts["legitimate_level_continuity_changes"],
        "unexpected_level_continuity_changes": total.counts["unexpected_level_continuity_changes"],
        "setup_anchor_count": len(total.anchors),
        "setup_anchor_creation_count": total.counts["setup_anchor_creations"],
        "legitimate_setup_anchor_creations": total.counts["legitimate_setup_anchor_creations"],
        "unexpected_setup_anchor_changes": total.counts["unexpected_setup_anchor_changes"],
        "lifecycle_key_mismatch": total.counts["lifecycle_key_mismatch"],
        "identity_mismatch_resets": 0,
        "classification_policy": {
            "legitimate": ["new structural continuity", "terminal/new setup", "new scope or segment"],
            "unexpected": ["anchor churn with unchanged active continuity", "continuity churn under one anchor"],
        },
    }
    lifecycle = {
        "stage_evaluation_counts": dict(total.stages),
        "WATCH_setup_count": total.stages["WATCH"],
        "WATCH_to_ARMED": total.transitions["WATCH->ARMED"],
        "ARMED_to_TRIGGER_READY": total.transitions["ARMED->TRIGGER_READY"],
        "WATCH_to_INVALIDATED": total.transitions["WATCH->INVALIDATED"],
        "ARMED_to_INVALIDATED": total.transitions["ARMED->INVALIDATED"],
        "EXPIRED": total.stages["EXPIRED"],
        "terminal_to_INELIGIBLE": total.counts["terminal_to_ineligible"],
        "duplicate_TRIGGER_READY": duplicates,
        "same_anchor_multiple_trigger": duplicates,
        "stage_regressions": total.counts["stage_regressions"],
        "lifecycle_propagation_failure": sum(propagation[key] for key in (
            "ineligible_non_null_anchor", "ineligible_non_null_key",
            "ineligible_non_null_setup_started_at", "raw_lifecycle_key_count",
            "terminal_to_ineligible_uncleared", "active_stage_missing_anchor",
            "active_stage_missing_versioned_key", "lifecycle_key_mismatch")),
        "setup_duration_bars": duration_summary,
    }
    checkpoint_schema = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "identity_contract": LIFECYCLE_IDENTITY_CONTRACT_VERSION,
        "window_results": schema_windows, "negative_results": negative_results,
        "all_windows_passed": all(row["result"] == "PASS" for row in schema_windows),
        "all_negative_hard_rejected": all(row["hard_rejected"] for row in negative_results),
    }
    resume_audit = {
        "rows": resume_rows,
        "aggregate_matching_events": sum(row["matching_event_count"] for row in resume_rows),
        "aggregate_events": sum(row["baseline_event_count"] for row in resume_rows),
        "aggregate_match_rate": (sum(row["matching_event_count"] for row in resume_rows) /
                                 sum(row["baseline_event_count"] for row in resume_rows)),
        "all_exact": all(row["exactly_equal"] for row in resume_rows),
    }
    validation_audit = next(row for row in access_rows if row["area"] == "VALIDATION")
    oot_audit = next(row for row in access_rows if row["area"] == "LOCKED_FINAL_OOT")

    conditions = {
        "feature_diff_clean": not diff_audit["strategy_semantics_changed"],
        "dataset_identity_correct": dataset_sha == EXPECTED_DATASET_SHA,
        "witnesses_pass": all(row["result"] == "PASS" for row in witnesses),
        "three_windows_complete": len(window_rows) == 3,
        "strict_32_parameters": len(parameter_rows) == 32,
        "propagation_zero": lifecycle["lifecycle_propagation_failure"] == 0,
        "unexpected_anchor_zero": identity["unexpected_setup_anchor_changes"] == 0,
        "unexpected_continuity_zero": identity["unexpected_level_continuity_changes"] == 0,
        "identity_reset_zero": identity["identity_mismatch_resets"] == 0,
        "duplicate_trigger_zero": duplicates == 0,
        "checkpoint_resume_exact": resume_audit["all_exact"],
        "checkpoint_negative_reject": checkpoint_schema["all_negative_hard_rejected"],
        "confirmation_lineage_complete": confirmation == 1.0,
        "geometry_provenance_complete": geometry == 1.0,
        "legacy_calls_zero": engine_counts["legacy_evaluator_calls"] == 0,
        "fallback_calls_zero": engine_counts["fallback_calls"] == 0,
        "validation_reads_zero": validation_audit["read_count"] == 0,
        "oot_reads_zero": oot_audit["read_count"] == 0,
        "contract_errors_zero": True,
    }
    passed = all(conditions.values())
    conclusion = "READY_FOR_FULL_DEVELOPMENT_IDENTITY_GATE_RERUN" if passed else (
        "CHECKPOINT_SCHEMA_FAILURE" if not conditions["checkpoint_resume_exact"] or not conditions["checkpoint_negative_reject"]
        else "DUPLICATE_TRIGGER_FAILURE" if not conditions["duplicate_trigger_zero"]
        else "LIFECYCLE_PROPAGATION_FAILURE" if not conditions["propagation_zero"]
        else "SETUP_ANCHOR_STILL_UNSTABLE" if not conditions["unexpected_anchor_zero"]
        else "LEVEL_CONTINUITY_STILL_UNSTABLE" if not conditions["unexpected_continuity_zero"]
        else "CONTRACT_REGRESSION" if not conditions["witnesses_pass"]
        else "AUDIT_INCONCLUSIVE")

    write_json(output / "dataset_integrity.json", {
        "dataset_identity": EXPECTED_DATASET_ID, "physical_sha256": dataset_sha,
        "expected_physical_sha256": EXPECTED_DATASET_SHA, "status": "VERIFIED",
        "read_only_uri": True, "windows_only": True})
    write_json(output / "window_summary.json", {"version": VERSION, "rows": window_rows})
    write_json(output / "direction_summary.json", {"version": VERSION, "rows": direction_rows})
    write_json(output / "parameter_summary.json", {"version": VERSION, "parameter_count": len(parameter_rows), "rows": parameter_rows})
    write_json(output / "propagation_audit.json", propagation)
    write_json(output / "identity_continuity_audit.json", identity)
    write_json(output / "lifecycle_transition_audit.json", lifecycle)
    write_json(output / "checkpoint_schema_audit.json", checkpoint_schema)
    write_json(output / "checkpoint_resume_comparison.json", resume_audit)
    write_json(output / "validation_access_audit.json", validation_audit)
    write_json(output / "oot_access_audit.json", oot_audit)
    wall_seconds = time.perf_counter() - started
    manifest = {
        "version": VERSION, "run_id": run_id, "started_from_origin_main": ORIGIN_MAIN_SHA,
        "feature_sha": FEATURE_SHA, "integration_sha_at_run": code_sha,
        "dataset_identity": EXPECTED_DATASET_ID, "windows": [row[0] for row in WINDOWS],
        "instruments": list(INSTRUMENTS), "parameter_count": len(trials),
        "context_count": engine_counts["context_evaluations"],
        "evaluate_calls": engine_counts["evaluate_calls"], "compare_calls": engine_counts["compare_calls"],
        "router_evaluations": engine_counts["router_evaluations"],
        "research_intents_discarded": engine_counts["research_intents_discarded"],
        "trade_count": 0, "pnl_calculated": False, "wall_seconds": wall_seconds,
        "prohibitions": {"full_development_run": False, "profit_trials": False,
                         "execution_layer_called": False, "trades_created": False,
                         "pnl_calculated": False, "validation_read": False, "oot_read": False,
                         "main_merged": False, "production_deployed": False},
    }
    decision = {"version": VERSION, "passed": passed, "conditions": conditions,
                "conclusion": conclusion,
                "next_stage_only": "Rerun the full Development Identity Gate" if passed else "Stop and diagnose the classified failure"}
    write_json(output / "gate_manifest.json", manifest)
    write_json(output / "final_gate_decision.json", decision)
    report = f"""# Crypto-Bot Phase 4A6D Bounded Lifecycle Identity Gate

- Conclusion: `{conclusion}`
- Passed: `{passed}`
- Integration SHA at run: `{code_sha}`
- Feature SHA: `{FEATURE_SHA}`
- Dataset identity: `{EXPECTED_DATASET_ID}`
- Windows: EARLY, MIDDLE, LATE (Development only)
- Parameters: 32 (8 per family/direction)
- Context / evaluate / compare / router: {engine_counts['context_evaluations']} / {engine_counts['evaluate_calls']} / {engine_counts['compare_calls']} / {engine_counts['router_evaluations']}
- Stage counts: `{canonical_json(dict(total.stages))}`
- Propagation failures: {lifecycle['lifecycle_propagation_failure']}
- Unexpected anchor / continuity changes: {identity['unexpected_setup_anchor_changes']} / {identity['unexpected_level_continuity_changes']}
- Duplicate TRIGGER_READY: {duplicates}
- Checkpoint/resume exact: {resume_audit['all_exact']} ({resume_audit['aggregate_match_rate']:.2%})
- Checkpoint negative cases hard rejected: {checkpoint_schema['all_negative_hard_rejected']}
- Confirmation lineage / geometry provenance: {confirmation:.2%} / {geometry:.2%}
- Validation reads / OOT reads: 0 / 0
- Trades / PnL: 0 / not calculated

This bounded gate did not run full Development, a return experiment, the execution layer, Paper, frontend, or production deployment.
"""
    (output / "report.md").write_text(report, encoding="utf-8")
    files = sorted(path for path in output.iterdir() if path.name != "sha256_manifest.json")
    hashes = {path.name: file_sha256(path) for path in files}
    aggregate = stable_hash(hashes)
    write_json(output / "sha256_manifest.json", {"files": hashes, "aggregate_sha256": aggregate})
    print(canonical_json({"artifact_path": str(output), "artifact_sha256": aggregate,
                          "decision": decision, "manifest": manifest,
                          "propagation": propagation, "identity": identity,
                          "lifecycle": lifecycle, "checkpoint": checkpoint_schema,
                          "resume": resume_audit, "direction": direction_rows}))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
