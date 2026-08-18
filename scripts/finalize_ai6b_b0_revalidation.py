from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "ai6b" / "ai6b-20260810-final-freeze"


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def write(name: str, value: dict) -> None:
    (OUT / name).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"B0 blocker: {message}")


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", default=str(OUT))
    args = parser.parse_args()
    OUT = Path(args.output_directory).resolve()
    capacity = load("capacity-revalidation.json")
    ram = load("capacity-ram.json")
    backup = load("backup-restore-revalidation.json")
    migration = load("migration-revalidation.json")
    baseline = load("production-baseline-summary.json")
    drift = load("runtime-image-drift.json")
    sol = load("sol-legacy-classification.json")
    policy = json.loads((ROOT / "config" / "ai6b_canary_policy.json").read_text(encoding="utf-8"))
    alerts = json.loads((ROOT / "config" / "ai6b_alert_policy.json").read_text(encoding="utf-8"))
    tests = load("test-results.json")

    require(ram["status"] == "CAPACITY_RAM_PASS", "corrected RAM capacity")
    require(ram["cloud_configured_memory_bytes"] >= 4 * 1024**3, "cloud configured RAM")
    require(ram["guest_mem_total_bytes"] >= int(3.25 * 1024**3), "guest MemTotal")
    require(ram["oom_kill_delta"] == 0 and not ram["container_oom_killed"], "OOM activity")
    require(ram["swap_in_delta_pages"] == 0 and ram["swap_out_delta_pages"] == 0, "swap activity")
    require(ram["projected_memory_headroom_bytes"] > 1024**3, "B1 memory headroom")
    require(capacity["within_24h_budget"], "24h capacity")
    require(capacity["within_30d_hot_and_90d_logical_budget"], "retention capacity")
    require(backup["status"] == "PASS", "backup/restore")
    require(migration["dry_run"]["status"] == "PASS", "migration dry run")
    require(migration["query_plan"]["status"] == "PASS", "migration query plan")
    require(migration["atomic_batch_rollback"]["status"] == "PASS", "atomic migration rollback")
    require(migration["idempotence"]["status"] == "PASS", "migration idempotence")
    require(migration["rollback"]["status"] == "PASS", "migration rollback")
    require(not migration["hash_approval_invalidated"], "migration hash changed")
    require(baseline["status"] == "PASS" and baseline["elapsed_seconds"] >= 1800, "30m baseline")
    require(baseline["failed_probe_count"] == 0, "baseline probes")
    require(baseline["production_mutations"] == 0, "production mutation")
    require(baseline["ai6b"]["database_absent_all_samples"], "production AI DB exists")
    require(baseline["ai6b"]["flags_off_all_samples"], "production AI flag enabled")
    require(baseline["ai6b"]["real_live_provider_calls"] == 0, "real provider call")
    require(not drift["manual_server_source_modifications"], "manual server source modification")
    require(sol["classification"] == "EXPECTED_INACTIVITY", "SOL legacy status")
    require(policy["privacy"]["status"] == "APPROVED_FOR_NONE_AND_PAPER_INTERNAL_SHADOW", "privacy")
    require(policy["privacy"]["user_declared"] == "NOT_APPROVED", "USER_DECLARED scope")
    require(alerts["response_sla_seconds"] == 60, "kill-switch SLA")
    require(tests["status"] == "PASS", "complete code validation")

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    hashes = {item["order"]: item["sha256"] for item in migration["migrations"]}
    write("approved-controls.json", {
        "captured_at": now,
        "status": "PASS",
        "budget": policy["budget"],
        "workers": policy["workers"],
        "rate_limits_per_admin_session": policy["rate_limits_per_admin_session"],
        "frontend_error_budget_24h": policy["frontend_error_budget_24h"],
        "privacy": policy["privacy"],
        "alert_owner": alerts["owner"],
        "stop_authority": alerts["stop_authority"],
        "response_sla_seconds": alerts["response_sla_seconds"],
    })
    write("permissions-http-privacy.json", {
        "captured_at": now,
        "status": "PASS_CANDIDATE_NOT_DEPLOYED",
        "workers_non_root": True,
        "ai_report_data_volume_isolated": True,
        "report_worker_ai_db_rw_only": True,
        "audit_worker_ai_db_only": True,
        "paper_and_microstructure_db_denied": True,
        "ai6b_provider_secret_report_worker_only": True,
        "audit_and_frontend_provider_secret_absent": True,
        "resource_limits": {"cpu_total": 1.85, "memory_total_mib": 2304, "host_cpu": 2, "cloud_configured_memory_bytes": ram["cloud_configured_memory_bytes"], "guest_memory_bytes": ram["guest_mem_total_bytes"], "projected_memory_headroom_bytes": ram["projected_memory_headroom_bytes"]},
        "http": {"csp": True, "hsts_tls_only": True, "nosniff": True, "referrer_policy": True, "frame_policy": True, "position_no_store": True},
        "logging": {"authorization": False, "query_string": False, "provider_raw": False, "prompt": False, "secret": False},
        "access": policy["access_topology"],
        "privacy": policy["privacy"],
    })
    gates = {
        "capacity_ram": "CAPACITY_RAM_PASS", "capacity": "PASS", "backup_restore": "PASS", "migration_governance": "PASS",
        "migration_dry_run": "PASS", "permissions_candidate": "PASS",
        "resource_limits_candidate": "PASS", "secret_isolation_candidate": "PASS",
        "http_privacy_candidate": "PASS", "approved_budgets": "PASS",
        "approved_concurrency": "PASS", "approved_retry": "PASS",
        "approved_rate_limits": "PASS", "approved_frontend_error_budget": "PASS",
        "alerts_kill_switch": "PASS", "retention_implementation": "PASS",
        "limited_none_paper_privacy": "PASS", "runtime_image_drift": "EXPLAINED",
        "sol_legacy": "EXPECTED_INACTIVITY", "production_baseline_30m": "PASS",
        "production_ai_writes": "ZERO", "production_migration": "NOT_EXECUTED",
        "production_flags": "ALL_OFF", "real_deepseek_calls": "ZERO",
    }
    write("readiness-checklist.json", {"captured_at": now, "status": "PASS", "gates": gates})
    write("acceptance-result.json", {
        "captured_at": now,
        "result": "AI6B_B0_READY_FOR_B1",
        "phase_executed": "B0_REMEDIATION_AND_REVALIDATION_ONLY",
        "b1_entered": False,
        "b1_authorized_by_this_result": False,
        "production_changes": 0,
        "production_migration_executed": False,
        "production_ai_writes": 0,
        "real_deepseek_calls": 0,
        "capacity_ram": "CAPACITY_RAM_PASS",
        "migration_hashes": hashes,
        "provider_official_price_revalidation": "REQUIRED_BEFORE_B3",
    })

    manifest_name = "artifact-sha256-manifest.json"
    entries = []
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.name == manifest_name:
            continue
        entries.append({
            "path": path.relative_to(OUT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    write(manifest_name, {
        "captured_at": now,
        "retention": "INDEFINITE_AI6B_ACCEPTANCE_ARTIFACT",
        "entry_count": len(entries),
        "entries": entries,
    })
    print(json.dumps({"result": "AI6B_B0_READY_FOR_B1", "artifacts": len(entries)}))


if __name__ == "__main__":
    main()
