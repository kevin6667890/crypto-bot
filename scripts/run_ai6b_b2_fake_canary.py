#!/usr/bin/env python3
"""Local-only deterministic AI-6B B2 Fake canary.

This module is inert unless invoked with ``--local-only``.  It performs no
network I/O and accepts only the repository's Fake provider and NONE/PAPER
position sources.  Every report traverses the production Context -> Registry
-> Prompt -> Provider -> Report -> Audit -> Presentation code path.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.ai_market_analysis.context_adapter import build_market_analysis_context
from dashboard.ai_market_analysis.presentation import build_report_presentation
from dashboard.ai_market_analysis.report_audit_jobs import AuditWorker, queue_audit
from dashboard.ai_market_analysis.report_audit_repository import (
    AuditRepository, freeze_report_bundle, migrate_audit_database,
)
from dashboard.ai_market_analysis.report_jobs import ReportWorker, TokenBudget
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from dashboard.ai_market_analysis.versions import TIMEFRAME_SECONDS

FIXTURE_PATH = ROOT / "fixtures/ai_market_analysis/ai6b_b2_fake_canary_v1.json"
ORDERFLOW_PATH = ROOT / "fixtures/ai_market_analysis/golden_eth_orderflow_v1.json"
BASE_EPOCH = 1_704_067_200


def _candles(count: int, timeframe: str, instrument: str, *, start: int, slope: float) -> list[dict[str, Any]]:
    width = TIMEFRAME_SECONDS[timeframe]
    rows = []
    for index in range(count):
        close = 100 + slope * index + ((index % 5) - 2) * 0.03
        rows.append({
            "instrument": instrument, "timeframe": timeframe, "ts": start + index * width,
            "open": close - 0.4, "high": close + 1, "low": close - 1, "close": close,
            "volume": 100 + index, "confirmed": True, "source": "ai6b-b2-deterministic-fixture",
            "source_timestamp": start + (index + 1) * width,
        })
    return rows


def _breakout_path(instrument: str) -> list[dict[str, Any]]:
    closes = [1848, 1854, 1862, 1872, 1882, 1888, 1880, 1870, 1858, 1848] * 4
    closes += [1895, 1904, 1920, 1925, 1915, 1906, 1900]
    rows = []
    for index, close in enumerate(closes):
        rows.append({
            "instrument": instrument, "timeframe": "15m", "ts": BASE_EPOCH + index * 900,
            "open": close - 1, "high": 1890 if close == 1888 else close + 3,
            "low": 1845 if close == 1848 else close - 3, "close": close,
            "volume": 80 if index < 40 else 160, "confirmed": True,
            "source": "ai6b-b2-deterministic-price-structure",
        })
    return rows


def deterministic_context(instrument: str, variant: str = "complete") -> dict[str, Any]:
    """Build a fresh deterministic context; never patch a final report JSON."""
    decision = BASE_EPOCH + 1400 * 86400
    def price_structure_with_warmup(timeframe: str) -> list[dict[str, Any]]:
        width = TIMEFRAME_SECONDS[timeframe]
        history = _candles(210, timeframe, instrument, start=decision - (210 + 47) * width, slope=2)
        delta = 1840 - history[-1]["close"]
        for row in history:
            for field in ("open", "high", "low", "close"):
                row[field] += delta
        tail = _breakout_path(instrument)
        start = decision - len(tail) * width
        tail = [dict(row, ts=start + index * width, timeframe=timeframe,
                     source_timestamp=start + (index + 1) * width) for index, row in enumerate(tail)]
        return history + tail

    daily = _candles(1400, "1D", instrument, start=BASE_EPOCH, slope=0)
    for index, row in enumerate(daily):
        close = 3000 - (2100 * index / 1369) if index < 1370 else 900 + (100 * (index - 1369) / 30)
        row.update(open=close + 2, high=close + 5, low=close - 5, close=close, volume=100 + index % 20)
    datasets = {
        "15m": price_structure_with_warmup("15m"),
        "1H": _candles(240, "1H", instrument, start=decision - 240 * 3600, slope=0.4),
        "4H": price_structure_with_warmup("4H"),
        "1D": daily,
    }
    orderflow = json.loads(ORDERFLOW_PATH.read_text(encoding="utf-8"))
    if variant == "missing_orderflow":
        orderflow["cvd"] = []
        orderflow["oi"] = []
    context = build_market_analysis_context(datasets, instrument, decision, orderflow=orderflow)
    context["provenance"]["fixture"] = True
    context["provenance"]["ai6b_b2_variant"] = variant
    if variant == "warning":
        context["data_quality"]["missing_sources"].append("AI6B_B2_SYNTHETIC_WARNING")
        context["data_quality"]["overall"] = "PARTIAL"
    if variant == "stale":
        context["provenance"]["expected_presentation_freshness"] = "STALE_AFTER_NEWER_CONTEXT"
    return context


def create_paper_fixture(path: Path, fixture: dict[str, Any]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE paper_trades(
          id INTEGER,instrument TEXT,side TEXT,entry REAL,stop_loss REAL,take_profit REAL,
          status TEXT,position_size REAL,mark_price REAL,pnl_usdt REAL,net_pnl REAL,
          created_at TEXT,closed_at TEXT,execution_timeframe TEXT,trade_rationale TEXT,
          accounting_version TEXT,risk_amount REAL,actual_risk_amount REAL)""")
        for instrument, row in fixture["paper_positions"].items():
            connection.execute(
                "INSERT INTO paper_trades VALUES(?,?,?,?,?,?,'OPEN',?,?,NULL,NULL,?,NULL,'15m',?,'ai6b-b2-v1',10,10)",
                (row["id"], instrument.replace("-SWAP", ""), row["side"], row["entry"], row["stop"],
                 row["target"], row["quantity"], row["mark"], "2027-10-01T00:00:00Z", "deterministic fake canary"),
            )


