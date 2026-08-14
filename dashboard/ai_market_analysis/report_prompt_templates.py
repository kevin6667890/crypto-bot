"""Load versioned prompt files and produce a stable prompt hash."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .canonical import canonical_json, stable_hash
from .versions import AI_REPORT_PROMPT_VERSION
from .report_response_contract import provider_json_schema

PROMPT_DIR = Path(__file__).with_name("prompts")
MODE_FILES = {"QUICK":"quick_v1.txt","FULL":"full_v1.txt","POSITION_AWARE":"position_aware_v1.txt"}


def compile_prompt(compiled_context: dict[str, Any], mode: str,
                   response_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    system = (PROMPT_DIR/"common_system_v1.txt").read_text(encoding="utf-8")
    mode_prompt = (PROMPT_DIR/MODE_FILES[mode]).read_text(encoding="utf-8")+(PROMPT_DIR/"strict_projection_v2.txt").read_text(encoding="utf-8")
    metadata=response_metadata or {
        "schema_version":"ai-market-report-response-v2","source_versions":{},
        "context_id":compiled_context["context_id"],"request_id":"__SERVICE_REQUEST_ID__",
        "mode":mode,"language":"zh-CN","model":"__PROVIDER_MODEL__",
        "prompt_version":AI_REPORT_PROMPT_VERSION,"audit_status":"PENDING",
    }
    contract=provider_json_schema(metadata,compiled_context)
    narrative_context={key:value for key,value in compiled_context.items() if key!="provider_claim_pack"}
    messages = [{"role":"system","content":system},{"role":"user","content":mode_prompt+
        "\nCANONICAL_RESPONSE_JSON_SCHEMA (must match exactly; no extra fields):\n"+canonical_json(contract)+
        "\nEXPECTED_SECTION_MANIFEST (authoritative; emit exactly the required ordered list and no forbidden section):\n"+
        canonical_json(contract["expected_section_manifest"])+
        "\nFACT_REGISTRY_JSON:\n"+canonical_json(narrative_context)}]
    return {"prompt_version":AI_REPORT_PROMPT_VERSION,"messages":messages,"prompt_hash":stable_hash(messages)}


def repair_prompt(raw_text: str, request_metadata: dict[str, Any]) -> dict[str, Any]:
    template=(PROMPT_DIR/"json_repair_v1.txt").read_text(encoding="utf-8")
    messages=[{"role":"system","content":template},{"role":"user","content":canonical_json({"metadata":request_metadata,"invalid_json":raw_text[:250000]})}]
    return {"prompt_version":AI_REPORT_PROMPT_VERSION,"messages":messages,"prompt_hash":stable_hash(messages)}
