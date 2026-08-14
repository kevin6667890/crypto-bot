from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from dashboard.ai_market_analysis.backup_restore import create_consistent_backup, verify_isolated_restore
from dashboard.ai_market_analysis.production_readiness import check_candidate_compose, check_nginx_readiness
from dashboard.ai_market_analysis.report_alerts import evaluate_alerts, load_alert_policy
from dashboard.ai_market_analysis.report_jobs import ReportWorker, TokenBudget
from dashboard.ai_market_analysis.report_jobs import provider_retry_allowed
from dashboard.ai_market_analysis.report_provider import ProviderError
from dashboard.ai_market_analysis.report_migrations import MigrationError, apply_migrations, migration_manifest
from dashboard.ai_market_analysis.report_repository import ReportRepository, migrate_database
from dashboard.ai_market_analysis.report_service import ReportService, output_limit
from dashboard.ai_market_analysis.storage_budget import maximum_ai_bytes_per_request, project_capacity
from dashboard.ai_market_analysis.live_provider_guard import HARD_STOP_EVENTS, assert_live_provider_allowed, status, trip, trip_if_armed
from dashboard.ai_market_analysis.retention_archive import archive_hot_expired, expire_archive_payloads, verify_archive
from .ai4_helpers import base_context


def test_manifest_hashes_every_ai_report_migration():
    manifest = migration_manifest()
    assert [item["order"] for item in manifest["migrations"]] == [1, 2, 3, 4, 5]
    assert all(not item["destructive"] for item in manifest["migrations"])
    assert all(not item["touches_paper_db"] and not item["touches_microstructure_db"] for item in manifest["migrations"])


def test_presentation_query_plans_are_covered_without_temp_sort(tmp_path):
    database = tmp_path / "plans.db"
    migrate_database(database)
    connection = sqlite3.connect(database)
    cases = [
        ("idx_ai_requests_presentation", "SELECT * FROM ai_report_requests WHERE instrument=? AND mode=? AND language=? ORDER BY created_at DESC,request_id DESC LIMIT 1", ("BTC", "FULL", "zh-CN")),
        ("idx_ai_reports_presentation", "SELECT * FROM ai_market_reports WHERE mode=? AND language=? ORDER BY created_at DESC,report_id DESC LIMIT 1", ("FULL", "zh-CN")),
        ("idx_ai_contexts_watermark", "SELECT * FROM ai_market_contexts WHERE instrument=? ORDER BY decision_time DESC,context_id DESC LIMIT 1", ("BTC",)),
    ]
    for index, query, arguments in cases:
        plan = " ".join(str(row) for row in connection.execute("EXPLAIN QUERY PLAN " + query, arguments))
        assert index in plan
        assert "TEMP B-TREE" not in plan
    connection.close()


def test_atomic_batch_rolls_back_every_pending_migration(tmp_path, monkeypatch):
    root = tmp_path / "migrations"
    root.mkdir()
    sql = {
        "001.sql": "CREATE TABLE ai_report_migrations(migration_key TEXT PRIMARY KEY,schema_version TEXT NOT NULL,file_sha256 TEXT NOT NULL,completed_at TEXT NOT NULL); CREATE TABLE first_step(id INTEGER);",
        "002.sql": "CREATE TABLE second_step(id INTEGER);",
        "003.sql": "THIS IS INVALID SQL;",
    }
    items = []
    for order, (name, content) in enumerate(sql.items(), 1):
        path = root / name
        path.write_text(content, encoding="utf-8")
        items.append({"order": order, "key": f"m{order}", "schema_version": f"v{order}", "file": name,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "destructive": False,
                      "touches_paper_db": False, "touches_microstructure_db": False})
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"migrations": items}), encoding="utf-8")
    monkeypatch.setattr("dashboard.ai_market_analysis.report_migrations.MIGRATION_ROOT", root)
    monkeypatch.setattr("dashboard.ai_market_analysis.report_migrations.MANIFEST_PATH", manifest)
    database = tmp_path / "atomic.db"
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(database)
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 0
    connection.close()


def _prepare_version(database: Path, through: int) -> sqlite3.Connection:
    manifest = migration_manifest()
    root = Path(__file__).resolve().parents[2] / "migrations/ai_report"
    connection = sqlite3.connect(database)
    for item in manifest["migrations"][:through]:
        connection.executescript((root / item["file"]).read_text(encoding="utf-8"))
        connection.execute("INSERT INTO ai_report_migrations VALUES(?,?,?,?)",
                           (item["key"], item["schema_version"], item["sha256"], "2026-01-01T00:00:00Z"))
    connection.commit()
    return connection


