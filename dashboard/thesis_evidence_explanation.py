"""Numerically closed, deterministic thesis evidence explanations.

The optional model may select only enum-valued emphasis.  It never receives
quantitative facts and never supplies prose.  All displayed words and every
number are owned by the deterministic bilingual renderer in this module.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol

try:
    from signal_identity import canonical_json
    from thesis_event_engine import ThesisTestServiceV1, ThesisValidationError
except ImportError:
    from .signal_identity import canonical_json
    from .thesis_event_engine import ThesisTestServiceV1, ThesisValidationError


EVIDENCE_FACT_SET_VERSION = "thesis-evidence-fact-set-v1"
EVIDENCE_FACT_SET_VERSION_V2 = "thesis-evidence-fact-set-v2"
EVIDENCE_PLAN_INPUT_VERSION = "thesis-evidence-plan-input-v1"
EVIDENCE_PLAN_VERSION = "thesis-evidence-narrative-plan-v1"
EVIDENCE_EXPLANATION_VERSION = "thesis-evidence-explanation-v1"
EVIDENCE_RENDERER_VERSION = "thesis-evidence-renderer-v1"
EXPLAIN_REQUEST_VERSION = "thesis-evidence-explain-request-v1"

PRIMARY = {"LONGEST_OUTCOME", "HIGHEST_POSITIVE_SHARE", "HIGHEST_MEDIAN_RETURN"}
SECONDARY = {"RETURN_RANGE", "DOWNSIDE_EXCURSION"}
ORDERING = {"OUTCOME_THEN_RISK", "RISK_THEN_OUTCOME"}
NUMERIC_LITERAL = re.compile(r"[0-9０-９٠-٩۰-۹]")


class EvidenceExplanationError(ValueError):
    pass


class EvidenceIdentityMismatch(EvidenceExplanationError):
    pass


class EvidencePlanProvider(Protocol):
    def generate(self, request: dict[str, Any]) -> Any: ...


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _relation(value: float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "FLAT"


def build_evidence_fact_set(result: Mapping[str, Any]) -> dict[str, Any]:
    result_version = result.get("result_version")
    if result_version not in {"thesis-test-result-v1", "thesis-test-result-v2"} or not result.get("result_hash"):
        raise EvidenceExplanationError("validated thesis result is required")
    horizons: dict[str, Any] = {}
    for horizon in result.get("thesis_spec", {}).get("forward_horizons", []):
        aggregate = result.get("aggregates", {}).get(horizon)
        if not isinstance(aggregate, Mapping):
            continue
        positive_rate = aggregate.get("historical_positive_rate")
        median = aggregate.get("median_return_fraction")
        p25, p75 = aggregate.get("p25_return_fraction"), aggregate.get("p75_return_fraction")
        horizons[horizon] = {
            **{key: aggregate.get(key) for key in (
                "eligible_n", "censored_n", "positive_n", "zero_n", "negative_n",
                "historical_positive_rate", "median_return_fraction", "p25_return_fraction",
                "p75_return_fraction", "median_mfe_fraction", "median_mae_fraction", "sample_quality",
                "sample_quality_policy_version",
            )},
            "positive_share_relation": ("ABOVE_HALF" if positive_rate is not None and positive_rate > .5
                                        else "BELOW_HALF" if positive_rate is not None and positive_rate < .5
                                        else "EVEN" if positive_rate is not None else "UNAVAILABLE"),
            "median_relation": _relation(median),
            "iqr_relation": ("SPANS_ZERO" if p25 is not None and p75 is not None and p25 <= 0 <= p75
                             else "ABOVE_ZERO" if p25 is not None and p25 > 0
                             else "BELOW_ZERO" if p75 is not None and p75 < 0 else "UNAVAILABLE"),
        }
    facts = {
        "version": (EVIDENCE_FACT_SET_VERSION_V2
                    if result_version == "thesis-test-result-v2" else EVIDENCE_FACT_SET_VERSION),
        "result_hash": result["result_hash"],
        "definition_hash": result["definition_hash"],
        "dataset_id": result.get("historical_data", {}).get("dataset_id"),
        "dataset_content_sha256": result.get("data_identity", {}).get("content_sha256"),
        "engine_version": result.get("engine_version"),
        "feature_versions": result.get("feature_versions", {}),
        "coverage_policy_version": result.get("coverage", {}).get("version"),
        "independent_event_count": result.get("independent_event_count", 0),
        "historical_data": result.get("historical_data", {}),
        "tested_range": result.get("tested_range", {}),
        "coverage_qualification": result.get("coverage", {}).get("qualification"),
        "warnings": sorted(result.get("warnings", [])),
        "horizons": horizons,
        "logic_summary": (result.get("compiled_definition", {}).get("expression")
                          if result_version == "thesis-test-result-v2" else None),
        "source_requirements": result.get("compiled_definition", {}).get("source_requirements", []),
        "derivative_limitations": [item for item in result.get("warnings", [])
                                   if any(group in str(item) for group in ("OI", "FUNDING", "BASIS", "CVD"))],
    }
    facts["facts_hash"] = _hash(facts)
    return facts


def plan_input(facts: Mapping[str, Any]) -> dict[str, Any]:
    # This is intentionally free of numbers, dates, instruments and user prose.
    available_primary = ["LONGEST_OUTCOME"]
    if facts.get("horizons"):
        available_primary += ["HIGHEST_POSITIVE_SHARE", "HIGHEST_MEDIAN_RETURN"]
    return {"available_primary": available_primary,
            "available_secondary": sorted(SECONDARY),
            "available_ordering": sorted(ORDERING)}


def deterministic_plan() -> dict[str, str]:
    return {"version": EVIDENCE_PLAN_VERSION, "primary": "LONGEST_OUTCOME",
            "secondary": "DOWNSIDE_EXCURSION", "ordering": "OUTCOME_THEN_RISK"}


def parse_plan(raw: str, allowed: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(raw, str) or NUMERIC_LITERAL.search(raw):
        raise EvidenceExplanationError("provider plan must contain no numeric literals")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise EvidenceExplanationError("provider plan is not JSON") from error
    if not isinstance(value, dict) or set(value) != {"primary", "secondary", "ordering"}:
        raise EvidenceExplanationError("provider plan schema is invalid")
    if value["primary"] not in allowed["available_primary"] or value["primary"] not in PRIMARY:
        raise EvidenceExplanationError("provider primary observation is invalid")
    if value["secondary"] not in allowed["available_secondary"] or value["secondary"] not in SECONDARY:
        raise EvidenceExplanationError("provider secondary observation is invalid")
    if value["ordering"] not in allowed["available_ordering"] or value["ordering"] not in ORDERING:
        raise EvidenceExplanationError("provider ordering is invalid")
    return {"version": EVIDENCE_PLAN_VERSION,
            **{key: str(value[key]) for key in ("primary", "secondary", "ordering")}}


def _percent(value: Any, language: str) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _date(value: Any) -> str:
    if value is None:
        return "—"
    return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%d")


def _chosen_horizon(facts: Mapping[str, Any], primary: str) -> tuple[str, Mapping[str, Any]] | None:
    items = list(facts.get("horizons", {}).items())
    eligible = [(name, value) for name, value in items if value.get("eligible_n", 0) > 0]
    if not eligible:
        return None
    if primary == "HIGHEST_POSITIVE_SHARE":
        return max(eligible, key=lambda item: (
            item[1].get("historical_positive_rate")
            if item[1].get("historical_positive_rate") is not None else -1,
            items.index(item),
        ))
    if primary == "HIGHEST_MEDIAN_RETURN":
        return max(eligible, key=lambda item: (
            item[1].get("median_return_fraction")
            if item[1].get("median_return_fraction") is not None else -10**9,
            items.index(item),
        ))
    return eligible[-1]


def render_explanation(facts: Mapping[str, Any], plan: Mapping[str, str], language: str,
                       *, status: str, fallback_reason: str | None,
                       provider_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if language not in {"en", "zh"}:
        raise EvidenceExplanationError("language must be en or zh")
    chosen = _chosen_horizon(facts, plan["primary"])
    blocks: list[dict[str, Any]] = []
    if facts.get("logic_summary"):
        sources = ", ".join(map(str, facts.get("source_requirements", []))) or "OHLCV"
        logic_text = (f"该事件使用服务端确定性布尔表达式评估；所需数据源为 {sources}。"
                      if language == "zh" else
                      f"The event used a server-evaluated deterministic Boolean expression; required sources: {sources}.")
        blocks.append({"template_id": "V2_LOGIC_SUMMARY", "text": logic_text,
                       "fact_refs": ["DEFINITION.EXPRESSION", "DEFINITION.SOURCE_REQUIREMENTS"]})
    if chosen is not None:
        horizon, aggregate = chosen
        if language == "zh":
            outcome = (f"在 {horizon} 周期，{aggregate['eligible_n']} 个可用独立事件的历史正收益占比为 "
                       f"{_percent(aggregate['historical_positive_rate'], language)}，收益中位数为 "
                       f"{_percent(aggregate['median_return_fraction'], language)}。")
            risk = (f"中间一半的结果介于 {_percent(aggregate['p25_return_fraction'], language)} 与 "
                    f"{_percent(aggregate['p75_return_fraction'], language)}；MAE 中位数为 "
                    f"{_percent(aggregate['median_mae_fraction'], language)}。")
        else:
            outcome = (f"At {horizon}, {aggregate['eligible_n']} eligible independent events had a "
                       f"{_percent(aggregate['historical_positive_rate'], language)} historical positive-return share "
                       f"and a median return of {_percent(aggregate['median_return_fraction'], language)}.")
            risk = (f"The middle half ranged from {_percent(aggregate['p25_return_fraction'], language)} to "
                    f"{_percent(aggregate['p75_return_fraction'], language)}; median MAE was "
                    f"{_percent(aggregate['median_mae_fraction'], language)}.")
        outcome_block = {"template_id": "HORIZON_OUTCOME", "text": outcome,
                         "fact_refs": [f"HORIZON.{horizon}.ELIGIBLE_N", f"HORIZON.{horizon}.POSITIVE_RATE",
                                       f"HORIZON.{horizon}.MEDIAN_RETURN"]}
        risk_block = {"template_id": "DISPERSION_AND_DOWNSIDE", "text": risk,
                      "fact_refs": [f"HORIZON.{horizon}.P25", f"HORIZON.{horizon}.P75",
                                    f"HORIZON.{horizon}.MEDIAN_MAE"]}
        blocks.extend([risk_block, outcome_block] if plan["ordering"] == "RISK_THEN_OUTCOME"
                      else [outcome_block, risk_block])
        quality = aggregate.get("sample_quality", "INSUFFICIENT")
    else:
        quality = "INSUFFICIENT"
        text = ("没有可用的前瞻结果，不能从这组历史数据形成结果摘要。" if language == "zh"
                else "No eligible forward outcomes are available, so this historical sample cannot support an outcome summary.")
        blocks.append({"template_id": "NO_ELIGIBLE_OUTCOMES", "text": text,
                       "fact_refs": ["SAMPLE.INDEPENDENT_N"]})
    history = facts.get("historical_data", {})
    limited = history.get("breadth_qualification") == "LIMITED_HISTORICAL_SPAN"
    if language == "zh":
        note = (f"样本质量为 {quality}，研究使用 {_date(history.get('raw_range', {}).get('start'))} 至 "
                f"{_date(history.get('raw_range', {}).get('end'))} 的历史数据。")
        if limited:
            note += f" 合格历史仅覆盖 {history.get('span_days', 0)} 天，跨度有限。"
        note += " 这是历史条件证据，不是预测或交易建议。"
    else:
        note = (f"Sample quality is {quality}; the study used history from "
                f"{_date(history.get('raw_range', {}).get('start'))} to {_date(history.get('raw_range', {}).get('end'))}.")
        if limited:
            note += f" Qualified history covers only {history.get('span_days', 0)} days and is limited."
        note += " This is historical conditional evidence, not a forecast or trading recommendation."
    blocks.append({"template_id": "MANDATORY_LIMITATION", "text": note,
                   "fact_refs": ["SAMPLE.QUALITY", "COVERAGE.RAW_RANGE", "COVERAGE.BREADTH"]})
    return {"version": EVIDENCE_EXPLANATION_VERSION, "status": status, "language": language,
            "result_hash": facts["result_hash"], "definition_hash": facts["definition_hash"],
            "dataset_id": facts.get("dataset_id"), "facts_version": facts.get("version", EVIDENCE_FACT_SET_VERSION),
            "facts_hash": facts["facts_hash"], "plan_version": EVIDENCE_PLAN_VERSION,
            "renderer_version": EVIDENCE_RENDERER_VERSION, "blocks": blocks,
            "provider": dict(provider_meta) if provider_meta else None,
            "fallback_reason": fallback_reason}


class ThesisEvidenceExplanationServiceV1:
    def __init__(self, thesis_service: ThesisTestServiceV1,
                 provider: EvidencePlanProvider | None = None, cache_size: int = 128) -> None:
        self.thesis_service, self.provider = thesis_service, provider
        self.cache_size = max(1, int(cache_size))
        self._plans: OrderedDict[str, tuple[dict[str, str], str, str | None, Mapping[str, Any] | None]] = OrderedDict()
        self._lock = RLock()

    def explain(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) != {"version", "thesis_spec", "result_hash", "language"}:
            raise EvidenceExplanationError("explanation request contains unsupported or missing fields")
        if payload.get("version") != EXPLAIN_REQUEST_VERSION:
            raise EvidenceExplanationError(f"version must be {EXPLAIN_REQUEST_VERSION}")
        thesis_spec, result_hash, language = payload.get("thesis_spec"), payload.get("result_hash"), payload.get("language")
        if not isinstance(thesis_spec, Mapping) or not isinstance(result_hash, str):
            raise EvidenceExplanationError("thesis_spec and result_hash are required")
        try:
            result, _rows = self.thesis_service.verified_result(thesis_spec, result_hash)
        except ThesisValidationError as error:
            if "identity" in str(error):
                raise EvidenceIdentityMismatch(str(error)) from error
            raise EvidenceExplanationError(str(error)) from error
        facts = build_evidence_fact_set(result)
        with self._lock:
            cached = self._plans.get(facts["facts_hash"])
            if cached is not None:
                self._plans.move_to_end(facts["facts_hash"])
        cache_hit = cached is not None
        if cached is None:
            plan, status, reason, provider_meta = deterministic_plan(), "FALLBACK", "AI_UNAVAILABLE", None
            if self.provider is not None and facts.get("horizons"):
                allowed = plan_input(facts)
                request = {"messages": [
                    {"role": "system", "content": "Select only one allowed enum plan. Return JSON only; no prose or numbers."},
                    {"role": "user", "content": json.dumps(allowed, separators=(",", ":"))},
                ], "max_output_tokens": 96}
                try:
                    provider_result = self.provider.generate(request)
                    raw = getattr(provider_result, "raw_text", provider_result)
                    plan = parse_plan(raw, allowed)
                    status, reason = "GENERATED", None
                    provider_meta = {"model": getattr(provider_result, "model", "configured"),
                                     "latency_ms": getattr(provider_result, "latency_ms", None)}
                except Exception:
                    status, reason, provider_meta = "FALLBACK", "PLAN_REJECTED_OR_PROVIDER_UNAVAILABLE", None
            cached = (plan, status, reason, provider_meta)
            with self._lock:
                self._plans[facts["facts_hash"]] = cached
                while len(self._plans) > self.cache_size:
                    self._plans.popitem(last=False)
        plan, status, reason, provider_meta = cached
        response = render_explanation(facts, plan, str(language), status=status,
                                      fallback_reason=reason, provider_meta=provider_meta)
        response["cache_status"] = "HIT" if cache_hit else "MISS"
        return response
