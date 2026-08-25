"""Current evidence and tracking contracts for ThesisExpression V2.

The historical baseline is immutable and opaque to this module.  Current
evidence is recomputed from the latest confirmed candle and optional
point-in-time derivative observations.  No historical result is migrated and
the V1 tracking repository/schema remains usable as the persistence layer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

try:
    from market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from signal_identity import canonical_json
    from thesis_breakout_features import (
        FEATURE_ROW_KEYS as STRUCTURE_ROW_KEYS,
        ROLLING_STRUCTURE_FEATURE_VERSION,
        RollingStructureParametersV1,
        compile_rolling_structure_rows,
    )
    from thesis_event_engine import FEATURE_REGISTRY, _compare, compile_feature_rows
    from thesis_event_engine_v2 import compile_thesis_v2
    from thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, FeatureContractV2,
        NotNode, ThesisSpecV2, TruthValue, parse_thesis_spec_v2,
    )
    from thesis_tracking import ThesisTrackingRepositoryV1, TrackingError
except ImportError:
    from .market_context_v2 import TIMEFRAME_SECONDS, confirmed_candles_as_of
    from .signal_identity import canonical_json
    from .thesis_breakout_features import (
        FEATURE_ROW_KEYS as STRUCTURE_ROW_KEYS,
        ROLLING_STRUCTURE_FEATURE_VERSION,
        RollingStructureParametersV1,
        compile_rolling_structure_rows,
    )
    from .thesis_event_engine import FEATURE_REGISTRY, _compare, compile_feature_rows
    from .thesis_event_engine_v2 import compile_thesis_v2
    from .thesis_expression import (
        AllNode, AnyNode, ConditionNode, ExpressionNode, FeatureContractV2,
        NotNode, ThesisSpecV2, TruthValue, parse_thesis_spec_v2,
    )
    from .thesis_tracking import ThesisTrackingRepositoryV1, TrackingError


TRACK_SCHEMA_VERSION_V2 = "tracked-thesis-v2"
TRACK_CREATE_REQUEST_VERSION_V2 = "track-thesis-request-v2"
CURRENT_EVALUATION_VERSION_V2 = "current-thesis-evaluation-v2"
CURRENT_EVALUATION_POLICY_VERSION_V2 = "current-thesis-evaluation-policy-v2"
CURRENT_DATASET_IDENTITY_VERSION_V2 = "current-composite-dataset-v1"
DELTA_VERSION_V2 = "thesis-expression-evaluation-delta-v2"
CURRENT_READER_LIMIT_V2 = 540

DERIVATIVE_SOURCE_GROUPS = frozenset({"OI", "FUNDING", "BASIS"})
DEFAULT_DERIVATIVE_MAX_AGE_SECONDS = {
    "OI": 4 * 60 * 60,
    "FUNDING": 12 * 60 * 60,
    "BASIS": 4 * 60 * 60,
}


def _utc_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_instrument(instrument: str) -> str:
    value = instrument.upper()
    return value if "-" in value else f"{value}-USDT"


def _walk_conditions(node: ExpressionNode) -> list[ConditionNode]:
    if isinstance(node, ConditionNode):
        return [node]
    if isinstance(node, NotNode):
        return _walk_conditions(node.child)
    output: list[ConditionNode] = []
    for child in node.children:
        output.extend(_walk_conditions(child))
    return output


def _truth(value: bool | None) -> TruthValue:
    return TruthValue.TRUE if value is True else TruthValue.FALSE if value is False else TruthValue.UNKNOWN


def _group_truth(node: ExpressionNode, children: Sequence[TruthValue]) -> TruthValue:
    if isinstance(node, NotNode):
        return {TruthValue.TRUE: TruthValue.FALSE, TruthValue.FALSE: TruthValue.TRUE,
                TruthValue.UNKNOWN: TruthValue.UNKNOWN}[children[0]]
    if isinstance(node, AllNode):
        if TruthValue.FALSE in children:
            return TruthValue.FALSE
        return TruthValue.TRUE if all(item is TruthValue.TRUE for item in children) else TruthValue.UNKNOWN
    if TruthValue.TRUE in children:
        return TruthValue.TRUE
    return TruthValue.FALSE if all(item is TruthValue.FALSE for item in children) else TruthValue.UNKNOWN


class CurrentExpressionEvaluatorV2:
    """Evaluate a validated V2 expression at one latest confirmed candle.

    ``derivative_reader.latest(instrument, source_group, as_of)`` must return
    ``{"timestamp": int, "available_at": int?, "values": {feature: value},
    "source": str, "source_version": str, "dataset_id": str?}``.
    ``available_at`` is the source publication timestamp and defaults to the
    observation timestamp.  Either timestamp after the candle close is
    rejected; old observations are UNKNOWN rather than indefinitely filled.
    """

    version = CURRENT_EVALUATION_POLICY_VERSION_V2

    def __init__(self, candle_reader: Any, feature_registry: Mapping[str, FeatureContractV2],
                 *, derivative_reader: Any | None = None,
                 derivative_max_age_seconds: Mapping[str, int] | None = None,
                 clock: Any = time.time) -> None:
        self.candle_reader = candle_reader
        self.feature_registry = dict(feature_registry)
        self.derivative_reader = derivative_reader
        self.derivative_max_age_seconds = {
            **DEFAULT_DERIVATIVE_MAX_AGE_SECONDS,
            **dict(derivative_max_age_seconds or {}),
        }
        self.clock = clock

    def _blocked(self, track: Mapping[str, Any], now: int, limitation: str,
                 *, status: str = "BLOCKED") -> dict[str, Any]:
        return {
            "version": CURRENT_EVALUATION_VERSION_V2,
            "evaluation_version": CURRENT_EVALUATION_VERSION_V2,
            "evaluation_policy_version": self.version,
            "track_id": track.get("track_id"), "definition_hash": track.get("definition_hash"),
            "evaluated_at": _utc_iso(now), "evaluated_at_epoch": now,
            "as_of": None, "source_candle_timestamp": None,
            "current_dataset_identity": None, "current_source_version": None,
            "overall_status": status, "tree_result": None,
            "leaf_results": [], "conditions": [],
            "freshness": {"state": "UNKNOWN", "age_seconds": None},
            "limitations": [limitation],
        }

    def _parse_spec(self, track: Mapping[str, Any]) -> ThesisSpecV2:
        raw = track.get("thesis_spec")
        if not isinstance(raw, Mapping):
            raise TrackingError("V2 thesis spec is unavailable")
        return parse_thesis_spec_v2(
            raw, self.feature_registry,
            supported_instruments=(str(raw.get("instrument", "")),),
            supported_timeframes=(str(raw.get("timeframe", "")),),
            supported_horizons=tuple(map(str, raw.get("forward_horizons", ()))),
        )

    @staticmethod
    def _contiguous_suffix(rows: Sequence[Mapping[str, Any]], width: int) -> list[dict[str, Any]]:
        start = 0
        timestamps = [int(row["ts"]) for row in rows]
        for index, (left, right) in enumerate(zip(timestamps, timestamps[1:])):
            if right - left != width:
                start = index + 1
        return [dict(row) for row in rows[start:]]

    @staticmethod
    def _candle_component(rows: Sequence[Mapping[str, Any]], instrument: str,
                          timeframe: str) -> dict[str, Any]:
        stable = [{key: row.get(key) for key in (
            "ts", "candle_close_ts", "open", "high", "low", "close", "volume",
            "confirmed", "source", "source_version", "_source_store",
        )} for row in rows]
        payload = {
            "component": "OHLCV", "instrument": instrument, "timeframe": timeframe,
            "latest_timestamp": stable[-1]["candle_close_ts"], "row_count": len(stable),
            "content_sha256": _hash(stable),
            "sources": sorted({str(row.get("source") or "unknown") for row in rows}),
            "source_versions": sorted({str(row.get("source_version") or "unknown") for row in rows}),
        }
        return {**payload, "dataset_id": _hash(payload)}

    def _derivative_observations(self, instrument: str, close_ts: int,
                                 groups: Sequence[str], *, timeframe: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        observations: dict[str, dict[str, Any]] = {}
        components: list[dict[str, Any]] = []
        for group in sorted(set(groups)):
            if self.derivative_reader is None:
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_SOURCE_UNAVAILABLE"}
                continue
            try:
                raw = self.derivative_reader.latest(instrument, group, close_ts,
                                                    timeframe=timeframe)
            except (OSError, sqlite3.Error, ValueError):
                raw = None
            if not isinstance(raw, Mapping):
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_SOURCE_UNAVAILABLE"}
                continue
            if raw.get("current_evidence") is not True:
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_SOURCE_UNAVAILABLE"}
                continue
            timestamp = raw.get("timestamp")
            available_at = raw.get("available_at", timestamp)
            values = raw.get("values")
            if (isinstance(timestamp, bool) or not isinstance(timestamp, int) or
                    isinstance(available_at, bool) or not isinstance(available_at, int) or
                    not isinstance(values, Mapping)):
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_OBSERVATION_INVALID"}
                continue
            if timestamp > close_ts or available_at > close_ts:
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_FUTURE_TIMESTAMP_REJECTED",
                                       "timestamp": timestamp, "available_at": available_at}
                continue
            age = close_ts - timestamp
            threshold = (24 * 60 * 60 if group == "OI" and timeframe == "1D"
                         else int(self.derivative_max_age_seconds[group]))
            if age > threshold:
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_SOURCE_STALE",
                                       "timestamp": timestamp, "age_seconds": age,
                                       "threshold_seconds": threshold}
                continue
            clean_values: dict[str, float | bool | None] = {}
            invalid = False
            for key, value in values.items():
                if value is not None and (isinstance(value, bool) or
                                          (isinstance(value, (int, float)) and math.isfinite(float(value)))):
                    clean_values[str(key)] = value
                elif value is None:
                    clean_values[str(key)] = None
                else:
                    invalid = True
                    break
            if invalid:
                observations[group] = {"limitation": "CURRENT_DERIVATIVE_OBSERVATION_INVALID"}
                continue
            component = {
                "component": group, "instrument": instrument, "latest_timestamp": timestamp,
                "available_at": available_at,
                "source": str(raw.get("source") or "unknown"),
                "source_version": str(raw.get("source_version") or "unknown"),
                "dataset_id": raw.get("dataset_id"),
                "observation_sha256": _hash({"timestamp": timestamp,
                                              "available_at": available_at,
                                              "values": clean_values}),
            }
            observations[group] = {"timestamp": timestamp, "values": clean_values,
                                   "age_seconds": age, "threshold_seconds": threshold,
                                   "component": component}
            components.append(component)
        return observations, components

    def evaluate(self, track: Mapping[str, Any], *, now: int | None = None) -> dict[str, Any]:
        current_time = int(self.clock() if now is None else now)
        if (track.get("schema_version") != TRACK_SCHEMA_VERSION_V2 or
                track.get("current_evaluation_policy_version") != self.version):
            return self._blocked(track, current_time, "TRACK_OR_EVALUATION_POLICY_VERSION_MISMATCH",
                                 status="BLOCKED_VERSION_MISMATCH")
        try:
            spec = self._parse_spec(track)
        except (KeyError, ValueError, TrackingError) as error:
            return self._blocked(track, current_time, f"DEFINITION_UNAVAILABLE:{error}",
                                 status="BLOCKED_VERSION_MISMATCH")
        expected_versions = {leaf.feature: self.feature_registry[leaf.feature].version
                             for leaf in _walk_conditions(spec.expression)}
        compiled_definition_hash = compile_thesis_v2(spec, self.feature_registry).definition_hash
        if (track.get("definition_hash") != compiled_definition_hash or
                track.get("feature_versions") != expected_versions):
            return self._blocked(track, current_time, "FEATURE_OR_DEFINITION_VERSION_MISMATCH",
                                 status="BLOCKED_VERSION_MISMATCH")
        instrument = _canonical_instrument(spec.instrument)
        try:
            raw_rows = self.candle_reader.candles(instrument, spec.timeframe,
                                                  current_time, CURRENT_READER_LIMIT_V2)
        except (OSError, sqlite3.Error, ValueError):
            return self._blocked(track, current_time, "CURRENT_CANONICAL_READER_UNAVAILABLE")
        rows = confirmed_candles_as_of(raw_rows, spec.timeframe, current_time)
        if not rows:
            return self._blocked(track, current_time, "NO_CONFIRMED_CURRENT_CANDLE")
        if rows[-1].get("_source_store") != "market_candles":
            return self._blocked(track, current_time,
                                 "LATEST_CANDLE_IS_NOT_FROM_CURRENT_LIVE_CANONICAL_STORE")
        width = TIMEFRAME_SECONDS[spec.timeframe]
        rows = self._contiguous_suffix(rows, width)
        close_ts = int(rows[-1]["candle_close_ts"])
        candle_age = max(0, current_time - close_ts)
        candle_stale = candle_age > width * 2
        compiled_ohlcv = compile_feature_rows(rows)

        leaves = _walk_conditions(spec.expression)
        groups = [self.feature_registry[item.feature].source_group for item in leaves
                  if self.feature_registry[item.feature].source_group in DERIVATIVE_SOURCE_GROUPS]
        derivative, derivative_components = self._derivative_observations(
            instrument, close_ts, groups, timeframe=spec.timeframe)
        structure_cache: dict[str, Mapping[str, Any]] = {}
        leaf_counter = 0
        flat_leaves: list[dict[str, Any]] = []

        def evaluate_leaf(node: ConditionNode, path: str) -> tuple[TruthValue, dict[str, Any]]:
            nonlocal leaf_counter
            leaf_counter += 1
            contract = self.feature_registry[node.feature]
            observed: Any = None
            context = None
            limitation = None
            quality = "AVAILABLE"
            source_timestamp = close_ts
            minimum_history = 1
            if candle_stale:
                limitation, quality = "CURRENT_CANDLE_SOURCE_STALE", "STALE"
            elif node.feature in STRUCTURE_ROW_KEYS:
                key = canonical_json(dict(node.parameters))
                if key not in structure_cache:
                    params = RollingStructureParametersV1.from_mapping(node.parameters)
                    structure_cache[key] = compile_rolling_structure_rows(rows, params)[-1]
                current = structure_cache[key]
                observed = current.get(STRUCTURE_ROW_KEYS[node.feature])
                context = (current.get("rolling_structure_event_contexts") or {}).get(node.feature)
                minimum_history = int(node.parameters["lookback_bars"]) + 1
                if len(rows) < minimum_history:
                    limitation, quality = "CURRENT_CONTIGUOUS_WARMUP_INCOMPLETE", "PARTIAL"
            elif contract.source_group == "OHLCV":
                feature = FEATURE_REGISTRY.get(node.feature)
                if feature is None:
                    limitation, quality = "CURRENT_FEATURE_IMPLEMENTATION_UNAVAILABLE", "UNAVAILABLE"
                else:
                    observed = feature.evaluator(compiled_ohlcv[-1])
                    minimum_history = feature.minimum_history
                    if len(rows) < minimum_history:
                        limitation, quality = "CURRENT_CONTIGUOUS_WARMUP_INCOMPLETE", "PARTIAL"
            elif contract.source_group in DERIVATIVE_SOURCE_GROUPS:
                observation = derivative.get(contract.source_group, {})
                limitation = observation.get("limitation")
                if limitation:
                    quality = "STALE" if limitation.endswith("_STALE") else "UNAVAILABLE"
                    source_timestamp = observation.get("timestamp")
                else:
                    observed = observation.get("values", {}).get(node.feature)
                    source_timestamp = observation.get("timestamp")
            else:
                limitation, quality = "CURRENT_SOURCE_GROUP_UNSUPPORTED", "UNAVAILABLE"
            if limitation is None and observed is None:
                limitation, quality = "FEATURE_WARMUP_OR_VALUE_UNAVAILABLE", "PARTIAL"
            if limitation is None:
                if (contract.value_type == "number" and
                        (isinstance(observed, bool) or not isinstance(observed, (int, float)) or
                         not math.isfinite(float(observed)))):
                    limitation, quality = "CURRENT_FEATURE_VALUE_INVALID", "UNAVAILABLE"
                elif contract.value_type == "boolean" and not isinstance(observed, bool):
                    limitation, quality = "CURRENT_FEATURE_VALUE_INVALID", "UNAVAILABLE"
            compared = None if limitation else _compare(observed, node.operator, node.value)
            state = _truth(compared)
            item = {
                "node_id": path, "leaf_index": leaf_counter - 1,
                "node_type": "CONDITION", "feature": node.feature,
                "feature_version": contract.version, "source_group": contract.source_group,
                "operator": node.operator, "value": node.value,
                "parameters": dict(node.parameters), "observed_value": observed,
                "state": state.value, "source_timestamp": source_timestamp,
                "quality": quality, "limitation": limitation, "event_context": context,
            }
            flat_leaves.append(item)
            return state, item

        def evaluate_node(node: ExpressionNode, path: str) -> tuple[TruthValue, dict[str, Any]]:
            if isinstance(node, ConditionNode):
                return evaluate_leaf(node, path)
            child_nodes = [node.child] if isinstance(node, NotNode) else list(node.children)
            evaluated = [evaluate_node(child, f"{path}.{index}")
                         for index, child in enumerate(child_nodes)]
            state = _group_truth(node, [item[0] for item in evaluated])
            return state, {"node_id": path, "node_type": node.node_type,
                           "state": state.value,
                           "children": [item[1] for item in evaluated]}

        root_state, tree = evaluate_node(spec.expression, "root")
        components = [self._candle_component(rows, instrument, spec.timeframe), *derivative_components]
        identity_payload = {
            "version": CURRENT_DATASET_IDENTITY_VERSION_V2,
            "instrument": instrument, "timeframe": spec.timeframe,
            "as_of": close_ts, "components": components,
        }
        identity = {**identity_payload, "dataset_id": _hash(identity_payload)}
        limitations = sorted({str(item["limitation"]) for item in flat_leaves if item["limitation"]})
        if root_state is TruthValue.TRUE:
            overall = "MATCHING"
        elif root_state is TruthValue.FALSE:
            overall = "NOT_MATCHING"
        elif limitations and all(item.endswith("_STALE") for item in limitations):
            overall = "STALE"
        else:
            overall = "PARTIAL"
        source_versions = sorted({str(version)
                                  for component in components
                                  for version in (component.get("source_versions") or
                                                  [component.get("source_version") or "unknown"])})
        return {
            "version": CURRENT_EVALUATION_VERSION_V2,
            "evaluation_version": CURRENT_EVALUATION_VERSION_V2,
            "evaluation_policy_version": self.version,
            "track_id": track["track_id"], "definition_hash": compiled_definition_hash,
            "evaluated_at": _utc_iso(current_time), "evaluated_at_epoch": current_time,
            "as_of": close_ts, "source_candle_timestamp": close_ts,
            "current_dataset_identity": identity, "current_source_version": source_versions,
            "overall_status": overall, "expression_state": root_state.value,
            "tree_result": tree, "leaf_results": flat_leaves,
            # Repository V1 and existing clients can retain their flat read path.
            "conditions": deepcopy(flat_leaves),
            "freshness": {"state": "STALE" if candle_stale else "FRESH",
                          "age_seconds": candle_age, "threshold_seconds": width * 2},
            "limitations": limitations,
        }


def _index_tree(tree: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(tree, Mapping):
        return {}
    output = {str(tree.get("node_id")): tree}
    for child in tree.get("children", ()):
        if isinstance(child, Mapping):
            output.update(_index_tree(child))
    return output


def expression_evaluation_delta(previous: Mapping[str, Any] | None,
                                current: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic leaf, group, overall, quality and source delta."""
    if previous is None:
        return {
            "version": DELTA_VERSION_V2, "initial_evaluation": True,
            "overall_change": None, "leaf_changes": [], "group_changes": [],
            "quality_changes": [], "source_changes": [], "material_change": False,
        }
    before, after = _index_tree(previous.get("tree_result")), _index_tree(current.get("tree_result"))
    leaf_changes: list[dict[str, Any]] = []
    group_changes: list[dict[str, Any]] = []
    quality_changes: list[dict[str, Any]] = []
    for node_id in sorted(set(before) & set(after)):
        old, new = before[node_id], after[node_id]
        if old.get("node_type") != new.get("node_type"):
            continue
        if old.get("state") != new.get("state"):
            target = leaf_changes if new.get("node_type") == "CONDITION" else group_changes
            target.append({"node_id": node_id, "node_type": new.get("node_type"),
                           "feature": new.get("feature"),
                           "from": old.get("state"), "to": new.get("state")})
        if (new.get("node_type") == "CONDITION" and
                old.get("quality") != new.get("quality")):
            quality_changes.append({"node_id": node_id, "feature": new.get("feature"),
                                    "from": old.get("quality"), "to": new.get("quality")})
    old_identity = previous.get("current_dataset_identity") or {}
    new_identity = current.get("current_dataset_identity") or {}
    source_changes = []
    old_components = old_identity.get("components", [])
    new_components = new_identity.get("components", [])
    old_sources = [{key: item.get(key) for key in (
        "component", "source", "source_version", "sources", "source_versions")}
                   for item in old_components if isinstance(item, Mapping)]
    new_sources = [{key: item.get(key) for key in (
        "component", "source", "source_version", "sources", "source_versions")}
                   for item in new_components if isinstance(item, Mapping)]
    if old_sources != new_sources:
        source_changes.append({"field": "current_sources", "from": old_sources, "to": new_sources})
    overall_change = None
    if previous.get("overall_status") != current.get("overall_status"):
        overall_change = {"from": previous.get("overall_status"),
                          "to": current.get("overall_status")}
    material = bool(overall_change or leaf_changes or group_changes or quality_changes or source_changes)
    return {
        "version": DELTA_VERSION_V2, "initial_evaluation": False,
        "overall_change": overall_change, "leaf_changes": leaf_changes,
        "group_changes": group_changes, "quality_changes": quality_changes,
        "source_changes": source_changes, "material_change": material,
    }


