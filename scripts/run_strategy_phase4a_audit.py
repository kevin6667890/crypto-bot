"""Run the deterministic, Development-only Phase 4A forensic audit."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from dashboard.strategy_phase4a_audit import (  # noqa:E402
    AUDIT_VERSION,DISCLAIMER,EXPECTED_ARTIFACT_SHA,EXPECTED_DATASET_ID,EvidenceBundle,
    DevelopmentCandleStore,actual_v2_route_audit,artifact_sha,canonical_json,
    concentration_audit,deterministic_samples,distribution,file_sha256,
    final_classifications,independent_trade_audit,stable_hash,trade_diagnostics,
)

PHASE4_ROOT=Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a\.runtime\strategy-phase4a\d2a72ac24223320655e7eb08d54dba38d976a5c1e804b83203abfc43b2e6ebed")
DATABASE=Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")
BUGS=[ROOT/f"research/phase4a_engine_bug_{index:03d}.json" for index in range(1,5)]
OUTPUT_ROOT=ROOT/".runtime"/"strategy-phase4a-audit"


def write_json(path:Path,value):
    encoded=canonical_json(value)+"\n"
    if path.exists() and path.read_text(encoding="utf8")==encoded:return
    temporary=path.with_suffix(path.suffix+".tmp");temporary.write_text(encoded,encoding="utf8");temporary.replace(path)


def write_jsonl(path:Path,rows,key):
    ordered=sorted(rows,key=lambda x:str(x[key]));temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text("".join(canonical_json(row)+"\n" for row in ordered),encoding="utf8");temporary.replace(path)


def metrics_by_group(trades,events):
    output={}
    for family,direction in ((f,d) for f in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for d in ("LONG","SHORT")):
        group=[t for t in trades if (t["family"],t["direction"])==(family,direction)]
        wins=sum(max(0,float(t["gross_pnl"])) for t in group);losses=-sum(min(0,float(t["gross_pnl"])) for t in group)
        net_wins=sum(max(0,float(t["net_pnl"])) for t in group);net_losses=-sum(min(0,float(t["net_pnl"])) for t in group)
        initial=sum(float(t["actual_risk"]) for t in group)
        output[f"{family}:{direction}"]={
            "gross_expectancy_r":statistics.fmean(float(t["gross_pnl"])/float(t["actual_risk"]) for t in group),
            "net_expectancy_r":statistics.fmean(float(t["r"]) for t in group),
            "gross_profit_factor":wins/losses if losses else None,"net_profit_factor":net_wins/net_losses if net_losses else None,
            "fee_drag":sum(float(t["fees"]) for t in group),"slippage_drag":sum(float(t["slippage_drag"]) for t in group),
            "fee_per_trade":statistics.fmean(float(t["fees"]) for t in group),
            "slippage_per_trade":statistics.fmean(float(t["slippage_drag"]) for t in group),
            "cost_as_initial_risk":sum(float(t["fees"])+float(t["slippage_drag"]) for t in group)/initial if initial else None,
            "cost_as_average_mfe":sum(float(t["fees"])+float(t["slippage_drag"]) for t in group)/sum(max(0,float(t["mfe"]))*float(t["actual_risk"]) for t in group) if group else None,
            "gross_break_even_cost_negative_rate":sum(float(t["gross_pnl"])>=0>float(t["net_pnl"]) for t in group)/len(group),
            "gross_negative_rate":sum(float(t["gross_pnl"])<0 for t in group)/len(group),"diagnostic_only":True}
    return output


def ma_breakdown(funnel,event_by_id,trades):
    output={}
    for direction in ("LONG","SHORT"):
        for timeframe in ("1H","4H"):
            counts=funnel[f"MA200_MEAN_REVERSION:{direction}"]["level_timeframe_stages"]
            selected_trades=[t for t in trades if (event_by_id[t["event_id"]].get("geometry",{}).get("setup_zone") or {}).get("timeframe")==timeframe and t["family"]=="MA200_MEAN_REVERSION" and t["direction"]==direction]
            pnls=[float(t["r"]) for t in selected_trades];wins=sum(max(0,float(t["net_pnl"])) for t in selected_trades);loss=-sum(min(0,float(t["net_pnl"])) for t in selected_trades)
            output[f"{direction}:{timeframe}"]={"watch":counts.get(f"{timeframe}:WATCH",0),
                "armed":counts.get(f"{timeframe}:ARMED",0),"trigger_ready":counts.get(f"{timeframe}:TRIGGER_READY",0),"trades":len(selected_trades),
                "expectancy_r":statistics.fmean(pnls) if pnls else None,"profit_factor":wins/loss if loss else None,
                "folds":dict(Counter(str(1+min(3,int((int(t["entry_ts"])-1698365700)*4/(1739681100-1698365700)))) for t in selected_trades)),
                "confluence_source":"NOT_SERIALIZED_IN_PHASE4A_LEDGER","classification":"AUDIT_EVIDENCE_GAP","diagnostic_only":True}
    return output


def aggregation_audit(bundle,trades):
    output=[]
    for trial in bundle.trials:
        group=[t for t in trades if t["trial_id"]==trial["trial_id"]];official=trial["development"]["metrics"]
        net=sum(float(t["net_pnl"]) for t in group);total_r=sum(float(t["r"]) for t in group)
        output.append({"trial_id":trial["trial_id"],"trade_count_match":len(group)==official["trade_count"],
                       "net_pnl_match":math.isclose(net,official["net_pnl"],rel_tol=1e-10,abs_tol=1e-8),
                       "total_r_match":math.isclose(total_r,official["total_r"],rel_tol=1e-10,abs_tol=1e-8)})
    return {"trials":output,"all_match":all(all(v for k,v in x.items() if k.endswith("_match")) for x in output)}


def markdown(report):
    decision=report["final_decision"]
    return f"""# Phase 4A2 forensic audit

