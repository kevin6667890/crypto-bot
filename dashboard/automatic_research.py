"""Durable automatic research cycle built from the existing Discovery core."""
from __future__ import annotations

import json
import os
import gc
import ctypes
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .discovery_execution import DiscoveryExecutionConfig, run_discovery_candidate_backtest
from .discovery_identity import build_candidate_identity
from .discovery_robustness import generate_cost_scenarios
from .discovery_scoring import evaluate_eligibility
from .discovery_service import ENGINE_VERSION, POLICY_VERSION, aggregate, buy_and_hold
from .factor_program_discovery import canonical_backtest, persist_candidates
from .factor_strategy_program import generate as generate_factor_programs, validate as validate_factor_program
from .discovery_scoring import calculate_score, DISCOVERY_SCORING_VERSION
from .approved_strategy_runtime import deserialize_program
from .okx_history import TIMEFRAME_SECONDS
from .strategy_registry import (
    ACTIVE_SCOPE_POLICY_VERSION, APPROVAL_POLICY_VERSION, ApprovedStrategyRegistry, ROUTER_TEMPLATE_MAP,
    StrategyApprovalPolicy, canonical_hash, utc_now,
)


AUTO_RESEARCH_POLICY_VERSION = "automatic-research-cycle-v1"
ROLLING_SPLIT_POLICY_VERSION = "rolling-development-70-holdout-20-oot-10-v1"
RUNTIME_ADAPTER_VERSION = "approved-strategy-runtime-v2.1-v1"
DEFAULT_TEMPLATES = ("TREND_PULLBACK_V2_1", "TREND_BREAKOUT_V2_1", "RANGE_MEAN_REVERSION_V2_1")
AUTO_RESEARCH_SCHEDULER_NAME = "automatic-research"
FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE = 25


def _loads(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def factor_program_development_batches(rows: list[Mapping[str, Any]], *,
                                       batch_size: int = FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE) -> list[list[Mapping[str, Any]]]:
    """Return unfinished programs in their durable candidate order.

    A completed candidate is its own durable resume marker.  The cycle checkpoint
    records completed batches for observability, while this database state makes a
    retry safe even if the host stops between the candidate update and checkpoint.
    """
    if batch_size < 1:
        raise ValueError("factor program batch size must be positive")
    pending = [row for row in rows if str(row.get("status") or "") != "DEVELOPMENT_CANDIDATE"]
    return [pending[index:index + batch_size] for index in range(0, len(pending), batch_size)]


def release_factor_program_transients() -> None:
    """Return completed canonical-backtest allocations before the next program.

    CPython can retain arenas after a large backtest.  On the Linux worker,
    ``malloc_trim`` makes that release observable to the container cgroup while
    retaining the same deterministic evaluator and evaluation pipeline.
    """
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (AttributeError, OSError):
        pass


def rolling_window(now: datetime | None = None, lookback_days: int = 730) -> tuple[int, int]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    end = current.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(lookback_days))
    return int(start.timestamp()), int(end.timestamp())


def rolling_splits(start: int, end: int) -> dict[str, Any]:
    duration = end - start
    if duration < 365 * 86400:
        raise ValueError("Formal automatic research requires at least 365 days.")
    development_end = start + duration * 70 // 100
    holdout_end = start + duration * 90 // 100
    unit = (development_end - start) // 7
    folds = []
    for index in range(5):
        validation_start = start + (index + 2) * unit
        validation_end = development_end if index == 4 else start + (index + 3) * unit
        folds.append([start, validation_start, validation_start, validation_end])
    return {
        "version": ROLLING_SPLIT_POLICY_VERSION, "development_start": start,
        "development_end": development_end, "development_folds": folds,
        "primary_holdout_start": development_end, "primary_holdout_end": holdout_end,
        "final_oot_start": holdout_end, "final_oot_end": end,
    }


def _router_parameters(template: str, parameters: Mapping[str, Any]) -> dict[str, Any] | None:
    if template == "TREND_PULLBACK_V2_1":
        return {
            "zone_buffer_atr": .2 if float(parameters["pullback_max_atr"]) <= .5 else .35,
            "trigger_score": 78 if parameters.get("volume_enabled") else 72,
            "minimum_r": 1.5 if float(parameters.get("target_r", 1.25)) >= 1.5 else 1.25,
        }
    if template == "TREND_BREAKOUT_V2_1":
        return {
            "boundary_buffer_atr": .15 if int(parameters["breakout_lookback"]) <= 20 else .3,
            "retest_wait_bars": 2 if int(parameters["breakout_lookback"]) <= 20 else 4,
            "minimum_r": 1.5 if float(parameters.get("target_r", 1.25)) >= 1.5 else 1.25,
        }
    return None


