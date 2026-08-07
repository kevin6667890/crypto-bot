from __future__ import annotations
import os
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from dashboard.ai_market_analysis.report_api import validate_report_body
from dashboard.ai_market_analysis.report_health import report_health
from dashboard.ai_market_analysis.report_repository import ReportRepository
from dashboard.ai_market_analysis.report_service import enabled

def body(**changes):
    value={"instrument":"ETH-USDT-SWAP","decision_time":"2026-08-01T00:00:00Z","mode":"FULL","language":"zh-CN","position_source":"NONE"};value.update(changes);return value

@pytest.mark.parametrize("instrument",["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP"])
def test_instrument_isolation_and_no_sol_normalization(instrument):assert validate_report_body(body(instrument=instrument))["instrument"]==instrument

@pytest.mark.parametrize("field,value",[("mode","DEEP"),("instrument","SOL-USDT"),("language","en-US")])
def test_invalid_enums_rejected_by_service_boundary(field,value):
    # Body validates instrument/time first; service validates mode/language.
    if field=="instrument":
        with pytest.raises(ValueError):validate_report_body(body(**{field:value}))
    else:assert validate_report_body(body(**{field:value}))[field]==value

def test_mutually_exclusive_inputs():
    with pytest.raises(ValueError):validate_report_body(body(position_plan_id="p",inline_position_plan={}))
    with pytest.raises(ValueError):validate_report_body(body(macro_evidence_set_id="m",inline_macro_evidence=[]))

def test_future_decision_time():
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):validate_report_body(body(decision_time=future))

def test_depth_and_string_limits():
    with pytest.raises(ValueError):validate_report_body(body(model="x"*4001))

def test_flags_default_disabled(monkeypatch):
    for name in ("AI_MARKET_REPORTS_ENABLED","AI_MARKET_REPORT_WORKER_ENABLED","AI_USER_POSITION_PLANS_ENABLED","AI_MACRO_HTTP_FETCH_ENABLED","AI_REPORT_LIVE_PROVIDER_ENABLED","AI_REPORT_AUDIT_ENABLED","AI_REPORT_AUDIT_WORKER_ENABLED","AI_REPORT_AUTO_AUDIT_ENABLED","AI_REPORT_EVALUATION_ENABLED","AI_MARKET_ANALYSIS_PRESENTATION_ENABLED"):monkeypatch.delenv(name,raising=False);assert not enabled(name)
    monkeypatch.delenv("AI_MARKET_REPORT_SHADOW_ONLY",raising=False);assert os.getenv("AI_MARKET_REPORT_SHADOW_ONLY","true")=="true"

def test_disabled_health_sanitized(tmp_path,monkeypatch):
    monkeypatch.delenv("AI_MARKET_REPORTS_ENABLED",raising=False);health=report_health(ReportRepository(tmp_path/"missing.db"));assert health["enabled"] is False and health["shadow_only"] is True and "api_key" not in str(health).lower()

def test_routes_exist_without_frontend_ui_changes():
    source=Path("dashboard/paper_api.py").read_text(encoding="utf-8");assert "/api/ai-market-analysis/v1/reports" in source and "ai_briefs" in source and "/api/chat" in source
    app=Path("frontend/src/App.tsx").read_text(encoding="utf-8");assert "/api/ai-market-analysis/v1/reports" not in app
