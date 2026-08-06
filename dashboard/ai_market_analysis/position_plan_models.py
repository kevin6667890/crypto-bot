"""Versioned user-declared position plans. No inference from chat or balances."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .canonical import identity, stable_hash
from .versions import AI_USER_POSITION_PLAN_VERSION, SUPPORTED_INSTRUMENTS

POSITION_SOURCES = ("NONE", "PAPER", "USER_DECLARED")
SIDES = ("LONG", "SHORT")
PLAN_STATUSES = ("ACTIVE", "COMPLETED", "CANCELLED", "SUPERSEDED")


def _time(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _number(value: Any, field: str, *, zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not zero and number == 0):
        raise ValueError(f"{field} must be finite and {'non-negative' if zero else 'positive'}")
    return number


def normalize_user_position_plan(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"plan_id", "plan_version", "instrument", "source", "side", "entries",
               "average_cost", "original_quantity", "remaining_quantity", "current_quantity",
               "original_thesis", "original_timeframe", "original_stop", "original_targets",
               "realised_exits", "planned_risk", "max_loss", "created_at", "effective_at",
               "supersedes_plan_id", "status", "user_notes", "plan_completed", "provenance",
               "payload_hash"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unknown position plan fields: {sorted(unknown)}")
    instrument = payload.get("instrument")
    if instrument not in SUPPORTED_INSTRUMENTS:
        raise ValueError("unsupported instrument")
    if payload.get("source", "USER_DECLARED") != "USER_DECLARED":
        raise ValueError("user plan source must be USER_DECLARED")
    side = payload.get("side")
    if side not in SIDES:
        raise ValueError("invalid side")
    entries = []
    for raw in payload.get("entries") or []:
        if set(raw) - {"price", "quantity", "timestamp", "source", "note"}:
            raise ValueError("unknown entry field")
        entries.append({"price": _number(raw.get("price"), "entry.price"),
                        "quantity": _number(raw.get("quantity"), "entry.quantity"),
                        "timestamp": _time(raw.get("timestamp"), "entry.timestamp"),
                        "source": str(raw.get("source") or "USER_DECLARED")[:80],
                        "note": str(raw.get("note") or "")[:500]})
    if not entries:
        raise ValueError("at least one entry is required")
    original_quantity = sum(item["quantity"] for item in entries)
    average_cost = sum(item["price"] * item["quantity"] for item in entries) / original_quantity
    exits = []
    for raw in payload.get("realised_exits") or []:
        exits.append({"price": _number(raw.get("price"), "exit.price"),
                      "quantity": _number(raw.get("quantity"), "exit.quantity"),
                      "timestamp": _time(raw.get("timestamp"), "exit.timestamp"),
                      "reason": str(raw.get("reason") or "")[:500],
                      "target_reference": raw.get("target_reference")})
    realised = sum(item["quantity"] for item in exits)
    if realised > original_quantity + 1e-12:
        raise ValueError("realised quantity exceeds original quantity")
    remaining = original_quantity - realised
    supplied_remaining = payload.get("remaining_quantity", payload.get("current_quantity"))
    if supplied_remaining is not None and abs(_number(supplied_remaining, "remaining_quantity", zero=True) - remaining) > 1e-9:
        raise ValueError("remaining quantity mismatch")
    targets = []
    for index, raw in enumerate(payload.get("original_targets") or []):
        quantity = raw.get("quantity")
        fraction = raw.get("fraction")
        if quantity is not None and fraction is not None:
            raise ValueError("target quantity and fraction are mutually exclusive")
        target = {"target_id": str(raw.get("target_id") or f"TARGET_{index+1:02d}"),
                  "price": _number(raw.get("price"), "target.price"),
                  "quantity": _number(quantity, "target.quantity") if quantity is not None else None,
                  "fraction": _number(fraction, "target.fraction") if fraction is not None else None,
                  "status": str(raw.get("status") or "PENDING"),
                  "completed_at": _time(raw["completed_at"], "target.completed_at") if raw.get("completed_at") else None}
        if target["fraction"] is not None and target["fraction"] > 1:
            raise ValueError("target fraction must not exceed one")
        target["completed"] = target["status"] == "COMPLETED" or target["completed_at"] is not None or any(
            item.get("target_reference") == target["target_id"] for item in exits)
        targets.append(target)
    created = _time(payload.get("created_at"), "created_at")
    effective = _time(payload.get("effective_at", created), "effective_at")
    plan_completed = remaining <= 1e-12 or (bool(targets) and all(item["completed"] for item in targets))
    core = {"plan_version": AI_USER_POSITION_PLAN_VERSION, "instrument": instrument,
            "source": "USER_DECLARED", "side": side, "entries": entries,
            "average_cost": average_cost, "original_quantity": original_quantity,
            "remaining_quantity": remaining, "current_quantity": remaining,
            "original_thesis": str(payload.get("original_thesis") or "")[:2000],
            "original_timeframe": str(payload.get("original_timeframe") or "")[:120],
            "original_stop": _number(payload["original_stop"], "original_stop") if payload.get("original_stop") is not None else None,
            "original_targets": targets, "realised_exits": exits,
            "planned_risk": _number(payload["planned_risk"], "planned_risk", zero=True) if payload.get("planned_risk") is not None else None,
            "max_loss": _number(payload["max_loss"], "max_loss", zero=True) if payload.get("max_loss") is not None else None,
            "created_at": created, "effective_at": effective,
            "supersedes_plan_id": payload.get("supersedes_plan_id"),
            "status": str(payload.get("status") or ("COMPLETED" if plan_completed else "ACTIVE")),
            "user_notes": str(payload.get("user_notes") or "")[:2000],
            "plan_completed": plan_completed,
            "provenance": {**(payload.get("provenance") or {}), "source": "USER_DECLARED",
                           "privacy": "REQUIRES_PRIVACY_REVIEW"}}
    if core["status"] not in PLAN_STATUSES:
        raise ValueError("invalid plan status")
    fingerprint = stable_hash(core)
    plan_id = payload.get("plan_id") or identity("plan", core)
    return {**core, "plan_id": plan_id, "payload_hash": fingerprint}


def plan_at_decision_time(plan: dict[str, Any], decision_time: str) -> bool:
    return _time(plan["effective_at"], "effective_at") <= _time(decision_time, "decision_time")