class AutomaticResearchService:
    def __init__(self, repository: Any, jobs: Any, discovery: Any,
                 evidence_factory: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None):
        self.repository, self.jobs, self.discovery = repository, jobs, discovery
        self.registry = ApprovedStrategyRegistry(repository)
        self.approval = StrategyApprovalPolicy()
        self.evidence_factory = evidence_factory
        jobs.register("AUTO_RESEARCH_CYCLE", self._job)
        jobs.register_terminal_handler("AUTO_RESEARCH_CYCLE", self._terminal)
        if getattr(jobs,"autostart",True): self._reconcile_restart()

    def _reconcile_restart(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE automatic_research_cycles SET status='INTERRUPTED',error='Service restarted during automatic research',updated_at=? "
                "WHERE status IN ('RUNNING','CANCEL_REQUESTED')", (utc_now(),)
            )

    @staticmethod
    def normalize_request(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        value = dict(payload or {})
        lookback = int(value.get("lookback_days", os.getenv("AUTO_RESEARCH_LOOKBACK_DAYS", "730")))
        if not 365 <= lookback <= 732:
            raise ValueError("lookback_days must be 365..732")
        if value.get("research_start") is None or value.get("research_end") is None:
            start, end = rolling_window(lookback_days=lookback)
        else:
            start, end = int(value["research_start"]), int(value["research_end"])
        splits = rolling_splits(start, end)
        budget = int(value.get("trial_budget", os.getenv("AUTO_RESEARCH_TRIAL_BUDGET", "24")))
        finalists = int(value.get("finalists", os.getenv("AUTO_RESEARCH_FINALISTS", "3")))
        if not 1 <= budget <= 32 or not 1 <= finalists <= min(10, budget):
            raise ValueError("trial_budget must be 1..32 and finalists must be 1..min(10,budget)")
        return {
            "research_start": start, "research_end": end, "lookback_days": lookback,
            "timeframe": "15m", "trial_budget": budget, "finalists": finalists,
            "seed": int(value.get("seed", 20260724)), "templates": list(DEFAULT_TEMPLATES),
            "workers": 1,
            "splits": splits, "execution_assumptions": {
                "initial_capital": 10000.0, "risk_per_trade": .01, "trading_fee": .0005,
                "slippage": .0003, "stop_loss_atr_multiplier": 1.0,
                "risk_reward_ratio": 2.0, "cooldown_bars": 16,
                "allow_long": True, "allow_short": True,
            },
            "policy_version": AUTO_RESEARCH_POLICY_VERSION,
        }

    def start(self, payload: Mapping[str, Any] | None = None, requester: str = "auto-research") -> dict[str, Any]:
        request = self.normalize_request(payload)
        fingerprint = canonical_hash({
            "request": request, "engine": ENGINE_VERSION, "discovery_policy": POLICY_VERSION,
            "approval": APPROVAL_POLICY_VERSION,
        })
        with self.repository.connect() as connection:
            active = connection.execute(
                "SELECT * FROM automatic_research_cycles WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if active:
                return self._cycle(dict(active), deduplicated=True)
            previous = connection.execute(
                "SELECT * FROM automatic_research_cycles WHERE request_fingerprint=? AND status='COMPLETED' ORDER BY id DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if previous:
                return self._cycle(dict(previous), deduplicated=True, evidence_reused=True)
            now = utc_now()
            versions = {
                "cycle": AUTO_RESEARCH_POLICY_VERSION, "split": ROLLING_SPLIT_POLICY_VERSION,
                "discovery_engine": ENGINE_VERSION, "discovery_policy": POLICY_VERSION,
                "approval": APPROVAL_POLICY_VERSION, "runtime_adapter": RUNTIME_ADAPTER_VERSION,
                "git": os.getenv("GIT_COMMIT", "unknown"), "build": os.getenv("BUILD_VERSION", "unknown"),
            }
            cursor = connection.execute(
                "INSERT INTO automatic_research_cycles(status,request,request_fingerprint,research_start,research_end,checkpoint,versions,created_at,updated_at) VALUES('QUEUED',?,?,?,?,?,?,?,?)",
                (_json(request), fingerprint, request["research_start"], request["research_end"], "{}", _json(versions), now, now),
            )
            cycle_id = int(cursor.lastrowid)
        job = self.jobs.enqueue(
            "AUTO_RESEARCH_CYCLE", {"cycle_id": cycle_id}, requester, priority=120,
            dedupe_payload={"formal_auto_research": True},
        )
        with self.repository.connect() as connection:
            connection.execute("UPDATE automatic_research_cycles SET job_id=?,updated_at=? WHERE id=?", (job["id"], utc_now(), cycle_id))
        return self.detail(cycle_id) or {}

    def resume(self, cycle_id: int, requester: str = "auto-research") -> dict[str, Any]:
        item = self.detail(cycle_id)
        if not item or item["status"] not in {"INTERRUPTED", "FAILED", "CANCELLED"}:
            raise ValueError("Only interrupted, failed or cancelled cycles can be resumed.")
        job = self.jobs.enqueue("AUTO_RESEARCH_CYCLE", {"cycle_id": int(cycle_id)}, requester, priority=120,
                                retry_of=item.get("job_id"), dedupe_payload={"formal_auto_research": True})
        with self.repository.connect() as connection:
            connection.execute("UPDATE automatic_research_cycles SET status='QUEUED',job_id=?,error=NULL,updated_at=? WHERE id=?", (job["id"], utc_now(), cycle_id))
        return self.detail(cycle_id) or {}

    def cancel(self, cycle_id: int) -> dict[str, Any]:
        item = self.detail(cycle_id)
        if not item or not item.get("job_id"):
            raise ValueError("Automatic research cycle not found.")
        self.jobs.cancel(int(item["job_id"]))
        return self.detail(cycle_id) or {}

    def _terminal(self, job: Mapping[str, Any]) -> None:
        cycle_id = int((job.get("request_payload") or {}).get("cycle_id") or 0)
        if not cycle_id or job.get("status") not in {"FAILED", "CANCELLED", "INTERRUPTED"}:
            return
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE automatic_research_cycles SET status=?,error=?,updated_at=?,completed_at=? WHERE id=? AND status!='COMPLETED'",
                (job["status"], job.get("error"), utc_now(), utc_now(), cycle_id),
            )

    def _cycle(self, item: dict[str, Any], **extra: Any) -> dict[str, Any]:
        for key in ("request", "checkpoint", "versions", "result"):
            item[key] = _loads(item.get(key), {} if key != "result" else None)
        item.update(extra)
        return item

    def detail(self, cycle_id: int) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            row = connection.execute("SELECT * FROM automatic_research_cycles WHERE id=?", (int(cycle_id),)).fetchone()
        return self._cycle(dict(row)) if row else None

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.repository.connect() as connection:
            rows = connection.execute("SELECT * FROM automatic_research_cycles ORDER BY id DESC LIMIT ?", (max(1,min(int(limit),100)),)).fetchall()
        return [self._with_research_counts(self._cycle(dict(row))) for row in rows]

    def _with_research_counts(self, cycle: dict[str, Any]) -> dict[str, Any]:
        run_id = cycle.get("discovery_run_id")
        counts = {"development_candidates": 0, "eligible_finalists": 0, "rejected_candidates": 0}
        if run_id:
            with self.repository.connect() as connection:
                row = connection.execute(
                    """SELECT COUNT(*) AS development_candidates,
                              SUM(CASE WHEN eligibility_status='ELIGIBLE' THEN 1 ELSE 0 END) AS eligible_finalists,
                              SUM(CASE WHEN eligibility_status='REJECTED' THEN 1 ELSE 0 END) AS rejected_candidates
                       FROM strategy_discovery_candidates WHERE discovery_run_id=?""",
                    (int(run_id),),
                ).fetchone()
            if row:
                counts = {key: int(row[key] or 0) for key in counts}
        cycle["research_counts"] = counts
        return cycle

    def scheduler_state(self, scheduler_name: str = AUTO_RESEARCH_SCHEDULER_NAME) -> dict[str, Any] | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automatic_research_scheduler_state WHERE scheduler_name=?",
                (scheduler_name,),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        return value

    def configure_scheduler(self, enabled: bool, interval_hours: int,
                            now: datetime | None = None,
                            scheduler_name: str = AUTO_RESEARCH_SCHEDULER_NAME) -> dict[str, Any]:
        interval = int(interval_hours)
        if interval < 1:
            raise ValueError("interval_hours must be positive")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
        current_iso = current.isoformat()
        initial_due = (current + timedelta(hours=interval)).isoformat()
        with self.repository.connect() as connection:
            connection.execute(
                """INSERT INTO automatic_research_scheduler_state(
                       scheduler_name,enabled,interval_hours,next_due_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(scheduler_name) DO UPDATE SET
                       enabled=excluded.enabled,
                       interval_hours=excluded.interval_hours,
                       updated_at=excluded.updated_at""",
                (scheduler_name, int(bool(enabled)), interval, initial_due, current_iso),
            )
        return self.scheduler_state(scheduler_name) or {}

    def record_scheduled_cycle(self, expected_due_at: str, cycle_id: int, interval_hours: int,
                               now: datetime | None = None,
                               scheduler_name: str = AUTO_RESEARCH_SCHEDULER_NAME) -> dict[str, Any]:
        interval = int(interval_hours)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
        due = datetime.fromisoformat(expected_due_at).astimezone(timezone.utc)
        while due <= current:
            due += timedelta(hours=interval)
        with self.repository.connect() as connection:
            connection.execute(
                """UPDATE automatic_research_scheduler_state
                   SET next_due_at=?,last_scheduled_at=?,last_started_cycle_id=?,updated_at=?
                   WHERE scheduler_name=? AND next_due_at=?""",
                (due.isoformat(), current.isoformat(), int(cycle_id), current.isoformat(),
                 scheduler_name, expected_due_at),
            )
        return self.scheduler_state(scheduler_name) or {}

    def summary(self) -> dict[str, Any]:
        history = self.history(10)
        scheduler = self.scheduler_state()
        return {"latest_cycle": history[0] if history else None, "recent_cycles": history,
                "active_strategy": self.registry.active(), "approved_strategies": self.registry.approved(10),
                "scheduler": scheduler, "scheduler_enabled": bool(scheduler and scheduler["enabled"]),
                "interval_hours": scheduler.get("interval_hours") if scheduler else None,
                "next_due_at": scheduler.get("next_due_at") if scheduler else None}

    def _save_checkpoint(self, cycle_id: int, stage: str, payload: Any = None, **updates: Any) -> None:
        item = self.detail(cycle_id)
        checkpoint = dict((item or {}).get("checkpoint") or {})
        checkpoint[stage] = payload if payload is not None else {"completed_at": utc_now()}
        fields = ["checkpoint=?", "updated_at=?"]
        values: list[Any] = [_json(checkpoint), utc_now()]
        for key, value in updates.items():
            fields.append(f"{key}=?")
            values.append(value)
        values.append(cycle_id)
        with self.repository.connect() as connection:
            connection.execute(f"UPDATE automatic_research_cycles SET {','.join(fields)} WHERE id=?", values)

    @staticmethod
    def _stage_pass(metrics: Mapping[str, Any], benchmark: Mapping[str, Any]) -> tuple[str, list[str]]:
        reasons = []
        if int(metrics.get("total_trades") or 0) <= 0: reasons.append("NO_TRADES")
        if float(metrics.get("total_return") or 0) < -10: reasons.append("RETURN_BELOW_FLOOR")
        drawdown = metrics.get("maximum_drawdown")
        if drawdown is None or float(drawdown) > 20: reasons.append("DRAWDOWN_ABOVE_LIMIT")
        excess = float(metrics.get("total_return") or 0) - float(benchmark.get("total_return") or 0)
        if excess <= -10: reasons.append("EXCESS_RETURN_CATASTROPHIC")
        return ("PASS" if not reasons else "FAIL", reasons)

    def _evaluate_range(self, candidate: Mapping[str, Any], instrument: str, start: int, end: int,
                        execution: DiscoveryExecutionConfig, fingerprint: str) -> dict[str, Any]:
        timeframe = str(candidate["timeframe"]); step = TIMEFRAME_SECONDS[timeframe]
        rows = self.repository.candles(instrument, timeframe, start - 240 * step, end - 1)
        rows = [row for row in rows if bool(row.get("confirmed", 1))]
        if candidate["template"] == "FACTOR_PROGRAM":
            program=deserialize_program(candidate["program_ast"])
            outcome=canonical_backtest(program,rows,instrument,timeframe,start,end-step)
        else:
            outcome = run_discovery_candidate_backtest(rows, instrument, timeframe, candidate["template"], candidate["parameters"], start, end-step, execution, fingerprint)
        benchmark = buy_and_hold(rows, start, end-step, execution)
        status, reasons = self._stage_pass(outcome["metrics"], benchmark)
        return {"status": status, "reason_codes": reasons, "instrument": instrument,
                "start": start, "end": end, "metrics": outcome["metrics"],
                "buy_hold_metrics": benchmark,
                "excess_return": float(outcome["metrics"]["total_return"]) - float(benchmark["total_return"])}

    def _real_evidence(self, cycle: Mapping[str, Any], checkpoint: Callable[..., Any]) -> list[dict[str, Any]]:
        request, splits = cycle["request"], cycle["request"]["splits"]
        cycle_id = int(cycle["id"]); timeframe = request["timeframe"]
        dataset = self.discovery.datasets.prepare({
            "start_ts": request["research_start"], "end_ts": request["research_end"],
            "instruments": ["BTC-USDT","ETH-USDT","SOL-USDT"], "timeframes": [timeframe], "rolling": True,
        }, lambda _, pct, msg, args: checkpoint(cycle["job_id"], min(12, int((pct or 0)*.12)), msg, args),
           lambda: self.jobs.checkpoint(cycle["job_id"]))
        fingerprint = dataset["dataset_fingerprint"]
        self._save_checkpoint(cycle_id, "dataset", {"id":dataset["id"],"fingerprint":fingerprint},
                              dataset_id=dataset["id"], dataset_fingerprint=fingerprint)
        discovery_id = cycle.get("discovery_run_id")
        if discovery_id:
            with self.repository.connect() as connection:
                row = connection.execute("SELECT request,status FROM strategy_discovery_runs WHERE id=?", (discovery_id,)).fetchone()
            if not row: raise ValueError("Persisted Discovery run is missing")
            discovery_request = {**_loads(row["request"], {}), "discovery_run_id": discovery_id}
        else:
            inline = self.discovery.start_inline({
                "dataset_id": dataset["id"], "instrument": "BTC-USDT", "timeframe": timeframe,
                "execution_assumptions": request["execution_assumptions"], "templates": request["templates"],
                "trial_budget": request["trial_budget"], "seed": request["seed"], "mode":"PRICE_ONLY",
                "development_folds": splits["development_folds"],
            })
            discovery_id, discovery_request = inline["id"], inline["request"]
            self._save_checkpoint(cycle_id, "discovery_created", {"run_id":discovery_id}, discovery_run_id=discovery_id)
        with self.repository.connect() as connection:
            template_development_complete = connection.execute(
                "SELECT COUNT(*) FROM strategy_discovery_candidates WHERE discovery_run_id=? AND template!='FACTOR_PROGRAM' AND status='DEVELOPMENT_CANDIDATE'",
                (int(discovery_id),),
            ).fetchone()[0] > 0
        if not template_development_complete:
            self.discovery._run_job(cycle["job_id"], discovery_request,
                lambda _, pct, msg, args=None: checkpoint(cycle["job_id"], 12+int((pct or 0)*.38), msg, args or {}))
        # Program search is opt-in per run.  It shares the durable discovery run
        # and exact development-only folds; recurring template scheduling is unchanged.
        if os.getenv("PROGRAM_DISCOVERY_ENABLED", "false").lower() == "true":
            budget = min(500, max(1, int(os.getenv("PROGRAM_DISCOVERY_SEARCH_BUDGET", "200"))))
            with self.repository.connect() as connection:
                existing_programs = connection.execute(
                    "SELECT id FROM strategy_discovery_candidates WHERE discovery_run_id=? AND template='FACTOR_PROGRAM' ORDER BY id",
                    (int(discovery_id),),
                ).fetchall()
            if not existing_programs:
                programs = [p for p in generate_factor_programs(request["seed"], budget) if not validate_factor_program(p)]
                persist_candidates(self.repository, int(discovery_id), programs, seed=request["seed"])
                # Generation is bounded, but its AST objects are not needed during
                # evaluation.  Re-read each persisted AST only in its own batch.
                del programs
                release_factor_program_transients()
            with self.repository.connect() as connection:
                program_rows = [dict(row) for row in connection.execute(
                    """SELECT id,status FROM strategy_discovery_candidates
                       WHERE discovery_run_id=? AND template='FACTOR_PROGRAM' ORDER BY id""",
                    (int(discovery_id),),
                ).fetchall()]
            batches = factor_program_development_batches(program_rows)
            total_batches = len(batches)
            execution = DiscoveryExecutionConfig(**request["execution_assumptions"]).validate()
            for batch_index, batch in enumerate(batches, 1):
                # Load the bounded evaluation window per batch, then release it
                # before the next one.  Do not retain 500 program evaluators,
                # backtest results, or candle windows in this worker.
                dev_rows = self.repository.candles(
                    "BTC-USDT", timeframe,
                    request["splits"]["development_start"] - 240 * TIMEFRAME_SECONDS[timeframe],
                    request["splits"]["development_end"] - 1,
                )
                dev_rows = [row for row in dev_rows if bool(row.get("confirmed", 1))]
                benchmarks = [buy_and_hold(dev_rows, start, end - TIMEFRAME_SECONDS[timeframe], execution)
                              for _, _, start, end in splits["development_folds"]]
                for item in batch:
                    candidate_id = int(item["id"])
                    with self.repository.connect() as connection:
                        candidate = connection.execute(
                            "SELECT program_ast,complexity FROM strategy_discovery_candidates WHERE id=?",
                            (candidate_id,),
                        ).fetchone()
                    program = deserialize_program(_loads(candidate["program_ast"], {}))
                    folds = []
                    for _, _, start, end in splits["development_folds"]:
                        outcome = canonical_backtest(program, dev_rows, "BTC-USDT", timeframe, start, end - TIMEFRAME_SECONDS[timeframe])
                        folds.append({"status": "COMPLETED", "metrics": outcome["metrics"],
                                      "buy_hold_metrics": benchmarks[len(folds)]})
                        del outcome
                    metrics = aggregate(folds); verdict = evaluate_eligibility(metrics, timeframe, "DEVELOPMENT_CANDIDATE")
                    # Existing discovery scoring accepts its structural-complexity
                    # band (5..8); preserve program complexity separately while
                    # mapping it into that established penalty scale.
                    scoring_complexity = min(8, max(5, int(candidate["complexity"])))
                    score, components = calculate_score(metrics, scoring_complexity, timeframe)
                    status = "ELIGIBLE" if verdict["eligible"] else "REJECTED"
                    with self.repository.connect() as connection:
                        connection.execute(
                            """UPDATE strategy_discovery_candidates
                               SET status='DEVELOPMENT_CANDIDATE',aggregate_metrics=?,eligibility_status=?,
                                   development_score=?,score_components=?,elimination_reasons=?,
                                   scoring_policy_version=?,completed_at=? WHERE id=?""",
                            (_json(metrics), status, score, _json(components),
                             _json([] if verdict["eligible"] else verdict["reasons"]),
                             DISCOVERY_SCORING_VERSION, utc_now(), candidate_id),
                        )
                    del candidate, program, folds, metrics, verdict, components
                    release_factor_program_transients()
                last_id = int(batch[-1]["id"])
                self._save_checkpoint(cycle_id, "factor_program_development", {
                    "run_id": int(discovery_id), "batch_size": FACTOR_PROGRAM_DEVELOPMENT_BATCH_SIZE,
                    "completed_batches": batch_index, "total_batches": total_batches,
                    "last_completed_candidate_id": last_id,
                })
                checkpoint(cycle["job_id"], 12 + int(batch_index / max(1, total_batches) * 38),
                           "auto_research.factor_program_development_batch",
                           {"batch": batch_index, "total_batches": total_batches,
                            "last_completed_candidate_id": last_id})
                del dev_rows, benchmarks
                release_factor_program_transients()
            # Eligible rank remains derived from the durable candidate records,
            # so a resumed run never needs to keep all candidate results in RAM.
            with self.repository.connect() as connection:
                eligible_rows = connection.execute(
                    """SELECT id FROM strategy_discovery_candidates
                       WHERE discovery_run_id=? AND template='FACTOR_PROGRAM' AND eligibility_status='ELIGIBLE'
                       ORDER BY development_score DESC,id DESC""", (int(discovery_id),),
                ).fetchall()
                connection.execute("UPDATE strategy_discovery_candidates SET eligible_rank=NULL WHERE discovery_run_id=? AND template='FACTOR_PROGRAM'", (int(discovery_id),))
                for rank, row in enumerate(eligible_rows, 1):
                    connection.execute("UPDATE strategy_discovery_candidates SET eligible_rank=? WHERE id=?", (rank, int(row["id"])))
            del program_rows, batches
            release_factor_program_transients()
        self._save_checkpoint(cycle_id, "development_complete", {"run_id":discovery_id})
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM strategy_discovery_candidates WHERE discovery_run_id=? ORDER BY eligible_rank,id",
                (discovery_id,),
            ).fetchall()
        candidates = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _loads(item["parameters"], {})
            item["program_ast"] = _loads(item.get("program_ast"), {})
            item["factor_versions"] = _loads(item.get("factor_versions"), {})
            item["aggregate_metrics"] = _loads(item["aggregate_metrics"], {})
            item["elimination_reasons"] = _loads(item["elimination_reasons"], [])
            if item.get("eligibility_status") == "ELIGIBLE": candidates.append(item)
        frozen = candidates[:request["finalists"]]
        execution = DiscoveryExecutionConfig(**request["execution_assumptions"]).validate()
        output = []
        for index, item in enumerate(frozen, 1):
            validation_key=f"validated_candidate_{item['id']}"
            saved=((self.detail(cycle_id) or {}).get("checkpoint") or {}).get(validation_key)
            if saved:
                output.append(saved); continue
            checkpoint(cycle["job_id"], 50+int(index/max(1,len(frozen))*45), "auto_research.validating_candidate", {"candidate":index,"total":len(frozen)})
            item["timeframe"] = timeframe
            holdout = self._evaluate_range(item,"BTC-USDT",splits["primary_holdout_start"],splits["primary_holdout_end"],execution,fingerprint)
            oot = self._evaluate_range(item,"BTC-USDT",splits["final_oot_start"],splits["final_oot_end"],execution,fingerprint)
            transfers = [self._evaluate_range(item,asset,splits["primary_holdout_start"],splits["final_oot_end"],execution,fingerprint) for asset in ("ETH-USDT","SOL-USDT")]
            cross_status = "PASS" if sum(value["status"]=="PASS" for value in transfers)>=1 and all(value["metrics"].get("maximum_drawdown",100)<=25 for value in transfers) else "FAIL"
            robust_execution = next(value["execution"] for value in generate_cost_scenarios(execution) if value["scenario_name"]=="COMBINED_2X")
            robust_item={**item,"parameters":{**item["parameters"],"trading_fee":robust_execution.trading_fee,"slippage":robust_execution.slippage}}
            robust_folds=[]
            for _,_,start,end in splits["development_folds"]:
                robust_folds.append(self._evaluate_range(robust_item,"BTC-USDT",start,end,robust_execution,fingerprint))
            robust_aggregate = aggregate([{
                "status": "COMPLETED", "metrics": value["metrics"],
                "buy_hold_metrics": value["buy_hold_metrics"],
            } for value in robust_folds])
            robust_eligibility = evaluate_eligibility(robust_aggregate, timeframe, "DEVELOPMENT_CANDIDATE")
            robust_status = "PASS" if robust_eligibility["eligible"] else "FAIL"
            is_program=item["template"] == "FACTOR_PROGRAM"
            router_parameters = _router_parameters(item["template"],item["parameters"])
            candidate_identity = item.get("candidate_identity") if is_program else build_candidate_identity(item["template"],item["parameters"],execution.execution_hash())
            definition = {
                "schema_version":"approved-deterministic-definition-v1", "template":item["template"],
                "template_version":item["template_version"], "parameters":item["parameters"],
                "direction":item.get("direction") or "BOTH", "router_family":ROUTER_TEMPLATE_MAP.get(item["template"]),
                "router_parameters":router_parameters, "runtime_adapter_version":RUNTIME_ADAPTER_VERSION,
                "dataset_range":{"start":request["research_start"],"end":request["research_end"]},
                "validation_status":{"development":"PASS","walk_forward":"PASS","holdout":holdout["status"],
                    "oot":oot["status"],"cross_asset":cross_status,"robustness":robust_status},
                "execution_assumptions":request["execution_assumptions"],
                "activation_scope":{"mode":"GLOBAL_CROSS_ASSET","policy_version":ACTIVE_SCOPE_POLICY_VERSION,
                    "instruments":["BTC-USDT","ETH-USDT","SOL-USDT"]},
            }
            if is_program:
                definition.update(program_ast=_loads(item.get("program_ast"),{}),factor_versions=_loads(item.get("factor_versions"),{}),program_version=item.get("program_version"))
            evidence = {
                "candidate": item, "candidate_identity": candidate_identity, "definition": definition,
                "configuration_hash": canonical_hash(definition), "development":{"eligible":True,"reasons":[],"score":item["development_score"]},
                "walk_forward":{"status":"PASS","metrics":item["aggregate_metrics"]}, "holdout":holdout,
                "final_oot":oot, "cross_asset":{"status":cross_status,"assets":transfers},
                "robustness":{"status":robust_status,"scenario":"COMBINED_2X","folds":robust_folds,
                    "aggregate_metrics":robust_aggregate,"eligibility":robust_eligibility,
                    "policy":"existing development eligibility applied to all five 2x-cost folds"},
                "contamination":{"state":"CLEAR","candidate_frozen_before_holdout":True,"post_holdout_adjustment":False},
                "identity":{"candidate_identity":candidate_identity,"configuration_hash":canonical_hash(definition)},
                "runtime":{"deterministic":True,"execution_compatible":router_parameters is not None or is_program,"router_family":ROUTER_TEMPLATE_MAP.get(item["template"]),"program_runtime":is_program},
                "dataset_id":dataset["id"],"dataset_fingerprint":fingerprint,"discovery_run_id":discovery_id,
            }
            self._save_checkpoint(cycle_id,validation_key,evidence)
            output.append(evidence)
        return output

    def _job(self, job_id: int, payload: dict[str, Any], checkpoint: Callable[..., Any]) -> dict[str, Any]:
        cycle_id = int(payload["cycle_id"]); cycle = self.detail(cycle_id)
        if not cycle: raise ValueError("Automatic research cycle not found")
        with self.repository.connect() as connection:
            connection.execute("UPDATE automatic_research_cycles SET status='RUNNING',started_at=COALESCE(started_at,?),updated_at=?,error=NULL WHERE id=?", (utc_now(),utc_now(),cycle_id))
        cycle = self.detail(cycle_id) or cycle; cycle["job_id"] = job_id
        checkpoint(job_id, 3, "auto_research.preparing", {})
        evidence_rows = self.evidence_factory(cycle) if self.evidence_factory else self._real_evidence(cycle, checkpoint)
        registered=[]
        for evidence in evidence_rows:
            decision = self.approval.evaluate(evidence)
            item=evidence["candidate"]; definition=evidence["definition"]
            registered.append(self.registry.register({
                "candidate_identity":evidence["candidate_identity"],"family":definition.get("router_family") or item["template"],
                "strategy_type":item["template"],"serialized_definition":definition,"parameters":item["parameters"],
                "instrument_scope":["BTC-USDT","ETH-USDT","SOL-USDT"],"timeframe":item.get("timeframe","15m"),
                "direction_capability":definition.get("direction","BOTH"),"discovery_run_id":evidence.get("discovery_run_id"),
                "research_cycle_id":cycle_id,"source_dataset_fingerprint":evidence["dataset_fingerprint"],
                "development_metrics":item.get("aggregate_metrics",{}),"walk_forward_metrics":evidence["walk_forward"],
                "holdout_result":evidence["holdout"],"final_oot_result":evidence["final_oot"],
                "cross_asset_result":evidence["cross_asset"],"robustness_result":evidence["robustness"],
                "contamination_state":evidence["contamination"],"strategy_version":item.get("template_version",item["template"]),
                "engine_version":ENGINE_VERSION,"policy_version":AUTO_RESEARCH_POLICY_VERSION,
                "configuration_hash":evidence["configuration_hash"],"development_score":item.get("development_score"),
                "pareto_rank":item.get("pareto_rank"),
            }, decision))
        active = self.registry.promote_best(cycle_id)
        result={"registered":len(registered),"approved":sum(item["status"] in {"APPROVED","ACTIVE"} for item in registered),
                "rejected":sum(item["status"]=="REJECTED" for item in registered),"active_registry_id":active["registry_id"] if active else None,
                "live_trading":False,"paper_only":True}
        with self.repository.connect() as connection:
            connection.execute("UPDATE automatic_research_cycles SET status='COMPLETED',result=?,checkpoint=?,updated_at=?,completed_at=? WHERE id=?",
                (_json(result),_json({**(self.detail(cycle_id) or {}).get("checkpoint",{}),"registry_complete":result}),utc_now(),utc_now(),cycle_id))
        checkpoint(job_id,99,"auto_research.completed",result)
        return {"research_cycle_id":cycle_id,"active_registry_id":result["active_registry_id"]}