def tracked_thesis_v2_artifact(result: Mapping[str, Any], *, language: str = "en",
                               original_text: str | None = None,
                               track_id: str | None = None) -> dict[str, Any]:
    """Create a V2 artifact without rewriting any V1 baseline or hash."""
    if result.get("status") != "COMPLETED" or not str(result.get("result_hash", "")):
        raise TrackingError("a completed, validated V2 historical result is required")
    raw_spec = result.get("thesis_spec")
    if not isinstance(raw_spec, Mapping) or raw_spec.get("version") != "thesis-spec-v2":
        raise TrackingError("a ThesisSpecV2 historical result is required")
    definition_hash = str(result.get("definition_hash") or "")
    if not definition_hash:
        raise TrackingError("historical definition hash is required")
    historical = result.get("historical_data")
    if not isinstance(historical, Mapping) or not historical.get("dataset_id"):
        raise TrackingError("historical composite dataset identity is required")
    feature_versions = result.get("feature_versions")
    if not isinstance(feature_versions, Mapping) or not feature_versions:
        raise TrackingError("V2 feature versions are required")
    if language not in {"en", "zh"}:
        raise TrackingError("language must be en or zh")
    if original_text is not None and (not isinstance(original_text, str) or len(original_text) > 2000):
        raise TrackingError("original_text must be a string of at most 2000 characters")
    baseline = {
        "version": "historical-thesis-baseline-v2",
        "result_hash": str(result["result_hash"]),
        "definition_hash": definition_hash,
        "historical_dataset_identity": str(historical["dataset_id"]),
        "historical_data": deepcopy(dict(historical)),
        "historical_tested_range": deepcopy(dict(result.get("tested_range") or {})),
        "historical_summary": deepcopy(dict(result.get("historical_summary") or {})),
        "captured_at": _utc_iso(int(time.time())),
    }
    return {
        "schema_version": TRACK_SCHEMA_VERSION_V2, "track_id": track_id or str(uuid4()),
        "original_text": original_text, "language": language,
        "thesis_spec": deepcopy(dict(raw_spec)), "definition_hash": definition_hash,
        "feature_versions": dict(feature_versions),
        "historical_result_hash": str(result["result_hash"]),
        "historical_dataset_identity": str(historical["dataset_id"]),
        "historical_engine_version": str(result.get("engine_version") or "unknown"),
        "historical_tested_range": deepcopy(dict(result.get("tested_range") or {})),
        "historical_baseline": baseline,
        "current_evaluation_policy_version": CURRENT_EVALUATION_POLICY_VERSION_V2,
        "is_active": True, "status": "WATCHING",
    }


