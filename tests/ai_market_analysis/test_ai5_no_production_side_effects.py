from pathlib import Path
def test_audit_modules_do_not_touch_orders_router_copilot_or_collectors():
    changed={p.as_posix() for p in Path("dashboard/ai_market_analysis").glob("report_audit*.py")};assert changed
    for path in changed:
        text=Path(path).read_text(encoding="utf-8").lower();assert "deepseek" not in text and "paper_trades.db" not in text and "create_order" not in text
def test_original_report_column_remains_pending_only():
    source=Path("dashboard/ai_market_analysis/report_repository.py").read_text(encoding="utf-8");assert "CHECK(audit_status='PENDING')" in source and "UPDATE ai_market_reports" not in source
