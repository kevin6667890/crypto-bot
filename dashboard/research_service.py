"""Application service coordinating cached data, backtests and validation workflows."""

from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from backtest_engine import run_backtest
    from okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient
    from research_repository import ResearchRepository, utc_now
    from strategy_rules import DEFAULT_PARAMETERS, validate_parameters
except ImportError:
    from .backtest_engine import run_backtest
    from .okx_history import INSTRUMENTS, TIMEFRAME_SECONDS, OkxHistoryClient
    from .research_repository import ResearchRepository, utc_now
    from .strategy_rules import DEFAULT_PARAMETERS, validate_parameters


def _date_ts(value: str, end: bool = False) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        parsed += timedelta(days=1) - timedelta(seconds=1)
    return int(parsed.timestamp())


class ResearchService:
    def __init__(self, db_path: Path) -> None:
        self.repository = ResearchRepository(db_path)
        self.history = OkxHistoryClient(self.repository)
        self._active: set[int] = set()
        self._lock = threading.Lock()

    @staticmethod
    def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
        instrument = str(payload.get("instrument", "BTC-USDT"))
        timeframe = str(payload.get("timeframe", "15m"))
        if instrument not in INSTRUMENTS:
            raise ValueError("Instrument must be BTC-USDT, ETH-USDT or SOL-USDT.")
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Timeframe must be 15m, 1H or 4H.")
        start_date, end_date = str(payload.get("start_date", "")), str(payload.get("end_date", ""))
        start_ts, end_ts = _date_ts(start_date), _date_ts(end_date, end=True)
        if start_ts >= end_ts:
            raise ValueError("Start date must be earlier than end date.")
        if end_ts > int(datetime.now(timezone.utc).timestamp()) + 86400:
            raise ValueError("End date cannot be in the future.")
        if end_ts - start_ts > 5 * 366 * 86400:
            raise ValueError("A single backtest range cannot exceed five years.")
        parameters = validate_parameters(payload.get("parameters"))
        return {"instrument": instrument, "timeframe": timeframe, "start_date": start_date, "end_date": end_date, "start_ts": start_ts, "end_ts": end_ts, "parameters": asdict(parameters), "strategy_config_id": payload.get("strategy_config_id"), "validation_split": float(payload.get("validation_split", 0.7))}

    def start_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = self.validate_request(payload)
        if not 0.5 <= request["validation_split"] <= 0.9:
            raise ValueError("Validation split must be between 50% and 90%.")
        run_id = self.repository.create_run(request)
        with self._lock:
            self._active.add(run_id)
        threading.Thread(target=self._execute, args=(run_id, request), daemon=True, name=f"backtest-{run_id}").start()
        return {"id": run_id, "status": "QUEUED", "progress": 0}

    def _execute(self, run_id: int, request: dict[str, Any]) -> None:
        try:
            self.repository.update_run(run_id, status="RUNNING", progress=3, progress_message="Checking SQLite candle cache")
            parameters = validate_parameters(request["parameters"])
            warmup = max(parameters.slow_ma, parameters.ema_pullback_period, parameters.rsi_period, parameters.atr_period) + 20
            candles, quality = self.history.get_candles(request["instrument"], request["timeframe"], request["start_ts"], request["end_ts"], warmup)
            self.repository.update_run(run_id, progress=25, progress_message=f"Loaded {len(candles)} confirmed OKX candles", data_quality=quality)
            result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, request["start_ts"], request["end_ts"], lambda value, message: self.repository.update_run(run_id, progress=25 + int(value * 0.6), progress_message=message))
            split_ts = request["start_ts"] + int((request["end_ts"] - request["start_ts"]) * request["validation_split"])
            is_result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, request["start_ts"], split_ts)
            oos_result = run_backtest(candles, request["instrument"], request["timeframe"], parameters, split_ts + 1, request["end_ts"])
            validation = {"split": request["validation_split"], "split_ts": split_ts, "in_sample": is_result["metrics"], "out_of_sample": oos_result["metrics"]}
            is_pf, oos_pf = is_result["metrics"]["profit_factor"], oos_result["metrics"]["profit_factor"]
            is_return, oos_return = is_result["metrics"]["total_return"], oos_result["metrics"]["total_return"]
            degradation = (is_pf and oos_pf is not None and oos_pf < is_pf * 0.6) or (is_return > 0 and oos_return < 0)
            validation["overfitting_warning"] = bool(degradation)
            validation["message"] = "OOS performance materially degraded; review robustness before paper use." if degradation else "No material IS/OOS degradation detected by the simple threshold check."
            result["validation"] = validation; result["data_quality"] = quality
            self.repository.save_result(run_id, result)
        except Exception as error:
            self.repository.update_run(run_id, status="FAILED", progress=100, progress_message="Failed", error=str(error))
        finally:
            with self._lock:
                self._active.discard(run_id)

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

    def walk_forward(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = self.validate_request(payload)
        train_days = int(payload.get("train_days", 90)); test_days = int(payload.get("test_days", 30)); step_days = int(payload.get("step_days", 30))
        if not 14 <= train_days <= 730 or not 7 <= test_days <= 365 or not 7 <= step_days <= 365:
            raise ValueError("Walk-forward windows are outside the supported ranges.")
        parameters = validate_parameters(request["parameters"])
        warmup = max(parameters.slow_ma, parameters.ema_pullback_period, parameters.rsi_period, parameters.atr_period) + 20
        candles, quality = self.history.get_candles(request["instrument"], request["timeframe"], request["start_ts"], request["end_ts"], warmup)
        windows, cursor = [], request["start_ts"]
        while cursor + (train_days + test_days) * 86400 <= request["end_ts"] and len(windows) < 36:
            train_end = cursor + train_days * 86400 - 1; test_end = train_end + test_days * 86400
            train = run_backtest(candles, request["instrument"], request["timeframe"], parameters, cursor, train_end)
            test = run_backtest(candles, request["instrument"], request["timeframe"], parameters, train_end + 1, test_end)
            windows.append({"train_start": cursor, "train_end": train_end, "test_end": test_end, "train": train["metrics"], "test": test["metrics"]})
            cursor += step_days * 86400
        if not windows:
            raise ValueError("Date range is too short for the selected walk-forward windows.")
        result = {"windows": windows, "data_quality": quality, "note": "Parameters are held constant; this validates rolling stability and does not perform brute-force optimization."}
        result["id"] = self.repository.save_walk_forward(request["instrument"], request["timeframe"], request["parameters"], windows, {"note": result["note"], "data_quality": quality})
        return result

    def reconciliation(self, run_id: int) -> dict[str, Any]:
        run = self.repository.run(run_id)
        if not run or run["status"] != "COMPLETED":
            raise ValueError("A completed backtest run is required.")
        backtest = self.repository.trades(run_id)
        with self.repository.connect() as connection:
            paper = [dict(row) for row in connection.execute("SELECT * FROM paper_trades WHERE instrument=? AND created_at>=? AND created_at<=? ORDER BY created_at", (run["instrument"], run["start_date"], run["end_date"] + "T23:59:59+00:00"))]
        bt_wins = sum(float(item["pnl"]) > 0 for item in backtest); paper_closed = [item for item in paper if item.get("status") != "OPEN"]
        paper_wins = sum(item.get("status") == "WIN" for item in paper_closed)
        paper_gross_win = sum(max(float(item.get("pnl_r") or 0), 0) for item in paper_closed); paper_gross_loss = abs(sum(min(float(item.get("pnl_r") or 0), 0) for item in paper_closed))
        paper_pf = paper_gross_win / paper_gross_loss if paper_gross_loss else None
        bt_metrics = run["result"]["metrics"]
        signal_difference = len(paper) - int(run["result"].get("signal_count", len(backtest)))
        status = "Normal" if abs(signal_difference) <= 1 else "Watch" if abs(signal_difference) <= 3 else "Diverging"
        return {"run_id": run_id, "paper_trades": len(paper), "backtest_trades": len(backtest), "paper_signal_count": len(paper), "backtest_signal_count": run["result"].get("signal_count", len(backtest)), "signal_count_difference": signal_difference,
                "paper_win_rate": paper_wins / len(paper_closed) * 100 if paper_closed else None, "backtest_win_rate": bt_metrics["win_rate"], "paper_profit_factor": paper_pf, "backtest_profit_factor": bt_metrics["profit_factor"],
                "average_entry_difference_pct": None, "slippage_difference": None, "missed_signals": max(0, run["result"].get("signal_count", 0) - len(paper)), "unexpected_signals": max(0, len(paper) - run["result"].get("signal_count", 0)), "drift_status": status,
                "limitations": ["Paper collection runs on a 60-second interval.", "Backtest signals use confirmed candle closes and next-open execution.", "Paper and historical API snapshots can differ.", "Configured slippage and paper observed prices are not identical.", "Cooldown, service restarts and missing collection intervals can suppress paper signals."]}


DEFAULT_RESEARCH_PARAMETERS = DEFAULT_PARAMETERS