class ThesisTrackingServiceV2:
    """V2 service facade over the unchanged V1 repository schema."""

    def __init__(self, repository: ThesisTrackingRepositoryV1,
                 evaluator: CurrentExpressionEvaluatorV2,
                 thesis_service: Any | None = None,
                 *, trackable_features: Sequence[str] | None = None) -> None:
        self.repository, self.evaluator = repository, evaluator
        self.thesis_service = thesis_service
        self.trackable_features = (frozenset(trackable_features)
                                   if trackable_features is not None else None)

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"version", "result_hash", "thesis_spec", "language", "original_text"}
        if not isinstance(payload, Mapping) or set(payload) - allowed:
            raise TrackingError("V2 track request contains unsupported fields")
        if payload.get("version") != TRACK_CREATE_REQUEST_VERSION_V2:
            raise TrackingError(f"version must be {TRACK_CREATE_REQUEST_VERSION_V2}")
        if self.thesis_service is None:
            raise TrackingError("V2 historical verification service is unavailable")
        spec = payload.get("thesis_spec")
        if not isinstance(spec, Mapping):
            raise TrackingError("thesis_spec is required")
        try:
            result, _rows = self.thesis_service.verified_result(
                spec, str(payload.get("result_hash", "")))
        except (ValueError, KeyError) as error:
            raise TrackingError(str(error)) from error
        return self.create_from_verified_result(
            result, language=str(payload.get("language") or "en"),
            original_text=payload.get("original_text"),
        )

    def create_from_verified_result(self, result: Mapping[str, Any], *, language: str = "en",
                                    original_text: str | None = None) -> dict[str, Any]:
        if self.trackable_features is not None:
            raw_spec = result.get("thesis_spec")
            if not isinstance(raw_spec, Mapping):
                raise TrackingError("a ThesisSpecV2 historical result is required")
            spec = parse_thesis_spec_v2(
                raw_spec, self.evaluator.feature_registry,
                supported_instruments=(str(raw_spec.get("instrument", "")),),
                supported_timeframes=(str(raw_spec.get("timeframe", "")),),
                supported_horizons=tuple(map(str, raw_spec.get("forward_horizons", ()))),
            )
            unavailable = sorted({leaf.feature for leaf in _walk_conditions(spec.expression)
                                  if leaf.feature not in self.trackable_features})
            if unavailable:
                raise TrackingError(f"HISTORICAL_ONLY:{unavailable[0]}")
        artifact = tracked_thesis_v2_artifact(result, language=language, original_text=original_text)
        track, created = self.repository.create(artifact)
        evaluated = self.evaluate(track["track_id"])
        return {**evaluated, "created": created}

    def evaluate(self, track_id: str, *, now: int | None = None) -> dict[str, Any]:
        track = self.repository.get(track_id)
        if track is None:
            raise TrackingError("tracked thesis not found")
        previous_bundle = self.repository.detail(track_id) or {}
        previous = previous_bundle.get("latest_evaluation")
        evaluation = self.evaluator.evaluate(track, now=now)
        stored, created = self.repository.record_evaluation(evaluation)
        # This returned delta is authoritative for V2 callers. Repository-level
        # dispatch is a small integration follow-up; no schema migration is needed.
        stored = {**stored, "delta": expression_evaluation_delta(previous, evaluation)}
        return {"track": self.repository.get(track_id), "latest_evaluation": stored,
                "evaluation_created": created,
                "outcome": "EVALUATED" if created else "NO_CHANGE"}
