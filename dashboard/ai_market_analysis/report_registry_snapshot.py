"""Immutable, content-addressed registry snapshots used by generation and audit."""
from __future__ import annotations

from typing import Any

from .canonical import canonical_json, identity, stable_hash
from .versions import (
    AI_REPORT_FACT_REGISTRY_VERSION,
    AI_REPORT_NUMERIC_REGISTRY_VERSION,
    AI_REPORT_REGISTRY_SNAPSHOT_VERSION,
)


class RegistryIdentityError(ValueError):
    """A stable registry identity was missing, divergent, or replaced."""


def build_registry_snapshot(*, request_id: str, report_context_id: str,
                            enriched_context_id: str, fact_registry: dict[str, Any],
                            prompt_hash: str, source_versions: dict[str, Any]) -> dict[str, Any]:
    numeric_registry = fact_registry.get("numeric_registry")
    if not isinstance(numeric_registry, list):
        raise RegistryIdentityError("NUMERIC_REGISTRY_DIVERGENCE")
    fact_hash = stable_hash(fact_registry)
    numeric_hash = stable_hash(numeric_registry)
    source_hash = stable_hash(source_versions)
    identity_input = {
        "snapshot_version": AI_REPORT_REGISTRY_SNAPSHOT_VERSION,
        "enriched_context_id": enriched_context_id,
        "fact_registry_hash": fact_hash,
        "numeric_registry_hash": numeric_hash,
        "prompt_hash": prompt_hash,
        "fact_registry_version": AI_REPORT_FACT_REGISTRY_VERSION,
        "numeric_registry_version": AI_REPORT_NUMERIC_REGISTRY_VERSION,
        "source_versions_hash": source_hash,
    }
    return {
        "registry_snapshot_id": identity("registry_snapshot", identity_input),
        "request_id": request_id,
        "report_context_id": report_context_id,
        "enriched_context_id": enriched_context_id,
        "snapshot_version": AI_REPORT_REGISTRY_SNAPSHOT_VERSION,
        "fact_registry_version": AI_REPORT_FACT_REGISTRY_VERSION,
        "numeric_registry_version": AI_REPORT_NUMERIC_REGISTRY_VERSION,
        "fact_registry": fact_registry,
        "fact_registry_hash": fact_hash,
        "numeric_registry": numeric_registry,
        "numeric_registry_hash": numeric_hash,
        "prompt_hash": prompt_hash,
        "source_versions": source_versions,
        "source_versions_hash": source_hash,
        "identity_input": identity_input,
    }


def validate_registry_snapshot(snapshot: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    fact = snapshot.get("fact_registry")
    numeric = snapshot.get("numeric_registry")
    if not isinstance(fact, dict) or stable_hash(fact) != snapshot.get("fact_registry_hash"):
        failures.append("FACT_REGISTRY_HASH_MISMATCH")
    if not isinstance(numeric, list) or stable_hash(numeric) != snapshot.get("numeric_registry_hash"):
        failures.append("NUMERIC_REGISTRY_HASH_MISMATCH")
    if isinstance(fact, dict) and fact.get("numeric_registry") != numeric:
        failures.append("NUMERIC_REGISTRY_DIVERGENCE")
    if isinstance(fact, dict) and fact.get("context_id") != snapshot.get("enriched_context_id"):
        failures.append("REGISTRY_CONTEXT_MISMATCH")
    if stable_hash(snapshot.get("source_versions", {})) != snapshot.get("source_versions_hash"):
        failures.append("REGISTRY_SOURCE_VERSION_MISMATCH")
    expected = identity("registry_snapshot", snapshot.get("identity_input", {}))
    if expected != snapshot.get("registry_snapshot_id"):
        failures.append("REGISTRY_IDENTITY_CONFLICT")
    return sorted(set(failures))


def snapshot_db_values(snapshot: dict[str, Any], created_at: str) -> tuple[Any, ...]:
    return (
        snapshot["registry_snapshot_id"], snapshot["request_id"], snapshot["report_context_id"],
        snapshot["enriched_context_id"], snapshot["snapshot_version"], snapshot["fact_registry_version"],
        snapshot["numeric_registry_version"], canonical_json(snapshot["fact_registry"]),
        snapshot["fact_registry_hash"], canonical_json(snapshot["numeric_registry"]),
        snapshot["numeric_registry_hash"], snapshot["prompt_hash"], canonical_json(snapshot["source_versions"]),
        snapshot["source_versions_hash"], created_at,
    )
