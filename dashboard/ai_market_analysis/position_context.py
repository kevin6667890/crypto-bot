"""Deterministic NONE, PAPER and USER_DECLARED position context builders."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .canonical import stable_hash
from .position_plan_models import normalize_user_position_plan, plan_at_decision_time
from .versions import AI_POSITION_CONTEXT_VERSION

DISCIPLINE_WARNINGS = ("PLAN_COMPLETED", "PLAN_MOSTLY_COMPLETED", "STOP_INVALIDATED",
    "TARGET_REACHED_NOT_RECORDED", "POSITION_EXCEEDS_ORIGINAL_QUANTITY",
    "REMAINING_QUANTITY_MISMATCH", "TIMEFRAME_DRIFT_RISK", "SHORT_TERM_PLAN_BEING_EXTENDED",
    "MISSING_ORIGINAL_STOP", "MISSING_ORIGINAL_TARGETS", "PARTIAL_EXIT_UNCONFIRMED", "NONE")


def _finish(context: dict[str, Any]) -> dict[str, Any]:
    core = {**context, "schema_version": AI_POSITION_CONTEXT_VERSION}
    core["position_fingerprint"] = stable_hash(core)
    return core


def none_position_context(instrument: str) -> dict[str, Any]:
    return _finish({"source": "NONE", "instrument": instrument, "status": "NONE",
                    "plan_id": None, "side": None, "average_cost": None,
                    "original_quantity": None, "remaining_quantity": None,
                    "current_mark": None, "original_stop": None, "original_targets": [],
                    "realised_exits": [], "original_timeframe": None, "unrealised_pnl": None,
                    "realised_pnl": None, "plan_completion_ratio": None,
                    "plan_completed": False, "discipline_warnings": ["NONE"],
                    "limitations": ["未提供持仓信息，本报告仅分析市场结构。"]})


def user_position_context(plan_payload: dict[str, Any], instrument: str, decision_time: str,
                          current_mark: float | None) -> dict[str, Any]:
    plan = normalize_user_position_plan(plan_payload)
    if plan["instrument"] != instrument:
        raise ValueError("position instrument mismatch")
    if not plan_at_decision_time(plan, decision_time):
        raise ValueError("position plan is from the future")
    remaining, original = plan["remaining_quantity"], plan["original_quantity"]
    completed_targets = sum(1 for target in plan["original_targets"] if target["completed"])
    completion = max((original - remaining) / original, completed_targets / len(plan["original_targets"]) if plan["original_targets"] else 0)
    warnings: list[str] = []
    if plan["plan_completed"] and remaining <= 1e-12:
        warnings.append("PLAN_COMPLETED")
    elif plan["plan_completed"] or completion >= .8:
        warnings.extend(["PLAN_MOSTLY_COMPLETED", "TIMEFRAME_DRIFT_RISK"])
    if not plan["original_stop"]:
        warnings.append("MISSING_ORIGINAL_STOP")
    if not plan["original_targets"]:
        warnings.append("MISSING_ORIGINAL_TARGETS")
    if current_mark is not None and plan["original_stop"] is not None:
        invalid = current_mark <= plan["original_stop"] if plan["side"] == "LONG" else current_mark >= plan["original_stop"]
        if invalid:
            warnings.append("STOP_INVALIDATED")
    if remaining > original + 1e-12:
        warnings.append("POSITION_EXCEEDS_ORIGINAL_QUANTITY")
    unrealised = None
    if current_mark is not None:
        sign = 1 if plan["side"] == "LONG" else -1
        unrealised = (current_mark - plan["average_cost"]) * remaining * sign
    return _finish({"source": "USER_DECLARED", "instrument": instrument, "status": "COMPLETE",
        "plan_id": plan["plan_id"], "plan_version": plan["plan_version"], "side": plan["side"],
        "average_cost": plan["average_cost"], "original_quantity": original,
        "remaining_quantity": remaining, "current_mark": current_mark,
        "original_thesis": plan["original_thesis"], "original_timeframe": plan["original_timeframe"],
        "original_stop": plan["original_stop"], "original_targets": plan["original_targets"],
        "realised_exits": plan["realised_exits"],
        "realised_quantity": original-remaining, "unrealised_pnl": unrealised,
        "realised_pnl": sum(((e["price"]-plan["average_cost"]) * e["quantity"] * (1 if plan["side"] == "LONG" else -1)) for e in plan["realised_exits"]),
        "distance_to_stop": ((current_mark-plan["original_stop"]) if plan["side"] == "LONG" else (plan["original_stop"]-current_mark)) if current_mark is not None and plan["original_stop"] is not None else None,
        "distance_to_targets": [{"target_id": t["target_id"], "distance": (t["price"]-current_mark) * (1 if plan["side"] == "LONG" else -1)} for t in plan["original_targets"] if current_mark is not None],
        "plan_completion_ratio": min(1.0, completion), "plan_completed": plan["plan_completed"],
        "discipline_warnings": warnings or ["NONE"], "limitations": [],
        "provenance": {"plan_payload_hash": plan["payload_hash"], "privacy": "REQUIRES_PRIVACY_REVIEW"}})


def paper_position_context(db_path: str | Path, instrument: str, current_mark: float | None,
                           decision_time: str | None = None) -> dict[str, Any]:
    uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
        if not cols:
            raise ValueError("paper_trades missing")
        query = "SELECT * FROM paper_trades WHERE instrument=?"
        args: list[Any] = [instrument.replace("-SWAP", "")]
        if decision_time:
            query += " AND created_at<=?"
            args.append(decision_time)
        rows = [dict(r) for r in conn.execute(query + " ORDER BY created_at DESC", args)]
    open_rows = [r for r in rows if r.get("status") == "OPEN"]
    if len(open_rows) > 1:
        return _finish({**none_position_context(instrument), "source": "PAPER", "status": "INVALID",
                        "discipline_warnings": ["POSITION_EXCEEDS_ORIGINAL_QUANTITY"],
                        "limitations": ["duplicate OPEN paper positions"]})
    row = open_rows[0] if open_rows else (rows[0] if rows else None)
    if not row:
        return _finish({**none_position_context(instrument), "source": "PAPER", "status": "NONE",
                        "limitations": ["no paper position"]})
    quantity = row.get("position_size")
    status = "COMPLETE" if quantity is not None and row.get("entry") is not None else "PARTIAL"
    side = str(row.get("side") or "").upper()
    if side in {"BUY", "LONG"}: side = "LONG"
    elif side in {"SELL", "SHORT"}: side = "SHORT"
    sign = 1 if side == "LONG" else -1
    mark = current_mark if current_mark is not None else row.get("mark_price")
    return _finish({"source": "PAPER", "instrument": instrument, "status": status,
        "paper_trade_id": row.get("id"), "plan_id": None, "side": side or None,
        "average_cost": row.get("entry"), "entry": row.get("entry"), "original_quantity": quantity,
        "remaining_quantity": quantity if row.get("status") == "OPEN" else 0 if quantity is not None else None,
        "current_mark": mark, "original_stop": row.get("stop_loss"),
        "original_targets": ([{"price": row.get("take_profit"), "status": "PENDING"}] if row.get("take_profit") is not None else []),
        "opened_at": row.get("created_at"), "original_timeframe": row.get("execution_timeframe"),
        "original_thesis": row.get("trade_rationale"), "realised_exits": [],
        "realised_pnl": row.get("net_pnl", row.get("pnl_usdt")),
        "unrealised_pnl": ((mark-row["entry"])*quantity*sign if mark is not None and row.get("entry") is not None and quantity is not None and row.get("status") == "OPEN" else None),
        "current_paper_risk": row.get("actual_risk_amount", row.get("risk_amount")),
        "existing_status": row.get("status"), "accounting_version": row.get("accounting_version"),
        "plan_completion_ratio": 1.0 if row.get("status") != "OPEN" else 0.0,
        "plan_completed": row.get("status") != "OPEN", "discipline_warnings": ["NONE"],
        "limitations": (["legacy paper row: quantity unavailable"] if quantity is None else []) + (["rationale unavailable"] if not row.get("trade_rationale") else []),
        "provenance": {"database": "paper read-only", "accounting_version": row.get("accounting_version")}})
