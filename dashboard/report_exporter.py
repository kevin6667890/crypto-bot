"""Sanitized public Markdown/JSON research report export."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .research_repository import ResearchRepository


DISCLAIMER = "Paper/research only. No live orders are placed and no strategy was automatically promoted. Historical results do not predict future performance; parameter search can overfit. Revealed holdout, final OOT and cross-asset evidence are evidence, not guarantees."


def _metrics(metrics: dict[str, Any] | None) -> str:
    if not metrics: return "Unavailable"
    fields = (("Return", "total_return"), ("Profit Factor", "profit_factor"), ("Sharpe", "sharpe_ratio"), ("Maximum drawdown", "maximum_drawdown"), ("Trades", "total_trades"))
    return "; ".join(f"{label}: {metrics.get(key, 'Unavailable')}" for label, key in fields)


def _public_run(run: dict[str, Any] | None, visible_holdout: bool) -> dict[str, Any] | None:
    """Explicit whitelist: reports must not serialize persistence/queue internals."""
    if not run:
        return None
    safe = {key: run.get(key) for key in ("id", "status", "experiment_family_id", "parent_run_id", "seed", "holdout_revealed_at", "post_holdout_adjustment", "search_space_changed", "base_parameters_changed", "created_at", "completed_at")}
    request = run.get("request") or {}
    safe["request"] = {key: request.get(key) for key in ("instrument", "timeframe", "start_date", "end_date", "trial_budget", "seed", "base_parameters", "development_end_ts", "holdout_start_ts", "holdout_end_ts")}
    policy = run.get("scoring_policy") or {}
    safe["scoring_policy"] = {key: policy.get(key) for key in ("version", "method", "weights", "neighborhood")}
    result = run.get("result") or {}
    safe["result"] = {key: result.get(key) for key in ("method", "data_quality", "development_start_ts", "validation_start_ts", "development_end_ts", "holdout_start_ts", "holdout_end_ts", "unused_gap_start_ts", "unused_gap_end_ts", "holdout_candidates", "warning")}
    safe["trials"] = [{key: trial.get(key) for key in ("id", "trial_number", "status", "parameters", "validation_metrics", "score", "score_components", "elimination_reasons", "runtime_ms", "engine_version", "error")} | ({"holdout_metrics": trial.get("holdout_metrics")} if visible_holdout else {}) for trial in run.get("trials", [])]
    return safe


def _public_suite(suite: dict[str, Any], final_oot_visible: bool) -> dict[str, Any]:
    safe = {key: suite.get(key) for key in ("id", "experiment_family_id", "source_optimization_run_id", "source_trial_id", "status", "policy_version", "created_at", "completed_at", "error", "retry_of_suite_id", "attempt_number")}
    safe["results"] = [{key: result.get(key) for key in ("stage", "instrument", "timeframe", "start_ts", "end_ts", "metrics", "buy_hold_metrics", "data_quality", "status", "error")} for result in suite.get("results", []) if result.get("stage") != "final_out_of_time" or final_oot_visible]
    return safe


def export_report(repository: ResearchRepository, output: Path, optimization_run: int | None = None, experiment_family: int | None = None, json_output: Path | None = None) -> dict[str, Any]:
    if bool(optimization_run) == bool(experiment_family): raise ValueError("Specify exactly one optimization run or experiment family.")
    # Fetch hidden metrics only after the durable reveal timestamp has been checked.
    initial_run = repository.optimization_run(optimization_run, include_holdout=False) if optimization_run else None
    run = initial_run
    family = repository.optimization_family(experiment_family) if experiment_family else None
    if optimization_run and not run: raise ValueError("Optimization run not found.")
    if experiment_family and not family: raise ValueError("Experiment family not found.")
    if family is None and run and run.get("experiment_family_id"): family = repository.optimization_family(int(run["experiment_family_id"]))
    if run is None and family and family["runs"]: run = repository.optimization_run(int(family["runs"][0]["id"]), include_holdout=False)
    if run and run.get("holdout_revealed_at"):
        run = repository.optimization_run(int(run["id"]), include_holdout=True)
    request = (run or {}).get("request", {}); result = (run or {}).get("result") or {}
    suites = [repository.validation_suite(int(item["id"])) for item in repository.validation_suites() if run and item["source_optimization_run_id"] == run["id"]]
    visible_holdout = bool(run and run.get("holdout_revealed_at"))
    trials = (run or {}).get("trials", []); completed = [trial for trial in trials if trial["status"] == "COMPLETED"]
    lines = ["# Public research report", "", f"Generated: {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}", "", f"> {DISCLAIMER}", "", "## Scope", "", f"- Status: {(run or {}).get('status', 'Unavailable')}", f"- Instrument/timeframe: {request.get('instrument', family.get('instrument') if family else 'Unavailable')} / {request.get('timeframe', family.get('timeframe') if family else 'Unavailable')}", f"- Data period: {request.get('start_date', 'Unavailable')} to {request.get('end_date', 'Unavailable')}", f"- Engine version: {(completed[0].get('engine_version') if completed else 'Unavailable')}", f"- Scoring policy: {(run or {}).get('scoring_policy', {}).get('version', 'Unavailable')}", f"- Seed / trial budget: {request.get('seed', (run or {}).get('seed', 'Unavailable'))} / {request.get('trial_budget', 'Unavailable')}"]
    if family: lines += [f"- Experiment family: {family['id']} / {family['family_fingerprint']}", f"- Primary holdout: {family['holdout_start_ts']} to {family['holdout_end_ts']}", f"- Final OOT: {family.get('final_oot_start_ts') or 'Not configured'} to {family.get('final_oot_end_ts') or ''}"]
    lines += ["", "## Method and assumptions", "", f"- Parameter ranges: `{json.dumps((run or {}).get('scoring_policy', {}).get('neighborhood', {}).get('parameters', {}), sort_keys=True)}`", f"- Base parameters include fees, slippage, stop/target and candle execution assumptions: `{json.dumps(request.get('base_parameters', {}), sort_keys=True)}`", f"- Data quality: `{json.dumps(result.get('data_quality', {}), sort_keys=True)}`", "", "## Trial evidence", "", f"Completed: {len(completed)}; eliminated: {sum(t['status']=='ELIMINATED' for t in trials)}; failed: {sum(t['status']=='FAILED' for t in trials)}"]
    for trial in completed[:5]:
        lines += [f"- Trial #{trial['trial_number']} score {trial.get('score', 'Unavailable')}: validation {_metrics(trial.get('validation_metrics'))}; parameters `{json.dumps(trial['parameters'], sort_keys=True)}`"]
        if visible_holdout: lines.append(f"  - Explicitly revealed primary holdout: {_metrics(trial.get('holdout_metrics'))}")
    if not visible_holdout: lines.append("- Primary holdout metrics are unavailable because holdout has not been explicitly revealed.")
    if run and run.get("post_holdout_adjustment"): lines += ["", "## Contamination warning", "", "This run was configured after the family holdout had already been revealed. Treat its holdout result as development evidence, not as untouched validation."]
    lines += ["", "## Out-of-time validation", ""]
    if suites:
        for suite in suites:
            for item in suite.get("results", []): lines.append(f"- {item['stage']} / {item['instrument']}: {item['status']}; {_metrics(item.get('metrics'))}")
    else: lines.append("Unavailable: no persisted validation suite for this report scope.")
    lines += ["", "## Limitations and conclusion", "", "Results are deterministic historical research evidence only. Holdout/OOT/cross-asset results never affect optimization ranking, do not authorize activation, and should be interpreted conservatively."]
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    public_family = {key: family.get(key) for key in ("id", "name", "instrument", "timeframe", "start_ts", "development_end_ts", "holdout_start_ts", "holdout_end_ts", "final_oot_start_ts", "final_oot_end_ts", "family_fingerprint", "holdout_revealed_at", "final_oot_revealed_at", "notes", "created_at")} if family else None
    payload = {"report_type": "public_research", "disclaimer": DISCLAIMER, "run": _public_run(run, visible_holdout), "family": public_family, "validation_suites": [_public_suite(suite, bool(family and family.get("final_oot_revealed_at"))) for suite in suites if suite]}
    if json_output: json_output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