def test_ai4_ai5_ai6a_upgrade_paths_preserve_immutable_rows(tmp_path):
    ai4 = tmp_path / "ai4.db"
    connection = _prepare_version(ai4, 1)
    connection.execute("INSERT INTO ai_market_contexts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("ctx","base","v","BTC","2026-01-01T00:00:00Z","none","none","{}","h","{}","OK","2026-01-01T00:00:00Z"))
    connection.execute("INSERT INTO ai_report_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("req","identity","ctx","BTC","QUICK","zh-CN","p","fake","fake",900,"2026-01-01T00:00:00Z"))
    connection.execute("INSERT INTO ai_market_reports VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("rep","req","ctx","QUICK","zh-CN","{}","rh","fake","fake","p","legacy body","PENDING","2026-01-01T00:00:00Z"))
    before_report = connection.execute("SELECT * FROM ai_market_reports").fetchone();connection.commit();connection.close()
    apply_migrations(ai4);connection=sqlite3.connect(ai4);assert connection.execute("SELECT * FROM ai_market_reports").fetchone()==before_report;connection.close()

    ai5 = tmp_path / "ai5.db"
    connection = _prepare_version(ai5, 2)
    connection.execute("INSERT INTO ai_report_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("a","r","q","c","rh","ch","av","pv","PASSED",1.0,1,"{}","ph","2026-01-01T00:00:00Z"))
    before_audit=connection.execute("SELECT * FROM ai_report_audits").fetchone();connection.commit();connection.close()
    apply_migrations(ai5);connection=sqlite3.connect(ai5);assert connection.execute("SELECT * FROM ai_report_audits").fetchone()==before_audit;connection.close()

    ai6a = tmp_path / "ai6a.db"
    connection = _prepare_version(ai6a, 3)
    snapshot=("s","q","rc","ec","sv","fv","nv","{}","fh","{}","nh","ph","{}","sh","2026-01-01T00:00:00Z")
    connection.execute("INSERT INTO ai_report_registry_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",snapshot)
    before_snapshot=connection.execute("SELECT * FROM ai_report_registry_snapshots").fetchone();connection.commit();connection.close()
    apply_migrations(ai6a);connection=sqlite3.connect(ai6a);assert connection.execute("SELECT * FROM ai_report_registry_snapshots").fetchone()==before_snapshot;connection.close()


def test_manifest_hash_mismatch_fails_before_database_write(tmp_path, monkeypatch):
    root = tmp_path / "migrations"
    root.mkdir()
    (root / "001.sql").write_text("CREATE TABLE x(id INTEGER);", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"migrations": [{"order": 1, "key": "m1", "schema_version": "v1", "file": "001.sql",
        "sha256": "0" * 64, "destructive": False, "touches_paper_db": False, "touches_microstructure_db": False}]}), encoding="utf-8")
    monkeypatch.setattr("dashboard.ai_market_analysis.report_migrations.MIGRATION_ROOT", root)
    monkeypatch.setattr("dashboard.ai_market_analysis.report_migrations.MANIFEST_PATH", manifest)
    database = tmp_path / "must-not-exist.db"
    with pytest.raises(MigrationError, match="MIGRATION_HASH_MISMATCH"):
        apply_migrations(database)
    assert not database.exists()


def test_consistent_backup_and_isolated_restore(tmp_path):
    database = tmp_path / "ai_market_reports.db"
    migrate_database(database)
    config = tmp_path / "flags.json"
    config.write_text('{"AI_MARKET_REPORTS_ENABLED":false}', encoding="utf-8")
    backup = tmp_path / "secured-backup"
    created = create_consistent_backup(database, backup, state_files={"feature_flags": config}, backup_id="backup-test")
    assert created["consistent"] is True and created["database_status"] == "BACKED_UP"
    restored = verify_isolated_restore(backup)
    assert restored["integrity_check"] == "ok"
    assert restored["artifact_hashes_valid"] is True
    assert restored["temporary_copy_deleted"] is True
    assert len(restored["schema_versions"]) == 5


def test_absent_database_still_backs_up_deployment_state(tmp_path):
    state = tmp_path / "deployment.json"
    state.write_text("{}", encoding="utf-8")
    backup = tmp_path / "backup"
    created = create_consistent_backup(tmp_path / "missing-ai-report.db", backup, state_files={"deployment": state})
    assert created["database_status"] == "DATABASE_NOT_YET_PRESENT"
    restored = verify_isolated_restore(backup)
    assert restored["integrity_check"] == "NOT_APPLICABLE_DATABASE_NOT_YET_PRESENT"
    assert restored["temporary_copy_deleted"] is True


@pytest.mark.parametrize("name", ["paper_trades.db", "market_microstructure.db"])
def test_backup_refuses_legacy_database_targets(tmp_path, name):
    with pytest.raises(MigrationError, match="LEGACY_DATABASE_TARGET_FORBIDDEN"):
        create_consistent_backup(tmp_path / name, tmp_path / "backup")


def test_candidate_compose_and_nginx_are_fail_closed():
    root = Path(__file__).resolve().parents[2]
    compose = check_candidate_compose(root / "deploy/compose/ai6b-production-candidate.yml")
    nginx = check_nginx_readiness(root / "deploy/nginx-ai6b-production.conf")
    assert compose["passed"], compose
    assert nginx["passed"], nginx
    value = yaml.safe_load((root / "deploy/compose/ai6b-production-candidate.yml").read_text(encoding="utf-8"))
    assert value["services"]["audit-worker"].get("secrets") is None
    assert "ai_report_provider_key" not in value["services"]["paper-api"]["secrets"]


def test_approved_budget_and_retention_are_frozen():
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "config/ai6b_canary_policy.json").read_text(encoding="utf-8"))
    assert policy["budget"] == {
        "live_provider_requests_per_24h": 15, "global_live_provider_concurrency": 1,
        "per_instrument_concurrency": 1, "queue_max": 10, "per_request_input_tokens": 500000,
        "quick_output_tokens": 200000, "full_output_tokens": 200000, "position_output_tokens": 200000,
        "daily_input_tokens": 500000, "daily_output_tokens": 1000000, "daily_total_tokens": 1500000,
        "daily_currency_cap_usd": 2.0, "cost_status": "REQUIRES_RUNTIME_AUDIT",
        "on_any_limit": "BLOCK_NEW_LIVE_PROVIDER_CALLS"}
    assert policy["retention"]["context_hot_days"] == 30
    assert policy["retention"]["archive_min_day"] == 31
    assert policy["retention"]["archive_max_day"] == 365
    assert policy["retention"]["user_declared_production_canary"] == "DISABLED"
    assert policy["privacy"]["status"] == "APPROVED_FOR_NONE_AND_PAPER_INTERNAL_SHADOW"
    assert policy["privacy"]["user_declared"] == "NOT_APPROVED"


