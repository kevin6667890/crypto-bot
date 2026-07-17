import json
from pathlib import Path

import pytest

from dashboard.lifecycle_service import LifecycleService, POLICY_VERSION
from dashboard.research_repository import ResearchRepository
from dashboard.shadow_service import ShadowService
from dashboard.strategy_rules import DEFAULT_PARAMETERS
from dashboard.validation_repository import ValidationRepository


def repository(tmp_path:Path):
    ResearchRepository(tmp_path/"phase4.db")
    return ValidationRepository(tmp_path/"phase4.db")


def test_migration_preserves_tables_and_initializes_draft(tmp_path):
    repo=repository(tmp_path);counts=repo.table_counts()
    assert counts["strategy_configs"]==3 and counts["strategy_lifecycle"]==3 and counts["strategy_audit_log"]==3
    with repo.connect() as c: assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


def test_shadow_accounts_independent_and_restart_safe(tmp_path):
    repo=repository(tmp_path);service=ShadowService(repo)
    first=service.create({"name":"One","parameters":DEFAULT_PARAMETERS,"instruments":["BTC-USDT"]});changed={**DEFAULT_PARAMETERS,"minimum_score":80};second=service.create({"name":"Two","parameters":changed,"instruments":["BTC-USDT"]})
    assert first["config_hash"]!=second["config_hash"] and first["current_equity"]==second["current_equity"]==10000
    restarted=ShadowService(ValidationRepository(repo.db_path));assert len(restarted.list())==2


def test_shadow_pause_resume_stop_history_and_duplicate_rejection(tmp_path):
    service=ShadowService(repository(tmp_path));item=service.create({"name":"One","parameters":DEFAULT_PARAMETERS,"instruments":["BTC-USDT"]});sid=item["shadow_strategy_id"]
    assert service.action(sid,"start")["status"]=="RUNNING"
    with pytest.raises(ValueError,match="identical"):service.create({"name":"Duplicate","parameters":DEFAULT_PARAMETERS,"instruments":["BTC-USDT"]})
    assert service.action(sid,"pause")["status"]=="PAUSED" and service.action(sid,"resume")["status"]=="RUNNING"
    assert service.action(sid,"stop")["status"]=="STOPPED" and service.trades(sid)==[]


def test_lifecycle_draft_candidate_shadow_and_insufficient(tmp_path):
    repo=repository(tmp_path);service=LifecycleService(repo);item=service.list()[0]
    assert item["status"]=="Draft" and item["policy_version"]==POLICY_VERSION
    item=service.transition(item["id"],"candidate");assert item["status"]=="Candidate"
    item=service.transition(item["id"],"shadow");assert item["status"]=="Shadow"
    evaluation=service.evaluate(item["id"]);assert evaluation["evidence_sufficient"] is False and evaluation["recommended_status"]!="Qualified"


def test_qualified_requires_manual_promote_and_single_active(tmp_path):
    repo=repository(tmp_path);service=LifecycleService(repo);items=service.list()[:2]
    with repo.connect() as c:
        for item in items:c.execute("UPDATE strategy_lifecycle SET status='Qualified' WHERE id=?",(item["id"],))
    first=service.transition(items[0]["id"],"promote");second=service.transition(items[1]["id"],"promote")
    states={x["id"]:x["status"] for x in service.list()}
    assert first["status"]=="Active" and second["status"]=="Active" and states[items[0]["id"]]=="Watch" and list(states.values()).count("Active")==1


def test_lifecycle_rollback_and_audit(tmp_path):
    repo=repository(tmp_path);service=LifecycleService(repo);items=service.list()[:2]
    with repo.connect() as c:
        for item in items:c.execute("UPDATE strategy_lifecycle SET status='Qualified' WHERE id=?",(item["id"],))
    service.transition(items[0]["id"],"promote");service.transition(items[1]["id"],"promote");rolled=service.transition(items[1]["id"],"rollback")
    assert rolled["status"]=="Watch" and next(x for x in service.list() if x["id"]==items[0]["id"])["status"]=="Active"
    assert any(x["action"]=="ROLLBACK" for x in service.audit(items[1]["id"]))


def test_critical_alert_blocks_evaluation(tmp_path):
    repo=repository(tmp_path)
    with repo.connect() as c:
        c.execute("CREATE TABLE system_alerts(id INTEGER PRIMARY KEY,severity TEXT,status TEXT)");c.execute("INSERT INTO system_alerts(severity,status) VALUES('critical','OPEN')")
    service=LifecycleService(repo);item=service.list()[0];evaluation=service.evaluate(item["id"])
    critical=next(x for x in evaluation["checks"] if x["key"]=="critical_alerts")
    assert critical["status"]=="failed"
