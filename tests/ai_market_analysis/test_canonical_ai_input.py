from __future__ import annotations

from dashboard.ai_market_analysis.enriched_context import build_enriched_context
from dashboard.ai_market_analysis.macro_evidence import freeze_macro_evidence_set
from dashboard.ai_market_analysis.position_context import none_position_context
from dashboard.ai_market_analysis.report_api import build_base_context_from_stores
from dashboard.ai_market_analysis.report_fact_registry import build_fact_registry
from tests.ai_market_analysis.test_ai6b_b2_http_boundary import _decision, _empty_micro, _seed_paper
from tests.ai_market_analysis.test_context_adapter import validator


def test_ai_context_is_bound_to_canonical_snapshot(tmp_path):
    decision = _decision()
    paper, micro = tmp_path / "paper.db", tmp_path / "micro.db"
    _seed_paper(paper, "ETH-USDT-SWAP", int(decision.timestamp()))
    _empty_micro(micro)
    context = build_base_context_from_stores({
        "instrument": "ETH-USDT-SWAP",
        "decision_time": decision.isoformat().replace("+00:00", "Z"),
        "mode": "QUICK",
    }, paper, micro)
    validator().validate(context)
    canonical = context["canonical_market_snapshot"]
    assert canonical["instrument"] == context["instrument"]
    assert canonical["causal_cutoff"] == int(decision.timestamp())
    assert canonical["snapshot_identity"] == context["provenance"]["input_snapshot_ids"][0]
    assert context["source_versions"]["canonical_market_snapshot"] == canonical["version"]
    assert canonical["fact_count"] > 0

    position = none_position_context(context["instrument"])
    macro = freeze_macro_evidence_set([], context["decision_time"])
    registry = build_fact_registry(build_enriched_context(context, position, macro))
    fact = next(item for item in registry["facts"]
                if item["fact_id"] == "CANONICAL_MARKET_SNAPSHOT")
    assert fact["value"]["snapshot_identity"] == canonical["snapshot_identity"]


def test_canonical_snapshot_mismatch_fails_closed():
    from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context

    try:
        build_market_analysis_context({}, "ETH-USDT-SWAP", 1_700_000_000,
                                      canonical_snapshot={
                                          "instrument": "BTC-USDT-SWAP",
                                          "causal_cutoff": 1_700_000_000,
                                          "snapshot_identity": "a" * 64,
                                      })
    except ValueError as error:
        assert str(error) == "canonical snapshot instrument mismatch"
    else:
        raise AssertionError("mismatched canonical input did not fail closed")
