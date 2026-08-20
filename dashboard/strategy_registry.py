"""Durable approved-strategy registry and the database-free Router V2 seam.

The repository/adapter boundary is deliberate: ``StrategyRouterV2`` remains a
pure deterministic function.  Database reads and provenance decoration happen
here, before and after the pure call.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .approved_strategy_runtime import RUNTIME_VERSION, canonical_instrument, evaluate_frozen_candidate


APPROVAL_POLICY_VERSION = "strategy-approval-policy-v1"
PROMOTION_POLICY_VERSION = "strategy-promotion-policy-v1"
REGISTRY_SCHEMA_VERSION = "approved-strategy-registry-v1"
ACTIVE_SCOPE_POLICY_VERSION = "global-cross-asset-active-v1"
ROUTER_TEMPLATE_MAP = {
    "TREND_PULLBACK_V2_1": "TREND_PULLBACK",
    "TREND_BREAKOUT_V2_1": "BREAKOUT_CONTINUATION",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in (
        "serialized_definition", "parameters", "instrument_scope",
        "development_metrics", "walk_forward_metrics", "holdout_result",
        "final_oot_result", "cross_asset_result", "robustness_result",
        "rejection_reasons", "contamination_state",
    ):
        raw = item.get(key)
        if isinstance(raw, str):
            try:
                item[key] = json.loads(raw)
            except json.JSONDecodeError:
                item[key] = None
    return item


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    status: str
    reason_codes: tuple[str, ...]


class StrategyApprovalPolicy:
    """Combine existing evidence; never runs a backtest or ranks on holdout."""

    version = APPROVAL_POLICY_VERSION

    @staticmethod
    def evaluate(evidence: Mapping[str, Any]) -> ApprovalDecision:
        reasons: list[str] = []
        development = evidence.get("development") or {}
        if not development.get("eligible"):
            reasons.extend(development.get("reasons") or ["DEVELOPMENT_INELIGIBLE"])
        if not development.get("score") and development.get("score") != 0:
            reasons.append("DEVELOPMENT_SCORE_MISSING")
        for key, code in (
            ("walk_forward", "WALK_FORWARD_INCOMPLETE"),
            ("holdout", "PRIMARY_HOLDOUT_FAILED"),
            ("final_oot", "FINAL_OOT_FAILED"),
            ("cross_asset", "CROSS_ASSET_FAILED"),
            ("robustness", "ROBUSTNESS_FAILED"),
        ):
            value = evidence.get(key) or {}
            if value.get("status") != "PASS":
                reasons.append(code)
        contamination = evidence.get("contamination") or {}
        if contamination.get("state") != "CLEAR":
            reasons.append("CONTAMINATED_EVIDENCE")
        identity = evidence.get("identity") or {}
        if not identity.get("candidate_identity") or not identity.get("configuration_hash"):
            reasons.append("INCOMPLETE_IDENTITY")
        definition = evidence.get("definition") or {}
        candidate = evidence.get("candidate") or {}
        if definition.get("parameters") != candidate.get("parameters"):
            reasons.append("PARAMETER_SNAPSHOT_MISMATCH")
        if definition and identity.get("configuration_hash") != canonical_hash(definition):
            reasons.append("FROZEN_DEFINITION_HASH_MISMATCH")
        scope = definition.get("activation_scope") or {}
        if scope.get("mode") != "GLOBAL_CROSS_ASSET" or scope.get("policy_version") != ACTIVE_SCOPE_POLICY_VERSION:
            reasons.append("ACTIVE_SCOPE_POLICY_INCOMPATIBLE")
        if set(scope.get("instruments") or ()) != {"BTC-USDT", "ETH-USDT", "SOL-USDT"}:
            reasons.append("ACTIVE_SCOPE_INCOMPLETE")
        runtime = evidence.get("runtime") or {}
        if not runtime.get("deterministic"):
            reasons.append("RUNTIME_NOT_DETERMINISTIC")
        if not runtime.get("execution_compatible"):
            reasons.append("EXECUTION_ASSUMPTIONS_INCOMPATIBLE")
        if runtime.get("router_family") not in set(ROUTER_TEMPLATE_MAP.values()) and not runtime.get("program_runtime"):
            reasons.append("ROUTER_DEFINITION_UNSUPPORTED")
        return ApprovalDecision(not reasons, "APPROVED" if not reasons else "REJECTED", tuple(dict.fromkeys(reasons)))


class ApprovedStrategyRegistry:
    def __init__(self, repository: Any):
        self.repository = repository

    def active(self) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approved_strategy_registry WHERE status='ACTIVE' ORDER BY active_at DESC LIMIT 1"
            ).fetchone()
        return _decode(row) if row else None

    def approved(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approved_strategy_registry WHERE status IN ('ACTIVE','APPROVED') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, development_score DESC, approved_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [_decode(row) for row in rows]

    def get(self, registry_id: str) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approved_strategy_registry WHERE registry_id=?", (registry_id,)
            ).fetchone()
        return _decode(row) if row else None

    def register(self, candidate: Mapping[str, Any], decision: ApprovalDecision) -> dict[str, Any]:
        now = utc_now()
        identity = str(candidate["candidate_identity"])
        registry_id = "asr_" + canonical_hash({
            "schema": REGISTRY_SCHEMA_VERSION,
            "candidate_identity": identity,
            "cycle": candidate["research_cycle_id"],
            "dataset": candidate["source_dataset_fingerprint"],
        })[:24]
        definition = dict(candidate["serialized_definition"])
        configuration_hash = str(candidate.get("configuration_hash") or canonical_hash(definition))
        with self.repository.connect() as connection:
            connection.execute(
                """INSERT INTO approved_strategy_registry(
                    registry_id,candidate_identity,family,strategy_type,serialized_definition,parameters,
                    instrument_scope,timeframe,direction_capability,discovery_run_id,research_cycle_id,
                    source_dataset_fingerprint,development_metrics,walk_forward_metrics,holdout_result,
                    final_oot_result,cross_asset_result,robustness_result,eligibility_status,rejection_reasons,
                    contamination_state,strategy_version,engine_version,policy_version,configuration_hash,
                    development_score,pareto_rank,status,created_at,approved_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(candidate_identity) DO UPDATE SET
                    rejection_reasons=excluded.rejection_reasons,eligibility_status=excluded.eligibility_status,
                    status=CASE WHEN approved_strategy_registry.status IN ('ACTIVE','RETIRED')
                                THEN approved_strategy_registry.status ELSE excluded.status END""",
                (
                    registry_id, identity, candidate["family"], candidate["strategy_type"], _json(definition),
                    _json(candidate["parameters"]), _json(candidate["instrument_scope"]), candidate["timeframe"],
                    candidate["direction_capability"], candidate.get("discovery_run_id"), candidate["research_cycle_id"],
                    candidate["source_dataset_fingerprint"], _json(candidate["development_metrics"]),
                    _json(candidate["walk_forward_metrics"]), _json(candidate["holdout_result"]),
                    _json(candidate["final_oot_result"]), _json(candidate["cross_asset_result"]),
                    _json(candidate["robustness_result"]), "ELIGIBLE" if decision.approved else "INELIGIBLE",
                    _json(list(decision.reason_codes)), _json(candidate["contamination_state"]),
                    candidate["strategy_version"], candidate["engine_version"], candidate["policy_version"],
                    configuration_hash, candidate.get("development_score"), candidate.get("pareto_rank"),
                    decision.status, now, now if decision.approved else None,
                ),
            )
            row = connection.execute(
                "SELECT * FROM approved_strategy_registry WHERE candidate_identity=?", (identity,)
            ).fetchone()
        return _decode(row)

    @staticmethod
    def _promotion_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        score = item.get("development_score")
        score_value = float(score) if isinstance(score, (int, float)) and math.isfinite(float(score)) else -math.inf
        rank = item.get("pareto_rank")
        return (score_value, -int(rank or 10**9), str(item.get("candidate_identity")))

    def promote_best(self, research_cycle_id: int) -> dict[str, Any] | None:
        candidates = self.approved(200)
        inactive = [item for item in candidates if item["status"] == "APPROVED"]
        if not inactive:
            return self.active()
        winner = max(inactive, key=self._promotion_key)
        current = self.active()
        if current and self._promotion_key(winner) <= self._promotion_key(current):
            return current
        now = utc_now()
        comparison = {
            "policy_version": PROMOTION_POLICY_VERSION,
            "basis": "development score, then Pareto rank, then immutable candidate identity",
            "previous_key": self._promotion_key(current) if current else None,
            "new_key": self._promotion_key(winner),
            "holdout_oot_cross_asset_used_for_ranking": False,
        }
        with self.repository.connect() as connection:
            if current:
                connection.execute(
                    "UPDATE approved_strategy_registry SET status='RETIRED',retired_at=? WHERE registry_id=? AND status='ACTIVE'",
                    (now, current["registry_id"]),
                )
            connection.execute(
                "UPDATE approved_strategy_registry SET status='ACTIVE',active_at=?,retired_at=NULL WHERE registry_id=? AND status='APPROVED'",
                (now, winner["registry_id"]),
            )
            connection.execute(
                "INSERT INTO strategy_registry_switches(previous_registry_id,new_registry_id,reason_code,comparison,research_cycle_id,created_at) VALUES(?,?,?,?,?,?)",
                (current["registry_id"] if current else None, winner["registry_id"],
                 "BETTER_APPROVED_DEVELOPMENT_EVIDENCE" if current else "FIRST_APPROVED_STRATEGY",
                 _json(comparison), int(research_cycle_id), now),
            )
        return self.get(winner["registry_id"])

    def switches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_registry_switches ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["comparison"] = json.loads(item["comparison"])
            output.append(item)
        return output


class StrategyRegistryAdapter:
    """Load outside the router, inject only a frozen deterministic definition."""

    def __init__(self, registry: ApprovedStrategyRegistry):
        self.registry = registry

    def _confirmed_candles(self, instrument: str, as_of: int, limit: int = 300) -> list[dict[str, Any]]:
        canonical = canonical_instrument(instrument)
        with self.registry.repository.connect() as connection:
            has_live = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_candles'"
            ).fetchone()
            if has_live:
                rows = connection.execute(
                    "SELECT ts,open,high,low,close,volume FROM market_candles "
                    "WHERE instrument=? AND bar='15m' AND ts+900<=? ORDER BY ts DESC LIMIT ?",
                    (canonical, int(as_of), int(limit)),
                ).fetchall()
                if rows:
                    return [
                        {**dict(row), "candle_close_ts": int(row["ts"]) + 900, "confirmed": True}
                        for row in reversed(rows)
                    ]
            rows = connection.execute(
                "SELECT ts,open,high,low,close,volume,confirmed FROM historical_candles "
                "WHERE instrument=? AND timeframe='15m' AND confirmed=1 AND ts+900<=? "
                "ORDER BY ts DESC LIMIT ?", (canonical, int(as_of), int(limit)),
            ).fetchall()
        return [
            {**dict(row), "candle_close_ts": int(row["ts"]) + 900}
            for row in reversed(rows)
        ]

    @staticmethod
    def provenance(item: Mapping[str, Any] | None) -> dict[str, Any]:
        if not item:
            return {"source": "LEGACY_BASELINE", "registry_status": None}
        definition = item["serialized_definition"]
        return {
            "source": "APPROVED_REGISTRY", "registry_id": item["registry_id"],
            "registry_status": item["status"], "candidate_identity": item["candidate_identity"],
            "research_cycle_id": item["research_cycle_id"], "approved_at": item["approved_at"],
            "strategy_version": item["strategy_version"],
            "configuration_hash": item["configuration_hash"],
            "active_scope_policy": ACTIVE_SCOPE_POLICY_VERSION,
            "dataset_range": definition.get("dataset_range"),
            "validation_status": definition.get("validation_status"),
        }

    def route(self, router: Any, context: Mapping[str, Any], state: Mapping[str, Any], *,
              candles: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        active = self.registry.active()
        if not active:
            result = router.route(context, state, **kwargs)
        else:
            definition = active["serialized_definition"]
            directions = ("LONG", "SHORT") if definition["direction"] == "BOTH" else (definition["direction"],)
            result = router.route(
                context, state, strategy_definitions=[{
                    "family": definition["router_family"], "direction": direction,
                    "parameter_set_id": active["configuration_hash"],
                    "parameter_set": definition["router_parameters"],
                } for direction in directions], **kwargs,
            )
            runtime_error = None
            try:
                runtime = evaluate_frozen_candidate(
                    active, str(context["instrument"]),
                    candles if candles is not None else self._confirmed_candles(str(context["instrument"]), int(context["as_of"])),
                    as_of=int(context["as_of"]),
                )
            except ValueError as error:
                runtime = {
                    "runtime_version": RUNTIME_VERSION, "action": "WAIT",
                    "strategy_registry_id": active["registry_id"],
                    "candidate_identity": active["candidate_identity"],
                    "strategy_version": active["strategy_version"],
                    "configuration_hash": active["configuration_hash"],
                }
                runtime_error = str(error)
            action = runtime["action"]
            candidates = []
            selected = None
            for candidate in result.get("candidates", ()):
                presented = dict(candidate)
                presented["presentation_only"] = True
                presented["execution_runtime_version"] = runtime["runtime_version"]
                if action in {"LONG", "SHORT"} and candidate.get("direction") == action and selected is None:
                    presented["state"] = "TRIGGER_READY"
                    presented["selection_status"] = "PRIMARY"
                    presented["selection_reason"] = "exact frozen approved candidate trigger"
                    selected = presented
                else:
                    presented["selection_status"] = "ALTERNATIVE"
                candidates.append(presented)
            result["candidates"] = tuple(candidates)
            result["primary_route"] = selected
            result["alternatives"] = tuple(item for item in candidates if item is not selected and item.get("state") != "INELIGIBLE")
            result["no_trade"] = {
                **result["no_trade"], "active": selected is None,
                "reasons": () if selected is not None else ({
                    "code": "APPROVED_CANDIDATE_DATA_UNAVAILABLE" if runtime_error else "APPROVED_CANDIDATE_WAIT",
                    "timeframe": "15m", "evidence": [runtime_error] if runtime_error else ["frozen candidate emitted WAIT"],
                    "temporary": True, "release_condition": "next confirmed candle completes the frozen candidate trigger",
                },),
            }
            result["execution_decision"] = {**runtime, "error": runtime_error, "authoritative": True}
        provenance=self.provenance(active)
        result["strategy_provenance"] = provenance
        for candidate in result.get("candidates",[]): candidate["strategy_provenance"]=dict(provenance)
        for candidate in result.get("alternatives",[]): candidate["strategy_provenance"]=dict(provenance)
        if result.get("primary_route"): result["primary_route"]["strategy_provenance"]=dict(provenance)
        result["approved_strategy"] = active
        return result
