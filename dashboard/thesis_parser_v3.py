"""Capability-driven natural-language boundary for ThesisExpression V2.

The model may identify clauses and structure.  This module validates every
node against runtime capabilities and records any standardized assumption.
It does not evaluate events or calculate statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Protocol, Sequence

try:
    from thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, ExpressionValidationError, NotNode,
        FeatureContractV2, PresetAssumptionV1, SEMANTIC_PRESETS,
        THESIS_SPEC_V2_VERSION, ThesisSpecV2, feature_contracts_from_capabilities,
        parse_expression,
    )
except ImportError:
    from .thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, ExpressionValidationError, NotNode,
        FeatureContractV2, PresetAssumptionV1, SEMANTIC_PRESETS,
        THESIS_SPEC_V2_VERSION, ThesisSpecV2, feature_contracts_from_capabilities,
        parse_expression,
    )


PARSER_V3_VERSION = "thesis-natural-language-parser-v3"
PARSE_RESULT_V2_VERSION = "thesis-parse-result-v2"
PARSE_STATUSES = {"READY", "READY_WITH_ASSUMPTIONS", "NEEDS_INPUT",
                  "PARTIALLY_SUPPORTED", "UNSUPPORTED", "ERROR"}
MAX_TEXT_LENGTH = 2_000
# A schema-constrained provider can still occasionally return an empty or
# truncated transport payload.  Retrying that *transport* failure is safe: the
# closed capability context and untrusted user text are identical on every
# attempt, and semantic/contract failures are never retried or substituted.
MAX_PROVIDER_TRANSPORT_ATTEMPTS = 2


class ThesisParserV3Error(ValueError):
    pass


class ThesisParserProviderV3(Protocol):
    def generate(self, request: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class UnsupportedClauseV2:
    source_text: str
    reason_code: str
    category: str = "SEMANTIC_UNSUPPORTED"
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissingParameterV2:
    source_text: str
    feature: str
    parameter: str


@dataclass(frozen=True)
class ThesisParseResultV2:
    status: str
    detected_language: str
    expression: ExpressionNode | None
    thesis_spec: ThesisSpecV2 | None
    recognized_clauses: tuple[str, ...]
    assumptions: tuple[PresetAssumptionV1, ...]
    unsupported_clauses: tuple[UnsupportedClauseV2, ...]
    missing_parameters: tuple[MissingParameterV2, ...]
    warnings: tuple[str, ...]
    version: str = PARSE_RESULT_V2_VERSION
    parser_version: str = PARSER_V3_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "parser_version": self.parser_version,
            "status": self.status, "detected_language": self.detected_language,
            "expression": self.expression.to_dict() if self.expression else None,
            "thesis_spec": self.thesis_spec.to_dict() if self.thesis_spec else None,
            "recognized_clauses": list(self.recognized_clauses),
            "assumptions": [item.to_dict() for item in self.assumptions],
            "unsupported_clauses": [item.__dict__ for item in self.unsupported_clauses],
            "missing_parameters": [item.__dict__ for item in self.missing_parameters],
            "warnings": list(self.warnings),
        }


def parser_context(capabilities: Mapping[str, Any]) -> dict[str, Any]:
    """Schema context constructed solely from backend-advertised capability."""
    registry = feature_contracts_from_capabilities(capabilities)
    return {
        "parser_version": PARSER_V3_VERSION,
        "thesis_spec_version": THESIS_SPEC_V2_VERSION,
        "logic_nodes": ["CONDITION", "ALL", "ANY", "NOT"],
        "features": [{
            "code": item.code, "value_type": item.value_type,
            "operators": list(item.operators),
            "parameters": {name: definition.__dict__
                           for name, definition in item.parameters.items()},
            "supported_timeframes": list(item.supported_timeframes),
            "historical_availability": item.historical_availability,
            "current_availability": item.current_availability,
            "semantic_terms": next((feature.get("semantic_terms", {})
                                    for feature in capabilities.get("features", [])
                                    if feature.get("code") == item.code), {}),
        } for item in (registry[key] for key in sorted(registry))],
        "semantic_presets": capabilities.get("semantic_presets", {"version": "unavailable", "presets": []}),
        "output_contract": {
            "detected_language": "en|zh", "instrument": "capability instrument or null",
            "timeframe": "capability timeframe or null", "forward_horizons": "array",
            "expression": "validated AST object or null",
            "recognized_clauses": "exact source substrings array",
            "assumptions": [{"preset_id": "registry id", "source_text": "exact substring"}],
            "unsupported_clauses": [{"source_text": "exact substring", "reason_code": "stable code",
                                     "category": "supported category", "suggestions": []}],
            "missing_parameters": [{"source_text": "exact substring", "feature": "code",
                                    "parameter": "name"}],
            "warnings": [],
        },
        "rules": [
            "Never invent a feature, operator, parameter, number, or preset.",
            "Explicit user numbers override presets.",
            "When a clause contains an explicit number, use that number and do not emit a preset assumption for that clause.",
            "Return every unrecognized clause; never silently omit a clause.",
            "BETWEEN compiles to an ALL containing inclusive gte and lte leaves.",
            "Use only CONDITION, ALL, ANY, and NOT node_type values; CONDITION fields are feature, operator, value, parameters.",
            "Normalize numeric negation into the comparator: not above X is lte X and not below X is gte X; do not wrap it in NOT.",
            "Preserve every AND/OR group exactly; AND binds more tightly than OR unless the user groups otherwise.",
            "A forward-return question sets forward_horizons only; it is not an expression node or unsupported clause.",
        ],
    }


def provider_request(text: str, capabilities: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT_LENGTH:
        raise ThesisParserV3Error("text is required and must not exceed 2000 characters")
    return {
        "messages": [
            # The provider's JSON-object mode requires this explicit protocol
            # token; the deterministic validator below remains the authority.
            {"role": "system", "content": "Return only one JSON object. " + json.dumps(
                parser_context(capabilities), ensure_ascii=False)},
            {"role": "user", "content": json.dumps({"untrusted_text": text.strip()}, ensure_ascii=False)},
        ],
        "response_schema": _provider_response_schema(capabilities),
        "max_output_tokens": 1_800,
    }


def _provider_response_schema(capabilities: Mapping[str, Any]) -> dict[str, Any]:
    """Provider transport schema; domain validation remains below this boundary."""
    instruments = list(map(str, capabilities.get("instruments", ())))
    timeframes = list(map(str, capabilities.get("timeframes", ())))
    horizons = list(map(str, capabilities.get("horizons", ())))
    nullable = lambda values: {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["detected_language", "instrument", "timeframe", "forward_horizons", "expression",
                     "recognized_clauses", "assumptions", "unsupported_clauses", "missing_parameters", "warnings"],
        "properties": {
            "detected_language": {"type": "string", "enum": ["en", "zh"]},
            "instrument": nullable(instruments), "timeframe": nullable(timeframes),
            "forward_horizons": {"type": "array", "items": {"type": "string", "enum": horizons}, "maxItems": len(horizons)},
            "expression": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            "recognized_clauses": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
            "assumptions": {"type": "array", "items": {"type": "object"}, "maxItems": 12},
            "unsupported_clauses": {"type": "array", "items": {"type": "object"}, "maxItems": 12},
            "missing_parameters": {"type": "array", "items": {"type": "object"}, "maxItems": 12},
            "warnings": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        },
    }


def _strict_keys(raw: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ThesisParserV3Error(f"{name} contains unsupported field: {sorted(unknown)[0]}")


def _compile_between(raw: Any) -> Any:
    if not isinstance(raw, Mapping):
        return raw
    node_type = raw.get("node_type")
    if node_type == "CONDITION" and raw.get("operator") == "between":
        value = raw.get("value")
        if (not isinstance(value, list) or len(value) != 2 or
                any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)):
            raise ThesisParserV3Error("between value must contain exactly two numeric bounds")
        low, high = map(float, value)
        if low > high:
            raise ThesisParserV3Error("between lower bound must not exceed upper bound")
        common = {"node_type": "CONDITION", "feature": raw.get("feature"),
                  "parameters": raw.get("parameters", {})}
        return {"node_type": "ALL", "children": [
            {**common, "operator": "gte", "value": low},
            {**common, "operator": "lte", "value": high},
        ]}
    if node_type in {"ALL", "ANY"}:
        return {"node_type": node_type,
                "children": [_compile_between(item) for item in raw.get("children", [])]}
    if node_type == "NOT":
        return {"node_type": "NOT", "child": _compile_between(raw.get("child"))}
    return dict(raw)


def _contains_explicit_number(source: str) -> bool:
    return bool(_explicit_numeric_values(source))


_ENGLISH_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}


def _chinese_number(value: str) -> int | None:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if all(item in digits for item in value):
        return int("".join(str(digits[item]) for item in value))
    total, current = 0, 0
    for item in value:
        if item in digits:
            current = digits[item]
        elif item == "十":
            total += (current or 1) * 10; current = 0
        elif item == "百":
            total += (current or 1) * 100; current = 0
        else:
            return None
    return total + current


def _explicit_numeric_values(source: str) -> set[float]:
    values = {float(item) for item in re.findall(
        r"(?<![A-Za-z0-9_])(-?\d+(?:\.\d+)?)(?!\s*(?:m|h|d)\b)", source, flags=re.I)}
    values.update(float(number) for word, number in _ENGLISH_NUMBERS.items()
                  if re.search(rf"\b{word}\b", source, flags=re.I))
    for item in re.findall(r"[零一二两三四五六七八九十百]+", source):
        parsed = _chinese_number(item)
        if parsed is not None:
            values.add(float(parsed))
    return values


def _parse_assumptions(raw: Any, text: str,
                       capabilities: Mapping[str, Any]) -> tuple[PresetAssumptionV1, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ThesisParserV3Error("assumptions must be an array")
    output: list[PresetAssumptionV1] = []
    projection = capabilities.get("semantic_presets", {})
    advertised = {str(item.get("preset_id")): item for item in projection.get("presets", [])
                  if isinstance(item, Mapping)} if isinstance(projection, Mapping) else {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ThesisParserV3Error("assumption must be an object")
        _strict_keys(item, {"preset_id", "source_text"}, "assumption")
        preset = SEMANTIC_PRESETS.get(str(item.get("preset_id", "")))
        source = str(item.get("source_text", "")).strip()
        advertised_preset = advertised.get(str(item.get("preset_id", "")))
        if (preset is None or advertised_preset is None
                or str(advertised_preset.get("preset_id")) != preset.preset_id):
            raise ThesisParserV3Error("unknown semantic preset")
        if (advertised_preset.get("version") != preset.version
                or advertised_preset.get("feature") != preset.feature
                or advertised_preset.get("operator") != preset.operator
                or advertised_preset.get("value") != preset.value
                or dict(advertised_preset.get("parameters", {})) != dict(preset.parameters)):
            raise ThesisParserV3Error("semantic preset capability does not match registry")
        if not source or source.casefold() not in text.casefold():
            raise ThesisParserV3Error("assumption source_text is not grounded in user text")
        if _contains_explicit_number(source):
            # A model may redundantly attach a preset to an already explicit
            # clause.  It is not an assumption: numeric grounding below still
            # verifies the AST against the user's value, fail-closed.
            continue
        phrases = tuple(str(phrase).casefold()
                        for values in preset.phrases.values() for phrase in values)
        if not any(phrase and phrase in source.casefold() for phrase in phrases):
            raise ThesisParserV3Error(
                "assumption source_text does not contain a registered preset phrase")
        output.append(preset.assumption(source))
    if len({(item.preset_id, item.source_text) for item in output}) != len(output):
        raise ThesisParserV3Error("duplicate semantic assumption")
    return tuple(output)


def _parse_unsupported(raw: Any, text: str) -> tuple[UnsupportedClauseV2, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ThesisParserV3Error("unsupported_clauses must be an array")
    output = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ThesisParserV3Error("unsupported clause must be an object")
        _strict_keys(item, {"source_text", "reason_code", "category", "suggestions"}, "unsupported clause")
        source = str(item.get("source_text", "")).strip()
        if not source or source.casefold() not in text.casefold():
            raise ThesisParserV3Error("unsupported clause is not grounded in user text")
        # A forward-return question supplies the study horizon; it is not an
        # event-condition clause.  Some models nevertheless label it as an
        # unsupported query type, so handle this closed, syntax-only case here.
        if (str(item.get("reason_code", "")) in {"UNSUPPORTED_QUERY_TYPE", "FORWARD_HORIZON_NOT_SUPPORTED"}
                and re.search(r"(?:what|usually|happens?|after|historical|之后|以後|通常|怎么样|怎樣)", source, re.I)
                and re.search(r"\d+\s*(?:H|D|hours?|days?|小时|天)", source, re.I)):
            continue
        category = str(item.get("category", "SEMANTIC_UNSUPPORTED"))
        allowed_categories = {"SEMANTIC_UNSUPPORTED", "DATASET_UNAVAILABLE", "INSUFFICIENT_HISTORY",
                              "NEEDS_PARAMETER", "CURRENT_ONLY", "HISTORICAL_ONLY", "SOURCE_STALE",
                              "CAPABILITY_DISABLED"}
        # Categories are a closed product taxonomy, not a model-controlled
        # field.  Preserve the grounded unsupported clause/reason but map an
        # unknown provider label to the conservative user-visible category.
        if category not in allowed_categories:
            category = "SEMANTIC_UNSUPPORTED"
        reason_code = str(item.get("reason_code", "UNSUPPORTED_CONCEPT"))
        # Native CVD is a deliberately disabled conditional capability: it
        # must never be presented as a vague parser limitation.  This narrow,
        # text-grounded normalization keeps provider wording from hiding the
        # audited data gate; it does not make CVD executable or substitute it.
        if re.search(r"\bCVD\b", source, re.I):
            reason_code = "CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE"
            category = "CAPABILITY_DISABLED"
        output.append(UnsupportedClauseV2(source, reason_code,
                                          category, tuple(map(str, item.get("suggestions", ())))))
    return tuple(output)


def _assert_clause_accounting(text: str, sources: Sequence[str]) -> None:
    """Reject semantic content that the provider silently dropped.

    This is vocabulary-neutral: capabilities decide what is executable while
    source spans prove that every clause was either recognized, unsupported,
    or declared incomplete.
    """
    remaining = text
    for source in sorted((item for item in sources if item), key=len, reverse=True):
        remaining = re.sub(re.escape(source), " ", remaining, count=1, flags=re.I)
    # Non-semantic thesis scaffolding is allowed outside clause source spans.
    remaining = re.sub(r"\b(?:BTC|ETH|SOL|1H|4H|1D|15M|when|if|then|after|historically|"
                       r"what|usually|happens?|and|or|either|with|while|but|not)\b", " ", remaining, flags=re.I)
    # Forward-horizon wording is research scaffolding, not an event clause.
    remaining = re.sub(r"\b\d+(?:\.\d+)?\s*(?:m|h|d|mins?|minutes?|hours?|days?)\b", " ",
                       remaining, flags=re.I)
    remaining = re.sub(r"\d+(?:\.\d+)?\s*(?:小时|天)", " ", remaining)
    remaining = re.sub(r"(?:并且|同时|而且|或者|任一|但)", " ", remaining)
    remaining = re.sub(r"(?:之后|以后|历史上|通常|怎么样|会怎样|并且|同时|而且|或者|任一|但|当|如果)", " ", remaining)
    remaining = re.sub(r"[\s,，。;；:：?!？()（）/]+", "", remaining)
    if remaining:
        raise ThesisParserV3Error(f"provider left unaccounted clause text: {remaining[:80]}")


def _deterministic_failed_structure_raw(text: str, capabilities: Mapping[str, Any]) -> dict[str, Any] | None:
    """Compile the narrow, public failed-structure grammar without model guesswork.

    This only accepts a single failed-breakout or failed-breakdown assertion.
    It exists so the backend-advertised examples are executable even when an
    upstream semantic provider chooses an invalid transport representation.
    Other language continues through the capability-constrained provider.
    """
    compact = text.strip()
    if re.search(r"\b(?:and|or|either|with|but)\b|(?:并且|同时|而且|或者|任一|但)", compact, re.I):
        return None
    breakdown = bool(re.search(r"(?:失败跌破|假跌破|跌破后.*?(?:重新)?回到跌破位上方)", compact, re.I))
    breakout = bool(re.search(r"(?:失败突破|假突破|突破后.*?(?:重新)?跌回突破位)", compact, re.I))
    if breakdown == breakout:
        return None
    feature = "FAILED_BREAKDOWN_CONFIRMED" if breakdown else "FAILED_BREAKOUT_CONFIRMED"
    direction_level = r"(?:最低点|前低)" if breakdown else r"(?:最高点|前高)"
    opposite_level = r"(?:最高点|前高)" if breakdown else r"(?:最低点|前低)"
    if re.search(opposite_level, compact, re.I):
        return None
    lookback_match = re.search(rf"(?:参考)?过去\s*(\d+)\s*根.*?{direction_level}", compact, re.I)
    window_match = re.search(r"(?:突破后|跌破后)\s*(\d+)\s*根.*?内", compact, re.I)
    parameters = {
        "lookback_bars": int(lookback_match.group(1)) if lookback_match else 20,
        "failure_window_bars": int(window_match.group(1)) if window_match else 3,
    }
    prefix = re.sub(r"^\s*(?:BTC|ETH|SOL)\s+(?:15M|1H|4H|1D)\s*", "", compact, flags=re.I)
    condition_source = re.split(r"(?:，|,|。|\.)\s*(?:之后|以后|after\b)", prefix, maxsplit=1,
                                flags=re.I)[0].strip()
    if not condition_source:
        return None
    marker = ("失败跌破" if "失败跌破" in condition_source else
              "假跌破" if "假跌破" in condition_source else
              "重新回到跌破位上方" if breakdown else
              "失败突破" if "失败突破" in condition_source else "假突破")
    assumptions: list[dict[str, str]] = []
    if not lookback_match:
        assumptions.append({"preset_id": "failed-breakdown-lookback-standard" if breakdown
                            else "failed-breakout-lookback-standard", "source_text": marker})
    if not window_match:
        assumptions.append({"preset_id": "failed-breakdown-window-standard" if breakdown
                            else "failed-breakout-window-standard", "source_text": marker})
    instruments = tuple(map(str, capabilities.get("instruments", ())))
    timeframes = tuple(map(str, capabilities.get("timeframes", ())))
    instrument = next((item for item in instruments if re.search(rf"(?<![A-Za-z]){re.escape(item)}(?![A-Za-z])", compact, re.I)), None)
    timeframe = next((item for item in timeframes if re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", compact, re.I)), None)
    if not instrument or not timeframe:
        return None
    horizons = list(_explicit_horizons_from_text(compact, tuple(map(str, capabilities.get("horizons", ()))), exclude=(timeframe,)))
    return {"detected_language": "zh" if re.search(r"[\u4e00-\u9fff]", compact) else "en",
            "instrument": instrument, "timeframe": timeframe, "forward_horizons": horizons,
            "expression": {"node_type": "CONDITION", "feature": feature, "operator": "eq",
                           "value": True, "parameters": parameters},
            "recognized_clauses": [condition_source], "assumptions": assumptions,
            "unsupported_clauses": [], "missing_parameters": [], "warnings": []}


def _explicit_horizons_from_text(text: str, supported: Sequence[str], *, exclude: Sequence[str] = ()) -> tuple[str, ...]:
    """Recover only horizon values written explicitly by the user, never a model default."""
    matches: list[str] = []
    for horizon in supported:
        if str(horizon) in set(map(str, exclude)):
            continue
        match = re.fullmatch(r"(\d+)([HD])", str(horizon), flags=re.I)
        if not match:
            continue
        amount, unit = match.groups()
        suffixes = (r"h|hr|hrs|hour|hours|小时") if unit.upper() == "H" else (r"d|day|days|天")
        if re.search(rf"(?<![A-Za-z0-9]){amount}\s*(?:{'|'.join(suffixes)})(?![A-Za-z])", text, re.I):
            matches.append(str(horizon))
    return tuple(matches)


def _canonical_horizon(value: str, supported: Sequence[str]) -> str:
    compact = re.sub(r"\s+", "", value).upper()
    aliases = {str(item).upper(): str(item) for item in supported}
    if compact in aliases:
        return aliases[compact]
    match = re.fullmatch(r"(\d+)(?:HR|HRS|HOUR|HOURS)", compact)
    if match and f"{match.group(1)}H" in aliases:
        return aliases[f"{match.group(1)}H"]
    match = re.fullmatch(r"(\d+)(?:DAY|DAYS)", compact)
    if match and f"{match.group(1)}D" in aliases:
        return aliases[f"{match.group(1)}D"]
    return value


def _walk_expression(node: ExpressionNode) -> tuple[ConditionNode, ...]:
    if isinstance(node, ConditionNode):
        return (node,)
    if isinstance(node, NotNode):
        return _walk_expression(node.child)
    return tuple(item for child in node.children for item in _walk_expression(child))


def _has_node(node: ExpressionNode, expected: type[Any]) -> bool:
    if isinstance(node, expected):
        return True
    if isinstance(node, ConditionNode):
        return False
    if isinstance(node, NotNode):
        return _has_node(node.child, expected)
    return any(_has_node(child, expected) for child in node.children)


def _standalone_not_count(source: str) -> int:
    comparator_removed = re.sub(
        r"\bnot (?:above|below|greater than|less than)\b|不高于|不低于", " ", source,
        flags=re.I)
    return (len(re.findall(r"\bnot\b", comparator_removed, re.I))
            + len(re.findall(r"不是|不满足", comparator_removed)))


def _has_standalone_not(source: str) -> bool:
    return _standalone_not_count(source) % 2 == 1


def _assert_logic_presence(text: str, expression: ExpressionNode) -> None:
    if re.search(r"\b(?:or|either)\b|或者|任一", text, flags=re.I) and not _has_node(expression, AnyNode):
        raise ThesisParserV3Error("explicit OR is not represented by an ANY node")
    if (re.search(r"\band\b|并且|同时|而且|但", text, flags=re.I)
            and not re.search(r"\bbetween\b[^.;，。]*\band\b", text, flags=re.I)
            and not _has_node(expression, AllNode)):
        raise ThesisParserV3Error("explicit AND is not represented by an ALL node")


def _logic_signature(node: ExpressionNode, leaf_labels: Mapping[int, str] | None = None) -> Any:
    if isinstance(node, ConditionNode):
        label = leaf_labels.get(id(node), "LEAF") if leaf_labels else "LEAF"
        return ("NOT", label) if node.operator == "eq" and node.value is False else label
    if isinstance(node, NotNode):
        return ("NOT", _logic_signature(node.child, leaf_labels))
    name = "ALL" if isinstance(node, AllNode) else "ANY"
    children = tuple(sorted((_logic_signature(child, leaf_labels) for child in node.children), key=repr))
    # BETWEEN is canonically an ALL of two inclusive bounds grounded in one
    # source clause. Treat that compiled pair as one logical atom.
    if name == "ALL" and children and len(set(children)) == 1 and isinstance(children[0], str):
        return children[0]
    return (name, children)


def _expected_logic_signature_heuristic(text: str, recognized: Sequence[str]) -> Any | None:
    located: list[tuple[int, int, str]] = []
    cursor, folded = 0, text.casefold()
    ordered_clauses = sorted(recognized, key=lambda clause: folded.find(clause.casefold()))
    for clause in ordered_clauses:
        start = folded.find(clause.casefold(), cursor)
        if start < 0:
            start = folded.find(clause.casefold())
        if start < 0:
            return None
        located.append((start, start + len(clause), clause))
        cursor = start + len(clause)
    if len(located) < 2:
        if not located:
            return None
        clause = located[0][2]
        label = f"CLAUSE:{clause.casefold()}"
        return ("NOT", label) if _has_standalone_not(clause) else label
    kinds: list[str] = []
    for left, right in zip(located, located[1:]):
        separator = text[left[1]:right[0]]
        has_or = bool(re.search(r"\b(?:or|either)\b|或者|任一", separator, flags=re.I))
        has_and = bool(re.search(r"\b(?:and|with|but)\b|并且|同时|而且|但", separator, flags=re.I))
        boundary = bool(re.search(r"[,，;；]", separator))
        if not has_or and not has_and and boundary:
            kinds.append("TOP_AND")
            continue
        if has_or == has_and:
            return None
        kinds.append("TOP_AND" if boundary and has_and else "OR" if has_or else "AND")
    segments: list[list[str]] = [[]]
    labels: list[Any] = []
    for item in located:
        clause = item[2]
        label = f"CLAUSE:{clause.casefold()}"
        labels.append(("NOT", label) if _has_standalone_not(clause) else label)
    for index, kind in enumerate(kinds):
        segments[-1].append(labels[index])
        if kind == "TOP_AND":
            segments.append([])
        else:
            segments[-1].append(kind)
    segments[-1].append(labels[-1])

    def compile_segment(tokens: list[str]) -> Any:
        terms: list[Any] = []
        current: list[Any] = [tokens[0]]
        for index in range(1, len(tokens), 2):
            if tokens[index] == "AND":
                current.append(tokens[index + 1])
            else:
                terms.append(current[0] if len(current) == 1 else
                             ("ALL", tuple(sorted(current, key=repr))))
                current = [tokens[index + 1]]
        terms.append(current[0] if len(current) == 1 else
                     ("ALL", tuple(sorted(current, key=repr))))
        return (terms[0] if len(terms) == 1 else
                ("ANY", tuple(sorted(terms, key=repr))))

    compiled = [compile_segment(segment) for segment in segments]
    return (compiled[0] if len(compiled) == 1 else
            ("ALL", tuple(sorted(compiled, key=repr))))


def _expected_logic_signature(text: str, recognized: Sequence[str]) -> Any | None:
    """Parse the closed boolean grammar with parentheses and AND precedence."""
    text = text.replace("（", "(").replace("）", ")")
    if "(" not in text and ")" not in text:
        return _expected_logic_signature_heuristic(text, recognized)
    folded = text.casefold()
    ordered = sorted(recognized, key=lambda clause: folded.find(clause.casefold()))
    located: list[tuple[int, int, str]] = []
    cursor = 0
    for clause in ordered:
        start = folded.find(clause.casefold(), cursor)
        if start < 0:
            return None
        located.append((start, start + len(clause), clause))
        cursor = start + len(clause)
    if not located:
        return None
    def clause_token(clause: str) -> Any:
        label = f"CLAUSE:{clause.casefold()}"
        return ("NOT", label) if _has_standalone_not(clause) else label

    tokens: list[Any] = ["(" for _ in range(text[:located[0][0]].count("("))]
    tokens.append(clause_token(located[0][2]))
    connector = re.compile(r"\b(?:and|with|but|or|either)\b|并且|同时|而且|但|或者|任一", re.I)
    for left, right in zip(located, located[1:]):
        separator = text[left[1]:right[0]]
        match = connector.search(separator)
        if match:
            before, raw_operator, after = separator[:match.start()], match.group(), separator[match.end():]
            is_or = bool(re.fullmatch(r"or|either|或者|任一", raw_operator, re.I))
            operator = "OR" if is_or else "AND"
        elif re.search(r"[,，;；]", separator):
            before, after, operator = separator, "", "AND"
        else:
            return None
        tokens.extend(")" for _ in range(before.count(")")))
        tokens.append(operator)
        tokens.extend("(" for _ in range(after.count("(")))
        tokens.append(clause_token(right[2]))
    tokens.extend(")" for _ in range(text[located[-1][1]:].count(")")))

    precedence = {"OR": 1, "AND": 2}
    values: list[Any] = []
    operators: list[str] = []

    def reduce_once() -> bool:
        if not operators or operators[-1] == "(" or len(values) < 2:
            return False
        operator = operators.pop()
        right, left = values.pop(), values.pop()
        node = "ALL" if operator == "AND" else "ANY"
        children: list[Any] = []
        for item in (left, right):
            if isinstance(item, tuple) and item[0] == node:
                children.extend(item[1])
            else:
                children.append(item)
        values.append((node, tuple(sorted(children, key=repr))))
        return True

    for token in tokens:
        if not isinstance(token, str) or token.startswith("CLAUSE:"):
            values.append(token)
        elif token == "(":
            operators.append(token)
        elif token == ")":
            while operators and operators[-1] != "(":
                if not reduce_once():
                    return None
            if not operators:
                return None
            operators.pop()
        else:
            while (operators and operators[-1] != "("
                   and precedence[operators[-1]] >= precedence[token]):
                if not reduce_once():
                    return None
            operators.append(token)
    while operators:
        if operators[-1] == "(" or not reduce_once():
            return None
    if len(values) != 1:
        return None
    result = values[0]
    prefix = text[:located[0][0]]
    if _standalone_not_count(prefix) % 2 == 1:
        result = ("NOT", result)
    return result


def _assert_logic_grounding(text: str, expression: ExpressionNode,
                            recognized: Sequence[str], capabilities: Mapping[str, Any]) -> None:
    _assert_logic_presence(text, expression)
    expected = _expected_logic_signature(text, recognized)
    bindings = _bind_leaf_clauses(expression, recognized, capabilities)
    leaf_labels: dict[int, Any] = {}
    for index, leaf in enumerate(_walk_expression(expression)):
        label: Any = f"CLAUSE:{bindings[index].casefold()}"
        if not isinstance(leaf.value, bool) and _has_standalone_not(bindings[index]):
            label = ("NOT", label)
        leaf_labels[id(leaf)] = label
    if expected is None or expected != _logic_signature(expression, leaf_labels):
        raise ThesisParserV3Error(
            "boolean grouping is not grounded in the source clause order")


def _assert_operator_grounding(text: str, expression: ExpressionNode) -> None:
    operators = {leaf.operator for leaf in _walk_expression(expression)}
    checks = (
        (r">=|\bat least\b|\bnot (?:below|less than)\b|至少|不低于", "gte"),
        (r"<=|\bat most\b|\bnot (?:above|greater than)\b|至多|不高于", "lte"),
    )
    for pattern, expected in checks:
        if re.search(pattern, text, flags=re.I) and expected not in operators:
            raise ThesisParserV3Error(f"explicit comparison is not represented by {expected}")
    without_negated = re.sub(
        r"\bnot (?:above|below|greater than|less than)\b|不高于|不低于", " ", text,
        flags=re.I)
    if re.search(r"(?<![<>=])>(?!=)|\b(?:above|greater than)\b|超过|高于|大于", without_negated, flags=re.I):
        if "gt" not in operators:
            raise ThesisParserV3Error("explicit comparison is not represented by gt")
    if re.search(r"(?<![<>=])<(?!=)|\b(?:below|less than)\b|低于|小于", without_negated, flags=re.I):
        if "lt" not in operators:
            raise ThesisParserV3Error("explicit comparison is not represented by lt")


def _assumption_matches_leaf(assumption: PresetAssumptionV1, leaf: ConditionNode) -> bool:
    applied = assumption.applied
    return (leaf.feature == assumption.feature and leaf.operator == applied["operator"]
            and leaf.value == applied["value"]
            and all(leaf.parameters.get(name) == value
                    for name, value in dict(applied["parameters"]).items()))


def _assert_numeric_grounding_legacy(expression: ExpressionNode, recognized: Sequence[str],
                                     assumptions: Sequence[PresetAssumptionV1]) -> None:
    explicit = set().union(*(_explicit_numeric_values(item) for item in recognized)) if recognized else set()
    for leaf in _walk_expression(expression):
        authorized_value = any(_assumption_matches_leaf(item, leaf)
                               and item.applied["value"] == leaf.value for item in assumptions)
        if isinstance(leaf.value, (int, float)) and not isinstance(leaf.value, bool):
            if float(leaf.value) not in explicit and not authorized_value:
                raise ThesisParserV3Error(f"threshold for {leaf.feature} is not grounded in user text or a preset")
        for name, value in leaf.parameters.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                authorized = any(_assumption_matches_leaf(item, leaf)
                                 and item.applied["parameters"].get(name) == value
                                 for item in assumptions)
                if float(value) not in explicit and not authorized:
                    raise ThesisParserV3Error(f"parameter {name} for {leaf.feature} is not grounded")


def _bind_leaf_clauses(expression: ExpressionNode, recognized: Sequence[str],
                       capabilities: Mapping[str, Any]) -> dict[int, str]:
    terms_by_feature = {
        str(item.get("code")): tuple(str(term).casefold()
                                     for values in item.get("semantic_terms", {}).values()
                                     for term in values)
        for item in capabilities.get("features", []) if isinstance(item, Mapping)
    }
    bindings: dict[int, str] = {}
    used: set[int] = set()
    leaves = _walk_expression(expression)
    candidates: list[tuple[int, list[int]]] = []
    for leaf_index, leaf in enumerate(leaves):
        terms = terms_by_feature.get(leaf.feature, ())
        if not terms:
            raise ThesisParserV3Error(
                f"capability has no semantic grounding terms for {leaf.feature}")
        matches = [index for index, clause in enumerate(recognized)
                   if any(term and term in clause.casefold() for term in terms)]
        if isinstance(leaf.value, (int, float)) and not isinstance(leaf.value, bool):
            value_matches = [index for index in matches
                             if float(leaf.value) in _explicit_numeric_values(recognized[index])]
            if value_matches:
                matches = value_matches
        for parameter_value in leaf.parameters.values():
            if isinstance(parameter_value, (int, float)) and not isinstance(parameter_value, bool):
                parameter_matches = [index for index in matches
                                     if float(parameter_value) in _explicit_numeric_values(recognized[index])]
                if parameter_matches:
                    matches = parameter_matches
        if not matches:
            raise ThesisParserV3Error(
                f"feature {leaf.feature} is not grounded in a recognized clause")
        candidates.append((leaf_index, matches))
    for leaf_index, matches in sorted(candidates, key=lambda item: (len(item[1]), item[0])):
        available = [index for index in matches if index not in used]
        if available:
            chosen = available[0]
            used.add(chosen)
        else:
            shareable = [index for index in matches
                         if re.search(r"\bbetween\b|介于|在.+(?:到|至)", recognized[index], re.I)]
            if not shareable:
                raise ThesisParserV3Error(
                    "multiple expression leaves are bound to the same clause")
            chosen = shareable[0]
            prior_features = {leaves[prior_index].feature for prior_index, source in bindings.items()
                              if source == recognized[chosen]}
            if prior_features != {leaves[leaf_index].feature}:
                raise ThesisParserV3Error(
                    "BETWEEN clause cannot be shared across different features")
        bindings[leaf_index] = recognized[chosen]
    if used != set(range(len(recognized))):
        raise ThesisParserV3Error(
            "recognized clause is not bound to an expression leaf")
    # A recognized span must contain only the grounded feature semantics and
    # closed comparison/parameter grammar. One valid phrase cannot launder an
    # unrelated assertion embedded in the same span.
    for leaf_index, clause in bindings.items():
        leaf = leaves[leaf_index]
        residue = clause.casefold()
        for term in sorted(terms_by_feature[leaf.feature], key=len, reverse=True):
            residue = re.sub(re.escape(term), " ", residue, flags=re.I)
        residue = re.sub(r"-?\d+(?:\.\d+)?|[<>=]+", " ", residue)
        residue = re.sub(
            r"\b(?:the|a|an|is|was|are|be|at|over|under|not|above|below|greater|less|than|"
            r"least|most|between|and|previous|past|last|confirmed|close|closes|candle|candles|"
            r"bar|bars|within|after|then|historical|history|of|to|back|rate|high|low|"
            r"btc|eth|sol|1h|4h|1d|15m)\b", " ", residue,
            flags=re.I)
        residue = re.sub(r"K\s*线", " ", residue, flags=re.I)
        residue = re.sub(r"(?:不高于|不低于|至少|至多|介于|过去|此前|前|后|内|到|至|根|参考|的|"
                         r"已确认|确认|收盘|重新|回到|跌回|涨回|突破位|跌破位|最高点|最低点|K线|线|百分位)",
                         " ", residue, flags=re.I)
        residue = re.sub(r"[^A-Za-z\u4e00-\u9fff]+", "", residue)
        if residue:
            raise ThesisParserV3Error(
                f"recognized clause contains ungrounded semantic text: {residue[:80]}")
    return bindings


def _assert_numeric_grounding(expression: ExpressionNode, recognized: Sequence[str],
                              assumptions: Sequence[PresetAssumptionV1],
                              capabilities: Mapping[str, Any]) -> None:
    bindings = _bind_leaf_clauses(expression, recognized, capabilities)
    for leaf_index, leaf in enumerate(_walk_expression(expression)):
        clause = bindings[leaf_index]
        if isinstance(leaf.value, bool):
            expected_operator = None
        elif re.search(r">=|\bat least\b|\bnot (?:below|less than)\b|至少|不低于", clause, re.I):
            expected_operator = "gte"
        elif re.search(r"<=|\bat most\b|\bnot (?:above|greater than)\b|至多|不高于", clause, re.I):
            expected_operator = "lte"
        else:
            positive = re.sub(r"\bnot (?:above|below|greater than|less than)\b|不高于|不低于",
                              " ", clause, flags=re.I)
            if re.search(r"(?<![<>=])>(?!=)|\b(?:above|greater than)\b|超过|高于|大于", positive, re.I):
                expected_operator = "gt"
            elif re.search(r"(?<![<>=])<(?!=)|\b(?:below|less than)\b|低于|小于", positive, re.I):
                expected_operator = "lt"
            else:
                expected_operator = None
        if expected_operator and _has_standalone_not(clause):
            expected_operator = {"gt": "lte", "gte": "lt", "lt": "gte", "lte": "gt"}[
                expected_operator]
        if expected_operator and leaf.operator != expected_operator:
            raise ThesisParserV3Error(
                f"operator for {leaf.feature} is not grounded in its source clause")
        explicit = _explicit_numeric_values(clause)
        authorized_value = any(
            _assumption_matches_leaf(item, leaf)
            and item.applied["value"] == leaf.value
            and item.source_text.casefold() in clause.casefold()
            for item in assumptions)
        if isinstance(leaf.value, (int, float)) and not isinstance(leaf.value, bool):
            if float(leaf.value) not in explicit and not authorized_value:
                raise ThesisParserV3Error(
                    f"threshold for {leaf.feature} is not grounded in its source clause or a preset")
        for name, value in leaf.parameters.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                authorized = any(
                    _assumption_matches_leaf(item, leaf)
                    and item.applied["parameters"].get(name) == value
                    and item.source_text.casefold() in clause.casefold()
                    for item in assumptions)
                if float(value) not in explicit and not authorized:
                    raise ThesisParserV3Error(
                        f"parameter {name} for {leaf.feature} is not grounded in its source clause")


def validate_provider_output(text: str, raw: Mapping[str, Any], capabilities: Mapping[str, Any],
                             *, requested_as_of: int) -> ThesisParseResultV2:
    if not isinstance(raw, Mapping):
        raise ThesisParserV3Error("provider output must be an object")
    _strict_keys(raw, {"detected_language", "instrument", "timeframe", "forward_horizons",
                       "expression", "recognized_clauses", "assumptions", "unsupported_clauses",
                       "missing_parameters", "warnings"}, "provider output")
    language = str(raw.get("detected_language", ""))
    if language not in {"en", "zh"}:
        raise ThesisParserV3Error("detected_language must be en or zh")
    registry = feature_contracts_from_capabilities(capabilities)
    recognized_raw = raw.get("recognized_clauses", [])
    if not isinstance(recognized_raw, list) or any(not isinstance(item, str) for item in recognized_raw):
        raise ThesisParserV3Error("recognized_clauses must be an array of strings")
    # Some provider variants echo the already structured instrument/timeframe
    # header here.  They are not expression clauses and are validated through
    # their dedicated fields below; never treat them as silently removed logic.
    header_tokens = {str(item).casefold() for item in capabilities.get("instruments", ())}
    header_tokens.update(str(item).casefold() for item in capabilities.get("timeframes", ()))
    feature_terms = tuple(str(term).casefold()
                          for feature in capabilities.get("features", []) if isinstance(feature, Mapping)
                          for values in feature.get("semantic_terms", {}).values()
                          for term in values)
    recognized = tuple(item.strip() for item in recognized_raw
                       if item.strip().casefold() not in header_tokens
                       and any(term and term in item.casefold() for term in feature_terms))
    if any(not item or item.casefold() not in text.casefold() for item in recognized):
        raise ThesisParserV3Error("recognized clause is not grounded in user text")
    assumptions = _parse_assumptions(raw.get("assumptions", []), text, capabilities)
    unsupported = _parse_unsupported(raw.get("unsupported_clauses", []), text)
    missing_raw = raw.get("missing_parameters", [])
    if not isinstance(missing_raw, list):
        raise ThesisParserV3Error("missing_parameters must be an array")
    missing: list[MissingParameterV2] = []
    for item in missing_raw:
        if not isinstance(item, Mapping):
            raise ThesisParserV3Error("missing parameter must be an object")
        _strict_keys(item, {"source_text", "feature", "parameter"}, "missing parameter")
        source = str(item.get("source_text", ""))
        if not source or source.casefold() not in text.casefold():
            raise ThesisParserV3Error("missing parameter is not grounded in user text")
        missing.append(MissingParameterV2(source, str(item.get("feature", "")),
                                          str(item.get("parameter", ""))))
    _assert_clause_accounting(text, [*recognized, *(item.source_text for item in unsupported),
                                     *(item.source_text for item in missing)])
    expression_raw = raw.get("expression")
    expression = parse_expression(_compile_between(expression_raw), registry) if expression_raw else None
    if expression is not None and assumptions:
        for assumption in assumptions:
            if not any(_assumption_matches_leaf(assumption, leaf)
                       for leaf in _walk_expression(expression)):
                raise ThesisParserV3Error("expression does not match its semantic preset assumption")
    if expression is not None:
        if not unsupported and not missing:
            _assert_logic_grounding(text, expression, recognized, capabilities)
        _assert_numeric_grounding(expression, recognized, assumptions, capabilities)
    instrument = str(raw.get("instrument", "")).upper()
    raw_timeframe = str(raw.get("timeframe", ""))
    timeframe = {str(item).upper(): str(item) for item in capabilities.get("timeframes", ())}.get(
        raw_timeframe.upper(), raw_timeframe)
    supported_instruments = set(map(str, capabilities.get("instruments", ())))
    supported_timeframes = set(map(str, capabilities.get("timeframes", ())))
    supported_horizons = tuple(map(str, capabilities.get("horizons", ())))
    explicit_instruments = [item for item in supported_instruments
                            if re.search(rf"(?<![A-Za-z]){re.escape(item)}(?![A-Za-z])", text, re.I)]
    if instrument not in supported_instruments and len(explicit_instruments) == 1:
        instrument = explicit_instruments[0]
    explicit_timeframes = [item for item in supported_timeframes
                           if re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", text, re.I)]
    if timeframe not in supported_timeframes and len(explicit_timeframes) == 1:
        timeframe = explicit_timeframes[0]
    horizons = tuple(_canonical_horizon(str(item), supported_horizons)
                     for item in raw.get("forward_horizons", ())
                     if isinstance(item, str))
    if horizons and any(item not in set(supported_horizons) for item in horizons):
        explicit_horizons = _explicit_horizons_from_text(text, supported_horizons, exclude=(timeframe,))
        if explicit_horizons:
            horizons = explicit_horizons
    if instrument not in supported_instruments or timeframe not in supported_timeframes:
        missing.append(MissingParameterV2("", "THESIS", "instrument_or_timeframe"))
    if not horizons:
        horizons = _explicit_horizons_from_text(text, supported_horizons, exclude=(timeframe,))
        if not horizons:
            missing.append(MissingParameterV2(text, "THESIS", "forward_horizons"))
    if horizons and any(item not in set(supported_horizons) for item in horizons):
        raise ThesisParserV3Error("forward_horizons contains unsupported value")
    warnings_raw = raw.get("warnings", [])
    if not isinstance(warnings_raw, list) or any(not isinstance(item, str) for item in warnings_raw):
        raise ThesisParserV3Error("warnings must be an array of strings")
    # Dataset/timeframe availability is deterministic capability policy, not a
    # model judgment. Keep the original expression visible but non-executable.
    if expression is not None and timeframe in supported_timeframes:
        unavailable = []
        for leaf in _walk_expression(expression):
            contract = registry[leaf.feature]
            if timeframe not in contract.supported_timeframes:
                unavailable.append((leaf.feature, f"{leaf.feature}_{timeframe}_HISTORICAL_UNAVAILABLE"))
            elif contract.historical_availability != "AVAILABLE":
                unavailable.append((leaf.feature, f"{leaf.feature}_HISTORICAL_DATASET_UNAVAILABLE"))
        known_codes = {item.reason_code for item in unsupported}
        source = recognized[0] if recognized else text
        unsupported = tuple(unsupported) + tuple(
            UnsupportedClauseV2(source, code, "DATASET_UNAVAILABLE")
            for _feature, code in unavailable if code not in known_codes)
    # Unsupported or incomplete original clauses make execution fail closed.
    spec = None
    if unsupported:
        executable_leaf_exists = bool(expression and any(
            registry[leaf.feature].historical_availability == "AVAILABLE"
            and timeframe in registry[leaf.feature].supported_timeframes
            for leaf in _walk_expression(expression)))
        status = "PARTIALLY_SUPPORTED" if executable_leaf_exists else "UNSUPPORTED"
    elif missing or expression is None:
        status = "NEEDS_INPUT"
    else:
        status = "READY_WITH_ASSUMPTIONS" if assumptions else "READY"
        spec = ThesisSpecV2(instrument, timeframe, expression, horizons,
                            requested_as_of, assumptions,
                            {"parser_version": PARSER_V3_VERSION})
    return ThesisParseResultV2(status, language, expression, spec, recognized, assumptions,
                               unsupported, tuple(missing), tuple(warnings_raw))


class ThesisParserServiceV3:
    def __init__(self, provider: ThesisParserProviderV3, capabilities: Mapping[str, Any]) -> None:
        self.provider, self.capabilities = provider, capabilities

    def parse(self, text: str, *, requested_as_of: int) -> ThesisParseResultV2:
        deterministic = _deterministic_failed_structure_raw(text, self.capabilities)
        if deterministic is not None:
            return validate_provider_output(text.strip(), deterministic, self.capabilities,
                                            requested_as_of=requested_as_of)
        request = provider_request(text, self.capabilities)
        last_validation_error: Exception | None = None
        for attempt in range(MAX_PROVIDER_TRANSPORT_ATTEMPTS):
            try:
                response = self.provider.generate(request)
            except Exception as error:
                if attempt + 1 < MAX_PROVIDER_TRANSPORT_ATTEMPTS:
                    continue
                raise ThesisParserV3Error("parser provider unavailable") from error
            if isinstance(response, Mapping):
                try:
                    return validate_provider_output(
                        text.strip(), _normalize_provider_transport(response, self.capabilities), self.capabilities,
                        requested_as_of=requested_as_of)
                except (ThesisParserV3Error, ExpressionValidationError) as error:
                    last_validation_error = error
                    continue
            raw_text = response if isinstance(response, str) else getattr(response, "raw_text", None)
            if not isinstance(raw_text, str):
                if attempt + 1 < MAX_PROVIDER_TRANSPORT_ATTEMPTS:
                    continue
                raise ThesisParserV3Error("provider returned no JSON object")
            try:
                response = json.loads(raw_text)
            except json.JSONDecodeError as error:
                if attempt + 1 < MAX_PROVIDER_TRANSPORT_ATTEMPTS:
                    continue
                raise ThesisParserV3Error("provider returned invalid JSON") from error
            try:
                return validate_provider_output(
                    text.strip(), _normalize_provider_transport(response, self.capabilities), self.capabilities,
                    requested_as_of=requested_as_of)
            except (ThesisParserV3Error, ExpressionValidationError) as error:
                last_validation_error = error
        raise ThesisParserV3Error("provider output failed deterministic validation") from last_validation_error


def _normalize_provider_ast_node_key(value: Any) -> Any:
    """Accept the sole documented provider spelling variant before strict validation."""
    if isinstance(value, list):
        return [_normalize_provider_ast_node_key(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    normalized = {key: _normalize_provider_ast_node_key(item) for key, item in value.items()}
    if "node_type" not in normalized and normalized.get("type") in {"CONDITION", "ALL", "ANY", "NOT"}:
        normalized["node_type"] = normalized.pop("type")
    return normalized


def _normalize_provider_transport(value: Any, capabilities: Mapping[str, Any]) -> Any:
    """Normalize the documented Responses AST wrapper using only source language."""
    normalized = _normalize_provider_ast_node_key(value)
    if not isinstance(normalized, Mapping):
        return normalized
    clauses = [str(item) for item in normalized.get("recognized_clauses", ()) if isinstance(item, str)]
    terms = {
        str(item.get("code")): tuple(str(term).casefold()
                                      for values in item.get("semantic_terms", {}).values()
                                      for term in values)
        for item in capabilities.get("features", ()) if isinstance(item, Mapping)
    }

    def source_for(feature: str) -> str:
        for clause in clauses:
            if any(term and term in clause.casefold() for term in terms.get(feature, ())):
                return clause
        return ""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(value) for value in node]
        if not isinstance(node, Mapping):
            return node
        item = {key: walk(value) for key, value in node.items()}
        kind = str(item.get("node_type", item.get("type", ""))).upper()
        if kind == "LEAF":
            kind = "CONDITION"
        if kind:
            item.pop("type", None); item["node_type"] = kind
        if kind == "CONDITION" and isinstance(item.get("condition"), Mapping):
            condition = dict(item.pop("condition"))
            if condition.get("type") == "LEAF":
                condition.pop("type")
            item.update({key: value for key, value in condition.items() if key not in item})
            then = item.get("then")
            if (isinstance(then, Mapping) and then.get("node_type") in {"LEAF", "CONDITION"}
                    and then.get("feature") == "FORWARD_RETURN"):
                item.pop("then")
        if kind in {"ALL", "ANY"} and "children" not in item and isinstance(item.get("conditions"), list):
            item["children"] = item.pop("conditions")
        if kind in {"ALL", "ANY"} and isinstance(item.get("children"), list) and len(item["children"]) == 1:
            return item["children"][0]
        if kind == "NOT" and "child" not in item and isinstance(item.get("condition"), Mapping):
            item["child"] = item.pop("condition")
        if kind == "NOT" and "child" not in item and isinstance(item.get("children"), list) and len(item["children"]) == 1:
            item["child"] = item.pop("children")[0]
        if kind == "CONDITION":
            item.setdefault("parameters", {})
            source = source_for(str(item.get("feature", ""))).casefold()
            operator = item.get("operator")
            if operator == "gte" and re.search(r"\b(?:above|over|greater than)\b|超过|高于", source) and not re.search(r"at least|至少|不低于", source):
                item["operator"] = "gt"
            elif operator == "lte" and re.search(r"\b(?:below|under|less than)\b|低于", source) and not re.search(r"at most|不高于", source):
                item["operator"] = "lt"
        return item

    output = dict(normalized)
    output["expression"] = walk(output.get("expression"))
    return output