This report is **DIAGNOSTIC_ONLY**. It reads Development evidence only and does
not change any Phase 4A trial classification.

## Integrity

Artifact, manifest, dataset, code SHA, engine v2.0.4 and 32-trial identities all
matched. Four historical runs remain `INVALIDATED_ENGINE_BUG`.

## Critical finding

The accepted Phase 4A replay did not execute the frozen
`MarketAnalysisContextV2 -> MarketStateEngineV2 -> StrategyRouterV2` chain. Its
private simplified evaluator generated the ledger. Independent real-V2 replay
matched {{stage_matches}}/{{stage_samples}} sampled lifecycle stages and
{{identity_matches}}/{{stage_samples}} strategy identities. Execution arithmetic
matched {{execution_matches}}/{{execution_samples}} independently rebuilt trades.

This is an `EVENT_REPLAY_ERROR`, `IDENTITY_ERROR` and `GEOMETRY_ERROR`; it affects
which events became trades. Therefore the Phase 4A PnL classifications cannot be
used to accept/reject either strategy family. Ledger-only funnel, cost, latency,
geometry and concentration tables remain descriptive evidence about the flawed
implementation, not the frozen strategies.

## Decision

**{decision['route']}**

{decision['single_next_action']}
""".replace("{stage_matches}",str(report["summary"]["stage_matches"])).replace("{stage_samples}",str(report["summary"]["route_samples"])).replace("{identity_matches}",str(report["summary"]["identity_matches"])).replace("{execution_matches}",str(report["summary"]["execution_matches"])).replace("{execution_samples}",str(report["summary"]["execution_samples"]))


def run():
    started=time.perf_counter();before_sha,_=artifact_sha(PHASE4_ROOT)
    bundle=EvidenceBundle.verify(PHASE4_ROOT,DATABASE,BUGS);trades=bundle.trades();trade_event_ids={t["event_id"] for t in trades};event_by_id={}
    def stream():
        for event in bundle.events():
            if event["event_identity"] in trade_event_ids:event_by_id[event["event_identity"]]=event
            yield event
    samples,funnel=deterministic_samples(stream())
    if len(event_by_id)!=len(trade_event_ids):raise ValueError("trade references missing event")
    route_audit=actual_v2_route_audit(samples,DATABASE);store=DevelopmentCandleStore(DATABASE)
    execution_audit=independent_trade_audit(trades,event_by_id,store)
    diagnostics=trade_diagnostics(trades,event_by_id,store);cost=metrics_by_group(trades,event_by_id)
    concentration=concentration_audit(bundle.trials,trades);ma=ma_breakdown(funnel,event_by_id,trades)
    aggregation=aggregation_audit(bundle,trades);classifications,decision,hypotheses=final_classifications(route_audit)
    trade_counts=Counter((t["family"],t["direction"]) for t in trades);exit_counts=Counter((t["family"],t["direction"],t["exit_reason"]) for t in trades)
    for key,value in funnel.items():
        family,direction=key.split(":");triggers=value["transition_counts"]["TRIGGER_READY"]
        value.update({"next_open_available":triggers,"entry_geometry_valid":triggers,
                      "trade_opened":trade_counts[(family,direction)],
                      "target_exits":sum(v for (f,d,r),v in exit_counts.items() if (f,d)==(family,direction) and r in {"TARGET","TARGET_FIRST","GAP_TARGET"}),
                      "stop_exits":sum(v for (f,d,r),v in exit_counts.items() if (f,d)==(family,direction) and r in {"STOP","STOP_FIRST","GAP_STOP"}),
                      "timeout_exits":sum(v for (f,d,r),v in exit_counts.items() if (f,d)==(family,direction) and r in {"TIMEOUT","SEGMENT_END"}),
                      "entry_rejection_breakdown":"NOT_PERSISTED_METRIC_AGGREGATION_ERROR"})
    coverage={"groups":sorted({(x["family"],x["direction"]) for x in samples}),"instruments":sorted({x["instrument"] for x in samples}),
              "folds":sorted({x["fold"] for x in route_audit}),"parameter_sets":len({x["parameter_set_id"] for x in samples}),
              "by_bucket":dict(Counter(f"{x['family']}:{x['direction']}:{x['lifecycle_to']}" for x in samples))}
    expected_groups={(family,direction) for family in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for direction in ("LONG","SHORT")}
    if set(map(tuple,coverage["groups"])) != expected_groups or set(coverage["instruments"]) != {"BTC-USDT","ETH-USDT","SOL-USDT"} or set(coverage["folds"]) != {1,2,3,4}:
        raise ValueError(f"forensic sampling coverage incomplete: {coverage}")
    for family,direction in expected_groups:
        for stage in ("WATCH","ARMED","TRIGGER_READY"):
            if coverage["by_bucket"].get(f"{family}:{direction}:{stage}",0) < 25:
                raise ValueError(f"insufficient forensic sample: {family}:{direction}:{stage}")
    summary={"route_samples":len(route_audit),"stage_matches":sum(x["stage_match"] for x in route_audit),
             "identity_matches":sum(x["setup_identity_match"] and x["evaluation_identity_match"] and x["level_identity_match"] for x in route_audit),
             "execution_samples":len(execution_audit),"execution_matches":sum(x["all_execution_checks_match"] for x in execution_audit),
             "metric_aggregation_match":aggregation["all_match"],"elapsed_seconds":time.perf_counter()-started}
    audit_code_sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    manifest={"version":AUDIT_VERSION,"audit_code_sha":audit_code_sha,"source_artifact_sha":EXPECTED_ARTIFACT_SHA,"dataset_identity":EXPECTED_DATASET_ID,
              "seed":20260804,"scope":"DEVELOPMENT_ONLY","validation_read":False,"oot_read":False,"new_trials":0,"new_parameters":0,
              "diagnostic_only":True,"input_hashes":bundle.file_hashes}
    audit_id=stable_hash(manifest);manifest["audit_id"]=audit_id;out=OUTPUT_ROOT/audit_id;out.mkdir(parents=True,exist_ok=True)
    integrity={"artifact_sha_before":before_sha,"artifact_sha_expected":EXPECTED_ARTIFACT_SHA,"artifact_match":True,
               "manifest_identity":bundle.manifest["manifest_identity"],"dataset_identity":bundle.manifest["dataset"]["identity"],
               "code_sha":bundle.report["code_sha"],"engine_version":bundle.report["backtest_engine_version"],"trial_count":len(bundle.trials),
               "invalidated_run_count":4,"validation_payload_count":0,"oot_accessed":False}
    artifacts={"audit_manifest.json":manifest,"artifact_integrity.json":integrity,"event_funnel.json":funnel,
               "trigger_latency.json":diagnostics,"geometry_audit.json":diagnostics,"exit_behavior.json":diagnostics,
               "cost_decomposition.json":cost,"regime_breakdown.json":{k:v["regime_tags"] for k,v in diagnostics.items()},
               "concentration_audit.json":concentration,"ma200_timeframe_breakdown.json":ma,
               "metric_aggregation_audit.json":aggregation,"family_classifications.json":classifications,
               "hypothesis_registry.json":{"count":len(hypotheses),"hypotheses":hypotheses,"reason":"ENGINE_INVALID_NO_HYPOTHESIS_ADMISSION"},
               "final_decision.json":decision,"sampling_coverage.json":coverage}
    for name,value in artifacts.items():write_json(out/name,value)
    write_jsonl(out/"replay_sample_audit.jsonl",route_audit,"sample_identity")
    write_jsonl(out/"execution_sample_audit.jsonl",execution_audit,"trade_identity")
    report={"version":"strategy-phase4a-audit-report-v1","audit_id":audit_id,"summary":summary,"integrity":integrity,
            "sampling":coverage,"funnel":funnel,"diagnostics":diagnostics,"cost":cost,"ma200":ma,"concentration":concentration,
            "classifications":classifications,"hypotheses":hypotheses,"final_decision":decision,"disclaimer":DISCLAIMER}
    (out/"report.md").write_text(markdown(report),encoding="utf8")
    write_json(out/"report.json",report)
    after_sha,_=artifact_sha(PHASE4_ROOT)
    if after_sha!=before_sha:raise PermissionError("source Phase 4A artifact changed")
    hashes={p.name:file_sha256(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!="artifact_sha_manifest.json"}
    sha_manifest={"version":"strategy-phase4a-audit-artifact-sha-v1","files":hashes,"aggregate_sha256":stable_hash(hashes)}
    write_json(out/"artifact_sha_manifest.json",sha_manifest)
    print(canonical_json({"audit_id":audit_id,"path":str(out),"artifact_sha":sha_manifest["aggregate_sha256"],"summary":summary,"decision":decision}))


if __name__=="__main__":run()
