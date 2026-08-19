from __future__ import annotations

from dashboard.automatic_research import AutomaticResearchService, AUTO_RESEARCH_POLICY_VERSION, rolling_splits
from dashboard.discovery_scoring import evaluate_eligibility
from dashboard.discovery_service import DiscoveryService, ENGINE_VERSION, aggregate
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository
from dashboard.strategy_registry import ACTIVE_SCOPE_POLICY_VERSION, ApprovedStrategyRegistry, StrategyRegistryAdapter, canonical_hash
from dashboard.strategy_router_v2 import StrategyRouterV2
from tests.test_strategy_router_v2 import inputs


def evidence(score: float, identity: str):
    metrics={"completed_fold_count":5,"failed_fold_count":0,"folds_with_trades":5,"total_trades":100,
             "median_trades_per_fold":20,"profitable_fold_ratio":.8,"benchmark_beating_fold_ratio":.8,
             "median_excess_return":3.0,"worst_validation_return":-2.0,"worst_excess_return":-3.0,
             "worst_maximum_drawdown":8.0,"validation_return_standard_deviation":2.0}
    definition={"schema_version":"approved-deterministic-definition-v1","template":"TREND_PULLBACK_V2_1",
                "template_version":"trend-pullback-v2.1","parameters":{"risk_per_trade":.01},"direction":"BOTH",
                "router_family":"TREND_PULLBACK","router_parameters":{"zone_buffer_atr":.2,"trigger_score":72,"minimum_r":1.25},
                "dataset_range":{"start":1,"end":2},"validation_status":{"development":"PASS","walk_forward":"PASS","holdout":"PASS","oot":"PASS","cross_asset":"PASS","robustness":"PASS"},
                "activation_scope":{"mode":"GLOBAL_CROSS_ASSET","policy_version":ACTIVE_SCOPE_POLICY_VERSION,"instruments":["BTC-USDT","ETH-USDT","SOL-USDT"]}}
    configuration_hash=canonical_hash(definition)
    passed={"status":"PASS","metrics":{"total_trades":10,"total_return":1,"maximum_drawdown":5}}
    return {"candidate":{"template":"TREND_PULLBACK_V2_1","template_version":"trend-pullback-v2.1","parameters":{"risk_per_trade":.01},"timeframe":"15m","aggregate_metrics":metrics,"development_score":score,"pareto_rank":1},
            "candidate_identity":identity,"definition":definition,"configuration_hash":configuration_hash,
            "development":{"eligible":True,"reasons":[],"score":score},"walk_forward":passed,"holdout":passed,
            "final_oot":passed,"cross_asset":{"status":"PASS","assets":[]},"robustness":{"status":"PASS","folds":[]},
            "contamination":{"state":"CLEAR","candidate_frozen_before_holdout":True},
            "identity":{"candidate_identity":identity,"configuration_hash":configuration_hash},
            "runtime":{"deterministic":True,"execution_compatible":True,"router_family":"TREND_PULLBACK"},
            "dataset_id":1,"dataset_fingerprint":"fixture-dataset","discovery_run_id":7}


def services(tmp_path, factory):
    repo=ResearchRepository(tmp_path/"research.db"); jobs=JobQueue(tmp_path/"research.db",autostart=False)
    discovery=DiscoveryService(repo,jobs); auto=AutomaticResearchService(repo,jobs,discovery,evidence_factory=factory)
    return repo,jobs,auto


def run_cycle(auto, jobs, payload):
    cycle=auto.start(payload); job=jobs.get(cycle["job_id"])
    auto._job(job["id"],job["request_payload"],jobs.checkpoint)
    return auto.detail(cycle["id"])


def runtime_candles(as_of: int, count: int = 240):
    start=as_of-count*900
    return [{"ts":start+i*900,"candle_close_ts":start+(i+1)*900,"open":100+i*.02,
             "high":101+i*.02,"low":99+i*.02,"close":100.2+i*.02,"volume":100+i%7,
             "confirmed":True} for i in range(count)]


def test_empty_registry_is_explicit_legacy_fallback(tmp_path):
    repo=ResearchRepository(tmp_path/"research.db"); registry=ApprovedStrategyRegistry(repo)
    value,state=inputs(); result=StrategyRegistryAdapter(registry).route(StrategyRouterV2(),value,state)
    assert result["strategy_provenance"]["source"]=="LEGACY_BASELINE"
    assert len(result["candidates"])==8


