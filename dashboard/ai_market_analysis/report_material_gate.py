"""Deterministic, auditable material-change gate for automatic AI reports.

The projection deliberately contains semantic states only.  Wall-clock fields,
exact prices, observation counts, and content identities are excluded so a new
confirmed candle does not by itself imply a materially new interpretation.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .canonical import stable_hash
from .intelligence_quality import classify_evidence_quality


AI_REPORT_MATERIAL_FINGERPRINT_VERSION = "ai-report-material-fingerprint-v1"
MATERIAL_TIMEFRAMES = ("15m", "1H", "4H", "1D", "1W")


def _timeframes(base: dict[str, Any]) -> list[dict[str, Any]]:
    structures = {
        str(item.get("timeframe")): item
        for item in base.get("timeframe_structures", [])
        if isinstance(item, dict)
    }
    coverage = base.get("timeframe_coverage") or {}
    output = []
    for timeframe in MATERIAL_TIMEFRAMES:
        item = structures.get(timeframe) or {}
        intelligence = item.get("deterministic_intelligence") or {}
        coverage_item = coverage.get(timeframe) or {}
        coverage_quality = coverage_item.get("quality")
        if isinstance(coverage_quality, dict):
            coverage_quality = coverage_quality.get("status")
        output.append({
            "timeframe": timeframe,
            "trend": item.get("trend_classification"),
            "structure": item.get("structure_classification"),
            "state": intelligence.get("state"),
            "extension": intelligence.get("extension_state"),
            "momentum": (intelligence.get("momentum") or {}).get("state"),
            "volume": (intelligence.get("volume") or {}).get("state"),
            "confidence": item.get("confidence"),
            "quality": coverage_quality or coverage_item.get("status"),
        })
    return output


def _important_level_lifecycle(base: dict[str, Any]) -> list[dict[str, Any]]:
    """Represent important level lifecycle without unstable price-based IDs."""
    values = Counter()
    for item in base.get("key_levels", []):
        if not isinstance(item, dict) or item.get("strength") not in {"STRONG", "MAJOR"}:
            continue
        key = (
            str(item.get("role") or "UNKNOWN"),
            str(item.get("state") or "UNKNOWN"),
            str(item.get("strength") or "UNKNOWN"),
            tuple(sorted(str(value) for value in (item.get("timeframes") or []))),
            tuple(sorted(str(value) for value in (item.get("confluences") or []))),
        )
        values[key] += 1
    return [{
        "role": key[0], "state": key[1], "strength": key[2],
        "timeframes": list(key[3]), "confluences": list(key[4]), "count": count,
    } for key, count in sorted(values.items())]


def _current_flow(base: dict[str, Any]) -> dict[str, Any]:
    phases = [item for item in base.get("order_flow_phases", []) if isinstance(item, dict)]
    current = next((item for item in reversed(phases) if item.get("phase") == "CURRENT"),
                   phases[-1] if phases else {})
    metrics = current.get("metrics") or {}
    quality = metrics.get("quality") or {}
    attribution = current.get("attribution") or {}
    return {
        "primary": attribution.get("primary"),
        "confidence": attribution.get("confidence"),
        "quadrant": metrics.get("quadrant"),
        "volume_regime": metrics.get("volume_regime"),
        "quality": quality.get("overall"),
        "flow_coverage": (quality.get("flow_coverage") or {}).get("state"),
    }


def material_projection(base: dict[str, Any]) -> dict[str, Any]:
    summary = base.get("multi_timeframe_summary") or {}
    timeline = base.get("market_timeline") or {}
    scenarios = base.get("scenario_tree") or {}
    evidence = classify_evidence_quality(base)
    quality = base.get("data_quality") or {}
    return {
        "version": AI_REPORT_MATERIAL_FINGERPRINT_VERSION,
        "instrument": base.get("instrument"),
        "timeframes": _timeframes(base),
        "multi_timeframe": {
            "relationships": [{
                "lower": item.get("lower"), "higher": item.get("higher"),
                "relationship": item.get("relationship"),
            } for item in summary.get("pair_relationships", []) if isinstance(item, dict)],
            "dominant_timeframe": summary.get("dominant_timeframe"),
            "alignment": summary.get("alignment"),
            "conflicts": sorted(str(item) for item in (summary.get("conflicts") or [])),
            "dominant_context": summary.get("dominant_context"),
            "timeframe_states": summary.get("timeframe_states") or {},
            "extension_states": summary.get("extension_states") or {},
        },
        "timeline": {
            "current_phase": timeline.get("current_phase"),
            "breakout_direction": timeline.get("breakout_direction"),
        },
        "important_levels": _important_level_lifecycle(base),
        "scenarios": {
            "status": scenarios.get("status"),
            "items": [{
                "type": item.get("type"), "direction": item.get("direction"),
                "likelihood": item.get("likelihood"),
            } for item in scenarios.get("scenarios", []) if isinstance(item, dict)],
        },
        "flow": _current_flow(base),
        "evidence_quality": {
            key: evidence.get(key) for key in (
                "core_quality", "flow_quality", "long_term_quality",
                "macro_quality", "analysis_availability",
            )
        },
        "data_quality": {
            "overall": quality.get("overall"),
            "stale_sources": sorted(str(item) for item in (quality.get("stale_sources") or [])),
            "missing_sources": sorted(str(item) for item in (quality.get("missing_sources") or [])),
            "gap_sources": sorted({str(item.get("source")) for item in (quality.get("gaps") or [])
                                   if isinstance(item, dict) and item.get("source")}),
            "watermark_mismatch_sources": sorted(str(item) for item in (
                quality.get("watermark_mismatches") or [])),
        },
    }


def material_fingerprint(base: dict[str, Any]) -> dict[str, Any]:
    projection = material_projection(base)
    return {
        "version": AI_REPORT_MATERIAL_FINGERPRINT_VERSION,
        "fingerprint": stable_hash(projection),
        "projection": projection,
    }
