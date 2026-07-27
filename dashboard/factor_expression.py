"""Immutable typed factor-expression DSL and deterministic bounded generation.

The DSL deliberately describes predictive variables, never entries, exits,
orders, position sizing, stops, or targets.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable


FACTOR_SCHEMA_VERSION = "factor-expression-schema-v1"
FACTOR_GRAMMAR_VERSION = "factor-grammar-v1"
FACTOR_SEARCH_POLICY_VERSION = "factor-autoresearch-v1"
FACTOR_IDENTITY_VERSION = "factor-identity-v1"
SEMANTIC_SEED = 20260727

FUNDING_LOOKBACKS = (3, 6, 12, 21)
INTRADAY_LOOKBACKS = (4, 8, 16, 32, 64)
LAGS = (1, 2, 4)
WINSOR_LIMITS = (2.5, 3.0, 4.0)
MAX_AST_DEPTH = 4
MAX_SEMANTIC_COMPLEXITY = 8
MAX_SOURCE_TERMINALS = 2

FORMAL_INSTRUMENTS = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
FUNDING_TERMINALS = (
    "settled_funding_level", "funding_change", "funding_rolling_mean",
    "funding_rolling_std", "funding_zscore", "time_since_settlement",
)
BASIS_TERMINALS = (
    "absolute_basis", "percentage_basis", "basis_change",
    "basis_rolling_mean", "basis_rolling_std", "basis_zscore",
    "basis_expansion_contraction",
)
PRICE_TERMINALS = (
    "mark_return", "index_return", "realized_volatility", "atr_percentage",
    "ma_slope", "price_to_ma_distance", "bollinger_bandwidth",
    "rolling_volume_ratio", "causal_regime_code",
)
BLOCKED_TERMINALS = (
    "cvd", "open_interest", "cvd_x_oi", "funding_x_oi",
    "predicted_funding", "liquidations", "eth_basis", "sol_basis",
)
UNARY_OPERATORS = (
    "lag", "difference", "rolling_mean", "rolling_std", "rolling_zscore",
    "rolling_rank", "sign", "absolute", "winsorize", "negate",
)
BINARY_OPERATORS = (
    "add", "subtract", "multiply", "safe_divide", "minimum", "maximum",
    "bounded_interaction",
)
COMMUTATIVE_OPERATORS = {
    "add", "multiply", "minimum", "maximum", "bounded_interaction"}
ROLLING_NORMALIZERS = {"rolling_zscore", "rolling_rank"}
SOURCE_GROUP = {
    **{name: "funding" for name in FUNDING_TERMINALS},
    **{name: "basis" for name in BASIS_TERMINALS},
    **{name: "price" for name in PRICE_TERMINALS},
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class FactorNode:
    """A fully immutable factor AST node."""

    operator: str
    terminal: str | None = None
    parameters: tuple[tuple[str, Any], ...] = ()
    children: tuple["FactorNode", ...] = ()

    @classmethod
    def term(cls, name: str, **parameters: Any) -> "FactorNode":
        return cls("terminal", name, tuple(sorted(parameters.items())), ())

    @classmethod
    def unary(cls, operator: str, child: "FactorNode",
              **parameters: Any) -> "FactorNode":
        return cls(operator, None, tuple(sorted(parameters.items())), (child,))

    @classmethod
    def binary(cls, operator: str, left: "FactorNode",
               right: "FactorNode") -> "FactorNode":
        return cls(operator, None, (), (left, right))

    @classmethod
    def conditional(cls, child: "FactorNode", regime: str) -> "FactorNode":
        return cls("conditional", None, (("regime", regime),), (child,))

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.parameters)

    @property
    def depth(self) -> int:
        return 1 + max((child.depth for child in self.children), default=0)

    @property
    def complexity(self) -> int:
        own = 1 if self.operator == "terminal" else 2
        return own + sum(child.complexity for child in self.children)

    @property
    def terminals(self) -> tuple[str, ...]:
        if self.operator == "terminal":
            return (str(self.terminal),)
        return tuple(item for child in self.children for item in child.terminals)

    @property
    def source_groups(self) -> tuple[str, ...]:
        return tuple(sorted({SOURCE_GROUP.get(item, "blocked")
                            for item in self.terminals}))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"operator": self.operator}
        if self.terminal is not None:
            result["terminal"] = self.terminal
        if self.parameters:
            result["parameters"] = dict(self.parameters)
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FactorNode":
        return cls(
            operator=str(raw["operator"]),
            terminal=raw.get("terminal"),
            parameters=tuple(sorted(raw.get("parameters", {}).items())),
            children=tuple(cls.from_dict(child)
                           for child in raw.get("children", [])),
        )


def canonicalize(node: FactorNode) -> FactorNode:
    """Algebraically normalize an AST without consulting mutable metadata."""
    children = tuple(canonicalize(child) for child in node.children)
    result = FactorNode(node.operator, node.terminal, node.parameters, children)
    if result.operator in COMMUTATIVE_OPERATORS:
        children = tuple(sorted(
            result.children, key=lambda item: _stable_json(item.to_dict())))
        result = FactorNode(
            result.operator, result.terminal, result.parameters, children)
    if result.operator == "negate" and result.children[0].operator == "negate":
        return result.children[0].children[0]
    if result.operator in {"minimum", "maximum"} and (
            result.children[0] == result.children[1]):
        return result.children[0]
    return result


def canonical_json(node: FactorNode) -> str:
    return _stable_json(canonicalize(node).to_dict())


def factor_identity(node: FactorNode) -> str:
    def identity_dict(item: FactorNode) -> dict[str, Any]:
        item = canonicalize(item)
        result: dict[str, Any] = {"operator": item.operator}
        if item.terminal is not None:
            result["terminal"] = item.terminal
        parameters = {
            key: value for key, value in item.parameters
            if key not in {
                "metadata_timestamp", "created_at", "updated_at",
                "ingested_at_ms", "feature_timestamps"}}
        if parameters:
            result["parameters"] = parameters
        if item.children:
            result["children"] = [
                identity_dict(child) for child in item.children]
        return result

    payload = "\x1f".join((
        FACTOR_IDENTITY_VERSION, FACTOR_SCHEMA_VERSION,
        FACTOR_GRAMMAR_VERSION, _stable_json(identity_dict(node))))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _condition_count(node: FactorNode) -> int:
    return int(node.operator == "conditional") + sum(
        _condition_count(child) for child in node.children)


def validate_expression(
    node: FactorNode, *, instrument: str,
    eligible_groups: Iterable[str] = ("funding", "basis", "price"),
) -> list[str]:
    """Return deterministic structural/semantic rejection reasons."""
    reasons: list[str] = []
    eligible = set(eligible_groups)
    if node.depth > MAX_AST_DEPTH:
        reasons.append("MAX_AST_DEPTH_EXCEEDED")
    if node.complexity > MAX_SEMANTIC_COMPLEXITY:
        reasons.append("MAX_SEMANTIC_COMPLEXITY_EXCEEDED")
    groups = set(node.source_groups)
    independent = groups - {"price"}
    if len(independent) + int("price" in groups) > MAX_SOURCE_TERMINALS:
        reasons.append("TOO_MANY_INDEPENDENT_SOURCES")
    if _condition_count(node) > 1:
        reasons.append("MULTIPLE_REGIME_CONDITIONS")
    if any(terminal in BLOCKED_TERMINALS for terminal in node.terminals):
        reasons.append("BLOCKED_SOURCE")
    if any(SOURCE_GROUP.get(terminal) not in eligible
           for terminal in node.terminals):
        reasons.append("INELIGIBLE_SOURCE_INSTRUMENT")
    if "basis" in groups and instrument != "BTC-USDT-SWAP":
        reasons.append("BASIS_NOT_FORMALLY_ELIGIBLE_FOR_INSTRUMENT")
    if node.operator not in (
            {"terminal", "conditional"} | set(UNARY_OPERATORS) |
            set(BINARY_OPERATORS)):
        reasons.append("UNKNOWN_OPERATOR")
    if node.operator == "terminal":
        if node.terminal not in SOURCE_GROUP:
            reasons.append("UNKNOWN_TERMINAL")
        if node.children:
            reasons.append("TERMINAL_WITH_CHILDREN")
    elif node.operator in set(UNARY_OPERATORS) | {"conditional"}:
        if len(node.children) != 1:
            reasons.append("INVALID_UNARY_ARITY")
    elif node.operator in BINARY_OPERATORS:
        if len(node.children) != 2:
            reasons.append("INVALID_BINARY_ARITY")
        elif node.children[0] == node.children[1]:
            reasons.append("DUPLICATED_EQUIVALENT_SUBEXPRESSION")
    if node.operator in ROLLING_NORMALIZERS and node.children and (
            node.children[0].operator in ROLLING_NORMALIZERS):
        reasons.append("NESTED_REDUNDANT_NORMALIZER")
    for child in node.children:
        reasons.extend(validate_expression(
            child, instrument=instrument, eligible_groups=eligible))
    return list(dict.fromkeys(reasons))


@dataclass(frozen=True, slots=True)
class GeneratedExpression:
    sequence: int
    instrument: str
    node: FactorNode
    trial_family: str
    parent_identities: tuple[str, ...] = ()


def _terminal_nodes(instrument: str, groups: set[str]) -> list[FactorNode]:
    result: list[FactorNode] = []
    if "funding" in groups:
        result.extend(FactorNode.term(name) for name in (
            "settled_funding_level", "funding_change",
            "time_since_settlement"))
        for lookback in FUNDING_LOOKBACKS:
            result.extend(FactorNode.term(name, lookback=lookback) for name in (
                "funding_rolling_mean", "funding_rolling_std",
                "funding_zscore"))
    if "basis" in groups and instrument == "BTC-USDT-SWAP":
        result.extend(FactorNode.term(name) for name in (
            "absolute_basis", "percentage_basis", "basis_change",
            "basis_expansion_contraction"))
        for lookback in INTRADAY_LOOKBACKS:
            result.extend(FactorNode.term(name, lookback=lookback) for name in (
                "basis_rolling_mean", "basis_rolling_std", "basis_zscore"))
    if "price" in groups:
        result.append(FactorNode.term("causal_regime_code", lookback=32))
        for lookback in INTRADAY_LOOKBACKS:
            result.extend(FactorNode.term(name, lookback=lookback) for name in (
                "mark_return", "index_return", "realized_volatility",
                "atr_percentage", "ma_slope", "price_to_ma_distance",
                "bollinger_bandwidth", "rolling_volume_ratio"))
    return result


def deterministic_generate(
    eligibility_by_instrument: dict[str, set[str]], *,
    raw_budget: int = 2500, seed: int = SEMANTIC_SEED,
) -> list[GeneratedExpression]:
    """Low-complexity enumeration plus a deterministic high-complexity beam.

    The final hash ordering is a deterministic quality-diversity interleave;
    there is no runtime model or stochastic optimizer.
    """
    raw: list[tuple[str, FactorNode, str, tuple[str, ...]]] = []
    for instrument in FORMAL_INSTRUMENTS:
        groups = eligibility_by_instrument.get(instrument, set())
        bases = _terminal_nodes(instrument, groups)
        for node in bases:
            raw.append((instrument, node, "terminal", ()))
        for node in bases:
            parent = (factor_identity(node),)
            for lag in LAGS:
                raw.append((instrument, FactorNode.unary("lag", node, lag=lag),
                            "unary_lag", parent))
                raw.append((instrument, FactorNode.unary(
                    "difference", node, lag=lag), "unary_difference", parent))
            for lookback in INTRADAY_LOOKBACKS:
                for operator in ("rolling_mean", "rolling_std",
                                 "rolling_zscore", "rolling_rank"):
                    raw.append((instrument, FactorNode.unary(
                        operator, node, lookback=lookback),
                        f"unary_{operator}", parent))
            for operator in ("sign", "absolute", "negate"):
                raw.append((instrument, FactorNode.unary(operator, node),
                            f"unary_{operator}", parent))
            for limit in WINSOR_LIMITS:
                raw.append((instrument, FactorNode.unary(
                    "winsorize", node, limit=limit),
                    "unary_winsorize", parent))
        # Bounded exhaustive pairs. Same-node pairs are deliberately recorded
        # and rejected so the global ledger contains generation failures.
        beam = bases[:48]
        for left_index, left in enumerate(beam):
            for right in beam[left_index:left_index + 18]:
                parents = (factor_identity(left), factor_identity(right))
                for operator in BINARY_OPERATORS:
                    raw.append((instrument, FactorNode.binary(
                        operator, left, right), f"binary_{operator}", parents))
        for node in bases[:30]:
            for regime in ("bull", "bear", "range", "high_volatility"):
                raw.append((instrument, FactorNode.conditional(node, regime),
                            f"regime_{regime}", (factor_identity(node),)))
    # Deterministic quality-diversity: round-robin family buckets, each with
    # seeded stable hash order. This avoids a single prolific family filling
    # the bounded budget.
    buckets: dict[str, list[tuple[str, FactorNode, str, tuple[str, ...]]]] = {}
    for item in raw:
        key = f"{item[0]}:{item[2]}"
        buckets.setdefault(key, []).append(item)
    for key, values in buckets.items():
        values.sort(key=lambda item: hashlib.sha256(
            f"{seed}:{key}:{_stable_json(item[1].to_dict())}".encode()).digest())
    selected: list[tuple[str, FactorNode, str, tuple[str, ...]]] = []
    keys = sorted(buckets)
    while len(selected) < raw_budget and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < raw_budget:
                selected.append(buckets[key].pop(0))
    return [
        GeneratedExpression(index + 1, instrument, node, family, parents)
        for index, (instrument, node, family, parents) in enumerate(selected)
    ]


def expression_plain_language(node: FactorNode) -> str:
    node = canonicalize(node)
    if node.operator == "terminal":
        suffix = (
            f" over {node.params['lookback']} observations"
            if "lookback" in node.params else "")
        return str(node.terminal).replace("_", " ") + suffix
    if node.operator == "conditional":
        return (
            f"{expression_plain_language(node.children[0])}, active only in "
            f"the causal {node.params['regime']} regime")
    if len(node.children) == 1:
        params = ", ".join(f"{key}={value}" for key, value in node.parameters)
        detail = f" ({params})" if params else ""
        return (
            f"{node.operator.replace('_', ' ')}{detail} of "
            f"{expression_plain_language(node.children[0])}")
    return (
        f"{expression_plain_language(node.children[0])} "
        f"{node.operator.replace('_', ' ')} "
        f"{expression_plain_language(node.children[1])}")
