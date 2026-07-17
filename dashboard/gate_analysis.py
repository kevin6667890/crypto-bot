"""Gate-funnel aggregation from complete canonical decision payloads."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

GATE_ORDER = (
    "indicator_warmup", "directional_bias", "higher_timeframe_alignment", "ma_structure",
    "ema_pullback", "rsi_range", "volume_ratio", "momentum_combined", "cvd_alignment",
    "oi_context", "flow_combined", "minimum_score", "risk_permission", "cooldown",
    "existing_position", "final_entry_allowed",
)
GATE_LABELS = {key: key.replace("_", " ").title() for key in GATE_ORDER}
GATE_LABELS.update({"ma_structure": "MA60 / MA200 Structure", "ema_pullback": "EMA20 Pullback", "oi_context": "OI Context", "cvd_alignment": "CVD Alignment"})


def decision_gates(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized gates, including a conservative legacy-payload adapter."""
    if decision.get("gate_results"):
        by_key = {item["key"]: dict(item) for item in decision["gate_results"]}
        return [by_key.get(key, {"key": key, "label": GATE_LABELS[key], "passed": False, "applicable": False, "blocking": True}) for key in GATE_ORDER]
    failed = set(decision.get("failed_gates") or [])
    aliases = {"indicator_warmup": "warmup", "directional_bias": "trend", "ema_pullback": "pullback", "momentum_combined": "momentum", "cvd_alignment": "flow_alignment", "flow_combined": "flow_alignment", "risk_permission": "risk"}
    flow_available = bool((decision.get("flow_context") or {}).get("available"))
    output = []
    for key in GATE_ORDER:
        legacy = aliases.get(key, key)
        applicable = flow_available if key in {"cvd_alignment", "oi_context", "flow_combined"} else True
        if key == "final_entry_allowed": passed = bool(decision.get("entry_allowed"))
        elif key == "ma_structure": passed = decision.get("bias") in {"LONG", "SHORT"}
        elif key == "rsi_range": passed = "momentum" not in failed
        elif key == "volume_ratio": passed = "momentum" not in failed
        else: passed = legacy not in failed
        output.append({"key": key, "label": GATE_LABELS[key], "passed": bool(passed) if applicable else True, "applicable": applicable, "blocking": key != "oi_context"})
    return output


def aggregate_gate_funnel(decisions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(decisions)
    stats = {key: {"gate": key, "label": GATE_LABELS[key], "evaluated_count": 0, "pass_count": 0, "fail_count": 0, "sequential_evaluated_count": 0, "sequential_pass_count": 0, "signals_lost": 0, "exclusive_failure_count": 0, "combined_failure_count": 0, "long": [0, 0], "short": [0, 0], "assets": defaultdict(lambda: [0, 0])} for key in GATE_ORDER}
    rejections, scores, daily = Counter(), Counter(), defaultdict(Counter)
    for decision in rows:
        gates = decision_gates(decision)
        blocking_failures = [item["key"] for item in gates if item.get("applicable", True) and item.get("blocking", True) and not item["passed"] and item["key"] != "final_entry_allowed"]
        for key in blocking_failures: rejections[key] += 1
        ts = int(decision.get("candle_close_ts", 0)); day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else "Unknown"
        for key in blocking_failures: daily[day][key] += 1
        scores[int(float(decision.get("score", 0)) // 5 * 5)] += 1
        alive = True
        for item in gates:
            key, record = item["key"], stats[item["key"]]
            if not item.get("applicable", True): continue
            record["evaluated_count"] += 1
            passed = bool(item["passed"])
            record["pass_count"] += int(passed); record["fail_count"] += int(not passed)
            side = str(decision.get("bias", "WAIT")).lower()
            if side in {"long", "short"}: record[side][0] += 1; record[side][1] += int(passed)
            asset = str(decision.get("instrument", "Unknown")); record["assets"][asset][0] += 1; record["assets"][asset][1] += int(passed)
            if alive:
                record["sequential_evaluated_count"] += 1; record["sequential_pass_count"] += int(passed)
                if not passed and item.get("blocking", True): record["signals_lost"] += 1; alive = False
            if not passed and item.get("blocking", True) and key != "final_entry_allowed":
                if len(blocking_failures) == 1: record["exclusive_failure_count"] += 1
                elif len(blocking_failures) > 1: record["combined_failure_count"] += 1
    result = []
    for key in GATE_ORDER:
        item = stats[key]; evaluated, seq_evaluated = item["evaluated_count"], item["sequential_evaluated_count"]
        item["pass_rate"] = item["pass_count"] / evaluated * 100 if evaluated else None
        item["conditional_pass_rate"] = item["sequential_pass_count"] / seq_evaluated * 100 if seq_evaluated else None
        item["long_pass_rate"] = item["long"][1] / item["long"][0] * 100 if item["long"][0] else None
        item["short_pass_rate"] = item["short"][1] / item["short"][0] * 100 if item["short"][0] else None
        item["per_asset_pass_rate"] = {asset: values[1] / values[0] * 100 if values[0] else None for asset, values in item["assets"].items()}
        del item["long"], item["short"], item["assets"]
        result.append(item)
    return {"decision_count": len(rows), "gates": result, "top_rejection_reasons": [{"gate": key, "label": GATE_LABELS[key], "count": count} for key, count in rejections.most_common()], "score_distribution": [{"bucket": f"{bucket}-{bucket+4}", "count": count} for bucket, count in sorted(scores.items())], "daily_rejection_timeline": [{"date": day, "rejections": dict(values), "total": sum(values.values())} for day, values in sorted(daily.items())]}


def filter_decisions(decisions: Iterable[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in decisions:
        flow = "with" if (row.get("flow_context") or {}).get("available") else "without"
        ts = int(row.get("candle_close_ts", 0)); dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        checks = (("instrument", row.get("instrument")), ("strategy_version", row.get("strategy_version")), ("config_hash", row.get("config_hash")), ("timeframe", row.get("execution_timeframe")), ("bias", row.get("bias")), ("regime", row.get("regime")), ("source", row.get("source")), ("flow", flow), ("hour", dt.hour if dt else None), ("weekday", dt.weekday() if dt else None))
        if all(filters.get(key) in (None, "", "ALL") or str(value).lower() == str(filters[key]).lower() for key, value in checks): output.append(row)
    return output
