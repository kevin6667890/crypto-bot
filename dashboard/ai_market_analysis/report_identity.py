"""Stable report request and immutable report identities."""
from __future__ import annotations
from typing import Any
from .canonical import identity, stable_hash
from .versions import AI_REPORT_REQUEST_VERSION, AI_REPORT_RESPONSE_VERSION


def report_request_identity(context_id: str, mode: str, language: str, prompt_version: str,
                            provider: str, model: str, generation_parameters_version: str = "v1") -> str:
    return identity("request", {"version":AI_REPORT_REQUEST_VERSION,"enriched_context_id":context_id,
        "mode":mode,"language":language,"prompt_version":prompt_version,"provider":provider,
        "model":model,"generation_parameters_version":generation_parameters_version})


def report_identity(response: dict[str, Any]) -> str:
    return identity("report", {"version":AI_REPORT_RESPONSE_VERSION,"response_hash":stable_hash(response)})
