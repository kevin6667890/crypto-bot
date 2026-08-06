"""Stable report request and immutable report identities."""
from __future__ import annotations
from typing import Any
from .canonical import identity, stable_hash
from .versions import (AI_REPORT_REQUEST_VERSION, AI_REPORT_RESPONSE_VERSION,
    AI_REPORT_FACT_REGISTRY_VERSION,AI_REPORT_CONTEXT_COMPILER_VERSION,
    AI_REPORT_PROMPT_VERSION,AI_REPORT_PROVIDER_VERSION,AI_REPORT_BASIC_VALIDATION_VERSION)

REPORT_PIPELINE_VERSIONS={"request":AI_REPORT_REQUEST_VERSION,"response":AI_REPORT_RESPONSE_VERSION,
    "fact_registry":AI_REPORT_FACT_REGISTRY_VERSION,"context_compiler":AI_REPORT_CONTEXT_COMPILER_VERSION,
    "prompt":AI_REPORT_PROMPT_VERSION,"provider":AI_REPORT_PROVIDER_VERSION,
    "basic_validation":AI_REPORT_BASIC_VALIDATION_VERSION}


def report_request_identity(context_id: str, mode: str, language: str, prompt_version: str,
                            provider: str, model: str, generation_parameters_version: str = "v1") -> str:
    return identity("request", {"source_versions":REPORT_PIPELINE_VERSIONS,"enriched_context_id":context_id,
        "mode":mode,"language":language,"prompt_version":prompt_version,"provider":provider,
        "model":model,"generation_parameters_version":generation_parameters_version})


def report_identity(response: dict[str, Any]) -> str:
    return identity("report", {"version":AI_REPORT_RESPONSE_VERSION,"response_hash":stable_hash(response)})
