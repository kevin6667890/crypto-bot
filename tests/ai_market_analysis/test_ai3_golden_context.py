from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from tests.ai_market_analysis.helpers import golden_datasets

ROOT=Path(__file__).resolve().parents[2]


def orderflow(): return json.loads((ROOT/"fixtures"/"ai_market_analysis"/"golden_eth_orderflow_v1.json").read_text())
def context(extra=None):
    data,decision=golden_datasets()
    return build_market_analysis_context(data,"ETH-USDT-SWAP",decision,orderflow=orderflow(),
        auxiliary=extra or {"previous_week_high":1980,"vpvr":{"poc":1888,"vah":1995,"val":1885}})


def validator():
    directory=ROOT/"schemas"/"ai_market_analysis"; registry=Registry()
    for path in directory.glob("*.json"):
        registry=registry.with_resource(path.name,Resource.from_contents(json.loads(path.read_text())))
    return Draft202012Validator(json.loads((directory/"market_analysis_context_v1.schema.json").read_text()),registry=registry,format_checker=FormatChecker())


def test_ai3_context_schema_and_referential_integrity():
    value=context(); validator().validate(value)
    level_ids={x["level_id"] for x in value["key_levels"]}; phase_ids={x["phase_id"] for x in value["order_flow_phases"]}
    for scenario in value["scenario_tree"]["scenarios"]:
        assert set(scenario["source_level_ids"])<=level_ids
        assert set(scenario["source_phase_ids"])<=phase_ids


def test_golden_impulse_is_short_covering_with_active_buying():
    impulse=next(p for p in context()["order_flow_phases"] if p["phase"]=="IMPULSE")
    assert impulse["attribution"]["primary"]=="SHORT_COVERING_DOMINANT"
    assert impulse["attribution"]["alternatives"][0]=="ACTIVE_BUYING_CONTRIBUTED"
    assert impulse["metrics"]["price_change"]>0 and impulse["metrics"]["cvd"]["signed_delta"]>0
    assert impulse["metrics"]["oi"]["percentage_change"]<0


def test_golden_pullback_does_not_claim_full_new_long_takeover():
    pullback=next(p for p in context()["order_flow_phases"] if p["phase"]=="PULLBACK")
    assert pullback["metrics"]["oi"]["percentage_change"]>0
    assert pullback["attribution"]["primary"]=="MIXED_POSITIONING"


def test_golden_core_flip_impulse_and_higher_pressure_levels():
    levels=context()["key_levels"]
    assert any(z["zone_low"]<=1885 and z["zone_high"]>=1890 and z["state"]=="FLIPPED" and z["role"]=="SUPPORT" for z in levels)
    assert any(z["zone_low"]<=1928<=z["zone_high"] and z["role"]=="RESISTANCE" for z in levels)
    assert any(z["zone_low"]<=1980 and z["zone_high"]>=2000 and z["role"]=="RESISTANCE" for z in levels)


def test_golden_three_paths():
    assert [s["type"] for s in context()["scenario_tree"]["scenarios"]]==["BULLISH_CONTINUATION","NORMAL_RETEST","FAILED_BREAKOUT"]


def test_context_identity_determinism_and_machine_path_independence():
    assert context()["context_id"]==context()["context_id"]


def test_future_orderflow_does_not_change_old_decision_context():
    data,decision=golden_datasets(); source=orderflow()
    before=build_market_analysis_context(data,"ETH-USDT-SWAP",decision,orderflow=source)
    future=deepcopy(source); future["cvd"].append({"timestamp":decision+900,"signed_delta":999,"trade_count":999})
    future["oi"].append({"timestamp":decision+900,"value":999,"unit":"USD"})
    after=build_market_analysis_context(data,"ETH-USDT-SWAP",decision,orderflow=future)
    assert before["context_id"]==after["context_id"]


def test_quality_change_changes_context_identity():
    data,decision=golden_datasets(); source=orderflow()
    before=build_market_analysis_context(data,"ETH-USDT-SWAP",decision,orderflow=source)
    source["cvd"][10]["status"]="PARTIAL_AFTER_GAP"
    after=build_market_analysis_context(data,"ETH-USDT-SWAP",decision,orderflow=source)
    assert before["context_id"]!=after["context_id"]


def test_ai3_cold_context_performance_and_size_budget():
    started=time.perf_counter(); value=context(); elapsed=(time.perf_counter()-started)*1000
    assert elapsed<=1200
    assert len(json.dumps(value,sort_keys=True,allow_nan=False).encode("utf-8"))<=500_000
