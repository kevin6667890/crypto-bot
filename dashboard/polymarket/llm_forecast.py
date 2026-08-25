"""Market-blind single-model independent forecast protocol."""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from .evidence import EVIDENCE_POLICY_VERSION, canonical_utc, strict_evidence_eligible
from .forecast import FORECAST_SCHEMA_VERSION
from .models import canonical_json, stable_hash, utc_now
from .repository import PolymarketRepository
from .llm_provider import (
    configured_provider,
    ProviderError,
    ProviderResult,
    DEEPSEEK_FALLBACK_FAILURES,
    DEEPSEEK_FALLBACK_FORMAT,
    DEEPSEEK_PRIMARY_FORMAT,
    DEEPSEEK_PROVIDER_POLICY_VERSION,
    provider_policy_hash,
)
from .cadence import (
    FORECAST_CADENCE_POLICY_VERSION,
    forecast_cadence_policy_hash,
    forecast_methodology,
    forecast_methodology_hash,
    has_initial_forecast,
)

LLM_FORECAST_SCHEMA_VERSION = "polymarket-independent-llm-v2"
PROMPT_VERSION = "polymarket-independent-prompt-v2"
MAX_STRICT_EVIDENCE = 3


class InitialForecastAlreadyExists(ValueError):
    """The formal initial forecast for this market/methodology already exists."""


def deepseek_model_call(request: dict[str, Any], *, response_format: str = "json_object") -> ProviderResult:
    """Compatibility shim; uses the configured OpenAI-compatible provider."""
    return configured_provider().generate_structured_forecast(request, response_format=response_format)


