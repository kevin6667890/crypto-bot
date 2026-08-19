"""Public, bounded projections of already-audited AI-6B presentations."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from .presentation import PresentationError, build_latest_presentation, build_report_presentation
from .report_repository import ReportRepository


TIMEFRAMES = ("15m", "1H", "4H", "1D", "1W")


def _timeframe_quality(repository: ReportRepository, presentation: dict[str, Any]) -> list[dict[str, Any]]:
    context = repository.load_context(presentation.get("context_id")) or {}
    base = context.get("base_context") or context
    facts = base.get("timeframe_facts") or {}
    output = []
    for timeframe in TIMEFRAMES:
        fact = facts.get(timeframe) or {}
        quality = fact.get("quality") or {}
        status = str(quality.get("status") or "MISSING")
        availability = ("AVAILABLE" if status == "COMPLETE" else "STALE" if status == "STALE"
                        else "MISSING" if status in {"MISSING", "INVALID"} else "PARTIAL")
        output.append({
            "timeframe": timeframe, "availability": availability,
            "quality": status, "bar_count": int(quality.get("actual_bars") or 0),
            "required_bar_count": 200, "latest_at": _timestamp(quality.get("latest")),
            "reason_code": ("INDICATOR_WARMUP_INCOMPLETE" if status == "WARMUP_INCOMPLETE"
                            else "FRESHNESS_LIMIT_EXCEEDED" if status == "STALE"
                            else "NO_CONFIRMED_CANDLES" if status == "MISSING"
                            else "SOURCE_PARTIAL_OR_GAPPED" if status == "GAP_AFFECTED" else None),
        })
    return output


def _driver(fact: dict[str, Any]) -> dict[str, Any]:
    value = fact.get("display_value", fact.get("value"))
    if isinstance(value, dict):
        value = value.get("value") or value.get("state") or value.get("trend")
    return {"label": fact.get("label") or fact.get("category") or "市场依据",
            "value": value, "quality": fact.get("quality")}


def _decision_label(report: dict[str, Any]) -> str:
    if report.get("confidence") in {"LOW", "INSUFFICIENT"}:
        return "风险等待"
    return {"BULLISH": "偏多观察", "BEARISH": "偏空观察"}.get(
        str(report.get("directional_bias")), "观察")


def _timestamp(value: int | float | None) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wall_clock_freshness(market_time: str | None) -> tuple[int | None, int]:
    threshold = max(900, int(os.getenv("AI_REPORT_PRESENTATION_FRESHNESS_SECONDS", "7200")))
    if not market_time:
        return None, threshold
    try:
        stamp = datetime.fromisoformat(market_time.replace("Z", "+00:00"))
    except ValueError:
        return None, threshold
    return max(0, int((datetime.now(timezone.utc) - stamp).total_seconds())), threshold


def _summary(presentation: dict[str, Any], repository: ReportRepository) -> dict[str, Any]:
    report = presentation.get("report")
    eligible = presentation.get("eligibility") == "AUDIT_PASSED_SHADOW_ONLY" and isinstance(report, dict)
    latest = presentation.get("latest_generated") or {}
    if latest.get("eligibility") == "AUDIT_FAILED":
        eligible = False
    freshness = presentation.get("freshness") or {"status": "UNKNOWN"}
    market_snapshot_at = _timestamp(presentation.get("latest_confirmed_market_time"))
    age_seconds, threshold_seconds = _wall_clock_freshness(market_snapshot_at)
    wall_clock_stale = age_seconds is None or age_seconds > threshold_seconds
    status = "NO_CURRENT_AUDITED_REPORT"
    if eligible:
        status = "STALE_AUDITED_REPORT" if wall_clock_stale or freshness.get("status") in {"STALE", "UNKNOWN", "SUPERSEDED"} else "CURRENT_AUDITED_REPORT"
    section = next((item for item in (report or {}).get("sections", []) if item.get("section_id") == "CONCLUSION"), None)
    section = section or next((item for item in (report or {}).get("sections", [])
                               if item.get("section_id") in {"QUICK_SUMMARY", "EXECUTIVE_SUMMARY"}), None)
    body = str((section or {}).get("body") or (report or {}).get("headline") or "")[:900]
    stored = repository.get_report(report_id=presentation.get("report_id")) or {}
    uncertainties = [str(item) for item in (section or {}).get("uncertainties", [])][:3]
    levels = [{key: item.get(key) for key in ("level_id", "representative_price", "asserted_role",
                                               "asserted_state", "primary_timeframe", "invalidation")}
              for item in presentation.get("referenced_levels", [])[:3]]
    scenarios = [{key: item.get(key) for key in ("scenario_id", "scenario_type", "direction", "status",
                                                  "trigger_text", "confirmation_text", "invalidation_text")}
                 for item in presentation.get("referenced_scenarios", [])[:2]]
    return {
        "instrument": presentation["instrument"], "mode": presentation["mode"],
        "report_id": presentation["report_id"], "display_eligible": eligible, "status": status,
        "generated_at": presentation.get("generated_at"),
        "market_snapshot_at": market_snapshot_at,
        "freshness": {**{key: freshness.get(key) for key in ("status", "quality", "confirmed_15m_bars_behind")},
                      "age_seconds": age_seconds, "threshold_seconds": threshold_seconds},
        "latest_generated": {key: latest.get(key) for key in ("report_id", "eligibility", "queue_status", "decision_time")},
        "audit": {key: (presentation.get("audit_summary") or {}).get(key) for key in ("status", "overall_score", "promotion_eligible")},
        "provider": stored.get("provider"), "model": stored.get("model"),
        "headline": (report or {}).get("headline") if eligible else None,
        "executive_summary": body if eligible else None,
        "decision_label": _decision_label(report or {}) if eligible else None,
        "market_phase": (report or {}).get("market_phase") if eligible else None,
        "directional_bias": (report or {}).get("directional_bias") if eligible else None,
        "confidence": (report or {}).get("confidence") if eligible else None,
        "drivers": [_driver(item) for item in presentation.get("referenced_facts", [])[:3]] if eligible else [],
        "risks": uncertainties if eligible else [], "levels": levels if eligible else [],
        "scenarios": scenarios if eligible else [],
        "timeframe_quality": _timeframe_quality(repository, presentation),
        "data_warnings": list((report or {}).get("data_warnings") or presentation.get("data_warnings") or [])[:12]
                         if eligible else [],
    }


def latest_workspace_brief(repository: ReportRepository, instrument: str,
                           mode: str = "QUICK", language: str = "zh-CN") -> dict[str, Any]:
    return _summary(build_latest_presentation(repository, instrument, mode, language), repository)


def research_history(repository: ReportRepository, instrument: str,
                     language: str = "zh-CN", limit: int = 20) -> dict[str, Any]:
    with repository.connect() as conn:
        rows = conn.execute(
            "SELECT p.report_id,p.mode FROM ai_market_reports p JOIN ai_report_requests r ON r.request_id=p.request_id "
            "WHERE r.instrument=? AND p.language=? ORDER BY p.created_at DESC,p.report_id DESC LIMIT ?",
            (instrument, language, max(1, min(limit, 50))),
        ).fetchall()
    items = []
    for row in rows:
        try:
            value = _summary(build_report_presentation(repository, row["report_id"], instrument=instrument, mode=row["mode"], language=language), repository)
        except PresentationError:
            continue
        # Never return a body for a failed/pending audit, even in history.
        if not value["display_eligible"]:
            value["headline"] = None
            value["executive_summary"] = None
        items.append(value)
    return {"instrument": instrument, "language": language, "items": items}


def research_report(repository: ReportRepository, report_id: str, *, instrument: str,
                    mode: str, language: str = "zh-CN") -> dict[str, Any]:
    presentation = build_report_presentation(repository, report_id, instrument=instrument, mode=mode, language=language)
    summary = _summary(presentation, repository)
    report = presentation.get("report") if summary["display_eligible"] else None
    return {
        "summary": summary,
        "report": None if report is None else {
            "headline": report.get("headline"), "market_phase": report.get("market_phase"),
            "directional_bias": report.get("directional_bias"), "confidence": report.get("confidence"),
            "sections": [{key: section.get(key) for key in ("section_id", "title", "body", "uncertainties")}
                         for section in report.get("sections", [])],
        },
    }
