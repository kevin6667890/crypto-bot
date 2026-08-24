"""Static fail-closed checks for the AI-6B candidate deployment files."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml

REQUIRED_FALSE_FLAGS=("AI_MARKET_REPORTS_ENABLED","AI_MARKET_REPORT_WORKER_ENABLED","AI_USER_POSITION_PLANS_ENABLED","AI_MACRO_HTTP_FETCH_ENABLED","AI_REPORT_LIVE_PROVIDER_ENABLED","AI_REPORT_AUDIT_ENABLED","AI_REPORT_AUDIT_WORKER_ENABLED","AI_REPORT_AUTO_AUDIT_ENABLED","AI_REPORT_EVALUATION_ENABLED","AI_MARKET_ANALYSIS_PRESENTATION_ENABLED")

def _memory_limit_mib(value: object) -> float:
    """Normalize Compose memory limits without treating ``1g`` as one MiB."""
    text = str(value).strip().lower()
    multipliers = {"k": 1 / 1024, "m": 1, "g": 1024}
    suffix = text[-1:] if text[-1:] in multipliers else ""
    number = text[:-1] if suffix else text
    return float(number) * multipliers.get(suffix, 1 / (1024 * 1024))

def check_nginx_readiness(path:str|Path)->dict[str,Any]:
    text=Path(path).read_text(encoding="utf-8")
    checks={
        "tls_listener":"listen 8443 ssl" in text,
        "hsts":"Strict-Transport-Security" in text,
        "csp":"Content-Security-Policy" in text,
        "x_content_type_options":"X-Content-Type-Options" in text,
        "referrer_policy":"Referrer-Policy" in text,
        "frame_policy":"frame-ancestors 'none'" in text and "X-Frame-Options \"DENY\"" in text,
        "sensitive_no_store":'add_header Cache-Control "no-store" always' in text,
        "authorization_not_logged":"$http_authorization" not in text.split("server {",1)[0],
        "query_not_logged":"$request " not in text and "$args" not in text and "$query_string" not in text,
        "sanitized_uri_logged":"$request_method $uri $server_protocol" in text,
    }
    return {"passed":all(checks.values()),"checks":checks}

def check_candidate_compose(path:str|Path)->dict[str,Any]:
    value=yaml.safe_load(Path(path).read_text(encoding="utf-8"));services=value["services"];checks={}
    for name in ("paper-api","microstructure-collector","report-worker","audit-worker","frontend"):
        service=services[name];checks[f"{name}_nonroot"]=str(service.get("user",value.get("x-app-security",{}).get("user",""))).split(":")[0] not in {"","0","root"};checks[f"{name}_limits"]=all(key in service or key in value.get("x-app-security",{}) for key in ("pids_limit","mem_limit","cpus"))
    report_secrets=set(services["report-worker"].get("secrets",[]));audit_secrets=set(services["audit-worker"].get("secrets",[]));frontend_secrets=set(services["frontend"].get("secrets",[]))
    checks["provider_secret_report_only"]="ai_report_provider_key" in report_secrets and "ai_report_provider_key" not in audit_secrets and "ai_report_provider_key" not in frontend_secrets and "ai_report_provider_key" not in set(services["paper-api"].get("secrets",[]))
    report_mounts=" ".join(services["report-worker"].get("volumes",[]));audit_mounts=" ".join(services["audit-worker"].get("volumes",[]));checks["workers_isolated_from_legacy_db"]="paper-data" not in report_mounts+audit_mounts and "microstructure-data" not in report_mounts+audit_mounts
    checks["audit_only_ai_db"]="ai-report-data:/var/lib/ai-report:rw" in audit_mounts and len(services["audit-worker"].get("volumes",[]))==1
    checks["report_only_ai_db"]="ai-report-data:/var/lib/ai-report:rw" in report_mounts and len(services["report-worker"].get("volumes",[]))==1
    checks["bounded_restart"]=all(services[name].get("restart",value.get("x-app-security",{}).get("restart"))=="on-failure:3" for name in services if name!="retention-dry-run") and services["retention-dry-run"]["restart"]=="no"
    checks["two_cpu_budget"]=sum(float(services[name]["cpus"]) for name in services)<=2.0
    checks["four_gib_memory_budget"]=sum(_memory_limit_mib(services[name]["mem_limit"]) for name in services)<=3072
    checks["owner_tunnel_only"]=services["frontend"].get("ports")==["127.0.0.1:8443:8443"]
    checks["independent_archive_volume"]="ai-report-archive:/var/lib/ai-report-archive:rw" in " ".join(services["retention-dry-run"].get("volumes",[])) and "ai-report-data:/var/lib/ai-report:ro" in " ".join(services["retention-dry-run"].get("volumes",[]))
    merged_disabled=value["x-ai-disabled"]
    checks["all_flags_disabled"]=all(str(merged_disabled.get(flag)).lower()=="false" for flag in REQUIRED_FALSE_FLAGS) and str(merged_disabled.get("AI_MARKET_REPORT_SHADOW_ONLY")).lower()=="true"
    return {"passed":all(checks.values()),"checks":checks}