def validate_independent_output(raw: str | Mapping[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else ""
                if text.rstrip().endswith("```"):
                    text = text.rstrip()[:-3].strip()
            start = text.find("{")
            if start < 0:
                raise ValueError("no_json_object")
            value, end = json.JSONDecoder().raw_decode(text[start:])
            tail = text[start + end:].strip()
            if tail not in ("", "```"):
                raise ValueError("multiple_or_trailing_json")
        else:
            value = dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("malformed_json") from exc
    required = {"probability_yes", "confidence", "evidence_refs", "uncertainties", "summary"}
    if set(value) != required:
        raise ValueError("schema_keys_invalid")
    probability = value["probability_yes"]
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 < float(probability) < 1:
        raise ValueError("probability_invalid")
    if value["confidence"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("confidence_invalid")
    for field in ("evidence_refs", "uncertainties"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            raise ValueError(f"{field}_invalid")
    if len(value["uncertainties"]) > 5:
        raise ValueError("uncertainties_too_long")
    if not isinstance(value["summary"], str) or len(value["summary"].split()) > 150:
        raise ValueError("summary_invalid")
    return value


def build_independent_request(context: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], evidence_cutoff_at: str) -> dict[str, Any]:
    request = {"prompt_version": PROMPT_VERSION, "task": "Estimate probability that YES resolves true. Return exactly one JSON object matching output_schema and the JSON example; no Markdown and no hidden reasoning.", "market": {"question": context["question"], "resolution_rule_text": context["resolution_rule_text"], "deadline": context["end_date"]}, "evidence_cutoff_at": canonical_utc(evidence_cutoff_at), "evidence": [{"evidence_id": e["evidence_id"], "title": e["title"], "content": str(e["content"])[:1200], "source_url": e["source_url"], "published_at": e["published_at"], "source_type": e["source_type"]} for e in evidence[:3]], "output_schema": {"probability_yes": "number (0,1)", "confidence": "LOW|MEDIUM|HIGH", "evidence_refs": ["input evidence_id"], "uncertainties": ["at most 5 short strings"], "summary": "at most 150 words"}, "json_example": {"probability_yes": 0.57, "confidence": "LOW", "evidence_refs": ["example-id"], "uncertainties": ["example"], "summary": "example"}}
    text = canonical_json(request).lower()
    if any(word in text for word in ("midpoint", "best_bid", "best_ask", "market_probability", "orderbook")):
        raise AssertionError("market pricing leaked into independent request")
    return request


def run_independent_forecast(repo: PolymarketRepository, *, market_id: str, market_snapshot_id: str, eligibility_decision_id: str, evidence_ids: Sequence[str], evidence_cutoff_at: str, provider_identity: Mapping[str, Any], generation_config: Mapping[str, Any], model_call: Callable[..., str | Mapping[str, Any] | ProviderResult], min_strict_evidence: int = 1, cohort_id: str | None = None) -> dict[str, Any]:
    cutoff = canonical_utc(evidence_cutoff_at)
    context = repo.independent_forecast_context(market_id, market_snapshot_id, eligibility_decision_id)
    rows = repo.evidence_rows(market_id, evidence_ids)
    root = stable_hash([str(row["payload_sha256"]) for row in rows])
    policy_version = DEEPSEEK_PROVIDER_POLICY_VERSION if provider_identity.get("provider") == "deepseek" else "single-provider-attempt-v1"
    policy_hash = provider_policy_hash() if provider_identity.get("provider") == "deepseek" else stable_hash({"version": policy_version, "max_attempts": 1})
    methodology = forecast_methodology(
        provider_identity=provider_identity,
        forecast_schema_version=LLM_FORECAST_SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        provider_policy_version=policy_version,
        provider_policy_hash=policy_hash,
    )
    methodology_hash = forecast_methodology_hash(
        provider_identity=provider_identity,
        forecast_schema_version=LLM_FORECAST_SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        provider_policy_version=policy_version,
        provider_policy_hash=policy_hash,
    )
    if has_initial_forecast(repo, market_id, methodology_hash, prompt_version=PROMPT_VERSION, provider_identity=provider_identity):
        raise InitialForecastAlreadyExists("INITIAL_FORECAST_ALREADY_EXISTS")
    strict: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        allowed, reason = strict_evidence_eligible(payload, cutoff)
        if not allowed:
            repo.insert_llm_attempt({"market_id": market_id, "market_snapshot_id": market_snapshot_id, "eligibility_decision_id": eligibility_decision_id, "attempted_at": utc_now(), "status": "FAILED", "failure_code": reason, "provider_identity": provider_identity, "generation_config": generation_config, "prompt_version": PROMPT_VERSION, "schema_version": LLM_FORECAST_SCHEMA_VERSION, "evidence_root_hash": root, "request_hash": stable_hash({"rejected_evidence": row["evidence_id"], "cutoff": cutoff}), "raw_response": None})
            raise ValueError(f"strict evidence rejected: {reason}")
        strict.append({"evidence_id": row["evidence_id"], **payload})
    if len(strict) < max(1, min_strict_evidence):
        repo.insert_llm_attempt({"market_id": market_id, "market_snapshot_id": market_snapshot_id, "eligibility_decision_id": eligibility_decision_id, "attempted_at": utc_now(), "status": "FAILED", "failure_code": "MIN_STRICT_EVIDENCE_NOT_MET", "provider_identity": provider_identity, "generation_config": generation_config, "prompt_version": PROMPT_VERSION, "schema_version": LLM_FORECAST_SCHEMA_VERSION, "evidence_root_hash": root, "request_hash": stable_hash({"minimum": min_strict_evidence, "actual": len(strict)}), "raw_response": None})
        raise ValueError("MIN_STRICT_EVIDENCE_NOT_MET")
    if len(strict) > MAX_STRICT_EVIDENCE:
        repo.insert_llm_attempt({"market_id": market_id, "market_snapshot_id": market_snapshot_id, "eligibility_decision_id": eligibility_decision_id, "attempted_at": utc_now(), "status": "FAILED", "failure_code": "MAX_STRICT_EVIDENCE_EXCEEDED", "provider_identity": provider_identity, "generation_config": generation_config, "prompt_version": PROMPT_VERSION, "schema_version": LLM_FORECAST_SCHEMA_VERSION, "evidence_root_hash": root, "request_hash": stable_hash({"maximum": MAX_STRICT_EVIDENCE, "actual": len(strict)}), "raw_response": None})
        raise ValueError("MAX_STRICT_EVIDENCE_EXCEEDED")
    request = build_independent_request(context, strict, cutoff)
    base = {"market_id": market_id, "market_snapshot_id": market_snapshot_id, "eligibility_decision_id": eligibility_decision_id, "attempted_at": utc_now(), "provider_identity": dict(provider_identity), "generation_config": dict(generation_config), "prompt_version": PROMPT_VERSION, "schema_version": LLM_FORECAST_SCHEMA_VERSION, "evidence_root_hash": root, "request_hash": stable_hash(request)}
    raw: str | Mapping[str, Any] | None = None
    output: dict[str, Any] | None = None
    # The fixed policy applies only to the configured production adapter. Tests
    # and explicit callers retain their single-call semantics.
    formats = (DEEPSEEK_PRIMARY_FORMAT, DEEPSEEK_FALLBACK_FORMAT) if provider_identity.get("provider") == "deepseek" else (None,)
    last_error: Exception | None = None
    for ordinal, response_format in enumerate(formats):
        attempt_config = dict(base["generation_config"])
        if response_format:
            attempt_config.update({"provider_policy_version": DEEPSEEK_PROVIDER_POLICY_VERSION,
                                   "provider_policy_hash": provider_policy_hash(), "response_format": response_format,
                                   "thinking": {"type": "disabled"}, "stream": False})
        diagnostic: dict[str, Any] = {}
        try:
            candidate = model_call(request, response_format=response_format) if response_format else model_call(request)
            if isinstance(candidate, ProviderResult):
                raw, diagnostic = candidate.content, candidate.diagnostic
                attempt_config["response_diagnostic"] = diagnostic
                # Preserve the complete, non-secret provider response shape for
                # forensic replay; the parser still consumes only `content`.
                if candidate.raw_payload is not None:
                    attempt_config["raw_provider_response_hash"] = stable_hash(candidate.raw_payload)
            else:
                raw = candidate
            output = validate_independent_output(raw)
            used = set(output["evidence_refs"])
            if not used.issubset(set(evidence_ids)):
                raise ValueError("evidence_reference_mismatch")
        except ProviderError as exc:
            diagnostic = exc.diagnostic
            if diagnostic: attempt_config["response_diagnostic"] = diagnostic
            last_error = exc
            repo.insert_llm_attempt({**base, "generation_config": attempt_config, "status": "FAILED", "failure_code": exc.code, "raw_response": raw})
            # A format/empty failure earns exactly one text fallback, no more.
            if ordinal == 0 and exc.code in DEEPSEEK_FALLBACK_FAILURES and len(formats) == 2:
                continue
            raise
        except Exception as exc:
            last_error = exc
            code = "INVALID_JSON" if str(exc) == "malformed_json" else str(exc)
            repo.insert_llm_attempt({**base, "generation_config": attempt_config, "status": "FAILED", "failure_code": code, "raw_response": raw})
            if ordinal == 0 and code in DEEPSEEK_FALLBACK_FAILURES and len(formats) == 2:
                continue
            raise
        else:
            base["generation_config"] = attempt_config
            break
    if output is None:
        raise last_error or RuntimeError("provider attempt failed")
    raw_text = raw if isinstance(raw, str) else canonical_json(raw)
    repo.insert_llm_attempt({**base, "status": "SUCCEEDED", "failure_code": None, "raw_response": raw_text})
    now = utc_now()
    config = {"provider_identity": dict(provider_identity), "generation_config": dict(generation_config), "methodology": methodology, "forecast_cadence_policy_version": FORECAST_CADENCE_POLICY_VERSION, "forecast_cadence_policy_hash": forecast_cadence_policy_hash()}
    forecast_id = repo.insert_forecast({"market_id": market_id, "market_snapshot_id": market_snapshot_id, "eligibility_decision_id": eligibility_decision_id, "forecasted_at": now, "evidence_cutoff_at": cutoff, "forecast_schema_version": LLM_FORECAST_SCHEMA_VERSION, "producer_kind": "LLM", "producer_identity": {**dict(provider_identity), "prompt_version": PROMPT_VERSION, "methodology_hash": methodology_hash, "provider_policy_version": policy_version, "provider_policy_hash": policy_hash, "raw_response_hash": stable_hash(raw_text)}, "config_hash": stable_hash(config), "probability": float(output["probability_yes"]), "rationale": output["summary"], "committed_at": now, "cohort_id": cohort_id, "forecast_methodology_hash": methodology_hash}, evidence_ids)
    return {"forecast_id": forecast_id, "output": output, "market_reveal": repo.market_probability_reveal(forecast_id)}