def assert_local_safety(position_source: str, provider: str = "fake") -> None:
    if provider != "fake":
        raise RuntimeError("B2_FAKE_PROVIDER_ONLY")
    if position_source not in {"NONE", "PAPER"}:
        raise RuntimeError("B2_POSITION_SOURCE_FORBIDDEN")
    forbidden = (
        "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_FILE", "AI_REPORT_PROVIDER_KEY",
        "AI_REPORT_API_KEY", "AI_REPORT_API_KEY_FILE",
    )
    if any(os.getenv(name) for name in forbidden):
        raise RuntimeError("B2_PROVIDER_SECRET_PRESENT")


def run_matrix(output_dir: Path) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=False)
    database = output_dir / "ai6b-b2-local.db"
    paper_database = output_dir / "ai6b-b2-paper-readonly-source.db"
    migrate_database(database)
    migrate_audit_database(database)
    create_paper_fixture(paper_database, fixture)
    reports, audits = ReportRepository(database), AuditRepository(database)
    provider_instances: list[FakeAIReportProvider] = []
    local_budget = TokenBudget()
    local_budget.daily_input = local_budget.daily_output = local_budget.daily_total = 1_000_000
    local_budget.instrument_total = 1_000_000

    def factory(request: dict[str, Any]) -> FakeAIReportProvider:
        if request["provider"] != "fake":
            raise RuntimeError("B2_FAKE_PROVIDER_ONLY")
        provider = FakeAIReportProvider(request["model"], behavior="success")
        provider_instances.append(provider)
        return provider

    cases = []
    for instrument in fixture["instruments"]:
        for mode in fixture["modes"]:
            for source in fixture["position_sources"]:
                assert_local_safety(source)
                context = deterministic_context(instrument)
                mark = fixture["paper_positions"][instrument]["mark"]
                submitted = ReportService(reports, str(paper_database)).submit(
                    context, mode=mode, language="zh-CN", position_source=source,
                    provider="fake", model=fixture["provider_model"], current_mark=mark,
                )
                if not ReportWorker(reports, factory, budget=local_budget).run_once():
                    raise RuntimeError("B2_REPORT_WORKER_DID_NOT_RUN")
                report = reports.get_report(request_id=submitted["request_id"])
                if not report:
                    raise RuntimeError("B2_REPORT_NOT_CREATED")
                audits.freeze_input(freeze_report_bundle(reports, report["report_id"]))
                queue_audit(audits, report["report_id"])
                if not AuditWorker(audits).run_once():
                    raise RuntimeError("B2_AUDIT_WORKER_DID_NOT_RUN")
                presentation = build_report_presentation(
                    reports, report["report_id"], instrument=instrument, mode=mode, language="zh-CN"
                )
                snapshot = reports.load_registry_snapshot(request_id=report["request_id"])
                audit = audits.latest(report["report_id"])
                passed = all((
                    presentation["eligibility"] == "AUDIT_PASSED_SHADOW_ONLY",
                    presentation["instrument"] == instrument,
                    presentation["mode"] == mode,
                    presentation["context_id"] == report["context_id"] == submitted["context_id"],
                    presentation["registry_snapshot_id"] == snapshot["registry_snapshot_id"] == audit["registry_snapshot_id"],
                    presentation["position_summary"]["source"] == source,
                ))
                cases.append({
                    "case_id": f"{instrument}:{mode}:{source}", "instrument": instrument, "mode": mode,
                    "position_source": source, "request_id": submitted["request_id"], "report_id": report["report_id"],
                    "context_id": report["context_id"], "registry_snapshot_id": snapshot["registry_snapshot_id"],
                    "audit_id": audit["audit_id"], "audit_status": audit["status"],
                    "eligibility": presentation["eligibility"], "passed": passed,
                })
    with reports.connect() as connection:
        attempts = [dict(row) for row in connection.execute("SELECT provider,COUNT(*) calls FROM ai_report_attempts GROUP BY provider")]
    result = {
        "schema_version": "ai6b-b2-local-canary-result-v1", "provider": "fake",
        "case_count": len(cases), "pass_count": sum(case["passed"] for case in cases), "cases": cases,
        "provider_attempts": attempts, "fake_provider_calls": sum(provider.calls for provider in provider_instances),
        "live_provider_calls": sum(row["calls"] for row in attempts if row["provider"] != "fake"),
        "paper_orders_created": 0, "production_connections": 0, "production_writes": 0,
    }
    result["passed"] = result["case_count"] == 12 and result["pass_count"] == 12 and result["live_provider_calls"] == 0
    (output_dir / "canary-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true", help="required safety acknowledgement")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.local_only:
        parser.error("--local-only is required; production execution is intentionally unsupported")
    result = run_matrix(args.output_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
