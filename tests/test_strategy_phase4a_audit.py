from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import pytest

from dashboard.strategy_phase4a_audit import (
    AUDIT_VERSION, DevelopmentCandleStore, EvidenceBundle, EXPECTED_ARTIFACT_SHA,
    EXPECTED_DATASET_ID, VALIDATION_START, artifact_sha, concentration_audit,
    deterministic_samples, distribution, final_classifications, stable_hash,
)


PHASE4=Path(r"C:\Users\ASUS\PycharmProjects\crypto-bot-strategy-phase4a\.runtime\strategy-phase4a\d2a72ac24223320655e7eb08d54dba38d976a5c1e804b83203abfc43b2e6ebed")
DATABASE=Path(r"C:\Users\ASUS\crypto-bot-research\data\canonical_ohlcv_2023_2025.db")


def fake_event(family, direction, stage, number):
    ts=1698365700+(number%4)*10_000+900
    return {"event_identity":stable_hash([family,direction,stage,number]),"family":family,"direction":direction,
            "lifecycle_to":stage,"instrument":("BTC-USDT","ETH-USDT","SOL-USDT")[number%3],
            "parameter_set_id":f"p{number%8}","context_timestamp":ts,"setup_timestamp":ts-900,
            "setup_identity":stable_hash(["setup",number]),"blockers":[],
            "geometry":{"setup_zone":{"type":"1H_MA200","timeframe":"1H"},"regime_tags":["HTF_UPTREND"]}}


def test_real_phase4_artifact_identity_and_required_files():
    assert PHASE4.is_dir()
    digest,files=artifact_sha(PHASE4)
    assert digest==EXPECTED_ARTIFACT_SHA
    assert {"manifest.json","trial_ledger.json","trade_ledger.jsonl","event_ledger.jsonl.gz"}<=set(files)


def test_real_manifest_dataset_trial_and_engine_identity():
    manifest=json.loads((PHASE4/"manifest.json").read_text(encoding="utf8"))
    report=json.loads((PHASE4/"report.json").read_text(encoding="utf8"))
    trials=json.loads((PHASE4/"trial_ledger.json").read_text(encoding="utf8"))
    assert manifest["dataset"]["identity"]==report["dataset_identity"]==EXPECTED_DATASET_ID
    assert len(trials)==32
    assert report["backtest_engine_version"]=="strategy-backtest-engine-v2.0.4"
    assert all(item["validation"] is None for item in trials)


def test_development_store_refuses_validation_and_oot(tmp_path:Path):
    db=tmp_path/"x.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE historical_candles(instrument,timeframe,ts,open,high,low,close,volume,confirmed)")
    store=DevelopmentCandleStore(db)
    with pytest.raises(PermissionError,match="Validation/OOT"):store.rows("BTC-USDT","15m",VALIDATION_START,VALIDATION_START+900)
    with pytest.raises(PermissionError,match="Validation/OOT"):store.rows("BTC-USDT","15m",0,VALIDATION_START+1)


def test_deterministic_sampling_has_25_each_bucket():
    events=[fake_event(f,d,s,n) for f in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for d in ("LONG","SHORT") for s in ("WATCH","ARMED","TRIGGER_READY") for n in range(40)]
    first,funnel=deterministic_samples(events);second,_=deterministic_samples(events)
    assert [x["event_identity"] for x in first]==[x["event_identity"] for x in second]
    counts={}
    for item in first:counts[(item["family"],item["direction"],item["lifecycle_to"])]=counts.get((item["family"],item["direction"],item["lifecycle_to"]),0)+1
    assert set(counts.values())=={25} and len(first)==300
    assert len(funnel)==4


@pytest.mark.parametrize("family,direction",[("TREND_PULLBACK","LONG"),("TREND_PULLBACK","SHORT"),("MA200_MEAN_REVERSION","LONG"),("MA200_MEAN_REVERSION","SHORT")])
def test_all_four_groups_are_sampled(family,direction):
    events=[fake_event(f,d,s,n) for f,d in ((family,direction),) for s in ("WATCH","ARMED","TRIGGER_READY") for n in range(30)]
    samples,_=deterministic_samples(events)
    assert {(x["family"],x["direction"]) for x in samples}=={(family,direction)}


