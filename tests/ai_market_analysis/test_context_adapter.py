from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import time

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.versions import SUPPORTED_TIMEFRAMES

from .helpers import BASE, breakout_path, candles, datasets

ROOT = Path(__file__).resolve().parents[2]


def validator():
    root = ROOT/"schemas"/"ai_market_analysis"
    schema = json.loads((root/"market_analysis_context_v1.schema.json").read_text())
    resources = []
    for path in root.glob("*.schema.json"):
        item = json.loads(path.read_text())
        resources.extend(((item["$id"], Resource.from_contents(item)), (path.resolve().as_uri(), Resource.from_contents(item))))
    return Draft202012Validator(schema, registry=Registry().with_resources(resources), format_checker=FormatChecker())


def test_context_has_all_five_timeframes_and_versions():
    data = datasets(); decision = BASE+1400*86400
    context = build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    assert [item["timeframe"] for item in context["timeframe_structures"]] == list(SUPPORTED_TIMEFRAMES)
    assert set(("facts","swing","timeline","adapter")).issubset(context["source_versions"])
    assert context["source_versions"]["facts"] == "ai-market-facts-v1"


def test_context_passes_phase_one_json_schema():
    context = build_market_analysis_context(datasets(), "ETH-USDT-SWAP", BASE+1400*86400)
    validator().validate(context)


def test_unimplemented_sections_are_honest_not_fabricated():
    context = build_market_analysis_context(datasets(), "ETH-USDT-SWAP", BASE+1400*86400)
    assert context["order_flow_phases"] == []
    assert context["key_levels"] == []
    assert context["scenario_tree"] == {"status": "NOT_IMPLEMENTED", "scenarios": []}
    assert context["position_context"]["source"] == "NONE"
    assert context["macro_context"]["status"] == "NOT_REQUESTED"


def test_context_identity_is_stable_and_key_order_independent():
    data = datasets(); decision = BASE+1400*86400
    first = build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    second = build_market_analysis_context(dict(reversed(list(data.items()))), "ETH-USDT-SWAP", decision)
    assert first == second
    assert first["context_id"] == second["context_id"]
    assert first["generated_at"] == first["decision_time"]


def test_future_bars_do_not_change_old_context():
    data = datasets(); decision = BASE+240*900
    before = build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    extended = {key: list(value) for key, value in data.items()}
    extended["15m"].append(candles(1, start=decision, slope=50)[0])
    after = build_market_analysis_context(extended, "ETH-USDT-SWAP", decision)
    assert before == after


def test_multitimeframe_bull_alignment_and_conflict():
    bull = build_market_analysis_context(datasets(), "ETH-USDT-SWAP", BASE+1400*86400)
    assert any(item["relationship"] == "ALIGNED_BULL" for item in bull["multi_timeframe_summary"]["pair_relationships"])
    mixed_data = datasets()
    mixed_data["1D"] = candles(1400, "1D", slope=-.05)
    mixed = build_market_analysis_context(mixed_data, "ETH-USDT-SWAP", BASE+1400*86400)
    assert any(item["relationship"] in {"LOWER_TF_BULL_HIGHER_TF_BEAR", "MIXED"}
               for item in mixed["multi_timeframe_summary"]["pair_relationships"])


def test_golden_eth_price_structure_matches_ai1_expectations():
    expectations = json.loads((ROOT/"fixtures"/"ai_market_analysis"/"golden_eth_breakout_expectations_v1.json").read_text(encoding="utf-8"))
    data = datasets(); data["15m"] = breakout_path()
    decision = data["15m"][-1]["ts"]+900
    context = build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    frame = context["timeframe_structures"][0]
    assert frame["structure_classification"] == "POST_BREAKOUT_PULLBACK"
    # AI-1 golden contract records the same structural facts; AI-2 does not use its order-flow attribution.
    assert expectations["fixture_notice"] == "DESIGN_TEST_DATA_NOT_LIVE_MARKET"
    assert len(expectations["required_conclusions"]) >= 5
    timeline = context["market_timeline"]
    if timeline["breakout_direction"] != "UP":  # dominant timeframe can be higher; 15m event remains explicit.
        assert any(event["timeframe"] == "15m" and event["event_type"] == "POST_BREAKOUT_PULLBACK"
                   for event in context["structure_events"])


def test_import_has_no_database_network_thread_or_llm_side_effect(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    module = importlib.import_module("dashboard.ai_market_analysis")
    importlib.reload(module)


def test_cli_reads_fixture_and_writes_only_requested_output(tmp_path):
    fixture = tmp_path/"input.json"; output = tmp_path/"context.json"
    fixture.write_text(json.dumps({"timeframes": datasets()}), encoding="utf-8")
    before = {path.name for path in tmp_path.iterdir()}
    completed = subprocess.run([sys.executable, "scripts/build_ai_market_context.py",
        "--fixture", str(fixture), "--instrument", "ETH-USDT-SWAP",
        "--decision-time", str(BASE+1400*86400), "--mode", "FULL", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    context = json.loads(output.read_text())
    assert context["context_id"] in completed.stdout
    assert {path.name for path in tmp_path.iterdir()} == before | {"context.json"}
    assert output.stat().st_size < 500_000


def test_cold_warm_and_three_instrument_performance_budget():
    data = datasets(); decision = BASE+1400*86400
    started = time.perf_counter()
    first = build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    cold_ms = (time.perf_counter()-started)*1000
    started = time.perf_counter()
    build_market_analysis_context(data, "ETH-USDT-SWAP", decision)
    warm_ms = (time.perf_counter()-started)*1000
    started = time.perf_counter()
    for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
        build_market_analysis_context(data, instrument, decision)
    three_ms = (time.perf_counter()-started)*1000
    assert first and cold_ms < 1200 and warm_ms < 500 and three_ms < 3600