def test_fixture_cycle_approves_activates_and_router_loads_without_code_change(tmp_path):
    repo,jobs,auto=services(tmp_path,lambda cycle:[evidence(70,"candidate-a")])
    cycle=run_cycle(auto,jobs,{"seed":1,"lookback_days":365,"trial_budget":1,"finalists":1})
    assert cycle["status"]=="COMPLETED" and cycle["result"]["approved"]==1
    active=auto.registry.active(); assert active["candidate_identity"]=="candidate-a" and active["status"]=="ACTIVE"
    value,state=inputs(); route=StrategyRegistryAdapter(auto.registry).route(StrategyRouterV2(),value,state,candles=runtime_candles(value["as_of"]))
    assert route["strategy_provenance"]["source"]=="APPROVED_REGISTRY"
    assert route["strategy_provenance"]["candidate_identity"]=="candidate-a"
    assert {(item["family"],item["direction"]) for item in route["candidates"]}=={("TREND_PULLBACK","LONG"),("TREND_PULLBACK","SHORT")}
    assert all(item["strategy_provenance"]["registry_id"]==active["registry_id"] for item in route["candidates"])
    assert route["execution_decision"]["configuration_hash"]==active["configuration_hash"]
    assert route["execution_decision"]["authoritative"] is True


def test_rolling_splits_are_contiguous_disjoint_and_oot_is_last_ten_percent():
    start=1_700_000_000; end=start+730*86_400; splits=rolling_splits(start,end)
    assert splits["development_end"]==splits["primary_holdout_start"]
    assert splits["primary_holdout_end"]==splits["final_oot_start"]
    assert splits["final_oot_end"]==end
    assert splits["final_oot_end"]-splits["final_oot_start"]==(end-start)//10
    validations=[]
    for train_start,train_end,validation_start,validation_end in splits["development_folds"]:
        assert train_start==start and train_end==validation_start
        validations.append((validation_start,validation_end))
    assert all(left[1]==right[0] for left,right in zip(validations,validations[1:]))
    assert validations[-1][1]==splits["development_end"]


def test_two_x_cost_robustness_uses_full_existing_eligibility_not_three_of_five():
    good={"total_return":2,"total_trades":10,"maximum_drawdown":5,"sharpe_ratio":1,
          "sortino_ratio":1,"profit_factor":1.2,"fees_paid":1}
    bad={**good,"total_return":-20,"total_trades":0,"maximum_drawdown":25}
    benchmark={"total_return":0}
    folds=[{"status":"COMPLETED","metrics":good,"buy_hold_metrics":benchmark} for _ in range(3)]
    folds += [{"status":"COMPLETED","metrics":bad,"buy_hold_metrics":benchmark} for _ in range(2)]
    decision=evaluate_eligibility(aggregate(folds),"15m","DEVELOPMENT_CANDIDATE")
    assert decision["eligible"] is False
    assert decision["reasons"]


def test_router_and_paper_share_exact_frozen_runtime_semantics(tmp_path):
    from dashboard.paper_api import PaperService
    repo,jobs,auto=services(tmp_path,lambda cycle:[evidence(70,"candidate-runtime")])
    run_cycle(auto,jobs,{"seed":11,"lookback_days":365,"trial_budget":1,"finalists":1})
    active=auto.registry.active(); value,state=inputs(); candles=runtime_candles(value["as_of"])
    route=StrategyRegistryAdapter(auto.registry).route(StrategyRouterV2(),value,state,candles=candles)
    paper=PaperService(tmp_path/"paper.db")
    paper_decision=paper._registry_decision(active,"BTC-USDT",candles,
        {"decision_cvd_delta":0,"decision_oi_change_pct":None,"source":"fixture","decision_quality":{}},
        {"allowed":True,"blockers":[]},{})
    execution=route["execution_decision"]
    assert (execution["action"],execution["candidate_identity"],execution["configuration_hash"]) == (
        paper_decision["bias"],paper_decision["candidate_identity"],paper_decision["strategy_configuration_hash"])
    assert paper_decision["decision_engine_version"]==execution["runtime_version"]


def test_active_scope_is_one_global_cross_asset_strategy(tmp_path):
    _,jobs,auto=services(tmp_path,lambda cycle:[evidence(70,"candidate-global")])
    run_cycle(auto,jobs,{"seed":12,"lookback_days":365,"trial_budget":1,"finalists":1})
    active=auto.registry.active()
    assert active["instrument_scope"]==["BTC-USDT","ETH-USDT","SOL-USDT"]
    assert active["serialized_definition"]["activation_scope"]["mode"]=="GLOBAL_CROSS_ASSET"
    with auto.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approved_strategy_registry WHERE status='ACTIVE'").fetchone()[0]==1


