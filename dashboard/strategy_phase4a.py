"""Frozen Phase 4A causal replay and research-only execution contracts.

This module is deliberately disconnected from HTTP, Paper, order and LLM code.
The only supported data source is a caller-supplied, read-only OHLCV SQLite file.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import gzip
import io
import itertools
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence


MANIFEST_VERSION = "phase4a-research-manifest-v1"
REPLAY_ENGINE_VERSION = "strategy-event-replay-engine-v2"
BACKTEST_ENGINE_VERSION = "strategy-backtest-engine-v2.0.4"
REPORT_VERSION = "strategy-phase4a-report-v1"
TRIAL_LEDGER_VERSION = "strategy-phase4a-trial-ledger-v1"
ROUTER_VERSION = "strategy-router-v2"
DEFINITIONS_VERSION = "strategy-family-definitions-v2.1"
CONTEXT_VERSION = "market-analysis-context-v2"
STATE_VERSION = "market-state-engine-v2"
DISCLAIMER = "研究结果；未访问最终OOT；未连接Paper；未部署策略。"

FAMILIES = ("TREND_PULLBACK", "MA200_MEAN_REVERSION")
DIRECTIONS = ("LONG", "SHORT")
INSTRUMENTS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
TIMEFRAME_SECONDS = {"15m": 900, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
CLASSIFICATIONS = (
    "INVALID_ENGINE_OR_DATA", "NO_EVENTS", "INSUFFICIENT_SAMPLE",
    "RETIRE_NEGATIVE_EXPECTANCY", "RETIRE_COST_SENSITIVE",
    "RETIRE_FOLD_INSTABILITY", "RETIRE_ASSET_CONCENTRATION",
    "RETIRE_INTRABAR_SENSITIVE", "DEVELOPMENT_PASS", "VALIDATION_FAIL",
    "VALIDATION_PASS_RESEARCH_ONLY",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class TimeSegmentV2:
    name: str
    start_ts: int
    end_ts: int
    identity: str

    def contains_close(self, close_ts: int) -> bool:
        return self.start_ts <= close_ts < self.end_ts


@dataclass(frozen=True)
class FrozenTrialV2:
    sequence: int
    trial_id: str
    family: str
    direction: str
    parameter_set_id: str
    parameters: dict[str, float | int]
    canonical_parameters: str
    config_hash: str


def frozen_trials(dataset_identity: str = "UNBOUND") -> tuple[FrozenTrialV2, ...]:
    ranges: dict[str, dict[str, tuple[float | int, ...]]] = {
        "TREND_PULLBACK": {
            "zone_buffer_atr": (.2, .35), "trigger_score": (72, 78),
            "minimum_r": (1.25, 1.5),
        },
        "MA200_MEAN_REVERSION": {
            "zone_buffer_atr": (.25, .4), "reclaim_bars": (1, 2),
            "minimum_r": (1.25, 1.5),
        },
    }
    result: list[FrozenTrialV2] = []
    sequence = 0
    for family in FAMILIES:
        keys = tuple(ranges[family])
        for direction in DIRECTIONS:
            for values in itertools.product(*(ranges[family][key] for key in keys)):
                sequence += 1
                parameters = dict(zip(keys, values))
                canonical = canonical_json(parameters)
                config_hash = stable_hash({
                    "family": family, "direction": direction,
                    "definitions_version": DEFINITIONS_VERSION,
                    "parameters": parameters,
                })
                parameter_set_id = f"phase4a-{family.lower().replace('_', '-')}-{direction.lower()}-{sequence:02d}"
                trial_id = stable_hash({
                    "dataset_identity": dataset_identity, "parameter_set_id": parameter_set_id,
                    "config_hash": config_hash, "engine": BACKTEST_ENGINE_VERSION,
                })
                result.append(FrozenTrialV2(
                    sequence, trial_id, family, direction, parameter_set_id,
                    parameters, canonical, config_hash,
                ))
    if len(result) != 32 or any(sum(t.family == f and t.direction == d for t in result) != 8
                                for f in FAMILIES for d in DIRECTIONS):
        raise AssertionError("Phase 4A trial space must be exactly 8 per family/direction and 32 total")
    return tuple(result)


def chronological_segments(first_close_ts: int, end_exclusive_ts: int, warmup_seconds: int) -> dict[str, TimeSegmentV2]:
    usable_start = first_close_ts + warmup_seconds
    if usable_start >= end_exclusive_ts:
        raise ValueError("dataset does not cover the frozen warm-up")
    width = end_exclusive_ts - usable_start
    development_end = usable_start + (width * 60 // 100 // 900) * 900
    validation_end = usable_start + (width * 80 // 100 // 900) * 900
    bounds = {
        "DEVELOPMENT": (usable_start, development_end),
        "VALIDATION": (development_end, validation_end),
        "LOCKED_FINAL_OOT": (validation_end, end_exclusive_ts),
    }
    return {name: TimeSegmentV2(name, start, end, stable_hash({"segment": name, "start": start, "end": end}))
            for name, (start, end) in bounds.items()}


class OOTAccessError(PermissionError):
    pass


class ReadOnlyOHLCVStoreV2:
    """Immutable SQLite reader with an irreversible per-instance OOT boundary."""

    def __init__(self, path: Path | str, *, dataset_identity: str, oot_start_ts: int | None = None) -> None:
        self.path = Path(path).resolve()
        self.dataset_identity = dataset_identity
        self.oot_start_ts = oot_start_ts
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _guard(self, start_ts: int | None, end_ts: int | None) -> None:
        if self.oot_start_ts is None:
            return
        if start_ts is not None and start_ts >= self.oot_start_ts:
            raise OOTAccessError("LOCKED_FINAL_OOT read refused")
        if end_ts is None or end_ts > self.oot_start_ts:
            raise OOTAccessError("query could expose LOCKED_FINAL_OOT")

    def candles(self, instrument: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        self._guard(start_ts, end_ts)
        if instrument not in INSTRUMENTS or timeframe not in TIMEFRAME_SECONDS or timeframe == "1W":
            raise ValueError("unsupported Phase 4A partition")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT ts,open,high,low,close,volume,confirmed,source
                   FROM historical_candles
                   WHERE instrument=? AND timeframe=? AND ts>=? AND ts<? AND confirmed=1
                   ORDER BY ts""", (instrument, timeframe, start_ts, end_ts)).fetchall()
        width = TIMEFRAME_SECONDS[timeframe]
        return [{**dict(row), "candle_close_ts": int(row["ts"]) + width} for row in rows]

    def coverage(self) -> list[dict[str, Any]]:
        if self.oot_start_ts is not None:
            raise OOTAccessError("coverage metadata is sealed after OOT lock")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT instrument,timeframe,count(*) rows,min(ts) first_ts,max(ts) last_ts,
                          sum(confirmed) confirmed_rows
                   FROM historical_candles GROUP BY instrument,timeframe
                   ORDER BY instrument,timeframe""").fetchall()
        return [dict(row) for row in rows]


@dataclass(frozen=True)
class ReplayEventV2:
    event_id: str
    instrument: str
    family: str
    direction: str
    parameter_set_id: str
    lifecycle_from: str
    lifecycle_to: str
    context_timestamp: int
    state_timestamp: int
    setup_timestamp: int | None
    trigger_timestamp: int | None
    source_candle_timestamps: tuple[int, ...]
    level_identity: str
    setup_identity: str
    evaluation_identity: str
    route_identity: str
    data_quality: str
    blockers: tuple[str, ...]
    geometry: dict[str, Any]
    engine_version: str = REPLAY_ENGINE_VERSION


@dataclass(frozen=True)
class EntryIntentV2:
    event: ReplayEventV2
    side: str
    stop: float
    target: float
    maximum_holding_bars: int
    minimum_structural_r: float


@dataclass(frozen=True)
class CostPolicyV2:
    fee_rate: float = .0005
    adverse_slippage: float = .0003


@dataclass(frozen=True)
class AccountPolicyV2:
    initial_equity: float = 10_000.0
    requested_risk_fraction: float = .01
    max_notional_fraction: float = .25


def structural_r(entry: float, stop: float, target: float, side: str) -> float | None:
    risk = entry - stop if side == "LONG" else stop - entry
    reward = target - entry if side == "LONG" else entry - target
    return reward / risk if risk > 0 and reward > 0 else None


class StrategyBacktestEngineV2:
    """Deterministic research executor for already replayed TRIGGER_READY events."""

    version = BACKTEST_ENGINE_VERSION

    def __init__(self, cost: CostPolicyV2 = CostPolicyV2(), account: AccountPolicyV2 = AccountPolicyV2(),
                 intrabar_policy: str = "STOP_FIRST") -> None:
        if intrabar_policy not in {"STOP_FIRST", "TARGET_FIRST", "DROP_AMBIGUOUS_BAR"}:
            raise ValueError("invalid intrabar policy")
        self.cost, self.account, self.intrabar_policy = cost, account, intrabar_policy

    def run(self, candles: Sequence[Mapping[str, Any]], intents: Sequence[EntryIntentV2], *,
            segment: TimeSegmentV2) -> dict[str, Any]:
        by_trigger = {intent.event.trigger_timestamp: intent for intent in intents}
        seen_setups: set[str] = set()
        trades: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        equity = self.account.initial_equity
        position: dict[str, Any] | None = None
        pending: EntryIntentV2 | None = None
        ambiguous = 0
        for index, candle in enumerate(candles):
            open_ts = int(candle["ts"]); close_ts = int(candle.get("candle_close_ts", open_ts + 900))
            if not segment.contains_close(close_ts):
                if position is not None and close_ts >= segment.end_ts:
                    equity = self._exit(position, float(candle["open"]), open_ts, "SEGMENT_END", equity, trades, gap=True)
                    position = None
                pending = None
                continue
            if pending is not None and position is None:
                # Candle timestamps are opens: the next bar opens at exactly the
                # prior bar's confirmed close timestamp.
                if open_ts < int(pending.event.trigger_timestamp or 0):
                    raise AssertionError("entry cannot precede trigger close")
                raw_open = float(candle["open"])
                entry = raw_open * (1 + self.cost.adverse_slippage if pending.side == "LONG" else 1 - self.cost.adverse_slippage)
                invalid = entry <= pending.stop if pending.side == "LONG" else entry >= pending.stop
                if invalid:
                    rejections.append(self._rejection(pending, "GAP_INVALIDATED_BEFORE_ENTRY", open_ts)); pending = None
                else:
                    ratio = structural_r(entry, pending.stop, pending.target, pending.side)
                    if ratio is None or ratio < pending.minimum_structural_r:
                        rejections.append(self._rejection(pending, "GEOMETRY_INVALID_AT_ENTRY", open_ts)); pending = None
                    else:
                        risk_per_unit = abs(entry - pending.stop)
                        requested_risk = equity * self.account.requested_risk_fraction
                        risk_units = requested_risk / risk_per_unit
                        max_units = equity * self.account.max_notional_fraction / entry
                        units = min(risk_units, max_units)
                        if units <= 0 or not math.isfinite(units):
                            rejections.append(self._rejection(pending, "GEOMETRY_INVALID_AT_ENTRY", open_ts)); pending = None
                        else:
                            entry_fee = entry * units * self.cost.fee_rate
                            position = {
                                "setup_identity": pending.event.setup_identity, "event_id": pending.event.event_id,
                                "instrument": pending.event.instrument, "family": pending.event.family,
                                "direction": pending.side, "parameter_set_id": pending.event.parameter_set_id,
                                "entry_ts": open_ts, "entry": entry, "raw_entry_open": raw_open,
                                "stop": pending.stop, "target": pending.target, "initial_risk": risk_per_unit,
                                "structural_r": ratio, "units": units, "requested_risk": requested_risk,
                                "actual_risk": units * risk_per_unit, "notional": units * entry,
                                "entry_fee": entry_fee, "entry_slippage": abs(entry - raw_open) * units,
                                "bars": 0, "maximum_holding_bars": pending.maximum_holding_bars,
                                "mae": 0.0, "mfe": 0.0,
                                "regime_tags": list(pending.event.geometry.get("regime_tags", ())),
                            }
                            equity -= entry_fee
                            seen_setups.add(pending.event.setup_identity); pending = None
            if position is not None:
                side = position["direction"]; candle_open = float(candle["open"])
                stop, target = position["stop"], position["target"]
                stop_gap = candle_open <= stop if side == "LONG" else candle_open >= stop
                target_gap = candle_open >= target if side == "LONG" else candle_open <= target
                if stop_gap:
                    equity = self._exit(position, candle_open, open_ts, "GAP_STOP", equity, trades, gap=True); position = None
                elif target_gap:
                    # A target gap never receives a price better than the frozen target.
                    conservative = min(candle_open, target) if side == "LONG" else max(candle_open, target)
                    equity = self._exit(position, conservative, open_ts, "GAP_TARGET", equity, trades, gap=True); position = None
                else:
                    hit_stop = float(candle["low"]) <= stop if side == "LONG" else float(candle["high"]) >= stop
                    hit_target = float(candle["high"]) >= target if side == "LONG" else float(candle["low"]) <= target
                    position["bars"] += 1
                    position["mae"] = min(position["mae"], (float(candle["low"])-position["entry"]) / position["initial_risk"] if side == "LONG" else (position["entry"]-float(candle["high"])) / position["initial_risk"])
                    position["mfe"] = max(position["mfe"], (float(candle["high"])-position["entry"]) / position["initial_risk"] if side == "LONG" else (position["entry"]-float(candle["low"])) / position["initial_risk"])
                    if hit_stop and hit_target:
                        ambiguous += 1
                        if self.intrabar_policy == "DROP_AMBIGUOUS_BAR":
                            equity += position["entry_fee"]
                            position = None
                        else:
                            price = stop if self.intrabar_policy == "STOP_FIRST" else target
                            equity = self._exit(position, price, close_ts, self.intrabar_policy, equity, trades); position = None
                    elif hit_stop:
                        equity = self._exit(position, stop, close_ts, "STOP", equity, trades); position = None
                    elif hit_target:
                        equity = self._exit(position, target, close_ts, "TARGET", equity, trades); position = None
                    elif position["bars"] >= position["maximum_holding_bars"]:
                        equity = self._exit(position, float(candle["close"]), close_ts, "TIMEOUT", equity, trades); position = None
            intent = by_trigger.get(close_ts)
            if intent is not None and intent.event.setup_identity not in seen_setups and position is None and pending is None:
                pending = intent
        if position is not None:
            last = candles[-1]
            equity = self._exit(position, float(last["close"]), min(int(last.get("candle_close_ts", int(last["ts"])+900)), segment.end_ts), "SEGMENT_END", equity, trades)
        return {"trades": trades, "rejections": rejections, "ambiguous_intrabar_count": ambiguous,
                "final_equity": equity, "net_pnl": equity-self.account.initial_equity,
                "intrabar_policy": self.intrabar_policy, "engine_version": self.version}

    @staticmethod
    def _rejection(intent: EntryIntentV2, code: str, ts: int) -> dict[str, Any]:
        return {"setup_identity": intent.event.setup_identity, "event_id": intent.event.event_id,
                "timestamp": ts, "classification": code}

    def _exit(self, position: dict[str, Any], reference: float, ts: int, reason: str,
              equity: float, trades: list[dict[str, Any]], gap: bool = False) -> float:
        side = position["direction"]
        if gap:
            exit_price = reference
            exit_slippage = 0.0
        else:
            exit_price = reference * (1 - self.cost.adverse_slippage if side == "LONG" else 1 + self.cost.adverse_slippage)
            exit_slippage = abs(exit_price-reference) * position["units"]
        gross = (exit_price-position["entry"]) * position["units"] * (1 if side == "LONG" else -1)
        exit_fee = exit_price * position["units"] * self.cost.fee_rate
        net = gross - exit_fee
        r_net = (net-position["entry_fee"]) / position["actual_risk"] if position["actual_risk"] else None
        trade = {**position, "exit_ts": ts, "exit": exit_price, "exit_reason": reason,
                 "gross_pnl": gross, "exit_fee": exit_fee,
                 "fees": position["entry_fee"]+exit_fee,
                 "slippage_drag": position["entry_slippage"]+exit_slippage,
                 "gap_drag": abs(reference-position["stop"])*position["units"] if reason == "GAP_STOP" else 0.0,
                 "net_pnl": net-position["entry_fee"], "r": r_net}
        trades.append(trade)
        return equity + gross - exit_fee


def metrics_v2(trades: Sequence[Mapping[str, Any]], initial_equity: float = 10_000.0) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0, "gross_pnl": 0.0, "net_pnl": 0.0, "net_return_pct": 0.0,
                "expectancy_r": None, "profit_factor": None, "profit_factor_reason": "NO_TRADES",
                "win_rate": None, "average_win": None, "average_loss": None,
                "median_trade_r": None, "max_drawdown": 0.0, "downside_deviation_r": None,
                "stop_exits": 0, "target_exits": 0, "timeout_exits": 0}
    pnls = [float(t["net_pnl"]) for t in trades]
    rs = [float(t["r"]) for t in trades if t.get("r") is not None]
    gross_win = sum(max(0.0, value) for value in pnls)
    gross_loss = -sum(min(0.0, value) for value in pnls)
    equity = initial_equity; peak = equity; max_dd = 0.0
    for value in pnls:
        equity += value; peak = max(peak, equity); max_dd = max(max_dd, (peak-equity)/peak if peak else 0.0)
    profit_factor = gross_win/gross_loss if gross_loss else None
    wins = [value for value in pnls if value > 0]; losses = [value for value in pnls if value < 0]
    negative_rs = [value for value in rs if value < 0]
    return {
        "trade_count": len(trades), "gross_pnl": sum(float(t.get("gross_pnl", 0)) for t in trades),
        "net_pnl": sum(pnls), "net_return_pct": sum(pnls)/initial_equity*100, "total_r": sum(rs),
        "expectancy_r": statistics.fmean(rs) if rs else None,
        "median_trade_r": statistics.median(rs) if rs else None,
        "profit_factor": profit_factor,
        "profit_factor_reason": "NO_LOSING_TRADES" if gross_win and not gross_loss else None,
        "win_rate": len(wins)/len(pnls), "average_win": statistics.fmean(wins) if wins else None,
        "average_loss": statistics.fmean(losses) if losses else None,
        "max_drawdown": max_dd,
        "downside_deviation_r": math.sqrt(sum(value*value for value in negative_rs)/len(rs)) if rs else None,
        "fees": sum(float(t.get("fees", 0)) for t in trades),
        "slippage_drag": sum(float(t.get("slippage_drag", 0)) for t in trades),
        "gap_drag": sum(float(t.get("gap_drag", 0)) for t in trades),
        "average_hold_bars": statistics.fmean(float(t.get("bars", 0)) for t in trades),
        "maximum_hold_bars": max(int(t.get("bars", 0)) for t in trades),
        "mae_r": statistics.fmean(float(t.get("mae", 0)) for t in trades),
        "mfe_r": statistics.fmean(float(t.get("mfe", 0)) for t in trades),
        "stop_exits": sum(str(t.get("exit_reason")) in {"STOP", "STOP_FIRST", "GAP_STOP"} for t in trades),
        "target_exits": sum(str(t.get("exit_reason")) in {"TARGET", "TARGET_FIRST", "GAP_TARGET"} for t in trades),
        "timeout_exits": sum(str(t.get("exit_reason")) in {"TIMEOUT", "SEGMENT_END"} for t in trades),
    }


def bootstrap_expectancy_interval(values: Sequence[float], *, seed: int, repetitions: int = 2000,
                                   block_size: int = 1) -> dict[str, Any]:
    if len(values) < 2:
        return {"lower": None, "upper": None, "reason": "INSUFFICIENT_SAMPLE"}
    rng = random.Random(seed); n = len(values); estimates: list[float] = []
    for _ in range(repetitions):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start+j) % n] for j in range(block_size))
        estimates.append(statistics.fmean(sample[:n]))
    estimates.sort()
    return {"lower": estimates[int(.025*repetitions)], "upper": estimates[min(repetitions-1, int(.975*repetitions))],
            "seed": seed, "repetitions": repetitions, "block_size": block_size}


class ArtifactWriterV2:
    """Atomic, idempotent JSON/JSONL artifact writer; never touches a database."""

    def __init__(self, root: Path | str, run_id: str) -> None:
        self.path = Path(root).resolve() / run_id
        self.path.mkdir(parents=True, exist_ok=True)

    def json(self, name: str, payload: Any) -> Path:
        path = self.path / name
        encoded = canonical_json(payload) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == encoded:
            return path
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
        return path

    def jsonl(self, name: str, rows: Iterable[Mapping[str, Any]], *, identity_key: str) -> Path:
        path = self.path / name
        unique: dict[str, Mapping[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    row = json.loads(line); unique[str(row[identity_key])] = row
        for row in rows:
            key = str(row[identity_key])
            encoded = canonical_json(row)
            if key in unique and canonical_json(unique[key]) != encoded:
                raise ValueError(f"identity collision in {name}: {key}")
            unique[key] = row
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text("".join(canonical_json(unique[key])+"\n" for key in sorted(unique)), encoding="utf-8")
        temporary.replace(path)
        return path

    def jsonl_gzip(self, name: str, rows: Iterable[Mapping[str, Any]], *, identity_key: str) -> Path:
        path = self.path / name
        unique: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            key = str(row[identity_key])
            if key in unique and canonical_json(unique[key]) != canonical_json(row):
                raise ValueError(f"identity collision in {name}: {key}")
            unique[key] = row
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as handle:
                    for key in sorted(unique): handle.write(canonical_json(unique[key])+"\n")
        temporary.replace(path)
        return path


def utc_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _ema(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = []; current: float | None = None; alpha = 2.0/(period+1)
    for index, value in enumerate(values):
        current = value if current is None else value*alpha + current*(1-alpha)
        result.append(current if index+1 >= period else None)
    return result


def _rolling_mean(values: Sequence[float], period: int) -> list[float | None]:
    output: list[float | None] = []; total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period: total -= values[index-period]
        output.append(total/period if index+1 >= period else None)
    return output


def _rolling_extreme(values: Sequence[float], period: int, maximum: bool) -> list[float | None]:
    # Periods are bounded (20/60); the simple causal implementation is clearer
    # than an opaque vectorized operation and never includes the current bar.
    output: list[float | None] = []
    func = max if maximum else min
    for index in range(len(values)):
        prior = values[max(0, index-period):index]
        output.append(func(prior) if len(prior) >= min(period, 5) else None)
    return output


def _rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    gains: list[float] = []; losses: list[float] = []
    for index in range(1, len(values)):
        change = values[index]-values[index-1]
        gains.append(max(change, 0.0)); losses.append(max(-change, 0.0))
        if len(gains) > period: gains.pop(0); losses.pop(0)
        if len(gains) == period:
            gain = sum(gains)/period; loss = sum(losses)/period
            output[index] = 100.0 if loss == 0 else 100-100/(1+gain/loss)
    return output


def _atr(rows: Sequence[Mapping[str, Any]], period: int = 14) -> list[float | None]:
    output: list[float | None] = [None]*len(rows); current: float | None = None; seed: list[float] = []
    for index in range(1, len(rows)):
        tr = max(float(rows[index]["high"])-float(rows[index]["low"]),
                 abs(float(rows[index]["high"])-float(rows[index-1]["close"])),
                 abs(float(rows[index]["low"])-float(rows[index-1]["close"])))
        if current is None:
            seed.append(tr)
            if len(seed) == period: current = sum(seed)/period
        else: current = (current*(period-1)+tr)/period
        output[index] = current
    return output


@dataclass(frozen=True)
class _CausalFrame:
    rows: tuple[dict[str, Any], ...]
    closes: tuple[float, ...]
    ema20: tuple[float | None, ...]
    ma60: tuple[float | None, ...]
    ma200: tuple[float | None, ...]
    ma200_slope: tuple[float | None, ...]
    atr14: tuple[float | None, ...]
    rsi14: tuple[float | None, ...]
    prior_high20: tuple[float | None, ...]
    prior_low20: tuple[float | None, ...]


def _frame(rows: Sequence[Mapping[str, Any]]) -> _CausalFrame:
    copied = tuple(dict(row) for row in rows); closes = tuple(float(row["close"]) for row in copied)
    ma200 = _rolling_mean(closes, 200)
    slopes = [None if index < 4 or ma200[index] is None or ma200[index-4] is None
              else float(ma200[index])-float(ma200[index-4]) for index in range(len(copied))]
    return _CausalFrame(
        copied, closes, tuple(_ema(closes, 20)), tuple(_rolling_mean(closes, 60)), tuple(ma200),
        tuple(slopes), tuple(_atr(copied)), tuple(_rsi(closes)),
        tuple(_rolling_extreme([float(row["high"]) for row in copied], 20, True)),
        tuple(_rolling_extreme([float(row["low"]) for row in copied], 20, False)),
    )


def _latest_closed_index(frame: _CausalFrame, as_of: int, cursor: int) -> int:
    while cursor+1 < len(frame.rows) and int(frame.rows[cursor+1]["candle_close_ts"]) <= as_of:
        cursor += 1
    return cursor


class StrategyEventReplayEngineV2:
    """Single-pass causal materialization of the frozen Context/State/Router rules.

    Indicator arrays are forward-only recurrences.  At evaluation time every
    frame is addressed through its last ``candle_close_ts <= as_of`` cursor.
    The output retains the public V2 versions and sufficient source timestamps
    to independently audit that invariant.
    """

    version = REPLAY_ENGINE_VERSION

    def replay(self, partitions: Mapping[str, Sequence[Mapping[str, Any]]], trials: Sequence[FrozenTrialV2],
               *, instrument: str, segment: TimeSegmentV2, event_frequency_only: bool = False,
               checkpoint: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if instrument not in INSTRUMENTS: raise ValueError("unsupported instrument")
        for timeframe in ("15m", "1H", "4H", "1D"):
            if timeframe not in partitions: raise ValueError(f"missing {timeframe} partition")
        if checkpoint and checkpoint.get("segment") != segment.identity:
            raise ValueError("checkpoint segment identity mismatch; setup cannot cross segment boundary")
        resume_after = int((checkpoint or {}).get("last_evaluated_ts", -1))
        frames = {name: _frame(rows) for name, rows in partitions.items()}
        execution = frames["15m"]; cursor = {"1H": -1, "4H": -1, "1D": -1}
        lifecycle: dict[str, dict[str, Any]] = dict((checkpoint or {}).get("lifecycle", {}))
        events: list[ReplayEventV2] = []; intents: list[EntryIntentV2] = []
        evaluations = 0; started_wall = time.perf_counter(); last_evaluated = resume_after
        for i, row in enumerate(execution.rows):
            as_of = int(row["candle_close_ts"])
            if as_of < segment.start_ts: continue
            if as_of >= segment.end_ts: break
            if as_of <= resume_after: continue
            for timeframe in cursor:
                cursor[timeframe] = _latest_closed_index(frames[timeframe], as_of, cursor[timeframe])
            if min(cursor.values()) < 199 or i < 200: continue
            last_evaluated = as_of
            indices = {"15m": i, **cursor}
            source_ts = tuple(int(frames[tf].rows[idx]["candle_close_ts"]) for tf, idx in indices.items())
            if any(value > as_of for value in source_ts): raise AssertionError("future candle became visible")
            for trial in trials:
                if trial.family not in FAMILIES: continue
                evaluations += 1
                desired, level, geometry, blockers, score = self._evaluate(frames, indices, trial)
                key = f"{instrument}:{trial.trial_id}"
                prior = lifecycle.get(key, {"state": "INELIGIBLE", "setup_ts": None, "setup_id": None,
                                            "expires_at": None, "triggered": set()})
                if isinstance(prior.get("triggered"), list): prior["triggered"] = set(prior["triggered"])
                level_key = ((level or {}).get("type"), (level or {}).get("timeframe"),
                             (level or {}).get("boundary"), (level or {}).get("source_timestamp"))
                prior_level_key = tuple(prior.get("level_key")) if prior.get("level_key") is not None else None
                if prior_level_key != level_key:
                    prior = {"state": "INELIGIBLE", "setup_ts": None, "setup_id": None,
                             "expires_at": None, "triggered": prior.get("triggered", set())}
                previous_state = str(prior["state"])
                setup_ts = prior.get("setup_ts")
                if desired in {"WATCH", "ARMED", "TRIGGER_READY"} and setup_ts is None:
                    setup_ts = as_of
                maximum_wait = 48 if trial.family == "MA200_MEAN_REVERSION" else 64
                expires_at = int(setup_ts)+maximum_wait*900 if setup_ts else None
                state = desired
                if desired in {"WATCH", "ARMED"} and expires_at and as_of > expires_at: state = "EXPIRED"
                setup_identity = prior.get("setup_id")
                if setup_identity is None:
                    setup_identity = stable_hash({"dataset": trial.trial_id, "instrument": instrument,
                                                  "family": trial.family, "direction": trial.direction,
                                                  "level_key": level_key, "setup_timestamp": setup_ts,
                                                  "segment": segment.identity})
                # A trigger is accepted only after the setup has existed in ARMED.
                if state == "TRIGGER_READY" and previous_state != "ARMED": state = "ARMED"
                if state == "TRIGGER_READY" and setup_identity in prior["triggered"]: state = "COOLDOWN_RESEARCH_ONLY"
                trigger_ts = as_of if state == "TRIGGER_READY" else None
                if state != previous_state:
                    level_identity = stable_hash({"instrument": instrument, "family": trial.family,
                                                  "direction": trial.direction, "level_key": level_key})
                    evaluation_id = stable_hash({"setup": setup_identity, "as_of": as_of,
                                                 "sources": source_ts, "trial": trial.trial_id})
                    route_id = stable_hash({"evaluation": evaluation_id, "router": ROUTER_VERSION})
                    event_id = stable_hash({"route": route_id, "from": previous_state, "to": state})
                    event = ReplayEventV2(
                        event_id, instrument, trial.family, trial.direction, trial.parameter_set_id,
                        previous_state, state, as_of, as_of, setup_ts, trigger_ts, source_ts,
                        level_identity, setup_identity, evaluation_id, route_id, "PRICE_CONFIRMED_FLOW_MISSING",
                        tuple(blockers), geometry, self.version,
                    )
                    events.append(event)
                    if state == "TRIGGER_READY":
                        prior["triggered"].add(setup_identity)
                        if not event_frequency_only:
                            intents.append(EntryIntentV2(event, trial.direction, float(geometry["stop"]),
                                                        float(geometry["target"]), int(geometry["maximum_holding_bars"]),
                                                        float(trial.parameters["minimum_r"])))
                lifecycle[key] = {"state": state, "setup_ts": setup_ts, "setup_id": setup_identity,
                                  "expires_at": expires_at, "triggered": prior["triggered"],
                                  "level_key": level_key, "score": score}
        wall = time.perf_counter()-started_wall
        serialized_lifecycle = {key: {**value, "triggered": sorted(value["triggered"])} for key, value in lifecycle.items()}
        return {"events": events, "intents": intents, "evaluations": evaluations,
                "evaluations_per_second": evaluations/wall if wall else None, "wall_seconds": wall,
                "checkpoint": {"lifecycle": serialized_lifecycle, "segment": segment.identity,
                               "engine_version": self.version, "last_evaluated_ts": last_evaluated}}

    @staticmethod
    def _evaluate(frames: Mapping[str, _CausalFrame], idx: Mapping[str, int], trial: FrozenTrialV2
                  ) -> tuple[str, dict[str, Any] | None, dict[str, Any], list[str], float]:
        e = frames["15m"]; h1 = frames["1H"]; h4 = frames["4H"]; d1 = frames["1D"]
        ei, hi, qi, di = idx["15m"], idx["1H"], idx["4H"], idx["1D"]
        side = 1 if trial.direction == "LONG" else -1
        price = e.closes[ei]; atr = e.atr14[ei]
        h4_trend = 1 if h4.ma60[qi] and h4.ma200[qi] and h4.closes[qi] > h4.ma60[qi] > h4.ma200[qi] else -1 if h4.ma60[qi] and h4.ma200[qi] and h4.closes[qi] < h4.ma60[qi] < h4.ma200[qi] else 0
        d1_trend = 1 if d1.ma60[di] and d1.closes[di] > d1.ma60[di] else -1 if d1.ma60[di] and d1.closes[di] < d1.ma60[di] else 0
        slope = h4.ma200_slope[qi]
        environment = side in {h4_trend, d1_trend} and h4_trend != -side and slope is not None and slope*side >= 0
        level: dict[str, Any] | None = None; blockers: list[str] = []; touched = recovered = confluence = False
        stop = target = None; env_score = 25.0 if environment else 0.0; structure_score = setup_score = trigger_score = 0.0
        buffer_atr = float(trial.parameters["zone_buffer_atr"])
        if atr is None:
            return "INELIGIBLE", None, {}, ["INSUFFICIENT_DATA"], 0.0
        if trial.family == "TREND_PULLBACK":
            candidates = [("1H_EMA20", h1.ema20[hi]), ("1H_MA60", h1.ma60[hi]), ("4H_EMA20", h4.ema20[qi]), ("4H_MA60", h4.ma60[qi])]
            valid = [(name, float(value)) for name, value in candidates if value is not None and (price-value)*side >= -buffer_atr*atr]
            if valid:
                name, boundary = min(valid, key=lambda item: abs(price-item[1]))
                level = {"type": name, "timeframe": name.split("_")[0], "boundary": round(boundary, 10),
                         "source_timestamp": int((h1 if name.startswith("1H") else h4).rows[hi if name.startswith("1H") else qi]["candle_close_ts"])}
                touched = abs(price-boundary) <= buffer_atr*atr
                pullback = (h1.closes[hi] <= float(h1.ema20[hi] or h1.closes[hi])) if side == 1 else (h1.closes[hi] >= float(h1.ema20[hi] or h1.closes[hi]))
                prior_close = e.closes[ei-1]
                momentum = e.rsi14[ei]; prior_momentum = e.rsi14[ei-1]
                recovered = bool(momentum is not None and prior_momentum is not None and
                                 ((side == 1 and prior_close <= boundary < price and momentum > prior_momentum and momentum >= 35) or
                                  (side == -1 and prior_close >= boundary > price and momentum < prior_momentum and momentum <= 65)))
                structure_score = 25.0; setup_score = 20.0 if touched and pullback else 10.0 if pullback else 0.0
                trigger_score = 20.0 if recovered else 8.0 if touched else 0.0
                pressure = (side == 1 and h1.prior_high20[hi] is not None and float(h1.prior_high20[hi])-price < atr*1.25) or (side == -1 and h1.prior_low20[hi] is not None and price-float(h1.prior_low20[hi]) < atr*1.25)
                if pressure: blockers.append("TOO_CLOSE_TO_OPPOSING_LEVEL")
                stop = min(float(e.rows[ei]["low"]), float(h1.prior_low20[hi] or boundary)-buffer_atr*atr) if side == 1 else max(float(e.rows[ei]["high"]), float(h1.prior_high20[hi] or boundary)+buffer_atr*atr)
                target = float(h1.prior_high20[hi] or price+2*atr) if side == 1 else float(h1.prior_low20[hi] or price-2*atr)
            desired = "TRIGGER_READY" if environment and recovered and not blockers else "ARMED" if environment and touched else "WATCH" if environment else "INELIGIBLE"
            threshold = float(trial.parameters["trigger_score"])
        else:
            candidates = [("1H_MA200", h1.ma200[hi], h1.ma200_slope[hi], hi, h1),
                          ("4H_MA200", h4.ma200[qi], h4.ma200_slope[qi], qi, h4)]
            valid = [(name, float(value), sl, ix, fr) for name, value, sl, ix, fr in candidates
                     if value is not None and sl is not None and sl*side >= 0 and (price-float(value))*side >= -buffer_atr*atr]
            if valid:
                name, boundary, _, lix, lframe = min(valid, key=lambda item: abs(price-item[1]))
                level = {"type": name, "timeframe": name.split("_")[0], "boundary": round(boundary, 10),
                         "source_timestamp": int(lframe.rows[lix]["candle_close_ts"])}
                touched = abs(price-boundary) <= buffer_atr*atr
                structural = float(h1.prior_low20[hi] if side == 1 else h1.prior_high20[hi])
                round_step = 10**math.floor(math.log10(price))/10 if price > 0 else 1
                confluence = abs(structural-boundary) <= max(2*atr, abs(boundary)*.003) or abs(round(price/round_step)*round_step-boundary) <= max(atr, abs(boundary)*.003)
                momentum = e.rsi14[ei]; oversold = momentum is not None and (momentum <= 40 if side == 1 else momentum >= 60)
                reclaim_bars = int(trial.parameters["reclaim_bars"])
                reclaim_window = e.closes[max(0, ei-reclaim_bars+1):ei+1]
                reclaimed = all((value > boundary if side == 1 else value < boundary) for value in reclaim_window)
                previous_outside = e.closes[ei-reclaim_bars] <= boundary if side == 1 else e.closes[ei-reclaim_bars] >= boundary
                candle_range = max(float(e.rows[ei]["high"])-float(e.rows[ei]["low"]), 1e-12)
                wick = ((min(float(e.rows[ei]["open"]), price)-float(e.rows[ei]["low"]))/candle_range if side == 1 else
                        (float(e.rows[ei]["high"])-max(float(e.rows[ei]["open"]), price))/candle_range)
                recovered = bool(reclaimed and previous_outside and wick >= .30 and momentum is not None and
                                 ((side == 1 and momentum >= 35) or (side == -1 and momentum <= 65)))
                structure_score = 25.0 if confluence else 12.0; setup_score = 20.0 if touched and oversold else 10.0
                trigger_score = 20.0 if recovered else 8.0 if touched else 0.0
                if not confluence: blockers.append("NO_STRUCTURAL_LEVEL")
                if touched and not reclaimed: blockers.append("MA200_TOUCH_WITHOUT_RECLAIM")
                stop = min(float(e.rows[ei]["low"]), boundary-buffer_atr*atr, float(h1.prior_low20[hi])) if side == 1 else max(float(e.rows[ei]["high"]), boundary+buffer_atr*atr, float(h1.prior_high20[hi]))
                target = float(h1.prior_high20[hi]) if side == 1 else float(h1.prior_low20[hi])
            desired = "TRIGGER_READY" if environment and recovered and confluence else "ARMED" if environment and touched else "WATCH" if level else "INELIGIBLE"
            threshold = 72.0
        quality_score = 8.0  # confirmed price complete; flow unavailable and contributes no score
        score = env_score+structure_score+setup_score+trigger_score+quality_score
        geometry: dict[str, Any] = {}
        if level and stop is not None and target is not None:
            ratio = structural_r(price, stop, target, trial.direction)
            valid_geometry = ratio is not None and ratio >= float(trial.parameters["minimum_r"])
            geometry = {"setup_zone": level, "trigger_boundary": level["boundary"], "stop": stop,
                        "target": target, "invalidation_reference": "confirmed swing / zone opposite boundary",
                        "stop_reference_type": "confirmed swing" if trial.family == "TREND_PULLBACK" else "MA zone opposite boundary",
                        "target_reference_type": "prior swing", "structural_r_at_trigger": ratio,
                        "minimum_structural_reward_risk": float(trial.parameters["minimum_r"]),
                        "maximum_wait_bars": 64 if trial.family == "TREND_PULLBACK" else 48,
                        "maximum_holding_bars": 96 if trial.family == "TREND_PULLBACK" else 64,
                        "entry_timing": "next confirmed 15m open", "intrabar_policy": "STOP_FIRST",
                        "gap_policy": "conservative-open-v1", "valid": valid_geometry,
                        "regime_tags": [
                            "HTF_UPTREND" if h4_trend == 1 else "HTF_DOWNTREND" if h4_trend == -1 else "TRANSITION",
                            "PULLBACK" if trial.family == "TREND_PULLBACK" else
                            ("MAJOR_SUPPORT_TEST" if trial.direction == "LONG" else "MAJOR_RESISTANCE_TEST"),
                            "HIGH_VOLATILITY" if atr/price >= .02 else "LOW_VOLATILITY" if atr/price <= .005 else "NORMAL_VOLATILITY",
                        ]}
            if not valid_geometry: blockers.append("INVALID_GEOMETRY")
        else:
            blockers.append("INVALID_GEOMETRY")
        if desired == "TRIGGER_READY" and (score < threshold or blockers or not geometry.get("valid")):
            desired = "ARMED" if setup_score == 20 else "WATCH"
        return desired, level, geometry, sorted(set(blockers)), score
