from __future__ import annotations
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context,macro_items,position_plan

def generate(tmp_path,mode="FULL",position=False,macro=False):
    path=tmp_path/f"{mode}.db";migrate_database(path);repo=ReportRepository(path);item=ReportService(repo).submit(base_context(),mode=mode,position_source="USER_DECLARED" if position else "NONE",position_plan=position_plan() if position else None,macro_evidence=macro_items() if macro else [],current_mark=1900);ReportWorker(repo,lambda r:FakeAIReportProvider(r["model"])).run_once();return repo.get_report(request_id=item["request_id"])

def test_golden_full_semantics(tmp_path):
    text=generate(tmp_path)["generated_text"]
    for phrase in ("突破已经发生","突破后回踩验证","不是纯新增多头","空头回补","主动买盘同样存在","不足以确认新多全面接力","15分钟保持偏强","1小时结构偏强","周线仍偏弱","失败突破路径","不能宣布长期牛市"):assert phrase in text

def test_golden_position_semantics(tmp_path):
    report=generate(tmp_path,"POSITION_AWARE",True);text=report["generated_text"];assert report["audit_status"]=="PENDING"
    for phrase in ("原计划主要任务已经完成","剩余持仓属于需要重新决策","不能因行情继续上涨自动改变原计划","不能把短线反弹计划自动升级为长期仓位","不虚构减仓比例或数量"):assert phrase in text
    assert "卖一半" not in text

def test_no_macro_and_fixture_macro(tmp_path):
    no=generate(tmp_path)["generated_text"];assert "本次未加入已验证宏观证据" in no and "Fed" not in no
    yes=generate(tmp_path,"FULL",False,True);assert any(s["section_id"]=="MACRO_BACKGROUND" and s["macro_refs"] for s in yes["response"]["sections"])

def test_generated_text_is_server_assembled(tmp_path):
    report=generate(tmp_path);assert report["generated_text"].startswith(report["response"]["headline"]) and all(s["title"] in report["generated_text"] for s in report["response"]["sections"])
