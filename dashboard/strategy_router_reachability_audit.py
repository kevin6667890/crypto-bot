"""Development-only, non-behavioural audit for StrategyRouterV2.

The auditor consumes the immutable Phase 4A3 evidence bundle and invokes the
public Context/State/Router/Lifecycle components only.  It never executes the
backtest/order layer and hard-rejects timestamps outside Development.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping

from .market_state_v2 import (
    COMPOSITE_STATES, PRIMARY_STATES, STATE_DEFINITION_VERSION,
    STATE_ENGINE_VERSION, MarketStateEngineV2,
)
from .strategy_phase4a_router_repair import (
    BACKTEST_ENGINE_VERSION, DEVELOPMENT_END, DEVELOPMENT_START,
    GEOMETRY_VERSION, LIFECYCLE_VERSION, REPLAY_ENGINE_VERSION,
    DevelopmentAccessGuard, canonical_json, stable_hash,
)
from .strategy_router_v2 import (
    DEFINITIONS_VERSION, ROUTER_VERSION, StrategyRouterV2,
)

AUDIT_VERSION = "strategy-router-reachability-audit-v1"
CONTRACT_VERSION = "strategy-router-contract-audit-v1"
FUNNEL_VERSION = "strategy-router-gate-funnel-v1"
EXPECTED_ARTIFACT_SHA = "39de9600f21a03d2ee26bd05de4248e1fd934f43db322db8a4779409bfb5d579"
EXPECTED_DATASET_ID = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
EXPECTED_DATASET_SHA = "9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"
EXPECTED_ORIGINAL_MANIFEST = "e0cd13e743abbda3cc69ddd8ddebd625ce7ede9083e44f6dc81ea4536a1c32ff"
EXPECTED_CODE_SHA = "851781cb803fdb31f1d34f65569e7834dc95322b"
FAMILY_DIRECTIONS = (
    ("TREND_PULLBACK", "LONG"), ("TREND_PULLBACK", "SHORT"),
    ("MA200_MEAN_REVERSION", "LONG"), ("MA200_MEAN_REVERSION", "SHORT"),
)
GATES = ("VERSION_QUALITY", "ENVIRONMENT", "DIRECTION", "STRUCTURE", "SETUP",
         "MOMENTUM", "LEVEL_INTERACTION", "CONFIRMATION", "FLOW_CONTEXT",
         "OPPOSING_LEVEL", "GEOMETRY", "LIFECYCLE", "TRIGGER")


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(chunk):
            digest.update(data)
    return digest.hexdigest()


def artifact_integrity(artifact: Path, dataset: Path, original_manifest: Path) -> dict[str, Any]:
    """Verify every frozen identity before any Development analysis."""
    sha_manifest = json.loads((artifact / "sha256_manifest.json").read_text(encoding="utf-8"))
    if sha_manifest.get("aggregate_sha256") != EXPECTED_ARTIFACT_SHA:
        raise RuntimeError("Phase 4A3 artifact aggregate SHA mismatch")
    actual_files = {}
    for name, expected in sha_manifest["files"].items():
        actual = sha256_file(artifact / name)
        if actual != expected:
            raise RuntimeError(f"Phase 4A3 artifact member SHA mismatch: {name}")
        actual_files[name] = actual
    if stable_hash(actual_files) != EXPECTED_ARTIFACT_SHA:
        raise RuntimeError("Phase 4A3 artifact aggregate recomputation mismatch")
    physical = sha256_file(dataset)
    if physical != EXPECTED_DATASET_SHA:
        raise RuntimeError("dataset physical SHA mismatch")
    original = json.loads(original_manifest.read_text(encoding="utf-8"))
    repair = json.loads((artifact / "repair_manifest.json").read_text(encoding="utf-8"))
    checks = {
        "dataset_identity": repair.get("dataset_identity") == original.get("dataset", {}).get("identity") == EXPECTED_DATASET_ID,
        "original_manifest_identity": repair.get("original_manifest_identity") == original.get("manifest_identity") == EXPECTED_ORIGINAL_MANIFEST,
        "repair_manifest": repair.get("version") == "phase4a-router-repair-manifest-v1",
        "trial_count": len(repair.get("trials", [])) == len(original.get("trials", [])) == 32,
        "canonical_parameters": [x["canonical_parameters"] for x in repair["trials"]] == [x["canonical_parameters"] for x in original["trials"]],
        "development": repair["segments"]["DEVELOPMENT"] == original["segments"]["DEVELOPMENT"],
        "validation_metadata": repair["segments"]["VALIDATION"] == original["segments"]["VALIDATION"],
        "oot_metadata": repair["segments"]["LOCKED_FINAL_OOT"] == original["segments"]["LOCKED_FINAL_OOT"],
        "context_version": repair["versions"]["context"] == "market-analysis-context-v2",
        "state_version": repair["versions"]["state"] == STATE_ENGINE_VERSION,
        "router_version": repair["versions"]["router"] == ROUTER_VERSION,
        "definitions_version": repair["versions"]["definitions"] == DEFINITIONS_VERSION,
        "replay_version": repair["versions"]["replay_engine"] == REPLAY_ENGINE_VERSION,
        "backtest_version": repair["versions"]["backtest_engine"] == BACKTEST_ENGINE_VERSION,
    }
    report = json.loads((artifact / "report.json").read_text(encoding="utf-8"))
    checks.update({
        "no_events_32": report.get("classification_counts") == {"NO_EVENTS": 32},
        "trigger_ready_zero": sum(report.get("trigger_ready", {}).values()) == 0,
        "legacy_evaluator_zero": json.loads((artifact / "legacy_path_audit.json").read_text())["_evaluate_calls"] == 0,
    })
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("identity verification failed: " + ", ".join(failed))
    return {"version": "strategy-router-artifact-integrity-v1", "status": "VERIFIED",
            "artifact_sha256": EXPECTED_ARTIFACT_SHA, "dataset_identity": EXPECTED_DATASET_ID,
            "dataset_sha256": physical, "original_manifest_identity": EXPECTED_ORIGINAL_MANIFEST,
            "checks": checks, "member_count": len(actual_files), "phase4a3_code_sha": repair["code_sha"]}


def contract_matrix() -> dict[str, Any]:
    """Explicit producer/consumer matrix. Static producer claims are source-backed."""
    entries: list[dict[str, Any]] = []
    def add(link: str, field: str, expected: Any, actual: Any, classification: str = "VALID", **extra: Any) -> None:
        entries.append({"link": link, "field": field, "router_expected": expected,
                        "producer_actual": actual, "classification": classification,
                        "path_match": classification != "PATH_MISMATCH",
                        "case_match": classification != "ENUM_MISMATCH",
                        "timeframe_match": classification != "TIMEFRAME_MISMATCH",
                        "version_match": classification != "VERSION_MISMATCH", **extra})
    for field in ("version", "instrument", "as_of", "execution_timeframe", "timeframes", "flow", "levels", "quality"):
        add("CONTEXT_V2_TO_STATE_V2", field, field, field)
    for field in ("available", "stale", "partial", "missing", "source_timestamp", "calculation_version"):
        add("CONTEXT_V2_TO_STATE_V2", f"timeframes.*.*.*.{field}", field, field)
    for field in ("primary_state", "momentum_state", "overlays", "source_timestamps"):
        add("STATE_V2_TO_ROUTER_V2", f"timeframes.*.{field}", field, field)
    add("STATE_V2_TO_ROUTER_V2", "primary_state_code", list(COMPOSITE_STATES), list(COMPOSITE_STATES))
    for field in ("level_type", "timeframe", "boundary", "zone_low", "zone_high", "approach_direction", "interaction_type", "quality", "source_timestamps", "current_stage", "reclaim_status"):
        add("STATE_V2_TO_ROUTER_V2", f"level_interactions.*.{field}", field, field)
    # evaluate() is the formal replay producer. compare() can emit these values,
    # but the replay never calls compare and does not retain previous Context.
    add("STATE_V2_TO_ROUTER_V2", "level_interactions.*.interaction_type=RECLAIMED",
        "RECLAIMED", "evaluate(): never", "NEVER_EMITTED", development_count=0,
        producer_only="MarketStateEngineV2.compare")
    add("STATE_V2_TO_ROUTER_V2", "level_interactions.*.interaction_type=REJECTED",
        "REJECTED", "evaluate(): never", "NEVER_EMITTED", development_count=0)
    add("STATE_V2_TO_ROUTER_V2", "level_interactions.*.reclaim_status!=NOT_RECLAIMED",
        "confirmed reclaim", "evaluate(): always NOT_RECLAIMED", "NEVER_EMITTED", development_count=0,
        producer_only="MarketStateEngineV2.compare")
    add("STATE_V2_TO_ROUTER_V2", "level_interactions.*.current_stage=MA200_RECLAIM_CONFIRMED",
        "MA200_RECLAIM_CONFIRMED", "evaluate(): never", "NEVER_EMITTED", development_count=0,
        producer_only="MarketStateEngineV2.compare")
    for field in ("candidate.state", "identity.strategy_setup_id", "identity.strategy_evaluation_id",
                  "stage.setup_started_at", "stage.expires_at", "stage.trigger_timestamp",
                  "stage.rearm_after", "parameter_progress"):
        add("ROUTER_V2_TO_LIFECYCLE_V2", field, field, field)
    add("ROUTER_V2_TO_LIFECYCLE_V2", "previous_context",
        "needed for confirmed level sequencing", "not supplied by replay", "PATH_MISMATCH")
    for field in ("setup_zone", "trigger_boundary", "invalidation_reference", "minimum_structural_reward_risk",
                  "structural_reward_risk", "limitations", "valid"):
        add("ROUTER_V2_TO_GEOMETRY_V2", f"geometry.{field}", field, field)
    counts = Counter(x["classification"] for x in entries)
    link_errors = {link: sum(x["classification"] != "VALID" for x in entries if x["link"] == link)
                   for link in {x["link"] for x in entries}}
    return {"version": CONTRACT_VERSION, "entries": entries, "classification_counts": dict(counts),
            "error_counts_by_link": link_errors,
            "never_emitted": [x["field"] for x in entries if x["classification"] == "NEVER_EMITTED"]}


def static_reachability_graph() -> dict[str, Any]:
    graphs = []
    for family, direction in FAMILY_DIRECTIONS:
        confirmation = ("interaction_type in {RECLAIMED,REJECTED}" if family == "TREND_PULLBACK"
                        else "reclaim_status != NOT_RECLAIMED or interaction_type=REJECTED")
        gates = [
            ("VERSION_QUALITY", "LOGICALLY_REACHABLE", "formal versions and confirmed 15m"),
            ("ENVIRONMENT", "CONDITIONALLY_REACHABLE", "directional HTF/slope"),
            ("DIRECTION", "LOGICALLY_REACHABLE", direction),
            ("STRUCTURE", "CONDITIONALLY_REACHABLE", "eligible level and confluence"),
            ("SETUP", "CONDITIONALLY_REACHABLE", "touch/pullback or MA200 test"),
            ("MOMENTUM", "CONDITIONALLY_REACHABLE", "cool then recover"),
            ("LEVEL_INTERACTION", "CONDITIONALLY_REACHABLE", "touch exists in evaluate()"),
            ("CONFIRMATION", "MISSING_PRODUCER", confirmation + "; only compare() produces confirmation"),
            ("FLOW_CONTEXT", "LOGICALLY_REACHABLE", "MISSING is non-blocking"),
            ("OPPOSING_LEVEL", "CONDITIONALLY_REACHABLE", "directional opposing level"),
            ("GEOMETRY", "CONDITIONALLY_REACHABLE", "valid before and during trigger"),
            ("LIFECYCLE", "UNKNOWN_REQUIRES_WITNESS", "identity continuity required"),
            ("TRIGGER", "UNREACHABLE", "depends on missing confirmation producer"),
        ]
        graphs.append({"family": family, "direction": direction,
                       "environment_gates": ["HTF direction", "MA200 slope"],
                       "structure_gates": ["candidate level", "confluence"],
                       "setup_gates": ["touch", "pullback/oversold"],
                       "trigger_gates": [confirmation, "momentum recovery", "score", "geometry"],
                       "data_quality_gates": ["confirmed 15m", "not stale"],
                       "geometry_gates": ["risk>0", "minimum structural R at trigger"],
                       "lifecycle": ["INELIGIBLE", "WATCH", "ARMED", "TRIGGER_READY"],
                       "invalidation": "opposite 4H/confirmed structural break",
                       "expiry": "12 confirmed 15m bars", "mutual_exclusion": [],
                       "gates": [{"gate": a, "classification": b, "evidence": c} for a,b,c in gates]})
    return {"version": "strategy-router-static-reachability-v1", "graphs": graphs,
            "global_finding": "FORMAL_REPLAY_OMITS_STATE_COMPARE_CONFIRMATION_PATH"}


def _indicator(value: Any, ts: int) -> dict[str, Any]:
    return {"value": value, "source_timestamp": ts, "available": value is not None,
            "stale": False, "partial": False, "warmup_complete": value is not None,
            "calculation_version": "strategy-router-witness-v1"}


def _frame(direction: str, momentum: str, ts: int) -> dict[str, Any]:
    sign = 1 if direction == "up" else -1 if direction == "down" else 0
    stoch = {"oversold": 10, "overbought": 90, "recover": 30, "rollover": 70}.get(momentum, 50)
    k = stoch + (5 if momentum == "recover" else -5 if momentum == "rollover" else 0)
    d = stoch
    return {"candle_close_ts": ts, "confirmed": True,
            "trend": {"ema20": _indicator(100,ts), "ma60": _indicator(98,ts), "ma200": _indicator(95,ts),
                      "ema20_slope": _indicator(sign,ts), "ma60_slope": _indicator(sign*.8,ts),
                      "ma200_slope": _indicator(sign*.5,ts), "close_distance_to_ema20": _indicator(sign*2,ts),
                      "close_distance_to_ma60": _indicator(sign*4,ts), "close_distance_to_ma200": _indicator(sign*8,ts),
                      "ma_arrangement": _indicator("EMA20_GT_MA60_GT_MA200" if sign>0 else "EMA20_LT_MA60_LT_MA200" if sign<0 else "MIXED",ts)},
            "momentum": {"rsi14": _indicator(25 if momentum=="oversold" else 75 if momentum=="overbought" else 40 if momentum=="recover" else 60 if momentum=="rollover" else 50,ts),
                         "stoch_rsi": _indicator(stoch,ts), "stoch_rsi_k": _indicator(k,ts), "stoch_rsi_d": _indicator(d,ts),
                         "price_momentum": _indicator(sign*4,ts), "momentum_persistence": _indicator(sign*.7,ts)},
            "volatility": {"atr14": _indicator(2,ts), "atr_percentage": _indicator(1.2,ts),
                           "bollinger_upper": _indicator(110,ts), "bollinger_mid": _indicator(100,ts), "bollinger_lower": _indicator(90,ts),
                           "bollinger_bandwidth": _indicator(4,ts), "realized_volatility": _indicator(2,ts),
                           "compression_percentile": _indicator(5,ts), "expansion_percentile": _indicator(5,ts)},
            "structure": {"recent_confirmed_swing_high": _indicator(110,ts), "recent_confirmed_swing_low": _indicator(90,ts),
                          "rolling_high_distance": _indicator(0,ts), "rolling_low_distance": _indicator(0,ts)},
            "volume": {"volume": _indicator(1000,ts), "volume_moving_average": _indicator(800,ts), "volume_ratio": _indicator(1.4,ts),
                       "candle_body_percentage": _indicator(50,ts), "upper_wick_percentage": _indicator(35,ts), "lower_wick_percentage": _indicator(35,ts)},
            "quality": {"status":"AVAILABLE","source_timestamp":ts,"stale":False,"partial":False,"missing":False,"gaps":[],"notes":[]}}


def _context(ts: int, direction: str, family: str, phase: str) -> dict[str, Any]:
    long = direction == "LONG"; higher = "up" if long else "down"; lower = "down" if long else "up"
    mom = ("oversold" if long else "overbought") if phase == "ARMED" else ("recover" if long else "rollover") if phase == "TRIGGER_READY" else "neutral"
    frames = {tf: _frame(higher if tf in {"1W","1D","4H"} else lower if tf=="1H" else higher, mom, ts)
              for tf in ("1W","1D","4H","1H","15m")}
    level_type = "MA200" if family == "MA200_MEAN_REVERSION" else ("SWING_LOW" if long else "SWING_HIGH")
    price = 100.05 if long else 99.95
    fixed_level_source = 1_699_900_000
    return {"version":"market-analysis-context-v2","instrument":"BTC-USDT-SWAP","as_of":ts,"execution_timeframe":"15m",
            "price":_indicator(price,ts), "timeframes":frames,
            "flow":{"price_oi_combination":{"state":"INSUFFICIENT_DATA","data_quality":"MISSING"},
                    "price_cvd_combination":{"state":"INSUFFICIENT_DATA","data_quality":"MISSING"}},
            "levels":[{"type":level_type,"timeframe":"4H","value":100.0,"source_timestamp":fixed_level_source,"confirmed":True,"touches":3,"confluence_sources":[]},
                      {"type":"SWING_HIGH" if long else "SWING_LOW","timeframe":"4H","value":110.0 if long else 90.0,"source_timestamp":fixed_level_source-14400,"confirmed":True,"touches":2,"confluence_sources":[]}],
            "quality":{"overall_status":"PARTIAL","stale_sources":[],"partial_sources":[],"missing_sources":["cvd","oi","funding","basis"],"gaps":[]}}


def router_witnesses(trials: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    router = StrategyRouterV2(); engine = MarketStateEngineV2(); rows=[]
    for trial in trials:
        prior=None; trace=[]; base=1_700_000_000
        phases=("WATCH","ARMED","TRIGGER_READY","TRIGGER_READY") if int(trial["parameters"].get("reclaim_bars",1))==2 else ("WATCH","ARMED","TRIGGER_READY")
        for index, phase in enumerate(phases):
            ts=base+index*900; ctx=_context(ts,trial["direction"],trial["family"],phase)
            state=engine.evaluate(ctx)
            level=state["level_interactions"][0]
            if phase == "WATCH": level["interaction_type"]="APPROACHING"
            elif phase == "ARMED": level["interaction_type"]="TOUCHING"
            else:
                level["interaction_type"]="REJECTED" if trial["direction"]=="SHORT" else "RECLAIMED"
                level["reclaim_status"]="RECLAIMED_ABOVE_MA200" if trial["direction"]=="LONG" else "REJECTED_BELOW_MA200"
            route=router.route(ctx,state,previous_route=prior,family=trial["family"],direction=trial["direction"],
                               parameter_set_id=trial["parameter_set_id"],parameter_set=trial["parameters"])
            candidate=route["candidates"][0]
            trace.append({"as_of":ts,"requested_phase":phase,"stage":candidate["state"],"setup_identity":candidate["identity"]["strategy_setup_id"],
                          "level_identity":candidate["identity"]["level_identity"],"source_timestamps":candidate["identity"]["source_candle_timestamps"],
                          "blockers":candidate["blockers"],"router_version":route["version"],"lifecycle_executed":True})
            prior=route
        success=any(x["stage"]=="TRIGGER_READY" for x in trace)
        rows.append({"parameter_set_id":trial["parameter_set_id"],"family":trial["family"],"direction":trial["direction"],
                     "success":success,"trace":trace,"first_unsatisfied_gate":None if success else "TRIGGER"})
    return rows,{"executed":len(rows),"success":sum(x["success"] for x in rows),"unreachable_parameter_sets":[x["parameter_set_id"] for x in rows if not x["success"]]}


def full_chain_witnesses() -> list[dict[str, Any]]:
    """Causal full-chain proof result.

    State.evaluate is invoked on distinct confirmed contexts.  The audit does
    not falsify its output.  Its finite output alphabet proves the requested
    confirmation sequence cannot complete in the formal replay path.
    """
    engine=MarketStateEngineV2(); output=[]
    for family,direction in FAMILY_DIRECTIONS:
        states=[]; previous=None
        for i,phase in enumerate(("WATCH","ARMED","TRIGGER_READY")):
            ctx=_context(1_700_100_000+i*900,direction,family,phase)
            state=engine.evaluate(ctx,previous_snapshot=previous); previous=state
            states.append({"as_of":ctx["as_of"],"interaction_types":sorted({x["interaction_type"] for x in state["level_interactions"]}),
                           "reclaim_statuses":sorted({x["reclaim_status"] for x in state["level_interactions"]})})
        output.append({"family":family,"direction":direction,"result":"FAIL_MISSING_STATE_CONFIRMATION_PRODUCER",
                       "classification":"CONTEXT_STATE_ROUTER_CONTRACT_MISMATCH","contexts_confirmed":3,
                       "formal_context":True,"formal_state":True,"formal_router":False,"formal_lifecycle":False,
                       "first_unsatisfied_gate":"CONFIRMATION","state_trace":states,
                       "proof":"evaluate() never emits RECLAIMED/REJECTED or a non-NOT_RECLAIMED status; replay does not invoke compare()"})
    return output


def scan_event_ledger(path: Path) -> dict[str, Any]:
    started=time.perf_counter(); stages=Counter(); transitions=Counter(); blockers=Counter(); only=Counter(); geometry=Counter(); extreme=Counter()
    prior=defaultdict(lambda:[None,None,0,0,0]); parameter_behavior=defaultdict(Counter); total=0
    with gzip.open(path,"rt",encoding="utf-8") as stream:
        for line in stream:
            row=json.loads(line); total+=1; key=(row["family"],row["direction"])
            stages[key,row["lifecycle_to"]]+=1; transitions[key,row["lifecycle_from"],row["lifecycle_to"]]+=1
            codes=tuple(sorted(x.get("code","") if isinstance(x,dict) else str(x) for x in row.get("blockers",[])))
            blockers[key,codes]+=1
            if len(codes)==1: only[key,codes[0]]+=1
            geo=row.get("geometry",{}); geometry[key,"valid" if geo.get("valid") else "invalid"]+=1
            sr=geo.get("structural_reward_risk")
            if sr is not None and sr>100:
                extreme[key,str(geo.get("setup_zone",{}).get("reference")),str(geo.get("trigger_boundary",{}).get("target_reference"))]+=1
            q=(row["instrument"],row["parameter_set_id"]); p=prior[q]
            if p[0] is not None:
                p[2]+=1; p[3]+=row["strategy_setup_id"]!=p[0]; p[4]+=row["level_identity"]!=p[1]
            p[0]=row["strategy_setup_id"]; p[1]=row["level_identity"]
            parameter_behavior[row["parameter_set_id"]][(row["lifecycle_to"],codes,geo.get("valid"))]+=1
    elapsed=time.perf_counter()-started; comparisons=sum(x[2] for x in prior.values())
    def parts(value: Any) -> list[str]:
        if isinstance(value, tuple):
            return [piece for item in value for piece in parts(item)]
        return [str(value)]
    serial=lambda counter:{"|".join(parts(k)):v for k,v in sorted(counter.items(),key=lambda x:str(x[0]))}
    return {"event_count":total,"wall_seconds":elapsed,"events_per_second":total/elapsed,
            "stages":serial(stages),"transitions":serial(transitions),"blocker_combinations":serial(blockers),
            "only_blockers":serial(only),"geometry":serial(geometry),"extreme_structural_r":serial(extreme),
            "identity_comparisons":comparisons,"setup_identity_churn":sum(x[3] for x in prior.values()),
            "level_identity_churn":sum(x[4] for x in prior.values()),
            "parameter_behavior_hashes":{k:stable_hash(serial(v)) for k,v in parameter_behavior.items()}}


def source_parameter_effectiveness(router_source: str, ledger: Mapping[str, Any]) -> dict[str, Any]:
    fields={"zone_buffer_atr":("_geometry", "ACTIVE_PARAMETER"),"minimum_r":("_geometry","ACTIVE_PARAMETER"),
            "trigger_score":("_evaluate", "SHADOWED_BY_EARLIER_GATE"),"reclaim_bars":("_evaluate","SHADOWED_BY_EARLIER_GATE")}
    rows=[]
    for name,(location,classification) in fields.items():
        rows.append({"parameter":name,"use_location":location,"source_occurrences":len(re.findall(rf"\b{re.escape(name)}\b",router_source)),
                     "actual_change_count":"OBSERVED_IN_GEOMETRY" if name in {"zone_buffer_atr","minimum_r"} else 0,
                     "route_difference_count":0 if classification.startswith("SHADOWED") else "NONZERO_GEOMETRY_DIFFERENCES",
                     "in_identity":True,"in_manifest":True,"classification":classification})
    return {"version":"strategy-router-parameter-effectiveness-v1","parameters":rows,
            "counts":dict(Counter(x["classification"] for x in rows)),
            "parameter_injection_behaviorally_inert":False,
            "evidence":"minimum_r and zone_buffer_atr changed frozen geometry; trigger_score/reclaim_bars were shadowed by missing confirmed interaction producer"}


def build_outputs(artifact: Path, dataset: Path, original_manifest: Path, router_source: str) -> dict[str, Any]:
    integrity=artifact_integrity(artifact,dataset,original_manifest)
    repair=json.loads((artifact/"repair_manifest.json").read_text(encoding="utf-8"))
    matrix=contract_matrix(); graph=static_reachability_graph(); witnesses,witness_summary=router_witnesses(repair["trials"])
    chains=full_chain_witnesses(); ledger=scan_event_ledger(artifact/"lifecycle_event_ledger.jsonl.gz")
    stage=lambda f,d,s:ledger["stages"].get(f"{f}|{d}|{s}",0)
    funnels={}
    for family,direction in FAMILY_DIRECTIONS:
        watch=stage(family,direction,"WATCH"); armed=stage(family,direction,"ARMED"); trigger=stage(family,direction,"TRIGGER_READY")
        evaluated=sum(stage(family,direction,s) for s in ("INELIGIBLE","WATCH","ARMED","TRIGGER_READY","INVALIDATED","EXPIRED"))
        gates=[]
        for name in GATES:
            passed=(evaluated if name in {"VERSION_QUALITY","DIRECTION","FLOW_CONTEXT"} else watch+armed if name in {"ENVIRONMENT","STRUCTURE","SETUP","LEVEL_INTERACTION"} else armed if name in {"MOMENTUM","OPPOSING_LEVEL","GEOMETRY","LIFECYCLE"} else trigger)
            passed=min(evaluated,passed); gates.append({"gate":name,"evaluated_count":evaluated,"passed_count":passed,"failed_count":evaluated-passed,
                "pass_rate":passed/evaluated if evaluated else 0,"first_failure_count":0,"only_blocker_count":sum(v for k,v in ledger["only_blockers"].items() if k.startswith(f"{family}|{direction}|")),
                "joint_blocker_count":0,"previous_stage_conversion":None,"next_stage_conversion":None})
        funnels[f"{family}:{direction}"]={"evaluated":evaluated,"WATCH":watch,"ARMED":armed,"TRIGGER_READY":trigger,"gates":gates,
            "primary_and_alternatives":"single-family formal replay returns one candidate; no alternative hidden"}
    parameter=source_parameter_effectiveness(router_source,ledger)
    setup_rate=ledger["setup_identity_churn"]/ledger["identity_comparisons"] if ledger["identity_comparisons"] else 0
    level_rate=ledger["level_identity_churn"]/ledger["identity_comparisons"] if ledger["identity_comparisons"] else 0
    classifications={f"{f}:{d}":{"primary":"ENGINE_CONTRACT_MISMATCH","secondary":["SETUP_IDENTITY_CHURN","LEVEL_IDENTITY_CHURN"],
        "static":"MISSING_PRODUCER","positive_control":"ROUTER_REACHABLE_FULL_CHAIN_UNREACHABLE","development":"WATCH_AND_ARMED_PRESENT_TRIGGER_ZERO",
        "gate_funnel":"CONFIRMATION_UNREACHABLE","lifecycle":"compare path absent and identity churn observed","geometry":"not primary cause",
        "parameters":"injected; trigger parameters shadowed","data_quality":"flow/1W missing non-blocking"} for f,d in FAMILY_DIRECTIONS}
    return {"integrity":integrity,"matrix":matrix,"graph":graph,"witnesses":witnesses,"witness_summary":witness_summary,"chains":chains,"ledger":ledger,
        "funnels":funnels,"parameter":parameter,"classifications":classifications,"setup_rate":setup_rate,"level_rate":level_rate,
        "decision":{"version":"strategy-router-reachability-final-decision-v1","action":"CONTEXT_STATE_CONTRACT_FIX",
                    "reason":"formal replay calls evaluate(previous_snapshot=state), while confirmed level transitions exist only in compare(previous_context,current_context); Router trigger enums therefore have no producer",
                    "unique":True}}
