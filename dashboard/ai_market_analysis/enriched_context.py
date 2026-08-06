"""Freeze an AI-3 context with position and macro evidence without mutating the base."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

from .canonical import identity, stable_hash
from .versions import AI_ENRICHED_CONTEXT_VERSION, AI_POSITION_CONTEXT_VERSION, AI_MACRO_EVIDENCE_SET_VERSION


def build_enriched_context(base: dict[str, Any], position: dict[str, Any], macro_set: dict[str, Any]) -> dict[str, Any]:
    frozen_base = deepcopy(base)
    base_hash = stable_hash(frozen_base)
    source_versions = {**base.get("source_versions", {}), "position_context": AI_POSITION_CONTEXT_VERSION,
                       "macro_evidence_set": AI_MACRO_EVIDENCE_SET_VERSION,
                       "enriched_context": AI_ENRICHED_CONTEXT_VERSION}
    identity_input = {"base_context_id": base["context_id"],
        "position_fingerprint": position["position_fingerprint"],
        "macro_evidence_set_fingerprint": macro_set["set_fingerprint"],
        "enriched_context_version": AI_ENRICHED_CONTEXT_VERSION}
    return {"enriched_context_version": AI_ENRICHED_CONTEXT_VERSION,
        "enriched_context_id": identity("enriched", identity_input), "base_context_id": base["context_id"],
        "base_context_hash": base_hash, "instrument": base["instrument"],
        "decision_time": base["decision_time"], "base_context": frozen_base,
        "position_context": deepcopy(position), "position_fingerprint": position["position_fingerprint"],
        "macro_context": deepcopy(macro_set), "macro_evidence_set_id": macro_set["evidence_set_id"],
        "source_versions": source_versions,
        "provenance": {"builder": "build_enriched_context", "identity_input_hash": stable_hash(identity_input)}}