def test_better_approved_candidate_switch_is_audited_and_old_identity_stays_retired(tmp_path):
    current={"score":60.0,"identity":"candidate-a"}
    repo,jobs,auto=services(tmp_path,lambda cycle:[evidence(current["score"],current["identity"])])
    run_cycle(auto,jobs,{"seed":1,"lookback_days":365,"trial_budget":1,"finalists":1})
    current.update(score=80.0,identity="candidate-b")
    run_cycle(auto,jobs,{"seed":2,"lookback_days":365,"trial_budget":1,"finalists":1})
    assert auto.registry.active()["candidate_identity"]=="candidate-b"
    assert auto.registry.get(next(item["registry_id"] for item in auto.registry.approved(20) if item["candidate_identity"]=="candidate-b"))["status"]=="ACTIVE"
    with repo.connect() as connection:
        old=connection.execute("SELECT status FROM approved_strategy_registry WHERE candidate_identity='candidate-a'").fetchone()
    assert old["status"]=="RETIRED"
    audit=auto.registry.switches(); assert audit[0]["previous_registry_id"] and audit[0]["reason_code"]=="BETTER_APPROVED_DEVELOPMENT_EVIDENCE"
    assert audit[0]["comparison"]["holdout_oot_cross_asset_used_for_ranking"] is False


def test_incomplete_validation_is_persisted_rejected(tmp_path):
    bad=evidence(90,"candidate-rejected"); bad["final_oot"]={"status":"FAIL"}
    _,jobs,auto=services(tmp_path,lambda cycle:[bad])
    cycle=run_cycle(auto,jobs,{"seed":3,"lookback_days":365,"trial_budget":1,"finalists":1})
    assert cycle["result"]["rejected"]==1 and auto.registry.active() is None
    with auto.repository.connect() as connection:
        row=connection.execute("SELECT status,rejection_reasons FROM approved_strategy_registry").fetchone()
    assert row["status"]=="REJECTED" and "FINAL_OOT_FAILED" in row["rejection_reasons"]


def test_mismatched_frozen_definition_identity_is_rejected(tmp_path):
    bad=evidence(90,"candidate-mismatch")
    bad["definition"]["parameters"]={"risk_per_trade":.02}
    _,jobs,auto=services(tmp_path,lambda cycle:[bad])
    run_cycle(auto,jobs,{"seed":4,"lookback_days":365,"trial_budget":1,"finalists":1})
    with auto.repository.connect() as connection:
        row=connection.execute("SELECT status,rejection_reasons FROM approved_strategy_registry").fetchone()
    assert row["status"]=="REJECTED"
    assert "PARAMETER_SNAPSHOT_MISMATCH" in row["rejection_reasons"]
    assert "FROZEN_DEFINITION_HASH_MISMATCH" in row["rejection_reasons"]


def test_schema_is_additive_and_paper_identity_columns_exist(tmp_path):
    repo=ResearchRepository(tmp_path/"research.db")
    with repo.connect() as connection:
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"automatic_research_cycles","approved_strategy_registry","strategy_registry_switches"} <= tables
    from dashboard.paper_api import PaperService
    paper=PaperService(tmp_path/"paper.db")
    with paper._connect() as connection:
        columns={row[1] for row in connection.execute("PRAGMA table_info(paper_trades)")}
    assert {"strategy_registry_id","candidate_identity","strategy_configuration_hash"} <= columns
    assert AUTO_RESEARCH_POLICY_VERSION and ENGINE_VERSION


def test_new_paper_trade_snapshots_registry_identity_without_mutating_history(tmp_path):
    from dashboard.paper_api import PaperService
    from tests.test_paper_provenance_risk_accounting import analysis, rationale
    service=PaperService(tmp_path/"paper.db"); item=analysis(service)
    item.update(strategy_registry_id="asr_fixture",candidate_identity="candidate-a",
                strategy_configuration_hash="approved-config-a",strategy_version="trend-pullback-v2.1")
    created=service.create_order(item,rationale(service,item)); assert created["ok"]
    with service._connect() as connection:
        row=connection.execute("SELECT strategy_registry_id,candidate_identity,strategy_version,config_hash,strategy_configuration_hash FROM paper_trades WHERE id=?",(created["trade_id"],)).fetchone()
    assert tuple(row)==("asr_fixture","candidate-a","trend-pullback-v2.1","approved-config-a","approved-config-a")
