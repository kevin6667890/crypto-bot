"""Application service coordinating cached data, backtests and validation workflows."""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from backtest_engine import run_backtest
    from okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient
    from research_repository import ResearchRepository, utc_now
    from strategy_rules import DEFAULT_PARAMETERS, validate_parameters
    from job_queue import JobCancelled, JobQueue
    from portfolio_backtest import PortfolioParameters, run_portfolio_backtest
    from reconciliation import reconcile
    from alert_service import AlertService
except ImportError:
    from .backtest_engine import run_backtest
    from .okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient
    from .research_repository import ResearchRepository, utc_now
    from .strategy_rules import DEFAULT_PARAMETERS, validate_parameters
    from .job_queue import JobCancelled, JobQueue
    from .portfolio_backtest import PortfolioParameters, run_portfolio_backtest
    from .reconciliation import reconcile
    from .alert_service import AlertService


def _date_ts(value: str, end: bool = False) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        parsed += timedelta(days=1) - timedelta(seconds=1)
    return int(parsed.timestamp())


class ResearchService:
    def __init__(self, db_path: Path) -> None:
        self.repository = ResearchRepository(db_path)
        self.history = OkxHistoryClient(self.repository)
        self.alerts = AlertService(db_path)
        self.jobs = JobQueue(db_path, max_queue=int(__import__('os').getenv('RESEARCH_MAX_QUEUE','10')))
        self.jobs.register("BACKTEST", self._job_backtest)
        self.jobs.register("WALK_FORWARD", self._job_walk_forward)
        self.jobs.register("PORTFOLIO_BACKTEST", self._job_portfolio)
        self.jobs.register("OPTIMIZATION", self._job_optimization)
        self.jobs.register_terminal_handler("OPTIMIZATION", self._optimization_job_terminal)
        self.repository.reconcile_optimization_jobs()

    OPTIMIZATION_ENGINE_VERSION = "optimization-lab-v1/canonical-v4"
    OPTIMIZATION_POLICY = {
        "version": "optimization-score-v1",
        "weights": {"validation_return": 30, "profit_factor": 20, "maximum_drawdown": 15, "sharpe": 10, "minimum_trades": 10, "relative_buy_hold": 10, "neighborhood_stability": 5},
        "minimum_validation_trades": 20,
        "maximum_trials": 500,
        "holdout_fraction": 0.20,
        "method": "deterministic stratified random sampling; not AI or automatic parameter tuning",
        "neighborhood": {
            "algorithm": "normalized_euclidean_distance",
            "parameters": {
                "minimum_score": [60.0, 90.0], "minimum_volume_ratio": [0.7, 1.6],
                "stop_loss_atr_multiplier": [0.7, 1.8], "risk_reward_ratio": [1.2, 3.0],
                "ema_pullback_distance": [0.002, 0.010],
            },
            "maximum_distance": 0.25,
            "stability_formula": "5 × (0.60 × positive-return share + 0.20 × PF≥1 share + 0.20 × drawdown≤30% share); neighbours use validation metrics only",
        },
        "retry_semantics": "No in-place resume. Retrying a terminal optimization creates a new run; the original evidence remains terminal.",
    }

    @staticmethod
    def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
        instrument = str(payload.get("instrument", "BTC-USDT"))
        timeframe = str(payload.get("timeframe", "15m"))
        if instrument not in INSTRUMENTS:
            raise ValueError("Instrument must be BTC-USDT, ETH-USDT or SOL-USDT.")
        if timeframe not in {"15m","1H","4H"}:
            raise ValueError("Timeframe must be 15m, 1H or 4H.")
        start_date, end_date = str(payload.get("start_date", "")), str(payload.get("end_date", ""))
        start_ts, end_ts = _date_ts(start_date), _date_ts(end_date, end=True)
        if start_ts >= end_ts:
            raise ValueError("Start date must be earlier than end date.")
        if end_ts > int(datetime.now(timezone.utc).timestamp()) + 86400:
            raise ValueError("End date cannot be in the future.")
        if end_ts - start_ts > 2 * 366 * 86400:
            raise ValueError("A single backtest range cannot exceed two years.")
        parameters = validate_parameters(payload.get("parameters"))
        return {"instrument": instrument, "timeframe": timeframe, "start_date": start_date, "end_date": end_date, "start_ts": start_ts, "end_ts": end_ts, "parameters": asdict(parameters), "strategy_config_id": payload.get("strategy_config_id"), "validation_split": float(payload.get("validation_split", 0.7))}

    def start_backtest(self, payload: dict[str, Any], requester_key: str = "public") -> dict[str, Any]:
        request = self.validate_request(payload)
        if not 0.5 <= request["validation_split"] <= 0.9:
            raise ValueError("Validation split must be between 50% and 90%.")
        existing=self.jobs.find_active("BACKTEST",request,requester_key)
        if existing:
            return {"id":int(existing["request_payload"]["run_id"]),"job_id":existing["id"],"status":existing["status"],"progress":existing["progress"],"deduplicated":True}
        run_id = self.repository.create_run(request)
        job=self.jobs.enqueue("BACKTEST",{**request,"run_id":run_id},requester_key,dedupe_payload=request)
        if job.get("deduplicated"):
            self.repository.update_run(run_id,status="FAILED",progress=100,progress_message="Duplicate request",error="An identical active request already exists")
            return {"id":int(job["request_payload"]["run_id"]),"job_id":job["id"],"status":job["status"],"progress":job["progress"],"deduplicated":True}
        return {"id": run_id, "job_id":job["id"], "status": "QUEUED", "progress": 0, "deduplicated":False}

    def start_optimization(self, payload: dict[str, Any], requester_key: str = "public") -> dict[str, Any]:
        request = self.validate_request(payload)
        if request["instrument"] != "BTC-USDT" or request["timeframe"] != "15m":
            raise ValueError("The first Optimization Lab release supports BTC-USDT 15m only.")
        trial_budget = int(payload.get("trial_budget", 100))
        if not 1 <= trial_budget <= self.OPTIMIZATION_POLICY["maximum_trials"]:
            raise ValueError("Trial budget must be between 1 and 500.")
        seed = int(payload.get("seed", 20260717))
        holdout_start = request["start_ts"] + int((request["end_ts"] - request["start_ts"]) * (1 - self.OPTIMIZATION_POLICY["holdout_fraction"]))
        request.update({"trial_budget": trial_budget, "seed": seed, "holdout_start_ts": holdout_start, "base_parameters": request["parameters"]})
        existing = self.jobs.find_active("OPTIMIZATION", request, requester_key)
        if existing:
            return {"id": existing["request_payload"]["optimization_run_id"], "job_id": existing["id"], "status": existing["status"], "progress": existing["progress"], "deduplicated": True}
        run_id = self.repository.create_optimization_run(request, self.OPTIMIZATION_POLICY, seed, holdout_start)
        try:
            job = self.jobs.enqueue("OPTIMIZATION", {**request, "optimization_run_id": run_id}, requester_key, priority=120, dedupe_payload=request)
        except OverflowError as error:
            self.repository.mark_optimization_run_terminal(run_id, "FAILED", str(error))
            raise
        if job.get("deduplicated"):
            # A competing request won the enqueue race. Keep this new audit row terminal,
            # and return the run actually referenced by the durable existing job payload.
            self.repository.mark_optimization_run_terminal(run_id, "CANCELLED", "Identical active optimization request already exists")
            return {"id": int(job["request_payload"]["optimization_run_id"]), "job_id": job["id"], "status": job["status"], "progress": job["progress"], "deduplicated": True}
        self.repository.update_optimization_run(run_id, job_id=job["id"])
        return {"id": run_id, "job_id": job["id"], "status": job["status"], "progress": job["progress"], "deduplicated": False}

    @staticmethod
    def _clamp(value: float, lower: float = 0, upper: float = 1) -> float:
        return max(lower, min(upper, value))

    def _optimization_parameters(self, base: dict[str, Any], rng: random.Random, trial_number: int) -> dict[str, Any]:
        # The first trial preserves the supplied canonical configuration; later samples are reproducible strata.
        if trial_number == 1:
            return dict(base)
        strata = (trial_number - 2) % 10
        sampled = dict(base)
        sampled.update({
            "minimum_score": round(60 + ((strata + rng.random()) / 10) * 30),
            "minimum_volume_ratio": round(0.7 + rng.random() * 0.9, 2),
            "stop_loss_atr_multiplier": round(0.7 + rng.random() * 1.1, 2),
            "risk_reward_ratio": round(1.2 + rng.random() * 1.8, 2),
            "ema_pullback_distance": round(0.002 + rng.random() * 0.008, 4),
        })
        return sampled

    def _optimization_score(self, metrics: dict[str, Any], buy_hold_return: float) -> tuple[float, dict[str, float], list[str]]:
        policy = self.OPTIMIZATION_POLICY
        trades, pf = int(metrics.get("total_trades") or 0), metrics.get("profit_factor")
        result_return, drawdown, sharpe = float(metrics.get("total_return") or 0), float(metrics.get("maximum_drawdown") or 100), float(metrics.get("sharpe_ratio") or -1)
        components = {
            "validation_return": 30 * self._clamp((result_return + 10) / 40),
            "profit_factor": 20 * self._clamp(((float(pf) if pf is not None else 0) - 0.8) / 1.2),
            "maximum_drawdown": 15 * self._clamp((30 - drawdown) / 30),
            "sharpe": 10 * self._clamp((sharpe + 0.5) / 2),
            "minimum_trades": 10 * self._clamp(trades / 40),
            "relative_buy_hold": 10 * self._clamp((result_return - buy_hold_return + 10) / 30),
            "neighborhood_stability": 0.0,
        }
        reasons = []
        if trades < policy["minimum_validation_trades"]: reasons.append("minimum_validation_trades")
        if drawdown > 35: reasons.append("maximum_drawdown")
        return round(sum(components.values()), 4), components, reasons

    @classmethod
    def _parameter_distance(cls, left: dict[str, Any], right: dict[str, Any]) -> float:
        ranges = cls.OPTIMIZATION_POLICY["neighborhood"]["parameters"]
        squares = []
        for name, (minimum, maximum) in ranges.items():
            span = maximum - minimum
            squares.append(((float(left[name]) - float(right[name])) / span) ** 2)
        return (sum(squares) / len(squares)) ** 0.5

    @classmethod
    def _neighborhood_stability(cls, trial: dict[str, Any], evaluated_trials: list[dict[str, Any]]) -> tuple[float, int]:
        threshold = float(cls.OPTIMIZATION_POLICY["neighborhood"]["maximum_distance"])
        neighbours = [other for other in evaluated_trials if other["id"] != trial["id"] and cls._parameter_distance(trial["parameters"], other["parameters"]) <= threshold]
        if not neighbours:
            return 0.0, 0
        metrics = [item["validation_metrics"] or {} for item in neighbours]
        positive = sum(float(item.get("total_return") or 0) > 0 for item in metrics) / len(metrics)
        profitable = sum(float(item.get("profit_factor") or 0) >= 1 for item in metrics) / len(metrics)
        controlled_drawdown = sum(float(item.get("maximum_drawdown") or 100) <= 30 for item in metrics) / len(metrics)
        return round(5 * (0.60 * positive + 0.20 * profitable + 0.20 * controlled_drawdown), 4), len(neighbours)

    def _optimization_job_terminal(self, job: dict[str, Any]) -> None:
        run_id = int(job["request_payload"].get("optimization_run_id", 0))
        if not run_id or job["status"] not in {"COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"}:
            return
        self.repository.mark_optimization_run_terminal(run_id, job["status"], job.get("error"), job.get("completed_at"))

    @staticmethod
    def _select_holdout_finalists(ranked_trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Only development-ranked, non-eliminated trials can consume the final holdout."""
        return [trial for trial in ranked_trials if trial["status"] == "COMPLETED"][:10]

    def _job_optimization(self, job_id: int, request: dict[str, Any], checkpoint) -> dict[str, Any]:
        run_id, budget, seed = int(request["optimization_run_id"]), int(request["trial_budget"]), int(request["seed"])
        self.repository.update_optimization_run(run_id, status="RUNNING")
        checkpoint(job_id, 3, "optimization.progress.loading_data", {})
        parameters = validate_parameters(request["base_parameters"])
        warmup = max(parameters.slow_ma, parameters.ema_pullback_period, parameters.rsi_period, parameters.atr_period) + 20
        candles, quality = self.history.get_candles("BTC-USDT", "15m", request["start_ts"], request["end_ts"], warmup)
        mtf_data = {frame: self.history.get_candles("BTC-USDT", frame, request["start_ts"], request["end_ts"], warmup)[0] for frame in ("1H", "4H")}
        development_end = int(request["holdout_start_ts"]) - 1
        validation_start = request["start_ts"] + int((development_end - request["start_ts"]) * 0.70)
        validation_candles = [row for row in candles if validation_start <= int(row["ts"]) <= development_end]
        buy_hold_return = ((float(validation_candles[-1]["close"]) / float(validation_candles[0]["open"])) - 1) * 100 if len(validation_candles) > 1 else 0.0
        rng = random.Random(seed)
        existing = self.repository.optimization_run(run_id, budget)
        if existing and existing["trials"]:
            raise RuntimeError("Optimization runs do not resume in place; retry creates a new run.")
        for index in range(1, budget + 1):
            checkpoint(job_id, 5 + int((index - 1) / budget * 75), "optimization.progress.running_trial", {"processed": index - 1, "total": budget})
            values = self._optimization_parameters(request["base_parameters"], rng, index)
            trial_id = self.repository.create_optimization_trial(run_id, index, values, seed, self.OPTIMIZATION_ENGINE_VERSION)
            started = time.monotonic()
            try:
                trial_params = validate_parameters(values)
                train = run_backtest(candles, "BTC-USDT", "15m", trial_params, request["start_ts"], validation_start - 1, timeframe_datasets=mtf_data)
                validation = run_backtest(candles, "BTC-USDT", "15m", trial_params, validation_start, development_end, timeframe_datasets=mtf_data)
                score, components, reasons = self._optimization_score(validation["metrics"], buy_hold_return)
                self.repository.complete_optimization_trial(trial_id, "ELIMINATED" if reasons else "COMPLETED", train_metrics=train["metrics"], validation_metrics=validation["metrics"], score=score, score_components=components, elimination_reasons=reasons, runtime_ms=int((time.monotonic() - started) * 1000))
            except Exception as error:
                self.repository.complete_optimization_trial(trial_id, "FAILED", elimination_reasons=["trial_error"], error=str(error)[:1000], runtime_ms=int((time.monotonic() - started) * 1000))
        detail = self.repository.optimization_run(run_id, budget) or {}
        evaluated = [trial for trial in detail["trials"] if trial["status"] in {"COMPLETED", "ELIMINATED"} and trial.get("validation_metrics")]
        eligible = [trial for trial in evaluated if trial["status"] == "COMPLETED"]
        for trial in eligible:
            stability, neighbour_count = self._neighborhood_stability(trial, evaluated)
            components = trial["score_components"] or {}; components["neighborhood_stability"] = stability; components["neighborhood_count"] = neighbour_count
            self.repository.complete_optimization_trial(trial["id"], "COMPLETED", score=round(sum(float(components.get(key, 0)) for key in self.OPTIMIZATION_POLICY["weights"]), 4), score_components=components, elimination_reasons=[])
        ranked = (self.repository.optimization_run(run_id, budget) or {})["trials"]
        finalists = self._select_holdout_finalists(ranked)
        checkpoint(job_id, 82, "optimization.progress.evaluating_holdout", {"count": len(finalists)})
        for position, trial in enumerate(finalists, 1):
            checkpoint(job_id, 82 + int(position / max(1, len(finalists)) * 15), "optimization.progress.evaluating_holdout", {"count": len(finalists), "processed": position})
            holdout = run_backtest(candles, "BTC-USDT", "15m", validate_parameters(trial["parameters"]), int(request["holdout_start_ts"]), request["end_ts"], timeframe_datasets=mtf_data)
            self.repository.complete_optimization_trial(trial["id"], "COMPLETED", holdout_metrics=holdout["metrics"])
        result = {"method": self.OPTIMIZATION_POLICY["method"], "data_quality": quality, "development_end_ts": development_end, "holdout_start_ts": request["holdout_start_ts"], "holdout_candidates": len(finalists), "warning": "Holdout metrics are reported after ranking and are never included in the optimization score. No strategy is promoted or traded automatically."}
        self.repository.update_optimization_run(run_id, status="COMPLETED", result=json.dumps(result), completed_at=utc_now())
        checkpoint(job_id, 99, "optimization.progress.completed", {"processed": budget, "total": budget})
        return {"optimization_run_id": run_id}

    def retry_optimization_job(self, job_id: int, requester_key: str = "public") -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job or job["job_type"] != "OPTIMIZATION":
            raise ValueError("Optimization job not found.")
        if job["status"] not in {"FAILED", "CANCELLED", "INTERRUPTED"}:
            raise ValueError("Only failed, cancelled or interrupted optimization jobs can be retried.")
        payload = dict(job["request_payload"])
        payload.pop("optimization_run_id", None)
        return self.start_optimization(payload, requester_key)

    def _job_backtest(self, job_id: int, request: dict[str, Any], checkpoint) -> dict[str, Any]:
        run_id=int(request["run_id"])
        try:
            self.repository.update_run(run_id, status="RUNNING", progress=3, progress_message="Checking SQLite candle cache", message_code="research.progress.checking_cache", message_params={})
            checkpoint(job_id,3,"research.progress.checking_cache",{})
            parameters = validate_parameters(request["parameters"])
            warmup = max(parameters.slow_ma, parameters.ema_pullback_period, parameters.rsi_period, parameters.atr_period) + 20
            candles, quality = self.history.get_candles(request["instrument"], request["timeframe"], request["start_ts"], request["end_ts"], warmup)
            if int(quality.get("missing_bars",0))>0:self.alerts.raise_alert("Data Gap Detected","warning","historical_data",f"{quality['missing_bars']} missing {request['timeframe']} bars detected",request["instrument"],related_job_id=job_id,key=f"data-gap|{request['instrument']}|{request['timeframe']}",message_code="alert.data_gap",message_params={"missing":quality["missing_bars"],"timeframe":request["timeframe"],"instrument":request["instrument"]})
            else:self.alerts.resolve(f"data-gap|{request['instrument']}|{request['timeframe']}")
            mtf_data={}
            if request["timeframe"]=="15m":
                for frame in (("1H","4H","1D") if parameters.enable_daily_context else ("1H","4H")):
                    mtf_data[frame],_=self.history.get_candles(request["instrument"],frame,request["start_ts"],request["end_ts"],warmup)
            loaded_params={"instrument":request["instrument"],"loaded":len(candles)}
            self.repository.update_run(run_id, progress=25, progress_message=f"Loaded {len(candles)} confirmed OKX candles", message_code="research.progress.loaded_candles", message_params=loaded_params, data_quality=quality)
            def report(value:int,_legacy_message:str)->None:
                progress_value=25+int(value*.6); processed=min(len(candles),int(len(candles)*value/90)); params={"processed":processed,"total":len(candles)}
                checkpoint(job_id,progress_value,"research.progress.running_backtest",params); self.repository.update_run(run_id,progress=progress_value,progress_message=f"Processing {processed} / {len(candles)} candles",message_code="research.progress.running_backtest",message_params=params)
            result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, request["start_ts"], request["end_ts"], report, mtf_data)
            split_ts = request["start_ts"] + int((request["end_ts"] - request["start_ts"]) * request["validation_split"])
            is_result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, request["start_ts"], split_ts, timeframe_datasets=mtf_data)
            oos_result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, split_ts + 1, request["end_ts"], timeframe_datasets=mtf_data)
            validation = {"split": request["validation_split"], "split_ts": split_ts, "in_sample": is_result["metrics"], "out_of_sample": oos_result["metrics"]}
            is_pf, oos_pf = is_result["metrics"]["profit_factor"], oos_result["metrics"]["profit_factor"]
            is_return, oos_return = is_result["metrics"]["total_return"], oos_result["metrics"]["total_return"]
            degradation = (is_pf and oos_pf is not None and oos_pf < is_pf * 0.6) or (is_return > 0 and oos_return < 0)
            validation["overfitting_warning"] = bool(degradation)
            validation["message"] = "OOS performance materially degraded; review robustness before paper use." if degradation else "No material IS/OOS degradation detected by the simple threshold check."
            validation["message_code"] = "research.validation.oosDegraded" if degradation else "research.validation.oosStable"
            result["validation"] = validation; result["data_quality"] = quality
            self.repository.update_run(run_id,progress=95,progress_message="Saving backtest trades and equity",message_code="research.progress.saving_results",message_params={})
            checkpoint(job_id,95,"research.progress.saving_results",{})
            self.repository.save_result(run_id, result)
            return {"run_id":run_id}
        except Exception as error:
            self.repository.update_run(run_id, status="FAILED", progress=100, progress_message="Failed", message_code="job.failed", message_params={"error":str(error)[:1000]}, error=str(error))
            self.alerts.raise_alert("Backtest Failed","warning","research",f"Backtest run #{run_id} failed",request.get("instrument"),related_job_id=job_id,key=f"backtest-failed|{run_id}",message_code="alert.backtest_failed",message_params={"id":run_id,"instrument":request.get("instrument")})
            raise

    def run_detail(self, run_id: int, include_series: bool = True) -> dict[str, Any] | None:
        run = self.repository.run(run_id)
        if not run:
            return None
        if include_series and run["status"] == "COMPLETED":
            run["trades"] = self.repository.trades(run_id)
            run["equity"] = self.repository.equity(run_id)
        return run

    def strategies(self) -> list[dict[str, Any]]:
        return self.repository.strategies()

    def save_strategy(self, payload: dict[str, Any], strategy_id: int | None = None) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()[:80]
        if not name:
            raise ValueError("Strategy name is required.")
        parameters = asdict(validate_parameters(payload.get("parameters")))
        clean = {**payload, "name": name, "parameters": parameters}
        return self.repository.save_strategy(clean, strategy_id)

    def duplicate_strategy(self, strategy_id: int) -> dict[str, Any]:
        source = next((item for item in self.strategies() if item["id"] == strategy_id), None)
        if not source:
            raise ValueError("Strategy not found.")
        names = {item["name"] for item in self.strategies()}
        base, number, name = f"{source['name']} Copy", 1, f"{source['name']} Copy"
        while name in names:
            number += 1; name = f"{base} {number}"
        return self.save_strategy({**source, "name": name}, None)

    def compare(self, run_ids: list[int]) -> list[dict[str, Any]]:
        output = []
        for run_id in run_ids[:8]:
            run = self.repository.run(int(run_id))
            if not run or run["status"] != "COMPLETED" or not run.get("result"):
                continue
            metrics = run["result"]["metrics"]
            output.append({"id": run["id"], "label": f"#{run['id']} {run['instrument']} {run['timeframe']}", "return": metrics["total_return"], "profit_factor": metrics["profit_factor"], "drawdown": metrics["maximum_drawdown"], "sharpe": metrics["sharpe_ratio"], "win_rate": metrics["win_rate"], "trades": metrics["total_trades"], "fees": metrics["fees_paid"], "expectancy": metrics["expectancy"]})
        return output

    def compare_strategies(self, strategy_ids: list[int]) -> list[dict[str, Any]]:
        output = []
        selected = {int(value) for value in strategy_ids[:8]}
        for strategy in self.strategies():
            metrics = strategy.get("latest_summary")
            if strategy["id"] not in selected or not metrics:
                continue
            output.append({"id": strategy["id"], "label": strategy["name"], "return": metrics["total_return"], "profit_factor": metrics["profit_factor"], "drawdown": metrics["maximum_drawdown"], "sharpe": metrics["sharpe_ratio"], "win_rate": metrics["win_rate"], "trades": metrics["total_trades"], "fees": metrics["fees_paid"], "expectancy": metrics["expectancy"]})
        return output

    def walk_forward(self, payload: dict[str, Any], progress_callback=None) -> dict[str, Any]:
        request = self.validate_request(payload)
        train_days = int(payload.get("train_days", 90)); test_days = int(payload.get("test_days", 30)); step_days = int(payload.get("step_days", 30))
        if not 14 <= train_days <= 730 or not 7 <= test_days <= 365 or not 7 <= step_days <= 365:
            raise ValueError("Walk-forward windows are outside the supported ranges.")
        parameters = validate_parameters(request["parameters"])
        warmup = max(parameters.slow_ma, parameters.ema_pullback_period, parameters.rsi_period, parameters.atr_period) + 20
        candles, quality = self.history.get_candles(request["instrument"], request["timeframe"], request["start_ts"], request["end_ts"], warmup)
        mtf_data={}
        if request["timeframe"]=="15m":
            for frame in (("1H","4H","1D") if parameters.enable_daily_context else ("1H","4H")):mtf_data[frame],_=self.history.get_candles(request["instrument"],frame,request["start_ts"],request["end_ts"],warmup)
        windows, cursor = [], request["start_ts"]
        total_windows=min(36,max(0,(request["end_ts"]-request["start_ts"]-(train_days+test_days)*86400)//(step_days*86400)+1))
        while cursor + (train_days + test_days) * 86400 <= request["end_ts"] and len(windows) < 36:
            train_end = cursor + train_days * 86400 - 1; test_end = train_end + test_days * 86400
            train = run_backtest(candles, request["instrument"], request["timeframe"], parameters, cursor, train_end,timeframe_datasets=mtf_data)
            test = run_backtest(candles, request["instrument"], request["timeframe"], parameters, train_end + 1, test_end,timeframe_datasets=mtf_data)
            windows.append({"train_start": cursor, "train_end": train_end, "test_end": test_end, "train": train["metrics"], "test": test["metrics"]})
            if progress_callback: progress_callback(len(windows),total_windows)
            cursor += step_days * 86400
        if not windows:
            raise ValueError("Date range is too short for the selected walk-forward windows.")
        result = {"windows": windows, "data_quality": quality, "note": "Parameters are held constant; this validates rolling stability and does not perform brute-force optimization."}
        result["id"] = self.repository.save_walk_forward(request["instrument"], request["timeframe"], request["parameters"], windows, {"note": result["note"], "data_quality": quality})
        return result

    def start_walk_forward(self,payload:dict[str,Any],requester_key:str="public")->dict[str,Any]:
        request=self.validate_request(payload)
        for key,default in (("train_days",90),("test_days",30),("step_days",30)): request[key]=int(payload.get(key,default))
        return self.jobs.enqueue("WALK_FORWARD",request,requester_key)

    def _job_walk_forward(self,job_id:int,payload:dict[str,Any],checkpoint)->dict[str,Any]:
        checkpoint(job_id,5,"research.progress.walk_forward_loading",{})
        return self.walk_forward(payload,lambda processed,total:checkpoint(job_id,10+int(processed/max(1,total)*85),"research.progress.walk_forward_window",{"processed":processed,"total":total}))

    def start_portfolio(self,payload:dict[str,Any],requester_key:str="public")->dict[str,Any]:
        assets=sorted(set(payload.get("assets",[])))
        if not assets or any(a not in INSTRUMENTS for a in assets): raise ValueError("Portfolio assets must contain BTC-USDT, ETH-USDT or SOL-USDT.")
        request=self.validate_request({**payload,"instrument":assets[0],"timeframe":"15m"}); request["assets"]=assets
        allowed={k:payload[k] for k in ("initial_capital","max_positions","max_asset_weight","max_asset_risk","max_portfolio_risk","max_long_exposure","max_short_exposure","asset_weights","risk_parity","portfolio_cooldown_bars") if k in payload}
        request["portfolio_parameters"]=asdict(PortfolioParameters(**allowed))
        return self.jobs.enqueue("PORTFOLIO_BACKTEST",request,requester_key)

    def _job_portfolio(self,job_id:int,request:dict[str,Any],checkpoint)->dict[str,Any]:
        params=validate_parameters(request["parameters"]); datasets={}
        warmup=max(params.slow_ma,params.ema_pullback_period,params.rsi_period,params.atr_period)+20
        assets=[asset for asset in ("BTC-USDT","ETH-USDT","SOL-USDT") if asset in request["assets"]]
        stage_width=66/len(assets)
        checkpoint(job_id,5,"portfolio.progress.checking_cache",{"assets":len(assets)})
        for index,asset in enumerate(assets):
            stage_start=8+stage_width*index; stage_end=8+stage_width*(index+1)
            checkpoint(job_id,int(stage_start),"portfolio.progress.loading_candles",{"instrument":asset})
            def load_progress(fetched,loaded,expected,cached,asset=asset,stage_start=stage_start,stage_end=stage_end):
                ratio=min(1,max(loaded,fetched)/max(1,expected))
                checkpoint(job_id,int(stage_start+(stage_end-stage_start)*ratio),"portfolio.progress.loading_candles",{"instrument":asset,"loaded":loaded,"expected":expected,"cached":cached})
            def rate_limited(attempt,delay,asset=asset):
                checkpoint(job_id,None,"portfolio.progress.rate_limited",{"instrument":asset,"attempt":attempt,"seconds":delay})
            def cancelled(): checkpoint(job_id,None)
            datasets[asset],_=self.history.get_candles(asset,"15m",request["start_ts"],request["end_ts"],warmup,load_progress,rate_limited,cancelled)
            checkpoint(job_id,int(stage_end),"portfolio.progress.loaded_candles",{"instrument":asset,"loaded":len(datasets[asset])})
        checkpoint(job_id,75,"portfolio.progress.aligning_timeline",{})
        run_id=self.repository.create_portfolio_run(request["portfolio_parameters"],job_id)
        try:
            def compute_progress(fraction,code,message_params):
                if code=="portfolio.progress.aligning_timeline": value=75
                elif code=="portfolio.progress.calculating_metrics": value=89
                else: value=78+int(float(fraction)*10)
                checkpoint(job_id,value,code,message_params)
            result=run_portfolio_backtest(datasets,params,PortfolioParameters(**request["portfolio_parameters"]),request["start_ts"],request["end_ts"],compute_progress)
            checkpoint(job_id,94,"portfolio.progress.metrics_complete",{"trades":len(result["trades"]),"points":len(result["equity"])})
            self.repository.save_portfolio_result(run_id,result,lambda code,message_params,fraction:checkpoint(job_id,95+int(float(fraction)*3),code,message_params))
            checkpoint(job_id,98,"portfolio.progress.results_saved",{"trades":len(result["trades"]),"points":len(result["equity"])})
            return {"portfolio_run_id":run_id}
        except JobCancelled:
            self.repository.cancel_portfolio_run(run_id); raise
        except Exception as error:
            self.repository.fail_portfolio_run(run_id,str(error)); raise

    def reconciliation(self, run_id: int) -> dict[str, Any]:
        run = self.repository.run(run_id)
        if not run or run["status"] != "COMPLETED":
            raise ValueError("A completed backtest run is required.")
        backtest_trades = self.repository.trades(run_id)
        with self.repository.connect() as connection:
            paper_trades = [dict(row) for row in connection.execute("SELECT * FROM paper_trades WHERE instrument=? AND created_at>=? AND created_at<=? ORDER BY created_at", (run["instrument"], run["start_date"], run["end_date"] + "T23:59:59+00:00"))]
            start_ts,end_ts=_date_ts(run["start_date"]),_date_ts(run["end_date"],True)
            paper_signals=[json.loads(row[0]) for row in connection.execute("SELECT decision_payload FROM decision_signals WHERE source='PAPER' AND instrument=? AND candle_close_ts BETWEEN ? AND ? AND action!='WAIT' ORDER BY candle_close_ts",(run["instrument"],start_ts,end_ts))]
            backtest_signals=[json.loads(row[0]) for row in connection.execute("SELECT d.decision_payload FROM decision_signals d JOIN decision_signal_runs dsr ON dsr.signal_id=d.signal_id WHERE d.source='BACKTEST' AND dsr.run_id=? AND d.action!='WAIT' ORDER BY d.candle_close_ts",(run_id,))]
        paper_exec={x.get("signal_id"):x for x in paper_trades if x.get("signal_id")}; backtest_exec={x.get("signal_id"):x for x in backtest_trades if x.get("signal_id")}
        paper=[{**signal,**paper_exec.get(signal.get("signal_id"),{})} for signal in paper_signals] or paper_trades
        backtest=[{**signal,**backtest_exec.get(signal.get("signal_id"),{})} for signal in backtest_signals] or backtest_trades
        result=reconcile(paper,backtest)
        drift_key=f"strategy-drift|{run_id}"
        if result["drift_status"]=="Diverging":self.alerts.raise_alert("Strategy Drift","warning","reconciliation",f"Run #{run_id} is diverging from paper lineage",run["instrument"],related_job_id=None,key=drift_key)
        elif result["drift_status"]=="Normal":self.alerts.resolve(drift_key)
        result.update({"run_id":run_id,"paper_trades":len(paper_trades),"backtest_trades":len(backtest_trades),"limitations":["Only identical strategy versions are eligible for exact reconciliation.","Legacy rows without reliable lineage remain unmatched."]}); return result


DEFAULT_RESEARCH_PARAMETERS = DEFAULT_PARAMETERS
