"""Read-only forensic audit of the frozen Phase 4A Development evidence.

The audit deliberately does not import the Phase 4A replay evaluator.  Route
samples are rebuilt through the real Market Context V2, Market State V2 and
Strategy Router V2 path, while fills are independently recalculated from OHLCV.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import gzip
import hashlib
import heapq
import bisect
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .market_context_v2 import BoundedMarketDataReaderV2, MarketContextServiceV2
from .market_state_v2 import MarketStateEngineV2
from .strategy_router_v2 import StrategyRouterV2


AUDIT_VERSION = "strategy-phase4a-forensic-audit-v1"
REPORT_VERSION = "strategy-phase4a-audit-report-v1"
SEED = 20260804
EXPECTED_ARTIFACT_SHA = "5b757fd8f2ca0b9ed4194de94b104b47382550420cb98432b84f44995fd26d18"
EXPECTED_DATASET_ID = "e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062"
EXPECTED_DATABASE_SHA = "9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3"
EXPECTED_CODE_SHA = "8f7cf55675e3adbe9aaae924978b7de80eef4f80"
EXPECTED_ENGINE = "strategy-backtest-engine-v2.0.4"
GROUPS = tuple((family, direction) for family in ("TREND_PULLBACK", "MA200_MEAN_REVERSION")
               for direction in ("LONG", "SHORT"))
STAGES = ("WATCH", "ARMED", "TRIGGER_READY")
VALIDATION_START = 1739681100
DISCLAIMER = "DIAGNOSTIC_ONLY; does not change Phase 4A classification or create a candidate."


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_sha(root: Path) -> tuple[str, dict[str, str]]:
    hashes = {path.name: file_sha256(path) for path in sorted(root.iterdir())
              if path.is_file() and path.name != "report.json"}
    return stable_hash(hashes), hashes


@dataclass(frozen=True)
class EvidenceBundle:
    root: Path
    manifest: dict[str, Any]
    report: dict[str, Any]
    trials: list[dict[str, Any]]
    file_hashes: dict[str, str]

    @classmethod
    def verify(cls, root: Path | str, dataset: Path | str, bug_files: Sequence[Path | str]) -> "EvidenceBundle":
        root = Path(root).resolve(); dataset = Path(dataset).resolve()
        required = {"manifest.json", "trial_ledger.json", "trade_ledger.jsonl",
                    "event_ledger.jsonl.gz", "aggregate_metrics.json", "checkpoint.json", "report.json"}
        if not root.is_dir() or required - {path.name for path in root.iterdir()}:
            raise ValueError("Phase 4A artifact is incomplete")
        digest, hashes = artifact_sha(root)
        if digest != EXPECTED_ARTIFACT_SHA: raise ValueError("Phase 4A artifact identity mismatch")
        manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        claimed = manifest.pop("manifest_identity")
        if stable_hash(manifest) != claimed: raise ValueError("manifest identity mismatch")
        manifest["manifest_identity"] = claimed
        report = json.loads((root/"report.json").read_text(encoding="utf-8"))
        trials = json.loads((root/"trial_ledger.json").read_text(encoding="utf-8"))
        checks = (
            (manifest["dataset"]["identity"] == EXPECTED_DATASET_ID, "dataset identity mismatch"),
            (report["dataset_identity"] == EXPECTED_DATASET_ID, "report dataset identity mismatch"),
            (report["code_sha"] == EXPECTED_CODE_SHA, "code SHA mismatch"),
            (report["backtest_engine_version"] == EXPECTED_ENGINE, "engine version mismatch"),
            (len(trials) == report["raw_trial_count"] == 32, "trial count mismatch"),
            (all(item.get("validation") is None for item in trials), "Validation result present"),
            (report["oot_access_audit"]["accessed"] is False, "OOT access audit failed"),
            (file_sha256(dataset) == EXPECTED_DATABASE_SHA, "database bytes mismatch"),
        )
        for passed, message in checks:
            if not passed: raise ValueError(message)
        for path in bug_files:
            bug = json.loads(Path(path).read_text(encoding="utf-8"))
            if bug.get("affected_run_status") != "INVALIDATED_ENGINE_BUG":
                raise ValueError("old run is not invalidated")
        return cls(root, manifest, report, trials, hashes)

    def events(self) -> Iterator[dict[str, Any]]:
        with gzip.open(self.root/"event_ledger.jsonl.gz", "rt", encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if event.get("segment") != "DEVELOPMENT":
                    raise PermissionError("non-Development event refused")
                if int(event["context_timestamp"]) >= VALIDATION_START:
                    raise PermissionError("Validation/OOT event refused")
                yield event

    def trades(self) -> list[dict[str, Any]]:
        output = []
        with (self.root/"trade_ledger.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                trade = json.loads(line)
                if trade.get("segment") != "DEVELOPMENT" or int(trade["entry_ts"]) >= VALIDATION_START:
                    raise PermissionError("non-Development trade refused")
                output.append(trade)
        return output


class DevelopmentCandleStore:
    """Hard-bound read-only access. The end is exclusive and cannot cross Development."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self._cache: dict[tuple[str,str],tuple[list[int],list[dict[str,Any]]]] = {}

    def _guard(self, start: int, end: int) -> None:
        if start >= VALIDATION_START or end > VALIDATION_START:
            raise PermissionError("Validation/OOT candle read refused")

    def rows(self, instrument: str, timeframe: str, start: int, end: int) -> list[dict[str, Any]]:
        self._guard(start, end)
        widths = {"15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
        key=(instrument.removesuffix("-SWAP"),timeframe)
        if key not in self._cache:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro&immutable=1", uri=True)
            connection.row_factory = sqlite3.Row; connection.execute("PRAGMA query_only=ON")
            selected = connection.execute(
                """SELECT ts,open,high,low,close,volume,confirmed FROM historical_candles
                   WHERE instrument=? AND timeframe=? AND ts<? AND confirmed=1 ORDER BY ts""",
                (key[0], timeframe, VALIDATION_START)).fetchall();connection.close()
            rows=[{**dict(row),"candle_close_ts":int(row["ts"])+widths[timeframe]} for row in selected]
            self._cache[key]=([int(row["ts"]) for row in rows],rows)
        timestamps,rows=self._cache[key]
        return rows[bisect.bisect_left(timestamps,start):bisect.bisect_left(timestamps,end)]


def deterministic_samples(events: Iterable[dict[str, Any]], per_bucket: int = 25) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    heaps: dict[tuple[str, str, str], list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    stage_counts: Counter[tuple[str, str, str]] = Counter(); blockers: Counter[tuple[str, str, str]] = Counter()
    level_counts: Counter[tuple[str, str, str]] = Counter(); regime_counts: Counter[tuple[str, str, str]] = Counter()
    level_stage_counts: Counter[tuple[str,str,str,str]] = Counter()
    waits: defaultdict[tuple[str, str, str], list[float]] = defaultdict(list)
    setup_ids: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    special_events: list[dict[str, Any]] = []
    for event in events:
        key2 = (event["family"], event["direction"]); stage = event["lifecycle_to"]
        stage_counts[key2+(stage,)] += 1; setup_ids[key2].add(event["setup_identity"])
        for blocker in event.get("blockers", []): blockers[key2+(blocker,)] += 1
        zone = event.get("geometry", {}).get("setup_zone") or {}
        level_counts[key2+(str(zone.get("type", "NONE")),)] += 1
        level_stage_counts[key2+(str(zone.get("timeframe", "NONE")),stage)] += 1
        for regime in event.get("geometry", {}).get("regime_tags", []): regime_counts[key2+(regime,)] += 1
        if event.get("setup_timestamp") is not None:
            waits[key2+(stage,)].append((int(event["context_timestamp"])-int(event["setup_timestamp"]))/900)
        bucket = key2+(stage,)
        # The protocol requires exhaustive inspection of lifecycle terminal
        # events, not a result-selected sample. Gap events live in the trade
        # ledger; every expiry/invalidation transition is retained here.
        if stage in {"EXPIRED", "INVALIDATED"}:
            special_events.append(event)
        if stage in STAGES:
            rank = int(stable_hash({"seed": SEED, "event": event["event_identity"]}), 16)
            entry = (-rank, event["event_identity"], event)
            heap = heaps[bucket]
            if len(heap) < per_bucket: heapq.heappush(heap, entry)
            elif entry > heap[0]: heapq.heapreplace(heap, entry)
    samples = [item[2] for heap in heaps.values() for item in heap] + special_events
    funnel: dict[str, Any] = {}
    for family, direction in GROUPS:
        key = f"{family}:{direction}"; counts = {stage: stage_counts[(family, direction, stage)] for stage in
            ("INELIGIBLE", "WATCH", "ARMED", "TRIGGER_READY", "INVALIDATED", "EXPIRED", "COOLDOWN_RESEARCH_ONLY")}
        funnel[key] = {
            "evaluated_candles": 1_101_744, "transition_counts": counts,
            "unique_setup_identities": len(setup_ids[(family, direction)]),
            "blockers": {name: value for (f, d, name), value in blockers.items() if (f, d)==(family, direction)},
            "level_sources": {name: value for (f, d, name), value in level_counts.items() if (f, d)==(family, direction)},
            "level_timeframe_stages": {f"{timeframe}:{stage}":value for (f,d,timeframe,stage),value in level_stage_counts.items() if (f,d)==(family,direction)},
            "regime_tags": {name: value for (f, d, name), value in regime_counts.items() if (f, d)==(family, direction)},
            "wait_bars": {stage: distribution(waits[(family, direction, stage)]) for stage in STAGES},
            "limitation": "event ledger contains transitions, not per-candle state occupancy or rejected-entry ledger",
        }
    return sorted(samples, key=lambda x: (x["family"],x["direction"],x["lifecycle_to"],x["event_identity"])), funnel


def distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values: return {"count": 0, "mean": None, "median": None, "p90": None}
    ordered=sorted(float(x) for x in values); index=min(len(ordered)-1, math.ceil(.9*len(ordered))-1)
    return {"count":len(ordered),"mean":statistics.fmean(ordered),"median":statistics.median(ordered),"p90":ordered[index],
            "minimum":ordered[0],"maximum":ordered[-1]}


def actual_v2_route_audit(samples: Sequence[dict[str, Any]], database: Path | str) -> list[dict[str, Any]]:
    service = MarketContextServiceV2(BoundedMarketDataReaderV2(database))
    state_engine = MarketStateEngineV2(); router = StrategyRouterV2(); output=[]
    for event in samples:
        context = service.context(event["instrument"], as_of=int(event["context_timestamp"]))
        state = state_engine.evaluate(context); route = router.route(context, state)
        candidate = next(item for item in route["candidates"] if item["family"]==event["family"] and item["direction"]==event["direction"])
        ledger_zone=(event.get("geometry",{}).get("setup_zone") or {})
        actual_zone=(candidate.get("geometry",{}).get("setup_zone") or {})
        output.append({
            "sample_identity":stable_hash({"audit":AUDIT_VERSION,"event":event["event_identity"]}),
            "event_identity":event["event_identity"],"family":event["family"],"direction":event["direction"],
            "instrument":event["instrument"],"fold":development_fold(int(event["context_timestamp"])),
            "ledger_stage":event["lifecycle_to"],"actual_router_stage":candidate["state"],
            "stage_match":candidate["state"]==event["lifecycle_to"],
            "ledger_setup_identity":event["setup_identity"],"actual_setup_identity":candidate["identity"]["strategy_setup_id"],
            "setup_identity_match":event["setup_identity"]==candidate["identity"]["strategy_setup_id"],
            "ledger_evaluation_identity":event["evaluation_identity"],"actual_evaluation_identity":candidate["identity"]["strategy_evaluation_id"],
            "evaluation_identity_match":event["evaluation_identity"]==candidate["identity"]["strategy_evaluation_id"],
            "ledger_level_identity":event["level_identity"],"actual_level_identity":candidate["identity"]["level_identity"],
            "level_identity_match":event["level_identity"]==candidate["identity"]["level_identity"],
            "trigger_match":event.get("trigger_timestamp")==candidate["identity"].get("trigger_timestamp"),
            "source_timestamps_causal":max(event["source_candle_timestamps"])<=event["context_timestamp"] and max(candidate["source_timestamps"])<=event["context_timestamp"],
            "ledger_level_type":ledger_zone.get("type"),"actual_level_reference":actual_zone.get("reference"),
            "ledger_geometry":event.get("geometry"),"actual_geometry":candidate.get("geometry"),
            "context_version":context["version"],"state_version":state["version"],"router_version":route["version"],
            "actual_primary_state":state["primary_state_code"],"diagnostic_only":True,
        })
    return output


def development_fold(ts: int) -> int:
    start,end=1698365700,VALIDATION_START; width=end-start
    return min(4,max(1,int((ts-start)*4/width)+1))


def independent_trade_audit(trades: Sequence[dict[str, Any]], event_by_id: Mapping[str, dict[str, Any]],
                            store: DevelopmentCandleStore, per_group: int = 20) -> list[dict[str, Any]]:
    chosen=[]
    for family,direction in GROUPS:
        group=[trade for trade in trades if (trade["family"],trade["direction"])==(family,direction)]
        ranked=sorted(group,key=lambda x:stable_hash({"seed":SEED,"trade":x["trade_identity"]}))
        extremes=sorted(group,key=lambda x:float(x["r"]))[:3]+sorted(group,key=lambda x:float(x["r"]),reverse=True)[:3]
        unique={x["trade_identity"]:x for x in ranked[:per_group]+extremes};chosen.extend(unique.values())
    output=[]
    for trade in chosen:
        event=event_by_id[trade["event_id"]]; start=int(trade["entry_ts"])-900
        end=min(VALIDATION_START,int(trade["entry_ts"])+(int(trade["maximum_holding_bars"])+2)*900)
        rows=store.rows(trade["instrument"],"15m",start,end); by_open={int(x["ts"]):x for x in rows}
        trigger_row=by_open[int(event["trigger_timestamp"])-900]; entry_row=by_open[int(trade["entry_ts"])]
        side=trade["direction"]; raw=float(entry_row["open"])
        entry=raw*(1.0003 if side=="LONG" else .9997)
        expected=simulate_exit(rows[1:],trade,entry)
        entry_fee=entry*float(trade["units"])*.0005
        exit_fee=expected["exit"]*float(trade["units"])*.0005
        gross=(expected["exit"]-entry)*float(trade["units"])*(1 if side=="LONG" else -1)
        net=gross-entry_fee-exit_fee; r=net/float(trade["actual_risk"])
        checks={
            "trigger_timestamp":int(event["trigger_timestamp"])==int(trade["entry_ts"]),
            "entry_timestamp":int(entry_row["ts"])==int(trade["entry_ts"]),
            "entry_price":close(entry,float(trade["entry"])),
            "stop":close(float(event["geometry"]["stop"]),float(trade["stop"])),
            "target":close(float(event["geometry"]["target"]),float(trade["target"])),
            "exit":close(expected["exit"],float(trade["exit"])),
            "exit_timestamp":int(expected["exit_ts"])==int(trade["exit_ts"]),
            "exit_reason":expected["reason"]==trade["exit_reason"],
            "fee":close(entry_fee+exit_fee,float(trade["fees"])),
            "slippage":close(abs(entry-raw)*float(trade["units"])+expected["exit_slippage"],float(trade["slippage_drag"])),
            "pnl_usdt":close(net,float(trade["net_pnl"])),"pnl_r":close(r,float(trade["r"])),
        }
        output.append({"trade_identity":trade["trade_identity"],"event_identity":event["event_identity"],
                       "family":trade["family"],"direction":side,"instrument":trade["instrument"],
                       "fold":development_fold(int(trade["entry_ts"])),"checks":checks,
                       "all_execution_checks_match":all(checks.values()),"recomputed":{**expected,"entry":entry,"fees":entry_fee+exit_fee,"net_pnl":net,"r":r},
                       "trigger_close":float(trigger_row["close"]),"diagnostic_only":True})
    return sorted(output,key=lambda x:x["trade_identity"])


def close(a: float,b: float) -> bool:
    return math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8)


def simulate_exit(rows: Sequence[Mapping[str,Any]], trade: Mapping[str,Any], entry: float) -> dict[str,Any]:
    side=trade["direction"]; stop=float(trade["stop"]);target=float(trade["target"]); bars=0
    for row in rows:
        raw_open=float(row["open"]);ts=int(row["ts"]);close_ts=ts+900
        stop_gap=raw_open<=stop if side=="LONG" else raw_open>=stop
        target_gap=raw_open>=target if side=="LONG" else raw_open<=target
        if stop_gap:return {"exit":raw_open,"exit_ts":ts,"reason":"GAP_STOP","exit_slippage":0.0}
        if target_gap:return {"exit":min(raw_open,target) if side=="LONG" else max(raw_open,target),"exit_ts":ts,"reason":"GAP_TARGET","exit_slippage":0.0}
        hit_stop=float(row["low"])<=stop if side=="LONG" else float(row["high"])>=stop
        hit_target=float(row["high"])>=target if side=="LONG" else float(row["low"])<=target
        bars+=1
        if hit_stop:
            value=stop*(.9997 if side=="LONG" else 1.0003)
            return {"exit":value,"exit_ts":close_ts,"reason":"STOP_FIRST" if hit_target else "STOP","exit_slippage":abs(value-stop)*float(trade["units"])}
        if hit_target:
            value=target*(.9997 if side=="LONG" else 1.0003)
            return {"exit":value,"exit_ts":close_ts,"reason":"TARGET","exit_slippage":abs(value-target)*float(trade["units"])}
        if bars>=int(trade["maximum_holding_bars"]):
            reference=float(row["close"]);value=reference*(.9997 if side=="LONG" else 1.0003)
            return {"exit":value,"exit_ts":close_ts,"reason":"TIMEOUT","exit_slippage":abs(value-reference)*float(trade["units"])}
    raise ValueError("independent execution window ended without exit")


def trade_diagnostics(trades: Sequence[dict[str,Any]], events: Mapping[str,dict[str,Any]], store: DevelopmentCandleStore) -> dict[str,Any]:
    result={}
    for family,direction in GROUPS:
        group=[t for t in trades if (t["family"],t["direction"])==(family,direction)]
        latency=[]; gross_r=[]; structural=[];stop_d=[];target_d=[];holds=[];categories=Counter();monthly=Counter();continuous_losses=0;run=0;max_run=0
        gross_absent=cost_consumed=mixed=cap=0; forward=defaultdict(list)
        for trade in group:
            event=events[trade["event_id"]]; side=1 if direction=="LONG" else -1
            rows=store.rows(trade["instrument"],"15m",int(trade["entry_ts"])-900,min(VALIDATION_START,int(trade["entry_ts"])+8*900+1))
            trigger_close=float(rows[0]["close"]);raw_open=float(trade["raw_entry_open"])
            latency.append(side*(raw_open-trigger_close)/trigger_close*10_000)
            for horizon in (1,2,4,8):
                if len(rows)>horizon:forward[horizon].append(side*(float(rows[horizon]["close"])/trigger_close-1)*100)
            risk=float(trade["actual_risk"]);gross=float(trade["gross_pnl"])/risk;gross_r.append(gross)
            if float(trade["gross_pnl"])<=0:gross_absent+=1
            elif float(trade["net_pnl"])<=0:cost_consumed+=1
            else:mixed+=1
            cap+=float(trade["actual_risk"])<float(trade["requested_risk"])*.999
            structural.append(float(trade["structural_r"]));stop_d.append(abs(float(trade["entry"])-float(trade["stop"]))/float(trade["entry"])*100)
            target_d.append(abs(float(trade["target"])-float(trade["entry"]))/float(trade["entry"])*100);holds.append(int(trade["bars"]));monthly[int(trade["entry_ts"])//(30*86400)]+=1
            mfe=float(trade["mfe"]);reason=str(trade["exit_reason"])
            if reason in {"STOP","STOP_FIRST","GAP_STOP"} and int(trade["bars"])<=2:cat="IMMEDIATE_FAILURE"
            elif reason in {"STOP","STOP_FIRST","GAP_STOP"} and mfe>=1:cat="LARGE_MFE_GIVEN_BACK"
            elif reason in {"STOP","STOP_FIRST","GAP_STOP"} and mfe>0:cat="SMALL_MFE_THEN_STOP"
            elif reason in {"STOP","STOP_FIRST","GAP_STOP"}:cat="NEVER_REACHED_MEANINGFUL_MFE"
            elif reason in {"TIMEOUT","SEGMENT_END"}:cat="TIMEOUT_WITH_POSITIVE_MFE" if mfe>0 else "TIMEOUT_WITH_NEGATIVE_MFE"
            elif float(trade["gross_pnl"])>0>=float(trade["net_pnl"]):cat="COST_ONLY_LOSS"
            else:cat="OTHER"
            categories[cat]+=1
            if float(trade["net_pnl"])<0:run+=1;max_run=max(max_run,run)
            else:run=0
        key=f"{family}:{direction}";unique_entries=len({(t["instrument"],t["entry_ts"]) for t in group})
        result[key]={"trade_count":len(group),"unique_instrument_entry_events":unique_entries,
            "raw_to_unique_trade_density":len(group)/unique_entries if unique_entries else None,
            "trigger_latency_bps":distribution(latency),"forward_return_pct":{str(k):distribution(v) for k,v in forward.items()},
            "gross_expectancy_r":statistics.fmean(gross_r) if gross_r else None,"net_expectancy_r":statistics.fmean(float(t["r"]) for t in group) if group else None,
            "fee_per_trade":statistics.fmean(float(t["fees"])/len(group) for t in group)*len(group) if group else None,
            "slippage_per_trade":statistics.fmean(float(t["slippage_drag"]) for t in group) if group else None,
            "gross_edge_absent":gross_absent,"gross_edge_consumed_by_cost":cost_consumed,"mixed_edge_cost_amplified":mixed,
            "structural_r":distribution(structural),"entry_stop_pct":distribution(stop_d),"entry_target_pct":distribution(target_d),
            "notional_cap_rate":cap/len(group) if group else None,"holding_bars":distribution(holds),
            "exit_counts":dict(Counter(t["exit_reason"] for t in group)),"failure_categories":dict(categories),
            "mae_r":distribution([float(t["mae"]) for t in group]),"mfe_r":distribution([float(t["mfe"]) for t in group]),
            "maximum_consecutive_losses":max_run,"monthly_trade_distribution":distribution(list(monthly.values())),
            "stop_reference_types":dict(Counter(events[t["event_id"]]["geometry"]["stop_reference_type"] for t in group)),
            "target_reference_types":dict(Counter(events[t["event_id"]]["geometry"]["target_reference_type"] for t in group)),
            "level_timeframes":dict(Counter(events[t["event_id"]]["geometry"]["setup_zone"]["timeframe"] for t in group)),
            "regime_tags":regime_metrics(group),"diagnostic_only":True}
    return result


def regime_metrics(trades: Sequence[Mapping[str,Any]]) -> dict[str,Any]:
    buckets:defaultdict[str,list[float]]=defaultdict(list)
    for trade in trades:
        for tag in trade.get("regime_tags",[]):buckets[tag].append(float(trade["r"]))
    return {tag:{"count":len(values),"expectancy_r":statistics.fmean(values),
                 "sample_label":"DESCRIPTIVE_ONLY_SMALL_SAMPLE" if len(values)<30 else "DESCRIPTIVE_ONLY"}
            for tag,values in sorted(buckets.items())}


def concentration_audit(trials: Sequence[dict[str,Any]], trades: Sequence[dict[str,Any]]) -> dict[str,Any]:
    output={}
    positive=[item for item in trials if item["development"]["metrics"].get("expectancy_r") is not None and item["development"]["metrics"]["expectancy_r"]>0]
    for trial in positive:
        group=[t for t in trades if t["trial_id"]==trial["trial_id"]]; base=statistics.fmean(float(t["r"]) for t in group)
        ranked=sorted(group,key=lambda t:float(t["net_pnl"]),reverse=True)
        by_asset=Counter();by_fold=Counter()
        for trade in group:by_asset[trade["instrument"]]+=float(trade["net_pnl"]);by_fold[development_fold(int(trade["entry_ts"]))]+=float(trade["net_pnl"])
        variants={"without_max_trade":ranked[1:],"without_top_2_trades":ranked[2:],
                  "without_max_asset":[t for t in group if t["instrument"]!=max(by_asset,key=by_asset.get)],
                  "without_max_fold":[t for t in group if development_fold(int(t["entry_ts"]))!=max(by_fold,key=by_fold.get)]}
        values={name:(statistics.fmean(float(t["r"]) for t in rows) if rows else None) for name,rows in variants.items()}
        fragile=any(value is None or value<0 for value in values.values())
        output[trial["trial_id"]]={"family":trial["family"],"direction":trial["direction"],"base_expectancy_r":base,
                                    "leave_one_out":values,"classification":"FRAGILE_CONCENTRATED_EVIDENCE" if fragile else "DESCRIPTIVE_ONLY",
                                    "diagnostic_only":True}
    return output


def final_classifications(route_audit: Sequence[dict[str,Any]]) -> tuple[dict[str,Any],dict[str,Any],list[dict[str,Any]]]:
    by_group=defaultdict(list)
    for row in route_audit:by_group[(row["family"],row["direction"])].append(row)
    output={}
    for family,direction in GROUPS:
        rows=by_group[(family,direction)];stage=sum(x["stage_match"] for x in rows);identity=sum(x["setup_identity_match"] and x["evaluation_identity_match"] and x["level_identity_match"] for x in rows)
        output[f"{family}:{direction}"]={"primary":"ENGINE_OR_DATA_INVALID",
            "secondary":["EVENT_REPLAY_ERROR","IDENTITY_ERROR","GEOMETRY_ERROR"],
            "actual_v2_stage_match_rate":stage/len(rows) if rows else None,
            "identity_match_rate":identity/len(rows) if rows else None,
            "evidence":f"actual V2 route matched {stage}/{len(rows)} sampled ledger stages; identities matched {identity}/{len(rows)}",
            "phase4a_classification_usable":False}
    decision={"route":"F. ENGINE_FIX_REQUIRED_BEFORE_ANY_RESEARCH",
              "single_next_action":"Create an isolated engine-repair/replay phase that makes Phase 4A consume the real Context V2 -> State V2 -> Router V2 contract, then invalidate and rerun all 32 Development trials without opening Validation/OOT.",
              "recommend_trend_pullback_v3":False,"recommend_ma200_v3":False,"recommend_phase4b":False,
              "reason":"The formal replay path did not execute the frozen Strategy Router V2 rules; strategy postmortem slices cannot support hypothesis admission."}
    return output,decision,[]
