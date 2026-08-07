from __future__ import annotations
import copy,json,sqlite3,subprocess,sys
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from dashboard.ai_market_analysis.report_audit_repository import freeze_report_bundle,migrate_audit_database
from dashboard.ai_market_analysis.report_audit_service import audit_report
from dashboard.ai_market_analysis.report_jobs import ReportWorker
from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
from dashboard.ai_market_analysis.report_repository import ReportRepository,migrate_database
from dashboard.ai_market_analysis.report_service import ReportService
from .ai4_helpers import base_context,macro_items,position_plan

ROOT=Path(__file__).resolve().parents[2]

def _database(tmp_path):
    path=tmp_path/"ai_reports.db";migrate_database(path);migrate_audit_database(path);repo=ReportRepository(path)
    submitted=ReportService(repo).submit(base_context(),mode="FULL");ReportWorker(repo,lambda request:FakeAIReportProvider(request["model"])).run_once();report=repo.get_report(request_id=submitted["request_id"])
    return path,repo,report["report_id"]

def _cli(path,report_id,tmp_path,label):
    output=tmp_path/f"{label}.json";trace=tmp_path/f"{label}.trace.json"
    result=subprocess.run([sys.executable,str(ROOT/"scripts/audit_ai_market_report.py"),"--database",str(path),"--report-id",report_id,"--output",str(output),"--connection-trace",str(trace)],cwd=ROOT,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return json.loads(output.read_text(encoding="utf-8")),json.loads(trace.read_text(encoding="utf-8"))

def _future_base():
    value=base_context();value["context_id"]="future_"+value["context_id"];decision=datetime.fromisoformat(value["decision_time"].replace("Z","+00:00"))+timedelta(days=1);value["decision_time"]=decision.isoformat().replace("+00:00","Z");value["provenance"]["fixture"]=True;return value

def test_real_sqlite_replay_survives_future_context_and_report(tmp_path):
    path,repo,report_id=_database(tmp_path);before,_=_cli(path,report_id,tmp_path,"before")
    future=ReportService(repo).submit(_future_base(),mode="FULL");ReportWorker(repo,lambda request:FakeAIReportProvider(request["model"])).run_once();assert repo.get_report(request_id=future["request_id"])
    after,_=_cli(path,report_id,tmp_path,"after");assert before["payload_hash"]==after["payload_hash"] and before["audit_id"]==after["audit_id"] and before["hard_failures"]==after["hard_failures"]

def test_real_sqlite_replay_survives_future_position_and_macro(tmp_path):
    path,repo,report_id=_database(tmp_path);before,_=_cli(path,report_id,tmp_path,"before")
    future=ReportService(repo).submit(_future_base(),mode="POSITION_AWARE",position_source="USER_DECLARED",position_plan=position_plan(),macro_evidence=macro_items(),current_mark=1900)
    ReportWorker(repo,lambda request:FakeAIReportProvider(request["model"])).run_once();assert repo.get_report(request_id=future["request_id"])
    after,_=_cli(path,report_id,tmp_path,"after");assert before["payload_hash"]==after["payload_hash"] and before["scorecard"]==after["scorecard"]

def test_audit_process_opens_only_ai_report_database_after_market_db_changes(tmp_path):
    path,repo,report_id=_database(tmp_path);paper=tmp_path/"paper.db";micro=tmp_path/"microstructure.db"
    for external in (paper,micro):
        conn=sqlite3.connect(external)
        try:conn.execute("CREATE TABLE future_data(ts INTEGER,value REAL)");conn.executemany("INSERT INTO future_data VALUES(?,?)",[(x,float(x)) for x in range(100)]);conn.commit()
        finally:conn.close()
    _,opened=_cli(path,report_id,tmp_path,"trace");assert opened and {Path(x).resolve() for x in opened}=={path.resolve()}

def test_audit_replay_survives_market_database_removal(tmp_path):
    path,repo,report_id=_database(tmp_path);paper=tmp_path/"paper.db";micro=tmp_path/"microstructure.db"
    for external in (paper,micro):
        conn=sqlite3.connect(external)
        try:conn.execute("CREATE TABLE future_data(value REAL)");conn.commit()
        finally:conn.close()
        external.rename(external.with_suffix(".removed"))
    first,_=_cli(path,report_id,tmp_path,"one");second,_=_cli(path,report_id,tmp_path,"two");assert first["payload_hash"]==second["payload_hash"]

def test_database_reload_replay_is_stable_twenty_times(tmp_path):
    path,repo,report_id=_database(tmp_path);identities=set();payloads=set();statuses=set()
    for index in range(20):
        audit,_=_cli(path,report_id,tmp_path,f"run_{index}");identities.add(audit["audit_id"]);payloads.add(audit["payload_hash"]);statuses.add(audit["status"])
    assert len(identities)==len(payloads)==len(statuses)==1

def test_registry_snapshot_rows_reject_update_and_delete(tmp_path):
    path,repo,report_id=_database(tmp_path);bundle=freeze_report_bundle(repo,report_id)
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError,match="REGISTRY_SNAPSHOT_MUTATED"):conn.execute("UPDATE ai_report_registry_snapshots SET prompt_hash='tampered' WHERE registry_snapshot_id=?",(bundle["registry_snapshot_id"],))
        with pytest.raises(sqlite3.IntegrityError,match="REGISTRY_SNAPSHOT_MUTATED"):conn.execute("DELETE FROM ai_report_registry_snapshots WHERE registry_snapshot_id=?",(bundle["registry_snapshot_id"],))

def test_frozen_bundle_queries_use_bounded_indexes(tmp_path):
    path,repo,report_id=_database(tmp_path);report=repo.get_report(report_id=report_id);request_id=report["request_id"];context_id=report["context_id"]
    with repo.connect() as conn:
        snapshot_id=conn.execute("SELECT registry_snapshot_id FROM ai_report_requests WHERE request_id=?",(request_id,)).fetchone()[0]
        plans=[]
        for sql,arg in (("SELECT * FROM ai_market_reports WHERE report_id=?",report_id),("SELECT * FROM ai_report_requests WHERE request_id=?",request_id),("SELECT * FROM ai_market_contexts WHERE context_id=?",context_id),("SELECT * FROM ai_report_registry_snapshots WHERE registry_snapshot_id=?",snapshot_id),("SELECT prompt_hash FROM ai_report_attempts WHERE request_id=? ORDER BY attempt_number DESC LIMIT 1",request_id)):
            plans.extend(str(x[3]) for x in conn.execute("EXPLAIN QUERY PLAN "+sql,(arg,)).fetchall())
    assert all("SEARCH" in plan and "SCAN" not in plan for plan in plans),plans
