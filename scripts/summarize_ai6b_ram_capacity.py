"""Derive the corrected AI-6B RAM gate from a production baseline and candidate Compose."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-summary", required=True)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline_summary).read_text(encoding="utf-8"))
    compose = yaml.safe_load(Path(args.compose).read_text(encoding="utf-8"))
    resources = baseline["resource"]
    services = compose["services"]
    memory_limits_mib = {
        name: int(str(service["mem_limit"]).rstrip("mM"))
        for name, service in services.items()
    }
    total_limit_bytes = sum(memory_limits_mib.values()) * 1024**2
    headroom = resources["memory_total_bytes"] - total_limit_bytes
    status = (
        resources["cloud_configured_memory_bytes"] >= 4 * 1024**3
        and resources["memory_total_bytes"] >= int(3.25 * 1024**3)
        and resources["oom_kill_delta"] == 0
        and not resources["container_oom_killed"]
        and resources["swap_in_delta_pages"] == 0
        and resources["swap_out_delta_pages"] == 0
        and resources["containers_healthy_all_samples"]
        and headroom > 1024**3
    )
    result = {
        "status": "CAPACITY_RAM_PASS" if status else "CAPACITY_RAM_FAIL",
        "cloud_configuration_source": resources["cloud_configuration_source"],
        "cloud_configured_memory_bytes": resources["cloud_configured_memory_bytes"],
        "guest_mem_total_bytes": resources["memory_total_bytes"],
        "guest_mem_available_bytes": resources["memory_available_last_bytes"],
        "guest_mem_available_min_bytes": resources["memory_available_min_bytes"],
        "guest_gate_min_bytes": resources["guest_memory_gate_min_bytes"],
        "swap_total_bytes": resources["swap_total_bytes"],
        "swap_used_bytes": resources["swap_used_last_bytes"],
        "swap_used_max_bytes": resources["swap_used_max_bytes"],
        "swap_in_delta_pages": resources["swap_in_delta_pages"],
        "swap_out_delta_pages": resources["swap_out_delta_pages"],
        "oom_kill_delta": resources["oom_kill_delta"],
        "container_oom_killed": resources["container_oom_killed"],
        "containers_healthy_all_samples": resources["containers_healthy_all_samples"],
        "existing_container_rss": [
            {"name": item["Name"], "memory_usage": item["MemUsage"], "memory_percent": item["MemPerc"]}
            for item in resources["container_stats_last"]
        ],
        "candidate_memory_limits_mib": memory_limits_mib,
        "report_worker_memory_limit_mib": memory_limits_mib["report-worker"],
        "audit_worker_memory_limit_mib": memory_limits_mib["audit-worker"],
        "candidate_total_memory_limit_bytes": total_limit_bytes,
        "projected_memory_headroom_bytes": headroom,
        "headroom_model": "guest_MemTotal_minus_all_candidate_service_hard_limits",
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, sort_keys=True))
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