def test_live_provider_is_blocked_until_price_audit(tmp_path):
    path = tmp_path / "reports.db"
    migrate_database(path)
    repository = ReportRepository(path)
    item = ReportService(repository).submit(base_context(), mode="QUICK", provider="deepseek", model="approved-later")
    worker = ReportWorker(repository, lambda _: pytest.fail("provider must not be constructed"), budget=TokenBudget())
    assert worker.run_once() is True
    assert repository.status(item["request_id"])["status"] == "BUDGET_BLOCKED"
    assert repository.status(item["request_id"])["events"][-1]["payload_json"] == '{"code":"PROVIDER_PRICE_AUDIT_REQUIRED"}'


def test_live_request_count_and_currency_caps(tmp_path, monkeypatch):
    path = tmp_path / "reports.db"
    migrate_database(path)
    repository = ReportRepository(path)
    for number in range(15):
        repository.save_attempt({"attempt_id":f"a{number}","request_id":f"r{number}","attempt_number":1,"provider":"deepseek","model":"m","started_at":"2099-01-01T00:00:00Z","completed_at":None,"latency_ms":None,"http_status":None,"input_tokens":1,"output_tokens":1,"total_tokens":2,"finish_reason":None,"raw_response_hash":None,"parse_status":"VALID","validation_status":"VALID","failure_code":None,"sanitized_error":None,"cost_status":"AUDITED","currency":"USD","price_schedule_version":"test","estimated_cost":0.1,"prompt_hash":None})
    # Move the stored attempts into today's UTC day without depending on wall-clock string construction.
    with repository.connect() as connection:
        connection.execute("UPDATE ai_report_attempts SET started_at=?", (__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%dT00:00:00Z'),))
    monkeypatch.setenv("AI_REPORT_COST_STATUS","AUDITED");monkeypatch.setenv("AI_REPORT_INPUT_USD_PER_MILLION","1");monkeypatch.setenv("AI_REPORT_OUTPUT_USD_PER_MILLION","1")
    assert TokenBudget().reason(repository,"BTC",1,1,"deepseek") == "LIVE_PROVIDER_REQUEST_CAP"


def test_output_limits_cannot_exceed_approved_values(monkeypatch):
    monkeypatch.setenv("AI_REPORT_QUICK_OUTPUT_TOKENS", "999999")
    monkeypatch.setenv("AI_REPORT_FULL_OUTPUT_TOKENS", "999999")
    assert output_limit("QUICK") == 200000
    assert output_limit("FULL") == 200000
    assert output_limit("POSITION_AWARE") == 200000


def test_alert_policy_is_complete_and_stop_capable():
    policy = load_alert_policy()
    assert len(policy["alerts"]) >= 24
    events = evaluate_alerts({"provider_401_5m": 1, "queue_depth": 11}, policy)
    assert {item["alert_id"] for item in events} == {"provider_401", "queue_growth"}
    assert all(item["stop"] for item in events)


def test_storage_projection_is_conservative_and_fail_closed():
    assert maximum_ai_bytes_per_request() > 5_000_000
    value = project_capacity(filesystem_total_bytes=110_570_917_888, filesystem_used_bytes=34_603_118_592,
        current_microstructure_bytes=19_059_113_984, microstructure_coverage_days=18.18135)
    assert value["within_24h_budget"] is True
    assert value["ai_hot_30d_bytes"] == value["maximum_ai_daily_growth_bytes"] * 30
    assert value["ai_logical_90d_total_bytes"] == value["maximum_ai_daily_growth_bytes"] * 90
    assert value["wal_peak_reserve_bytes"] == 512 * 1024**2
    assert value["docker_image_cache_reserve_bytes"] == 8 * 1024**3
    assert value["backup_temporary_space_bytes"] == value["ai_hot_30d_bytes"] * 2
    assert value["remaining_hot_retention_days"] == pytest.approx(30 - 18.18135)
    assert value["projected_non_ai_daily_growth_bytes"] == int(19_059_113_984 / 18.18135)


def test_storage_projection_does_not_double_count_existing_hot_coverage():
    value = project_capacity(filesystem_total_bytes=110_570_917_888, filesystem_used_bytes=37_720_215_552,
        current_microstructure_bytes=21_984_579_584, microstructure_coverage_days=18.08,
        observed_non_ai_daily_growth_bytes=2_122_609_849)
    expected_remaining = 30 - 18.08
    expected = (37_720_215_552 + 2_122_609_849 * expected_remaining +
                value["ai_logical_90d_total_bytes"] + value["wal_peak_reserve_bytes"] +
                value["docker_image_cache_reserve_bytes"] + value["backup_temporary_space_bytes"])
    assert value["projected_used_with_30d_hot_and_90d_logical_bytes"] == int(expected)
    assert value["within_30d_hot_and_90d_logical_budget"] is True


@pytest.mark.parametrize("error,allowed", [
    (ProviderError("CONNECTION_ERROR", retryable=True), True),
    (ProviderError("TIMEOUT", retryable=True), True),
    (ProviderError("HTTP_429", retryable=True, http_status=429), True),
    (ProviderError("HTTP_503", retryable=True, http_status=503), True),
    (ProviderError("SCHEMA_FAILURE", retryable=True), False),
    (ProviderError("AUDIT_FAILURE", retryable=True), False),
    (ProviderError("NUMERIC_HALLUCINATION", retryable=True), False),
    (ProviderError("REFERENCE_FAILURE", retryable=True), False),
    (ProviderError("HTTP_422", retryable=True, http_status=422), False),
])
def test_provider_retry_whitelist_is_authoritative(error, allowed):
    assert provider_retry_allowed(error) is allowed


def test_schema_failure_never_causes_provider_retry(tmp_path):
    path=tmp_path/"reports.db";migrate_database(path);repository=ReportRepository(path)
    item=ReportService(repository).submit(base_context(),mode="QUICK",provider="fake",model="invalid_json")
    from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
    provider=FakeAIReportProvider("fake",behavior="invalid_json");worker=ReportWorker(repository,lambda _:provider,budget=TokenBudget())
    assert worker.run_once() is True
    assert provider.calls == 1
    assert repository.status(item["request_id"])["status"] == "FAILED_FINAL"


def test_kill_switch_blocks_next_call_well_inside_sixty_seconds(tmp_path, monkeypatch):
    path=tmp_path/"live-provider-disabled.json";monkeypatch.setenv("AI_REPORT_LIVE_PROVIDER_ENABLED","true");monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE",str(path))
    started=time.monotonic();result=trip("CONTEXT_MISMATCH",path=path,evidence_id="isolated-test")
    with pytest.raises(ProviderError,match="LIVE_PROVIDER_KILL_SWITCHED"):
        assert_live_provider_allowed(path)
    assert time.monotonic()-started < 60
    assert result["live_provider_disabled"] is True and status(path)["event"] == "CONTEXT_MISMATCH"
    assert set(HARD_STOP_EVENTS) >= {"WRONG_SYMBOL","WRONG_MODE","AUDIT_MISMATCH","REGISTRY_MISMATCH","SECRET_EXPOSURE","DB_CORRUPTION","DISK_CRITICAL"}


def test_automatic_hard_stop_is_armed_in_candidate(tmp_path,monkeypatch):
    path=tmp_path/"automatic-stop.json";monkeypatch.setenv("AI6B_KILL_SWITCH_AUTOMATION_ENABLED","true");monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE",str(path))
    assert trip_if_armed("AUDIT_MISMATCH",evidence_id="isolated-auto-test")["live_provider_disabled"] is True
    assert status(path)["event"]=="AUDIT_MISMATCH"


def test_interrupted_live_call_never_auto_retries_or_risks_duplicate_charge(tmp_path,monkeypatch):
    database=tmp_path/"interrupted.db";migrate_database(database);repository=ReportRepository(database)
    item=ReportService(repository).submit(base_context(),mode="QUICK",provider="deepseek",model="future-approved")
    repository.event(item["request_id"],"RUNNING",{"attempt":1});repository.interrupt_running()
    switch=tmp_path/"kill.json";monkeypatch.setenv("AI6B_KILL_SWITCH_AUTOMATION_ENABLED","true");monkeypatch.setenv("AI_REPORT_KILL_SWITCH_FILE",str(switch))
    called=False
    def factory(_request):
        nonlocal called;called=True;raise AssertionError("provider must not be called")
    assert ReportWorker(repository,factory).run_once() is True
    assert called is False and repository.status(item["request_id"])["status"]=="FAILED_FINAL"
    assert status(switch)["event"]=="DUPLICATE_PROVIDER_CHARGE"


def test_retention_archives_complete_identity_closure_before_pruning(tmp_path):
    database=tmp_path/"ai-market-reports.db";archive=tmp_path/"independent-archive";migrate_database(database)
    repository=ReportRepository(database);item=ReportService(repository).submit(base_context(),mode="QUICK",provider="fake",model="fake")
    from dashboard.ai_market_analysis.report_provider import FakeAIReportProvider
    assert ReportWorker(repository,lambda _:FakeAIReportProvider()).run_once() is True
    old=(datetime.now(timezone.utc)-timedelta(days=31)).replace(microsecond=0).isoformat().replace("+00:00","Z")
    with repository.connect() as connection:
        connection.execute("UPDATE ai_report_requests SET created_at=?",(old,))
    dry=archive_hot_expired(database,archive,apply=False);assert [x["request_id"] for x in dry["archived"]]==[item["request_id"]]
    applied=archive_hot_expired(database,archive,apply=True);manifest=Path(applied["archived"][0]["manifest"])
    verified=verify_archive(manifest);assert verified["request_id"]==item["request_id"]
    identity=json.loads(manifest.read_text(encoding="utf-8"))["identity"]
    assert identity["request_id"]==item["request_id"] and identity["context_id"]==item["context_id"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_report_requests").fetchone()[0]==0
        trigger=connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='trg_ai_registry_snapshot_no_delete'").fetchone()[0]
        assert trigger==1
    assert applied["vacuum_used"] is False
    expired=expire_archive_payloads(archive,now=datetime.now(timezone.utc)+timedelta(days=366))
    assert expired["expired_payloads"]==[item["request_id"]] and expired["identity_manifests_deleted"]==0
    assert manifest.exists() and (archive/"expiry-receipts"/manifest.name).exists()


def test_candidate_rate_limits_and_frontend_stop_contract_are_frozen():
    root=Path(__file__).resolve().parents[2]
    backend=(root/"dashboard/paper_api.py").read_text(encoding="utf-8")
    frontend=(root/"frontend/src/aiMarketAnalysis/ShadowMarketAnalysisPage.tsx").read_text(encoding="utf-8")
    for token in ('"ai-presentation-read-minute",10,60','"ai-position-detail-minute",5,60','"ai-report-generation-minute",2,60','"ai-audit-trigger-minute",5,60'):
        assert token in backend
    assert "status===401" in frontend and "status===429" in frontend
    assert "AUDITED_BODY_SELECTION_MISMATCH_HARD_STOP" in frontend
    assert '"/api/ai-market-analysis/v1/kill-switch"' in backend