@pytest.mark.parametrize("instrument",["BTC-USDT","ETH-USDT","SOL-USDT"])
def test_sampling_fixture_covers_assets(instrument):
    events=[fake_event("TREND_PULLBACK","LONG","WATCH",n) for n in range(90)]
    samples,_=deterministic_samples(events)
    assert instrument in {x["instrument"] for x in samples}


def test_distribution_is_deterministic_and_has_p90():
    assert distribution(range(1,11))==distribution(range(1,11))
    assert distribution(range(1,11))["p90"]==9
    assert distribution([])["mean"] is None


def test_final_decision_is_deterministic_and_engine_fix_only():
    rows=[]
    for family,direction in ((f,d) for f in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for d in ("LONG","SHORT")):
        rows.append({"family":family,"direction":direction,"stage_match":False,"setup_identity_match":False,
                     "evaluation_identity_match":False,"level_identity_match":False})
    one=final_classifications(rows);two=final_classifications(rows)
    assert one==two
    assert one[1]["route"].startswith("F.")
    assert one[1]["recommend_phase4b"] is False
    assert one[2]==[]


@pytest.mark.parametrize("key",["recommend_trend_pullback_v3","recommend_ma200_v3","recommend_phase4b"])
def test_invalid_engine_admits_no_new_hypothesis_or_route(key):
    rows=[{"family":f,"direction":d,"stage_match":False,"setup_identity_match":False,
           "evaluation_identity_match":False,"level_identity_match":False} for f in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for d in ("LONG","SHORT")]
    _,decision,hypotheses=final_classifications(rows)
    assert decision[key] is False and hypotheses==[]


@pytest.mark.parametrize("forbidden",["requests.get","httpx","openai","DeepSeek","create_order","paper_api","decision_engine"])
def test_audit_has_no_network_llm_paper_or_old_decision_dependency(forbidden):
    source=Path("dashboard/strategy_phase4a_audit.py").read_text(encoding="utf8")+Path("scripts/run_strategy_phase4a_audit.py").read_text(encoding="utf8")
    assert forbidden not in source


def test_audit_version_and_seed_are_frozen():
    assert AUDIT_VERSION=="strategy-phase4a-forensic-audit-v1"
    source=Path("dashboard/strategy_phase4a_audit.py").read_text(encoding="utf8")
    assert "SEED = 20260804" in source


def test_concentration_leave_out_is_deterministic():
    trial={"trial_id":"t","family":"MA200_MEAN_REVERSION","direction":"LONG","development":{"metrics":{"expectancy_r":1}}}
    trades=[{"trial_id":"t","r":r,"net_pnl":p,"instrument":a,"entry_ts":1698365700+i*1000} for i,(r,p,a) in enumerate([(2,20,"BTC-USDT"),(-1,-10,"ETH-USDT"),(-1,-10,"SOL-USDT")])]
    assert concentration_audit([trial],trades)==concentration_audit([trial],trades)
    assert concentration_audit([trial],trades)["t"]["classification"]=="FRAGILE_CONCENTRATED_EVIDENCE"


@pytest.mark.parametrize("name",["phase4a_engine_bug_001.json","phase4a_engine_bug_002.json","phase4a_engine_bug_003.json","phase4a_engine_bug_004.json"])
def test_all_old_runs_remain_invalidated(name):
    payload=json.loads((Path("research")/name).read_text(encoding="utf8"))
    assert payload["affected_run_status"]=="INVALIDATED_ENGINE_BUG"


def test_audit_does_not_define_parameter_ranges_or_new_trials():
    source=Path("dashboard/strategy_phase4a_audit.py").read_text(encoding="utf8")+Path("scripts/run_strategy_phase4a_audit.py").read_text(encoding="utf8")
    assert "parameter_ranges" not in source
    assert "frozen_trials(" not in source
    assert '"new_trials":0' in source and '"new_parameters":0' in source


@pytest.mark.parametrize("classification",["EVENT_REPLAY_ERROR","IDENTITY_ERROR","GEOMETRY_ERROR"])
def test_engine_invalid_classification_is_explicit(classification):
    rows=[{"family":f,"direction":d,"stage_match":False,"setup_identity_match":False,
           "evaluation_identity_match":False,"level_identity_match":False} for f in ("TREND_PULLBACK","MA200_MEAN_REVERSION") for d in ("LONG","SHORT")]
    classes,_,_=final_classifications(rows)
    assert all(classification in value["secondary"] for value in classes.values())
