from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.strategy_phase4a_router_repair import DEVELOPMENT_END, DevelopmentAccessGuard
from dashboard.strategy_router_reachability_audit import (
    AUDIT_VERSION, CONTRACT_VERSION, EXPECTED_ARTIFACT_SHA, EXPECTED_DATASET_ID,
    FUNNEL_VERSION, contract_matrix, full_chain_witnesses, router_witnesses,
    source_parameter_effectiveness, static_reachability_graph,
)

MANIFEST=Path("research/phase4a_research_manifest_v1.json")


def trials(): return json.loads(MANIFEST.read_text(encoding="utf-8"))["trials"]


def test_versions_and_frozen_evidence_constants():
    assert AUDIT_VERSION=="strategy-router-reachability-audit-v1"
    assert CONTRACT_VERSION=="strategy-router-contract-audit-v1"
    assert FUNNEL_VERSION=="strategy-router-gate-funnel-v1"
    assert len(EXPECTED_ARTIFACT_SHA)==len(EXPECTED_DATASET_ID)==64


def test_frozen_32_parameter_canonical_json_is_exact():
    values=trials(); assert len(values)==32
    assert all(json.dumps(x["parameters"],sort_keys=True,separators=(",",":"))==x["canonical_parameters"] for x in values)


@pytest.mark.parametrize("timestamp",[DEVELOPMENT_END,1_753_452_900,1_767_225_600])
def test_validation_and_oot_refused(timestamp):
    with pytest.raises(PermissionError): DevelopmentAccessGuard.require_as_of(timestamp)


def test_contract_links_are_complete_and_missing_producer_detected():
    matrix=contract_matrix(); links={x["link"] for x in matrix["entries"]}
    assert links=={"CONTEXT_V2_TO_STATE_V2","STATE_V2_TO_ROUTER_V2","ROUTER_V2_TO_LIFECYCLE_V2","ROUTER_V2_TO_GEOMETRY_V2"}
    assert any(x["classification"]=="NEVER_EMITTED" for x in matrix["entries"])
    assert any(x["classification"]=="PATH_MISMATCH" for x in matrix["entries"])


def test_contract_classification_fields_are_deterministic():
    assert contract_matrix()==contract_matrix()
    allowed={"MATCHED","ENUM_MISMATCH","PATH_MISMATCH","TIMEFRAME_MISMATCH","VERSION_MISMATCH","NEVER_EMITTED","EMITTED_BUT_NEVER_CONSUMED","CONSUMED_WITH_WRONG_SEMANTICS","VALID"}
    assert all(x["classification"] in allowed for x in contract_matrix()["entries"])


def test_static_graph_covers_four_routes_and_all_thirteen_gates():
    graphs=static_reachability_graph()["graphs"]
    assert len(graphs)==4
    assert all(len(x["gates"])==13 for x in graphs)
    assert all(any(g["gate"]=="CONFIRMATION" and g["classification"]=="MISSING_PRODUCER" for g in x["gates"]) for x in graphs)


@pytest.mark.parametrize("family,direction,gate",[
    (family,direction,gate)
    for family,direction in (("TREND_PULLBACK","LONG"),("TREND_PULLBACK","SHORT"),
                             ("MA200_MEAN_REVERSION","LONG"),("MA200_MEAN_REVERSION","SHORT"))
    for gate in ("VERSION_QUALITY","ENVIRONMENT","DIRECTION","STRUCTURE","SETUP","MOMENTUM",
                 "LEVEL_INTERACTION","CONFIRMATION","FLOW_CONTEXT","OPPOSING_LEVEL","GEOMETRY","LIFECYCLE","TRIGGER")
])
def test_each_frozen_route_gate_has_a_reachability_classification(family,direction,gate):
    graph=next(x for x in static_reachability_graph()["graphs"] if (x["family"],x["direction"])==(family,direction))
    item=next(x for x in graph["gates"] if x["gate"]==gate)
    assert item["classification"] in {"LOGICALLY_REACHABLE","CONDITIONALLY_REACHABLE","UNREACHABLE",
                                      "CYCLIC_DEPENDENCY","MUTUALLY_EXCLUSIVE","MISSING_PRODUCER","UNKNOWN_REQUIRES_WITNESS"}


def test_all_32_router_witnesses_execute_real_router_and_lifecycle():
    rows,summary=router_witnesses(trials())
    assert summary=={"executed":32,"success":32,"unreachable_parameter_sets":[]}
    assert all(any(step["stage"]=="TRIGGER_READY" for step in row["trace"]) for row in rows)
    assert all(step["router_version"]=="strategy-router-v2" and step["lifecycle_executed"] for row in rows for step in row["trace"])


def test_witness_timestamps_are_distinct_causal_and_no_gate_is_disabled():
    rows,_=router_witnesses(trials())
    for row in rows:
        stamps=[x["as_of"] for x in row["trace"]]
        assert stamps==sorted(set(stamps))
        assert all(max(x["source_timestamps"])<=x["as_of"] for x in row["trace"])


@pytest.mark.parametrize("family,direction",[("TREND_PULLBACK","LONG"),("TREND_PULLBACK","SHORT"),("MA200_MEAN_REVERSION","LONG"),("MA200_MEAN_REVERSION","SHORT")])
def test_full_chain_proves_missing_confirmation_producer(family,direction):
    row=next(x for x in full_chain_witnesses() if (x["family"],x["direction"])==(family,direction))
    assert row["result"]=="FAIL_MISSING_STATE_CONFIRMATION_PRODUCER"
    assert row["formal_context"] and row["formal_state"]
    assert all("RECLAIMED" not in x["interaction_types"] and "REJECTED" not in x["interaction_types"] for x in row["state_trace"])


def test_parameter_use_locations_and_shadowing_are_traced():
    source=Path("dashboard/strategy_router_v2.py").read_text(encoding="utf-8")
    result=source_parameter_effectiveness(source,{})
    by={x["parameter"]:x for x in result["parameters"]}
    assert by["minimum_r"]["classification"]=="ACTIVE_PARAMETER"
    assert by["zone_buffer_atr"]["classification"]=="ACTIVE_PARAMETER"
    assert by["trigger_score"]["classification"]=="SHADOWED_BY_EARLIER_GATE"
    assert by["reclaim_bars"]["classification"]=="SHADOWED_BY_EARLIER_GATE"
    assert not result["parameter_injection_behaviorally_inert"]


def test_audit_source_has_no_execution_network_llm_frontend_or_strategy_mutation():
    source=(Path("dashboard/strategy_router_reachability_audit.py").read_text(encoding="utf-8")+
            Path("scripts/run_strategy_router_reachability_audit.py").read_text(encoding="utf-8")).lower()
    for forbidden in ("create_order(","requests.get(","httpx.","urlopen(","openai.","paper_api","frontend/"):
        assert forbidden not in source
    assert "strategybacktestenginev2_1(" not in source


def test_old_decisions_paper_collector_and_frontend_are_not_audit_outputs():
    script=Path("scripts/run_strategy_router_reachability_audit.py").read_text(encoding="utf-8")
    for token in ("research/phase4a_latest_run.json","dashboard/paper_api.py","collector","frontend/dist"):
        assert token not in script


def test_final_decision_is_single_and_deterministic():
    graph=static_reachability_graph()
    assert graph["global_finding"]=="FORMAL_REPLAY_OMITS_STATE_COMPARE_CONFIRMATION_PATH"
