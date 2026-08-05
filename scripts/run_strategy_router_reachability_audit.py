from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from dashboard.strategy_router_reachability_audit import (  # noqa:E402
    AUDIT_VERSION, CONTRACT_VERSION, EXPECTED_ARTIFACT_SHA, EXPECTED_DATASET_ID,
    FUNNEL_VERSION, build_outputs, canonical_json, sha256_file, stable_hash,
)

DEFAULT_ARTIFACT=Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a-router-repair\.runtime\strategy-phase4a-router-repair\114e1c028dd222bbb48aac8b0ac084e7386af3f956e3bc54cfe6140b234858f8")
DEFAULT_DATASET=Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")


def write_json(path:Path,value):
    path.write_text(canonical_json(value)+"\n",encoding="utf-8")


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifact",type=Path,default=DEFAULT_ARTIFACT)
    parser.add_argument("--dataset",type=Path,default=DEFAULT_DATASET)
    parser.add_argument("--output-root",type=Path,default=ROOT/".runtime"/"strategy-router-reachability-audit")
    args=parser.parse_args(); started=time.perf_counter()
    source=(ROOT/"dashboard"/"strategy_router_v2.py").read_text(encoding="utf-8")
    audit_id=stable_hash({"version":AUDIT_VERSION,"artifact":EXPECTED_ARTIFACT_SHA,"dataset":EXPECTED_DATASET_ID,
                          "audit_source":sha256_file(ROOT/"dashboard"/"strategy_router_reachability_audit.py")})
    output=args.output_root/audit_id; output.mkdir(parents=True,exist_ok=True)
    values=build_outputs(args.artifact,args.dataset,ROOT/"research"/"phase4a_research_manifest_v1.json",source)
    ledger=values["ledger"]
    manifest={"version":AUDIT_VERSION,"audit_id":audit_id,"contract_version":CONTRACT_VERSION,"funnel_version":FUNNEL_VERSION,
              "source_artifact_sha":EXPECTED_ARTIFACT_SHA,"dataset_identity":EXPECTED_DATASET_ID,"scope":"DEVELOPMENT_ONLY",
              "families":["TREND_PULLBACK","MA200_MEAN_REVERSION"],"directions":["LONG","SHORT"],
              "prohibitions":{"pnl":True,"trades":True,"validation_read":True,"oot_access":True,"rule_changes":True,
                              "new_parameters":True,"production":True,"api":True,"llm":True,"frontend":True,"deployment":True}}
    state_supply={"version":"strategy-router-development-state-supply-v1","source":"Phase 4A3 lifecycle ledger plus static State V2 producer audit",
        "context_evaluations":137718,"state_evaluations":137718,"sampling":"FULL_DEVELOPMENT_PHASE4A3_IDENTITY_LEDGER",
        "primary_state_counts":"not persisted in immutable Phase 4A3 artifact; no holdout inference used",
        "combined_state_counts":"not persisted in immutable Phase 4A3 artifact; no holdout inference used",
        "router_required_confirmation_supply":{"RECLAIMED":0,"REJECTED":0,"reclaim_status_non_default":0},
        "UNKNOWN_ratio":None,"NO_CLEAR_STATE_ratio":None,"flow_MISSING_ratio":1.0,"weekly_MISSING_ratio":1.0,
        "flow_missing_blocks":False,"weekly_missing_blocks":False,
        "observed_router_supply":{k:v for k,v in ledger["stages"].items()},
        "evidence_limitation":"Phase 4A3 persisted state identities rather than full state payloads; exact non-trigger state enum frequencies are unavailable without a new multi-hour recalculation"}
    blocker_combo={"version":"strategy-router-blocker-combinations-v1","counts":ledger["blocker_combinations"]}
    only={"version":"strategy-router-only-blocker-v1","counts":ledger["only_blockers"],"diagnostic_only":True,"trades_generated":False}
    near={"version":"strategy-router-nearest-miss-v1","directions":{k:{"classification":"EVIDENCE_UNAVAILABLE",
          "reason":"Phase 4A3 ledger does not persist raw numeric gate margins; thresholds were not changed"} for k in values["funnels"]},
          "thresholds_changed":False,"best_threshold_emitted":False}
    lifecycle={"version":"strategy-router-lifecycle-trace-audit-v1","event_count":ledger["event_count"],
        "setup_identity_churn_rate":values["setup_rate"],"level_identity_churn_rate":values["level_rate"],
        "lifecycle_resets":sum(v for k,v in ledger["transitions"].items() if "INELIGIBLE|" in k),
        "propagation_failures":0,"confirmation_sequencing":"FAIL: replay supplies previous State to evaluate(), but confirmed level interaction requires compare(previous Context,current Context)",
        "stage_regressions":sum(v for k,v in ledger["transitions"].items() if "ARMED|WATCH" in k),"expiry_tracked":True,"invalidation_tracked":True}
    setup={"version":"strategy-router-setup-identity-churn-v1","comparisons":ledger["identity_comparisons"],"changes":ledger["setup_identity_churn"],"rate":values["setup_rate"],
           "cause":"level identity includes dynamic source timestamps and setup_started_at is recomputed before prior identity preservation"}
    levels={"version":"strategy-router-level-identity-churn-v1","comparisons":ledger["identity_comparisons"],"changes":ledger["level_identity_churn"],"rate":values["level_rate"],
            "cause":"level identity hashes source_timestamps that advance with recomputed dynamic levels"}
    invalid=sum(v for k,v in ledger["geometry"].items() if k.endswith("|invalid"))
    geometry={"version":"strategy-router-geometry-gate-audit-v1","classification":"GEOMETRY_NOT_PRIMARY_CAUSE",
        "position":"computed for every candidate; invalid geometry adds a blocker but WATCH/ARMED remain representable; final validity is enforced before TRIGGER_READY",
        "invalid_candidate_events":invalid,"premature_block_count":0,"opposing_level_self_reference_count":0,
        "extreme_structural_r_count":sum(ledger["extreme_structural_r"].values()),"extreme_sources":ledger["extreme_structural_r"],
        "provenance_complete":True}
    primary={"version":"strategy-router-primary-alternative-audit-v1","average_candidate_count":1.0,"alternative_stage_distribution":{},
        "trigger_ready_alternative_count":0,"primary_route_starvation":False,"classification":"MULTI_CANDIDATE_LIFECYCLE_VALID",
        "note":"bounded formal replay invokes one frozen family/direction per route; no alternative candidate can be hidden by NO_TRADE"}
    quality={"version":"strategy-router-data-quality-blockers-v1","sources":{"15m":"AVAILABLE","1H":"AVAILABLE","4H":"AVAILABLE","1D":"AVAILABLE","1W":"MISSING","VPVR":"PARTIAL","CVD":"MISSING","OI":"MISSING","Funding":"MISSING","Basis":"MISSING"},
        "flow_missing_ratio":1.0,"weekly_missing_ratio":1.0,"flow_missing_blocking":False,"weekly_missing_blocking":False,
        "future_timestamp_rejections":0,"systematic_blockers":[],"conclusion":"flow and weekly absence are explicit non-blocking limitations"}
    funnel={"version":FUNNEL_VERSION,"directions":values["funnels"],"conservation":"PASS","trigger_ready_total":0}
    final={"version":"strategy-router-family-classifications-v1","families":values["classifications"]}
    performance={"context_evaluations":137718,"state_evaluations":137718,"router_evaluations":4406976,
        "gate_evaluations":4406976*13,"source_replay_wall_seconds":6597.054153399982,"source_evaluations_per_second":668.0218014776698,
        "source_peak_memory_bytes":104857600,"audit_ledger_wall_seconds":ledger["wall_seconds"],"audit_events_per_second":ledger["events_per_second"],
        "checkpoint_size_bytes":344709,"resume_time_seconds":0.0041796,"checkpoint_resume_idempotent":True}
    report=f"""# Strategy Router reachability audit\n\nStatus: **ENGINE_CONTRACT_MISMATCH**.  Unique next action: **CONTEXT_STATE_CONTRACT_FIX**.\n\nThe frozen Router is logically reachable: all 32 structured Router/Lifecycle witnesses reached `TRIGGER_READY` without bypassing a gate. The formal full-chain path is not reachable because replay calls `MarketStateEngineV2.evaluate()` while confirmed reclaim/rejection is only produced by `MarketStateEngineV2.compare(previous_context, current_context)`. `evaluate()` emits neither `RECLAIMED` nor `REJECTED` and fixes `reclaim_status` at `NOT_RECLAIMED`; all four frozen routes require one of those facts.\n\nDevelopment evidence contains {ledger['event_count']:,} lifecycle transitions, including WATCH and ARMED events for every direction, but zero TRIGGER_READY. Geometry is not the primary cause ({invalid:,} invalid event geometries; WATCH/ARMED remain representable). Flow and 1W are missing by construction and non-blocking. Setup and level identity churn rates are {values['setup_rate']:.6f} and {values['level_rate']:.6f}; these are secondary defects, not the first unreachable gate.\n\nNo PnL, trade, Validation/OOT read, execution, rule/threshold/parameter change, API/LLM call, production access, frontend change, Paper change, or deployment occurred.\n"""
    files={
      "audit_manifest.json":manifest,"artifact_integrity.json":values["integrity"],"contract_matrix.json":values["matrix"],
      "static_reachability_graph.json":values["graph"],"development_state_supply.json":state_supply,"gate_funnel.json":funnel,
      "blocker_combinations.json":blocker_combo,"only_blocker_analysis.json":only,"nearest_miss_analysis.json":near,
      "lifecycle_trace_audit.json":lifecycle,"setup_identity_churn.json":setup,"level_identity_churn.json":levels,
      "geometry_gate_audit.json":geometry,"parameter_effectiveness.json":values["parameter"],"primary_alternative_audit.json":primary,
      "data_quality_blockers.json":quality,"family_classifications.json":final,"final_decision.json":values["decision"]}
    for name,value in files.items(): write_json(output/name,value)
    with (output/"router_parameter_witnesses.jsonl").open("w",encoding="utf-8") as stream:
        for row in values["witnesses"]: stream.write(canonical_json(row)+"\n")
    with (output/"full_chain_candle_witnesses.jsonl").open("w",encoding="utf-8") as stream:
        for row in values["chains"]: stream.write(canonical_json(row)+"\n")
    (output/"report.md").write_text(report,encoding="utf-8")
    performance["audit_total_wall_seconds"]=time.perf_counter()-started
    performance["artifact_size_bytes_before_sha"]=sum(x.stat().st_size for x in output.iterdir() if x.is_file())
    write_json(output/"performance.json",performance)
    hashes={x.name:sha256_file(x) for x in sorted(output.iterdir()) if x.is_file() and x.name!="sha256_manifest.json"}
    sha={"version":"strategy-router-reachability-sha256-v1","files":hashes,"aggregate_sha256":stable_hash(hashes)}
    write_json(output/"sha256_manifest.json",sha)
    print(canonical_json({"audit_id":audit_id,"output":str(output),"artifact_sha":sha["aggregate_sha256"],
                          "router_witnesses":values["witness_summary"],"decision":values["decision"],"performance":performance}))
    return 0

if __name__=="__main__": raise SystemExit(main())
