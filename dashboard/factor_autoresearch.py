"""Deterministic, resumable Phase 6F factor AutoResearch.

This module is intentionally independent of strategy-program search, paper
execution, and order APIs. Its diagnostic long/short portfolios are statistical
measurement devices only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Iterable

import numpy as np

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

from .factor_expression import (
    BASIS_TERMINALS,
    BLOCKED_TERMINALS,
    FACTOR_GRAMMAR_VERSION,
    FACTOR_IDENTITY_VERSION,
    FACTOR_SCHEMA_VERSION,
    FACTOR_SEARCH_POLICY_VERSION,
    FUNDING_TERMINALS,
    INTRADAY_LOOKBACKS,
    PRICE_TERMINALS,
    SEMANTIC_SEED,
    FactorNode,
    canonical_json,
    canonicalize,
    deterministic_generate,
    expression_plain_language,
    factor_identity,
    validate_expression,
)
from .factor_statistics import (
    MULTIPLE_TESTING_POLICY_VERSION,
    benjamini_hochberg,
    bonferroni,
    correlation_clusters,
    deflated_sharpe_ratio,
    moments,
    pbo_from_blocks,
    probabilistic_sharpe_ratio,
)
from .microstructure import (
    MICROSTRUCTURE_FEATURE_VERSION,
    MICROSTRUCTURE_SCHEMA_VERSION,
    MICROSTRUCTURE_SOURCE_VERSION,
    MicrostructureStore,
)


FACTOR_EVALUATION_VERSION = "factor-evaluation-v1"
FACTOR_PORTFOLIO_VERSION = "factor-research-portfolio-v1"
FACTOR_TRIAL_LEDGER_VERSION = "factor-trial-ledger-v1"

DISCLAIMER = (
    "FACTOR VALIDATION RESEARCH ONLY - NOT A STRATEGY OR TRADING SIGNAL")
HORIZONS_MS = {
    "15m": 900_000, "30m": 1_800_000, "1H": 3_600_000,
    "2H": 7_200_000, "4H": 14_400_000, "8H": 28_800_000,
    "24H": 86_400_000,
}
PURGE_EMBARGO_MS = HORIZONS_MS["24H"]
GRID_MS = 900_000
PORTFOLIO_COST_PER_TURNOVER = 0.0005
RAW_BUDGET = 2500
STRUCTURAL_BUDGET = 1500
SEMANTIC_BUDGET = 800
BEHAVIOR_BUDGET = 400
EVALUATION_BUDGET = 200
LOCKED_BUDGET = 60
LIBRARY_BUDGET = 20
MIN_EVENTS = 30

CLASSIFICATIONS = (
    "RETAIN_FACTOR_CANDIDATE", "RETAIN_DIAGNOSTIC_ONLY",
    "RETIRE_NO_RELATIONSHIP", "RETIRE_VALIDATION_REVERSAL",
    "RETIRE_LOCKED_VERIFICATION_FAILURE", "RETIRE_MULTIPLE_TESTING",
    "RETIRE_REDUNDANT", "RETIRE_EXCESSIVE_TURNOVER",
    "RETIRE_CONCENTRATED", "INSUFFICIENT_SAMPLE",
    "STRUCTURALLY_INVALID",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(*parts: object) -> str:
    return hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DatasetSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    identity: str

    @classmethod
    def from_path(cls, path: Path | str) -> "DatasetSnapshot":
        resolved = Path(path).resolve()
        sha = file_sha256(resolved)
        size = resolved.stat().st_size
        identity = stable_hash(
            "microstructure-snapshot-v1", sha, size,
            MICROSTRUCTURE_SCHEMA_VERSION, MICROSTRUCTURE_SOURCE_VERSION,
            MICROSTRUCTURE_FEATURE_VERSION)
        return cls(resolved, sha, size, identity)


class ControlledInterruption(RuntimeError):
    """Expected interruption used to prove checkpoint/resume behavior."""


class FactorTrialLedger:
    """Append-preserving SQLite record of every attempted expression."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=60000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS factor_runs(
                    run_id TEXT PRIMARY KEY, ledger_version TEXT NOT NULL,
                    dataset_identity TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
                    seed INTEGER NOT NULL, workers INTEGER NOT NULL,
                    stage TEXT NOT NULL, status TEXT NOT NULL,
                    eligibility_snapshot_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, report_json TEXT);
                CREATE TABLE IF NOT EXISTS factor_trials(
                    trial_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, factor_identity TEXT NOT NULL,
                    canonical_expression TEXT NOT NULL,
                    expression_ast TEXT NOT NULL, source_versions TEXT NOT NULL,
                    dataset_identity TEXT NOT NULL, instrument TEXT NOT NULL,
                    horizon TEXT NOT NULL, chronological_segment TEXT NOT NULL,
                    parameter_values TEXT NOT NULL,
                    feature_timestamps TEXT NOT NULL,
                    evaluation_version TEXT NOT NULL,
                    trial_family TEXT NOT NULL, parent_expressions TEXT NOT NULL,
                    structural_status TEXT NOT NULL, status TEXT NOT NULL,
                    rejection_reason TEXT, classification TEXT,
                    complexity INTEGER NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence));
                CREATE INDEX IF NOT EXISTS idx_factor_trials_run_status
                    ON factor_trials(run_id,status);
                CREATE TABLE IF NOT EXISTS factor_evaluations(
                    run_id TEXT NOT NULL, trial_id TEXT NOT NULL,
                    factor_identity TEXT NOT NULL, instrument TEXT NOT NULL,
                    segment TEXT NOT NULL, horizon TEXT NOT NULL,
                    metrics_json TEXT NOT NULL, raw_p_value REAL,
                    fdr_q_value REAL, bonferroni_p REAL,
                    PRIMARY KEY(run_id,trial_id,segment,horizon));
                CREATE TABLE IF NOT EXISTS factor_portfolio_exposures(
                    run_id TEXT NOT NULL, trial_id TEXT NOT NULL,
                    segment TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
                    score REAL, exposure REAL NOT NULL, turnover REAL NOT NULL,
                    gross_return REAL, cost_drag REAL NOT NULL, net_return REAL,
                    PRIMARY KEY(run_id,trial_id,segment,timestamp_ms));
                CREATE TABLE IF NOT EXISTS factor_library(
                    run_id TEXT NOT NULL, factor_identity TEXT NOT NULL,
                    entry_json TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(run_id,factor_identity));
                """
            )

    def begin_run(
        self, run_id: str, snapshot: DatasetSnapshot,
        eligibility: dict[str, Any], workers: int,
    ) -> None:
        policy = {
            "schema_version": FACTOR_SCHEMA_VERSION,
            "grammar_version": FACTOR_GRAMMAR_VERSION,
            "search_policy_version": FACTOR_SEARCH_POLICY_VERSION,
            "identity_version": FACTOR_IDENTITY_VERSION,
            "evaluation_version": FACTOR_EVALUATION_VERSION,
            "portfolio_version": FACTOR_PORTFOLIO_VERSION,
            "ledger_version": FACTOR_TRIAL_LEDGER_VERSION,
            "multiple_testing_version": MULTIPLE_TESTING_POLICY_VERSION,
            "budgets": {
                "raw": RAW_BUDGET, "structural": STRUCTURAL_BUDGET,
                "semantic": SEMANTIC_BUDGET, "behavior": BEHAVIOR_BUDGET,
                "evaluation": EVALUATION_BUDGET, "locked": LOCKED_BUDGET,
                "library": LIBRARY_BUDGET,
            },
        }
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO factor_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET
                   workers=excluded.workers,updated_at=excluded.updated_at""",
                (run_id, FACTOR_TRIAL_LEDGER_VERSION, snapshot.identity,
                 snapshot.sha256, SEMANTIC_SEED, workers, "INITIALIZED",
                 "RUNNING", json.dumps(eligibility, sort_keys=True),
                 json.dumps(policy, sort_keys=True), now, now, None))

    def stage(self, run_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT stage FROM factor_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return str(row["stage"]) if row else "MISSING"

    def set_stage(
        self, run_id: str, stage: str, *, status: str = "RUNNING",
        report: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE factor_runs SET stage=?,status=?,updated_at=?,
                   report_json=COALESCE(?,report_json) WHERE run_id=?""",
                (stage, status, utc_now(),
                 json.dumps(report, sort_keys=True) if report else None, run_id))

    def add_trial(
        self, run_id: str, generated: Any, snapshot: DatasetSnapshot,
        *, structural_status: str, rejection_reason: str | None,
    ) -> str:
        canonical = canonical_json(generated.node)
        identity = factor_identity(generated.node)
        trial_id = stable_hash(run_id, generated.sequence)
        source_versions = json.dumps({
            "microstructure_schema": MICROSTRUCTURE_SCHEMA_VERSION,
            "microstructure_source": MICROSTRUCTURE_SOURCE_VERSION,
            "microstructure_feature": MICROSTRUCTURE_FEATURE_VERSION,
        }, sort_keys=True)
        now = utc_now()
        ledger_status = (
            structural_status if rejection_reason else "GENERATED")
        classification = (
            "STRUCTURALLY_INVALID"
            if structural_status == "STRUCTURALLY_INVALID"
            else "RETIRE_REDUNDANT"
            if structural_status == "SEMANTIC_DUPLICATE" else None)
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO factor_trials(
                   trial_id,run_id,sequence,factor_identity,
                   canonical_expression,expression_ast,source_versions,
                   dataset_identity,instrument,horizon,chronological_segment,
                   parameter_values,feature_timestamps,evaluation_version,
                   trial_family,parent_expressions,structural_status,status,
                   rejection_reason,classification,complexity,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trial_id, run_id, generated.sequence, identity, canonical,
                 json.dumps(generated.node.to_dict(), sort_keys=True),
                 source_versions, snapshot.identity, generated.instrument,
                 "ALL_HORIZONS", "PENDING_CHRONOLOGICAL_SPLIT",
                 json.dumps(dict(generated.node.parameters), sort_keys=True),
                 json.dumps({"policy": "causal-source-timestamps-v1"},
                            sort_keys=True),
                 FACTOR_EVALUATION_VERSION, generated.trial_family,
                 json.dumps(generated.parent_identities),
                 structural_status,
                 ledger_status, rejection_reason, classification,
                 generated.node.complexity, now, now))
        return trial_id

    def normalize_generation_rejections(self, run_id: str) -> None:
        """Repair legacy/in-progress rejection labels idempotently."""
        with self.connect() as connection:
            connection.execute(
                """UPDATE factor_trials
                   SET status=structural_status,
                       classification=CASE
                         WHEN structural_status='STRUCTURALLY_INVALID'
                           THEN 'STRUCTURALLY_INVALID'
                         WHEN structural_status='SEMANTIC_DUPLICATE'
                           THEN 'RETIRE_REDUNDANT'
                         ELSE NULL END,
                       updated_at=?
                   WHERE run_id=? AND status='REJECTED'""",
                (utc_now(), run_id))

    def update_trial(
        self, trial_id: str, *, status: str,
        classification: str | None = None,
        reason: str | None = None,
        feature_timestamps: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE factor_trials SET status=?,
                   classification=COALESCE(?,classification),
                   rejection_reason=COALESCE(?,rejection_reason),
                   feature_timestamps=COALESCE(?,feature_timestamps),
                   updated_at=? WHERE trial_id=?""",
                (status, classification, reason,
                 json.dumps(feature_timestamps, sort_keys=True)
                 if feature_timestamps else None,
                 utc_now(), trial_id))

    def save_evaluation(
        self, run_id: str, trial_id: str, factor_id: str,
        instrument: str, segment: str, horizon: str,
        metrics: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO factor_evaluations
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, trial_id, factor_id, instrument, segment, horizon,
                 json.dumps(metrics, sort_keys=True, allow_nan=False),
                 metrics.get("raw_p_value"), metrics.get("fdr_q_value"),
                 metrics.get("bonferroni_p_value")))

    def save_exposures(
        self, run_id: str, trial_id: str, segment: str,
        rows: Iterable[tuple[int, float, float, float, float, float, float]],
    ) -> None:
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO factor_portfolio_exposures
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(run_id, trial_id, segment, *row) for row in rows])

    def trial_count(self, run_id: str) -> int:
        with self.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM factor_trials WHERE run_id=?",
                (run_id,)).fetchone()[0])

    def counts(self, run_id: str) -> dict[str, int]:
        with self.connect() as connection:
            result = {
                str(row["status"]): int(row["n"])
                for row in connection.execute(
                    """SELECT status,COUNT(*) n FROM factor_trials
                       WHERE run_id=? GROUP BY status""", (run_id,))
            }
            result["total_trials"] = int(connection.execute(
                "SELECT COUNT(*) FROM factor_trials WHERE run_id=?",
                (run_id,)).fetchone()[0])
            result["evaluations"] = int(connection.execute(
                "SELECT COUNT(*) FROM factor_evaluations WHERE run_id=?",
                (run_id,)).fetchone()[0])
            result["exposures"] = int(connection.execute(
                "SELECT COUNT(*) FROM factor_portfolio_exposures WHERE run_id=?",
                (run_id,)).fetchone()[0])
        return result


class EligibilityGate:
    """Translate authoritative Phase 6E per-feature eligibility into terminals."""

    def __init__(self, snapshot_path: Path) -> None:
        self.store = MicrostructureStore(snapshot_path)

    def snapshot(self) -> dict[str, Any]:
        actual = self.store.per_feature_eligibility()
        groups: dict[str, list[str]] = {}
        decisions: dict[str, Any] = {}
        funding = actual["feature_groups"]["settled_funding"]["instruments"]
        basis = actual["feature_groups"]["basis"]["instruments"]
        for instrument in sorted(funding):
            allowed = {"price"}
            funding_row = funding[instrument]
            basis_row = basis[instrument]
            if funding_row["source_data_status"] == "FORMAL_RESEARCH_READY":
                allowed.add("funding")
            if (instrument == "BTC-USDT-SWAP"
                    and basis_row["source_data_status"] ==
                    "FORMAL_RESEARCH_READY"):
                allowed.add("basis")
            groups[instrument] = sorted(allowed)
            decisions[instrument] = {
                "settled_funding": funding_row,
                "basis": basis_row,
            }
        return {
            "actual_phase6e_eligibility": actual,
            "formal_groups_by_instrument": groups,
            "decisions": decisions,
            "formal_terminals": {
                "funding": list(FUNDING_TERMINALS),
                "basis_btc_only": list(BASIS_TERMINALS),
                "price_context": list(PRICE_TERMINALS),
            },
            "blocked_terminals": list(BLOCKED_TERMINALS),
        }


def _rolling(values: np.ndarray, lookback: int, kind: str) -> np.ndarray:
    result = np.full(values.size, np.nan)
    minimum = max(2, lookback // 2)
    finite = np.isfinite(values)
    clean = np.where(finite, values, 0.0)
    cumulative = np.r_[0.0, np.cumsum(clean)]
    squared = np.r_[0.0, np.cumsum(clean * clean)]
    counts = np.r_[0, np.cumsum(finite.astype(np.int64))]
    indices = np.arange(values.size)
    starts = np.maximum(0, indices - lookback + 1)
    sums = cumulative[indices + 1] - cumulative[starts]
    sum_squares = squared[indices + 1] - squared[starts]
    window_counts = counts[indices + 1] - counts[starts]
    valid = window_counts >= minimum
    means = np.divide(
        sums, window_counts, out=np.zeros_like(sums), where=window_counts > 0)
    variance = np.divide(
        sum_squares - np.divide(
            sums * sums, window_counts, out=np.zeros_like(sums),
            where=window_counts > 0),
        window_counts - 1, out=np.zeros_like(sums),
        where=window_counts > 1)
    deviations = np.sqrt(np.maximum(0.0, variance))
    if kind == "mean":
        result[valid] = means[valid]
    elif kind == "std":
        result[valid] = deviations[valid]
    elif kind == "zscore":
        z_valid = valid & finite
        result[z_valid] = 0.0
        nonzero = z_valid & (deviations > 0)
        result[nonzero] = (
            values[nonzero] - means[nonzero]) / deviations[nonzero]
    elif kind == "rank":
        padded = np.r_[np.full(lookback - 1, np.nan), values]
        windows = np.lib.stride_tricks.sliding_window_view(
            padded, lookback)
        rank_counts = np.sum(
            windows <= values[:, None], axis=1)
        finite_counts = np.sum(np.isfinite(windows), axis=1)
        rank_valid = valid & finite
        result[rank_valid] = (
            rank_counts[rank_valid] / finite_counts[rank_valid])
    return result


def _lag(values: np.ndarray, count: int) -> np.ndarray:
    result = np.full(values.size, np.nan)
    if count < values.size:
        result[count:] = values[:-count]
    return result


def _asof(
    grid: np.ndarray, timestamps: np.ndarray, values: np.ndarray,
    *, maximum_age_ms: int | None = None,
) -> np.ndarray:
    result = np.full(grid.size, np.nan)
    if not timestamps.size:
        return result
    positions = np.searchsorted(timestamps, grid, side="right") - 1
    valid = positions >= 0
    result[valid] = values[positions[valid]]
    if maximum_age_ms is not None:
        age = np.full(grid.size, np.inf)
        age[valid] = grid[valid] - timestamps[positions[valid]]
        result[age > maximum_age_ms] = np.nan
    return result


class FactorData:
    """Causal 15-minute research grid sourced only from snapshot tables."""

    def __init__(self, path: Path, instrument: str) -> None:
        self.path = path
        self.instrument = instrument
        self.timestamps: np.ndarray
        self.mark: np.ndarray
        self.high: np.ndarray
        self.low: np.ndarray
        self.index: np.ndarray
        self._funding_ts: np.ndarray
        self._funding: np.ndarray
        self._basis_ts: np.ndarray
        self._basis_abs: np.ndarray
        self._basis_pct: np.ndarray
        self._basis_expansion: np.ndarray
        self._terminal_cache: dict[str, np.ndarray] = {}
        self._load()

    def _connect(self) -> sqlite3.Connection:
        result = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro", uri=True)
        result.row_factory = sqlite3.Row
        return result

    def _load(self) -> None:
        with self._connect() as connection:
            marks = connection.execute(
                """SELECT source_ts_ms,high,low,close
                   FROM mark_price_observations
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY source_ts_ms""", (self.instrument,)).fetchall()
            index_instrument = self.instrument.removesuffix("-SWAP")
            indexes = connection.execute(
                """SELECT source_ts_ms,close FROM index_price_observations
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY source_ts_ms""", (index_instrument,)).fetchall()
            funding = connection.execute(
                """SELECT funding_time_ms,funding_rate FROM funding_settled
                   WHERE instrument=? ORDER BY funding_time_ms""",
                (self.instrument,)).fetchall()
            basis = connection.execute(
                """SELECT bucket_ms,last_basis,last_basis_pct,expansion
                   FROM basis_aggregates
                   WHERE instrument=? AND resolution='1H'
                   ORDER BY bucket_ms""", (self.instrument,)).fetchall()
        buckets: dict[int, list[float]] = {}
        for row in marks:
            bucket = int(row["source_ts_ms"]) // GRID_MS * GRID_MS
            value = buckets.setdefault(bucket, [
                float(row["close"]), float(row["high"] or row["close"]),
                float(row["low"] or row["close"])])
            value[0] = float(row["close"])
            value[1] = max(value[1], float(row["high"] or row["close"]))
            value[2] = min(value[2], float(row["low"] or row["close"]))
        self.timestamps = np.asarray(sorted(buckets), dtype=np.int64)
        self.mark = np.asarray(
            [buckets[int(ts)][0] for ts in self.timestamps], dtype=float)
        self.high = np.asarray(
            [buckets[int(ts)][1] for ts in self.timestamps], dtype=float)
        self.low = np.asarray(
            [buckets[int(ts)][2] for ts in self.timestamps], dtype=float)
        index_ts = np.asarray(
            [int(row["source_ts_ms"]) for row in indexes], dtype=np.int64)
        index_values = np.asarray(
            [float(row["close"]) for row in indexes], dtype=float)
        self.index = _asof(
            self.timestamps, index_ts, index_values,
            maximum_age_ms=2 * GRID_MS)
        self._funding_ts = np.asarray(
            [int(row["funding_time_ms"]) for row in funding], dtype=np.int64)
        self._funding = np.asarray(
            [float(row["funding_rate"]) for row in funding], dtype=float)
        self._basis_ts = np.asarray(
            [int(row["bucket_ms"]) for row in basis], dtype=np.int64)
        self._basis_abs = np.asarray(
            [float(row["last_basis"]) for row in basis], dtype=float)
        self._basis_pct = np.asarray(
            [float(row["last_basis_pct"]) for row in basis], dtype=float)
        self._basis_expansion = np.asarray(
            [float(row["expansion"]) for row in basis], dtype=float)

    def _returns(self, values: np.ndarray, lookback: int) -> np.ndarray:
        prior = _lag(values, lookback)
        with np.errstate(divide="ignore", invalid="ignore"):
            result = values / prior - 1.0
        result[~np.isfinite(result)] = np.nan
        return result

    def terminal(self, node: FactorNode) -> np.ndarray:
        key = canonical_json(node)
        if key in self._terminal_cache:
            return self._terminal_cache[key].copy()
        name = str(node.terminal)
        lookback = int(node.params.get("lookback", 1))
        if name == "settled_funding_level":
            result = _asof(self.timestamps, self._funding_ts, self._funding)
        elif name == "time_since_settlement":
            positions = np.searchsorted(
                self._funding_ts, self.timestamps, side="right") - 1
            result = np.full(self.timestamps.size, np.nan)
            valid = positions >= 0
            result[valid] = (
                self.timestamps[valid] - self._funding_ts[positions[valid]]
            ) / 3_600_000
        elif name.startswith("funding_"):
            if name == "funding_change":
                event = self._funding - _lag(self._funding, 1)
            else:
                kind = {
                    "funding_rolling_mean": "mean",
                    "funding_rolling_std": "std",
                    "funding_zscore": "zscore",
                }[name]
                event = _rolling(self._funding, lookback, kind)
            result = _asof(self.timestamps, self._funding_ts, event)
        elif name in BASIS_TERMINALS:
            base = (
                self._basis_abs if name == "absolute_basis"
                else self._basis_expansion
                if name == "basis_expansion_contraction"
                else self._basis_pct)
            if name == "basis_change":
                base = base - _lag(base, 1)
            elif name.startswith("basis_rolling_") or name == "basis_zscore":
                kind = {
                    "basis_rolling_mean": "mean",
                    "basis_rolling_std": "std",
                    "basis_zscore": "zscore",
                }[name]
                base = _rolling(base, lookback, kind)
            result = _asof(
                self.timestamps, self._basis_ts, base,
                maximum_age_ms=2 * 3_600_000)
        elif name == "mark_return":
            result = self._returns(self.mark, lookback)
        elif name == "index_return":
            result = self._returns(self.index, lookback)
        elif name == "realized_volatility":
            result = _rolling(self._returns(self.mark, 1), lookback, "std")
        elif name == "atr_percentage":
            true_range = np.maximum(
                self.high - self.low,
                np.maximum(
                    np.abs(self.high - _lag(self.mark, 1)),
                    np.abs(self.low - _lag(self.mark, 1))))
            result = _rolling(true_range, lookback, "mean") / self.mark
        elif name in {"ma_slope", "price_to_ma_distance"}:
            average = _rolling(self.mark, lookback, "mean")
            result = (
                average / _lag(average, lookback) - 1.0
                if name == "ma_slope" else self.mark / average - 1.0)
        elif name == "bollinger_bandwidth":
            average = _rolling(self.mark, lookback, "mean")
            result = 4.0 * _rolling(self.mark, lookback, "std") / average
        elif name == "causal_regime_code":
            slope = self.terminal(FactorNode.term(
                "ma_slope", lookback=lookback))
            result = np.where(slope > 0.005, 1.0,
                              np.where(slope < -0.005, -1.0, 0.0))
            result[~np.isfinite(slope)] = np.nan
        elif name == "rolling_volume_ratio":
            # No complete, formal volume series spans these histories. Missing
            # remains missing and the trial becomes INSUFFICIENT_SAMPLE.
            result = np.full(self.timestamps.size, np.nan)
        else:
            result = np.full(self.timestamps.size, np.nan)
        result = np.asarray(result, dtype=float)
        result[~np.isfinite(result)] = np.nan
        self._terminal_cache[key] = result.copy()
        return result

    def evaluate(self, node: FactorNode) -> np.ndarray:
        node = canonicalize(node)
        if node.operator == "terminal":
            return self.terminal(node)
        child = self.evaluate(node.children[0])
        params = node.params
        if node.operator == "lag":
            return _lag(child, int(params["lag"]))
        if node.operator == "difference":
            return child - _lag(child, int(params["lag"]))
        if node.operator.startswith("rolling_"):
            kind = node.operator.removeprefix("rolling_")
            return _rolling(child, int(params["lookback"]), kind)
        if node.operator == "sign":
            return np.sign(child)
        if node.operator == "absolute":
            return np.abs(child)
        if node.operator == "negate":
            return -child
        if node.operator == "winsorize":
            zscore = _rolling(child, 64, "zscore")
            return np.clip(zscore, -float(params["limit"]), float(params["limit"]))
        if node.operator == "conditional":
            regime = self.terminal(FactorNode.term(
                "causal_regime_code", lookback=32))
            predicate = {
                "bull": regime > 0, "bear": regime < 0,
                "range": regime == 0,
                "high_volatility": self.terminal(FactorNode.term(
                    "realized_volatility", lookback=32)) >
                    _rolling(self.terminal(FactorNode.term(
                        "realized_volatility", lookback=32)), 64, "mean"),
            }[str(params["regime"])]
            return np.where(predicate, child, np.nan)
        right = self.evaluate(node.children[1])
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            if node.operator == "add":
                result = child + right
            elif node.operator == "subtract":
                result = child - right
            elif node.operator == "multiply":
                result = child * right
            elif node.operator == "safe_divide":
                finite_right = np.abs(right[np.isfinite(right)])
                scale = (
                    float(np.median(finite_right))
                    if finite_right.size else float("nan"))
                epsilon = max(1e-12, float(scale) * 1e-6
                              if np.isfinite(scale) else 1e-12)
                result = child / np.where(np.abs(right) >= epsilon, right, np.nan)
            elif node.operator == "minimum":
                result = np.minimum(child, right)
            elif node.operator == "maximum":
                result = np.maximum(child, right)
            elif node.operator == "bounded_interaction":
                result = np.tanh(child) * np.tanh(right)
            else:
                result = np.full(child.size, np.nan)
        result[~np.isfinite(result)] = np.nan
        return result

    def labels(self, horizon_ms: int) -> np.ndarray:
        steps = horizon_ms // GRID_MS
        future = _lag(self.mark[::-1], int(steps))[::-1]
        result = future / self.mark - 1.0
        result[~np.isfinite(result)] = np.nan
        return result


@dataclass(frozen=True)
class SegmentMasks:
    discovery: np.ndarray
    selection_validation: np.ndarray
    locked_verification: np.ndarray
    boundary_one_ms: int
    boundary_two_ms: int


def chronological_segments(
    timestamps: np.ndarray, usable: np.ndarray,
    *, purge_embargo_ms: int = PURGE_EMBARGO_MS,
) -> SegmentMasks:
    valid_times = timestamps[usable]
    if valid_times.size < 3:
        empty = np.zeros(timestamps.size, dtype=bool)
        return SegmentMasks(empty, empty.copy(), empty.copy(), 0, 0)
    boundary_one = int(valid_times[min(
        valid_times.size - 1, int(valid_times.size * 0.60))])
    boundary_two = int(valid_times[min(
        valid_times.size - 1, int(valid_times.size * 0.80))])
    discovery = usable & (timestamps < boundary_one - purge_embargo_ms)
    selection = usable & (timestamps >= boundary_one + purge_embargo_ms) & (
        timestamps < boundary_two - purge_embargo_ms)
    locked = usable & (timestamps >= boundary_two + purge_embargo_ms)
    return SegmentMasks(
        discovery, selection, locked, boundary_one, boundary_two)


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    result = np.empty(values.size, dtype=float)
    result[order] = np.arange(values.size, dtype=float)
    unique, inverse, counts = np.unique(
        values, return_inverse=True, return_counts=True)
    del unique
    sums = np.bincount(inverse, weights=result)
    return sums[inverse] / counts[inverse]


def _correlation(x: np.ndarray, y: np.ndarray) -> float | None:
    valid = np.isfinite(x) & np.isfinite(y)
    if int(valid.sum()) < 5:
        return None
    x, y = x[valid], y[valid]
    if np.std(x) == 0 or np.std(y) == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _p_value(correlation: float | None, count: int) -> float | None:
    if correlation is None or count < 5:
        return None
    bounded = min(0.999999, max(-0.999999, correlation))
    z = abs(math.atanh(bounded)) * math.sqrt(count - 3)
    return math.erfc(z / math.sqrt(2.0))


def _quantiles(scores: np.ndarray, returns: np.ndarray) -> tuple[list[float], float, float]:
    order = np.argsort(scores, kind="mergesort")
    groups = np.array_split(order, 5)
    means = [float(np.mean(returns[group])) if group.size else 0.0
             for group in groups]
    spread = means[-1] - means[0]
    direction = sum(
        1 if means[index] > means[index - 1] else -1
        for index in range(1, len(means))) / 4.0
    return means, spread, direction


def _causal_exposure(scores: np.ndarray) -> np.ndarray:
    exposure = np.zeros(scores.size)
    for index in range(64, scores.size):
        history = scores[max(0, index - 256):index]
        history = history[np.isfinite(history)]
        if history.size < 32 or not np.isfinite(scores[index]):
            continue
        lower, upper = np.quantile(history, [0.2, 0.8])
        if scores[index] >= upper:
            exposure[index] = 1.0
        elif scores[index] <= lower:
            exposure[index] = -1.0
    return exposure


def evaluate_segment(
    data: FactorData, scores: np.ndarray, mask: np.ndarray,
    horizon: str,
) -> tuple[dict[str, Any], list[tuple[int, float, float, float, float, float, float]]]:
    labels = data.labels(HORIZONS_MS[horizon])
    valid = mask & np.isfinite(scores) & np.isfinite(labels)
    x, y = scores[valid], labels[valid]
    count = int(x.size)
    if count < MIN_EVENTS:
        return ({
            "event_count": count, "status": "INSUFFICIENT_SAMPLE",
            "pearson_ic": None, "spearman_ic": None,
            "raw_p_value": None,
        }, [])
    pearson = _correlation(x, y)
    spearman = _correlation(_rank(x), _rank(y))
    quantiles, spread, monotonicity = _quantiles(x, y)
    # Daily IC distribution supplies IC mean/std/IR and temporal stability.
    day = data.timestamps[valid] // 86_400_000
    block_ics: list[float] = []
    for value in np.unique(day):
        block = day == value
        ic = _correlation(x[block], y[block])
        if ic is not None:
            block_ics.append(ic)
    ic_mean = float(np.mean(block_ics)) if block_ics else pearson
    ic_std = float(np.std(block_ics, ddof=1)) if len(block_ics) > 1 else 0.0
    ic_ir = ic_mean / ic_std if ic_std and ic_mean is not None else None
    sign_consistency = (
        sum(np.sign(value) == np.sign(ic_mean) for value in block_ics)
        / len(block_ics) if block_ics and ic_mean else 0.0)

    exposure_full = _causal_exposure(scores)
    # Non-overlapping returns: sample at the horizon step only.
    step = max(1, HORIZONS_MS[horizon] // GRID_MS)
    selected_indices = np.flatnonzero(valid)
    selected_indices = selected_indices[
        np.arange(selected_indices.size) % step == 0]
    exposure = exposure_full[selected_indices]
    gross = exposure * labels[selected_indices]
    turnover = np.abs(exposure - np.r_[0.0, exposure[:-1]])
    cost = turnover * PORTFOLIO_COST_PER_TURNOVER
    net = gross - cost
    sample_length, mean, std, skew, kurtosis = moments(net)
    periods_per_year = 365.25 * 86_400_000 / HORIZONS_MS[horizon]
    sharpe = (
        mean / std * math.sqrt(periods_per_year) if std > 0 else 0.0)
    cumulative = np.cumsum(net)
    drawdown = cumulative - np.maximum.accumulate(np.r_[0.0, cumulative])[-cumulative.size:]
    maximum_drawdown = abs(float(np.min(drawdown))) if drawdown.size else 0.0
    psr = probabilistic_sharpe_ratio(
        sharpe, 0.0, sample_length, skew, kurtosis)
    abs_returns = np.abs(net)
    concentration = (
        float(np.sum(np.sort(abs_returns)[-max(1, len(abs_returns) // 10):])
              / np.sum(abs_returns))
        if np.sum(abs_returns) > 0 else 0.0)
    regime = data.terminal(FactorNode.term(
        "causal_regime_code", lookback=32))[valid]
    regime_counts = [
        int(np.sum(regime == value)) for value in (-1.0, 0.0, 1.0)]
    regime_concentration = max(regime_counts) / count if count else 0.0
    rows = [
        (int(data.timestamps[index]), float(scores[index]),
         float(exposure_full[index]), float(turnover[position]),
         float(gross[position]), float(cost[position]), float(net[position]))
        for position, index in enumerate(selected_indices)
    ]
    metrics = {
        "status": "EVALUATED",
        "event_count": count,
        "pearson_ic": pearson,
        "spearman_ic": spearman,
        "ic_mean": ic_mean,
        "ic_standard_deviation": ic_std,
        "ic_information_ratio": ic_ir,
        "quantile_returns": quantiles,
        "top_minus_bottom_spread": spread,
        "monotonicity": monotonicity,
        "sign_consistency": sign_consistency,
        "turnover": float(np.mean(turnover)) if turnover.size else 0.0,
        "temporal_concentration": concentration,
        "regime_concentration": regime_concentration,
        "instrument_consistency": None,
        "horizon_consistency": None,
        "raw_p_value": _p_value(spearman, count),
        "portfolio": {
            "construction": "causal 20/80 quantile, fixed unit notional",
            "return_treatment": "non-overlapping at evaluated horizon",
            "cost_per_unit_turnover": PORTFOLIO_COST_PER_TURNOVER,
            "sample_length": sample_length,
            "gross_return": float(np.sum(gross)),
            "cost_drag": float(np.sum(cost)),
            "net_return": float(np.sum(net)),
            "sharpe": sharpe,
            "skew": skew,
            "kurtosis": kurtosis,
            "maximum_drawdown": maximum_drawdown,
            "time_in_market": float(np.mean(exposure != 0))
            if exposure.size else 0.0,
            "psr": psr,
        },
    }
    return metrics, rows


def _behavior_key(scores: np.ndarray, mask: np.ndarray) -> tuple[bytes, bytes]:
    values = scores[mask & np.isfinite(scores)]
    if values.size == 0:
        return b"", b""
    rounded = np.round(values, 10).tobytes()
    lower, upper = np.quantile(values, [0.2, 0.8])
    assignments = np.where(values <= lower, -1,
                           np.where(values >= upper, 1, 0)).astype(np.int8)
    return hashlib.sha256(rounded).digest(), hashlib.sha256(
        assignments.tobytes()).digest()


def _family(node: FactorNode) -> str:
    groups = set(node.source_groups)
    if groups == {"funding"}:
        return "funding-only"
    if groups == {"basis"}:
        return "basis-only"
    if groups == {"funding", "price"}:
        return "funding-x-price-context"
    if groups == {"basis", "price"}:
        return "basis-x-price-context"
    if groups == {"funding", "basis"}:
        return "funding-x-basis"
    return "price-context-only"


def _apply_horizon_consistency(
    metrics_by_horizon: dict[str, dict[str, Any]],
) -> float:
    signs = [
        int(np.sign(metric["spearman_ic"]))
        for metric in metrics_by_horizon.values()
        if metric.get("status") == "EVALUATED"
        and metric.get("spearman_ic") not in (None, 0)]
    consistency = (
        max(signs.count(1), signs.count(-1)) / len(signs)
        if signs else 0.0)
    for metric in metrics_by_horizon.values():
        metric["horizon_consistency"] = consistency
    return consistency


class FactorAutoResearch:
    """End-to-end deterministic Phase 6F coordinator."""

    def __init__(
        self, snapshot_path: Path | str, ledger_path: Path | str,
        *, workers: int = 2, seed: int = SEMANTIC_SEED,
    ) -> None:
        if workers not in (1, 2):
            raise ValueError("workers must be 1 or 2")
        if seed != SEMANTIC_SEED:
            raise ValueError("Phase 6F semantic seed is fixed")
        self.snapshot = DatasetSnapshot.from_path(snapshot_path)
        self.ledger = FactorTrialLedger(ledger_path)
        self.workers = workers
        self.seed = seed
        self.eligibility = EligibilityGate(self.snapshot.path).snapshot()
        self.run_id = stable_hash(
            FACTOR_SEARCH_POLICY_VERSION, self.snapshot.identity, seed)

    def _memory_mb(self) -> float | None:
        if resource is None:
            try:
                class MemoryCounters(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.c_ulong),
                        ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]
                counters = MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
                get_memory.argtypes = [
                    wintypes.HANDLE, ctypes.POINTER(MemoryCounters),
                    wintypes.DWORD]
                get_memory.restype = wintypes.BOOL
                success = get_memory(
                    ctypes.windll.kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb)
                return (
                    counters.PeakWorkingSetSize / (1024 * 1024)
                    if success else None)
            except (AttributeError, OSError):
                return None
        try:
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return float(value / 1024 if value > 1024 * 1024 else value)
        except Exception:
            return None

    def run(
        self, *, interrupt_after: str | None = None,
        report_path: Path | None = None,
        library_path: Path | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        self.ledger.initialize()
        self.ledger.begin_run(
            self.run_id, self.snapshot, self.eligibility, self.workers)
        groups = {
            instrument: set(values)
            for instrument, values in self.eligibility[
                "formal_groups_by_instrument"].items()
        }
        generated = deterministic_generate(groups, raw_budget=RAW_BUDGET)
        semantic_seen: dict[tuple[str, str], str] = {}
        usable: list[tuple[Any, str]] = []
        structural_rejects = 0
        semantic_duplicates = 0
        for item in generated:
            reasons = validate_expression(
                item.node, instrument=item.instrument,
                eligible_groups=groups[item.instrument])
            structural_reason = "|".join(reasons) if reasons else None
            identity_key = (item.instrument, factor_identity(item.node))
            if structural_reason:
                structural_rejects += 1
                status = "STRUCTURALLY_INVALID"
            elif identity_key in semantic_seen:
                semantic_duplicates += 1
                structural_reason = "SEMANTIC_DUPLICATE"
                status = "SEMANTIC_DUPLICATE"
            elif len(usable) >= SEMANTIC_BUDGET:
                structural_reason = "SEMANTIC_UNIQUE_BUDGET_EXHAUSTED"
                status = "BUDGET_REJECTED"
            elif len(usable) >= STRUCTURAL_BUDGET:
                structural_reason = "STRUCTURAL_BUDGET_EXHAUSTED"
                status = "BUDGET_REJECTED"
            else:
                status = "STRUCTURALLY_VALID"
            trial_id = self.ledger.add_trial(
                self.run_id, item, self.snapshot,
                structural_status=status,
                rejection_reason=structural_reason)
            if status == "STRUCTURALLY_VALID":
                semantic_seen[identity_key] = trial_id
                usable.append((item, trial_id))
        self.ledger.normalize_generation_rejections(self.run_id)
        self.ledger.set_stage(self.run_id, "GENERATED")
        if interrupt_after == "generation":
            self.ledger.set_stage(
                self.run_id, "GENERATED", status="INTERRUPTED")
            raise ControlledInterruption(
                "controlled interruption after durable generation checkpoint")

        data_by_instrument = {
            instrument: FactorData(self.snapshot.path, instrument)
            for instrument in groups
        }
        scored: list[dict[str, Any]] = []
        behavior_seen: dict[tuple[str, bytes, bytes], dict[str, Any]] = {}
        behavior_duplicates = 0
        for item, trial_id in usable:
            data = data_by_instrument[item.instrument]
            scores = data.evaluate(item.node)
            one_hour = data.labels(HORIZONS_MS["1H"])
            available = np.isfinite(scores) & np.isfinite(one_hour)
            segments = chronological_segments(data.timestamps, available)
            finite_positions = np.flatnonzero(np.isfinite(scores))
            self.ledger.update_trial(
                trial_id, status="FACTOR_VALUES_COMPUTED",
                feature_timestamps={
                    "first_factor_timestamp_ms": (
                        int(data.timestamps[finite_positions[0]])
                        if finite_positions.size else None),
                    "last_factor_timestamp_ms": (
                        int(data.timestamps[finite_positions[-1]])
                        if finite_positions.size else None),
                    "discovery_selection_boundary_ms":
                        segments.boundary_one_ms,
                    "selection_locked_boundary_ms":
                        segments.boundary_two_ms,
                    "purge_embargo_ms": PURGE_EMBARGO_MS,
                })
            count = int(segments.discovery.sum())
            if count < MIN_EVENTS:
                self.ledger.update_trial(
                    trial_id, status="INSUFFICIENT_SAMPLE",
                    classification="INSUFFICIENT_SAMPLE",
                    reason="DISCOVERY_SAMPLE_BELOW_MINIMUM")
                continue
            key = (item.instrument,) + _behavior_key(
                scores, segments.discovery)
            if key in behavior_seen:
                previous = behavior_seen[key]
                if item.node.complexity < previous["item"].node.complexity:
                    self.ledger.update_trial(
                        previous["trial_id"], status="BEHAVIOR_DUPLICATE",
                        classification="RETIRE_REDUNDANT",
                        reason="BEHAVIOR_DUPLICATE_SIMPLER_REPRESENTATIVE")
                    behavior_seen[key] = {
                        "item": item, "trial_id": trial_id, "scores": scores,
                        "segments": segments}
                else:
                    self.ledger.update_trial(
                        trial_id, status="BEHAVIOR_DUPLICATE",
                        classification="RETIRE_REDUNDANT",
                        reason="BEHAVIOR_DUPLICATE")
                behavior_duplicates += 1
                continue
            record = {
                "item": item, "trial_id": trial_id, "scores": scores,
                "segments": segments}
            behavior_seen[key] = record
        behavior_candidates = list(behavior_seen.values())
        # Discovery-only deterministic beam score and QD family interleave.
        for record in behavior_candidates:
            data = data_by_instrument[record["item"].instrument]
            mask = record["segments"].discovery
            labels = data.labels(HORIZONS_MS["1H"])
            ic = _correlation(
                record["scores"][mask], labels[mask])
            record["beam_score"] = (
                abs(ic or 0.0) / max(1, record["item"].node.complexity))
            record["source_family"] = _family(record["item"].node)
        family_buckets: dict[str, list[dict[str, Any]]] = {}
        for record in behavior_candidates:
            family_buckets.setdefault(record["source_family"], []).append(record)
        for values in family_buckets.values():
            values.sort(key=lambda record: (
                -record["beam_score"], record["item"].node.complexity,
                factor_identity(record["item"].node),
                record["item"].instrument))
        behavior_set: list[dict[str, Any]] = []
        while len(behavior_set) < BEHAVIOR_BUDGET and any(
                family_buckets.values()):
            for family in sorted(family_buckets):
                if (family_buckets[family]
                        and len(behavior_set) < BEHAVIOR_BUDGET):
                    behavior_set.append(family_buckets[family].pop(0))
        for record in behavior_candidates:
            if record not in behavior_set:
                self.ledger.update_trial(
                    record["trial_id"], status="BEHAVIOR_BUDGET_REJECTED",
                    classification="RETIRE_REDUNDANT",
                    reason="BEHAVIOR_UNIQUE_BUDGET_EXHAUSTED")
        evaluation_buckets: dict[str, list[dict[str, Any]]] = {}
        for record in behavior_set:
            evaluation_buckets.setdefault(
                record["source_family"], []).append(record)
        evaluation_set: list[dict[str, Any]] = []
        while len(evaluation_set) < EVALUATION_BUDGET and any(
                evaluation_buckets.values()):
            for family in sorted(evaluation_buckets):
                if (evaluation_buckets[family]
                        and len(evaluation_set) < EVALUATION_BUDGET):
                    evaluation_set.append(evaluation_buckets[family].pop(0))
        for record in behavior_set:
            if record not in evaluation_set:
                self.ledger.update_trial(
                    record["trial_id"], status="BEAM_NOT_SELECTED",
                    classification="RETIRE_NO_RELATIONSHIP",
                    reason="DISCOVERY_BEAM_BUDGET_OR_WEAK_RELATIONSHIP")

        discovery_records: list[dict[str, Any]] = []
        all_p_values: list[float | None] = []
        p_locations: list[tuple[dict[str, Any], str]] = []
        for record in evaluation_set:
            item = record["item"]
            data = data_by_instrument[item.instrument]
            metrics_by_horizon: dict[str, Any] = {}
            for horizon in HORIZONS_MS:
                metrics, rows = evaluate_segment(
                    data, record["scores"],
                    record["segments"].discovery, horizon)
                metrics_by_horizon[horizon] = metrics
                if metrics["status"] == "EVALUATED":
                    self.ledger.save_exposures(
                        self.run_id, record["trial_id"], "DISCOVERY", rows)
                all_p_values.append(metrics.get("raw_p_value"))
                p_locations.append((record, horizon))
            record["discovery"] = metrics_by_horizon
            discovery_records.append(record)
        # Complete applicable family means every evaluated factor/horizon,
        # successful or insufficient (None p-values remain explicitly present).
        family_size = sum(value is not None for value in all_p_values)
        q_values: list[float | None] = [None] * len(all_p_values)
        bh_family_sizes: dict[str, int] = {}
        for record in discovery_records:
            _apply_horizon_consistency(record["discovery"])
        identity_groups: dict[str, list[dict[str, Any]]] = {}
        for record in discovery_records:
            identity_groups.setdefault(
                factor_identity(record["item"].node), []).append(record)
        for records in identity_groups.values():
            signs = []
            for record in records:
                value = record["discovery"]["1H"].get("spearman_ic")
                if value:
                    signs.append(int(np.sign(value)))
            consistency = (
                max(signs.count(1), signs.count(-1)) / len(signs)
                if len(signs) >= 2 else None)
            for record in records:
                for metric in record["discovery"].values():
                    metric["instrument_consistency"] = consistency
                    metric["instrument_consistency_count"] = len(signs)
        for family in sorted({
                record["source_family"] for record, _ in p_locations}):
            positions = [
                index for index, (record, _) in enumerate(p_locations)
                if record["source_family"] == family]
            adjusted = benjamini_hochberg(
                [all_p_values[index] for index in positions])
            bh_family_sizes[family] = sum(
                all_p_values[index] is not None for index in positions)
            for index, value in zip(positions, adjusted):
                q_values[index] = value
        for (record, horizon), q_value in zip(p_locations, q_values):
            metrics = record["discovery"][horizon]
            metrics["fdr_q_value"] = q_value
            metrics["bonferroni_p_value"] = bonferroni(
                metrics.get("raw_p_value"), family_size)
            item = record["item"]
            self.ledger.save_evaluation(
                self.run_id, record["trial_id"], factor_identity(item.node),
                item.instrument, "DISCOVERY", horizon, metrics)
        # Freeze on multi-horizon discovery evidence before selection validation.
        selection_set = []
        for record in discovery_records:
            evaluated = [value for value in record["discovery"].values()
                         if value["status"] == "EVALUATED"]
            directions = [np.sign(value["spearman_ic"])
                          for value in evaluated
                          if abs(value["spearman_ic"] or 0.0) >= 0.01]
            consistent = (
                max(directions.count(1.0), directions.count(-1.0))
                if directions else 0)
            if consistent >= 2:
                selection_set.append(record)
            else:
                self.ledger.update_trial(
                    record["trial_id"], status="DISCOVERY_RETIRED",
                    classification="RETIRE_NO_RELATIONSHIP",
                    reason="NO_MULTI_HORIZON_DISCOVERY_RELATIONSHIP")
        selection_set.sort(key=lambda record: (
            -record["beam_score"], factor_identity(record["item"].node),
            record["item"].instrument))
        selection_not_opened = selection_set[120:]
        selection_set = selection_set[:120]
        for record in selection_not_opened:
            self.ledger.update_trial(
                record["trial_id"], status="SELECTION_BUDGET_REJECTED",
                classification="RETIRE_NO_RELATIONSHIP",
                reason="SELECTION_VALIDATION_BUDGET_EXHAUSTED")
        locked_set: list[dict[str, Any]] = []
        for record in selection_set:
            item = record["item"]
            data = data_by_instrument[item.instrument]
            record["selection_validation"] = {}
            reversals = 0
            consistent_horizons = 0
            for horizon in HORIZONS_MS:
                metrics, rows = evaluate_segment(
                    data, record["scores"],
                    record["segments"].selection_validation, horizon)
                record["selection_validation"][horizon] = metrics
                self.ledger.save_evaluation(
                    self.run_id, record["trial_id"], factor_identity(item.node),
                    item.instrument, "SELECTION_VALIDATION", horizon, metrics)
                if metrics["status"] == "EVALUATED":
                    self.ledger.save_exposures(
                        self.run_id, record["trial_id"],
                        "SELECTION_VALIDATION", rows)
                    discovery_ic = record["discovery"][horizon].get(
                        "spearman_ic")
                    if discovery_ic and metrics.get("spearman_ic"):
                        if np.sign(discovery_ic) != np.sign(
                                metrics["spearman_ic"]):
                            reversals += 1
                        else:
                            consistent_horizons += 1
            _apply_horizon_consistency(record["selection_validation"])
            for horizon, metrics in record["selection_validation"].items():
                self.ledger.save_evaluation(
                    self.run_id, record["trial_id"], factor_identity(item.node),
                    item.instrument, "SELECTION_VALIDATION", horizon, metrics)
            if reversals <= 2 and consistent_horizons >= 2:
                locked_set.append(record)
            else:
                self.ledger.update_trial(
                    record["trial_id"], status="VALIDATION_RETIRED",
                    classification="RETIRE_VALIDATION_REVERSAL",
                    reason="MATERIAL_SELECTION_VALIDATION_REVERSAL")
        locked_set.sort(key=lambda record: (
            -record["beam_score"], record["item"].node.complexity,
            factor_identity(record["item"].node)))
        locked_set = locked_set[:LOCKED_BUDGET]
        for record in locked_set:
            item = record["item"]
            data = data_by_instrument[item.instrument]
            record["locked_verification"] = {}
            for horizon in HORIZONS_MS:
                metrics, rows = evaluate_segment(
                    data, record["scores"],
                    record["segments"].locked_verification, horizon)
                record["locked_verification"][horizon] = metrics
                self.ledger.save_evaluation(
                    self.run_id, record["trial_id"], factor_identity(item.node),
                    item.instrument, "LOCKED_VERIFICATION", horizon, metrics)
                if metrics["status"] == "EVALUATED":
                    self.ledger.save_exposures(
                        self.run_id, record["trial_id"],
                        "LOCKED_VERIFICATION", rows)
            _apply_horizon_consistency(record["locked_verification"])
            for horizon, metrics in record["locked_verification"].items():
                self.ledger.save_evaluation(
                    self.run_id, record["trial_id"], factor_identity(item.node),
                    item.instrument, "LOCKED_VERIFICATION", horizon, metrics)

        # Correlation and DSR use all complete statistical trials, not survivors.
        return_vectors: dict[str, np.ndarray] = {}
        trial_sharpes: list[float] = []
        for record in discovery_records:
            metric = record["discovery"]["1H"]
            if metric["status"] == "EVALUATED":
                trial_sharpes.append(metric["portfolio"]["sharpe"])
                data = data_by_instrument[record["item"].instrument]
                indices = np.flatnonzero(record["segments"].discovery)
                labels = data.labels(HORIZONS_MS["1H"])[indices]
                exposures = _causal_exposure(record["scores"])[indices]
                return_vectors[record["trial_id"]] = exposures * labels
        clusters, cluster_count = correlation_clusters(return_vectors)
        effective_trials = max(1, cluster_count)
        raw_trial_count = self.ledger.trial_count(self.run_id)
        library: list[dict[str, Any]] = []
        retained_clusters: set[int] = set()
        diagnostic_only = 0
        retirement_counts: dict[str, int] = {}
        for record in locked_set:
            item = record["item"]
            locked_metrics = [
                value for value in record["locked_verification"].values()
                if value["status"] == "EVALUATED"]
            stable = [
                value for horizon, value in record["locked_verification"].items()
                if value["status"] == "EVALUATED"
                and record["discovery"][horizon].get("spearman_ic")
                and value.get("spearman_ic")
                and np.sign(record["discovery"][horizon]["spearman_ic"]) ==
                np.sign(value["spearman_ic"])]
            one_hour = record["locked_verification"]["1H"]
            if one_hour["status"] == "EVALUATED":
                portfolio = one_hour["portfolio"]
                dsr, benchmark = deflated_sharpe_ratio(
                    portfolio["sharpe"], portfolio["sample_length"],
                    portfolio["skew"], portfolio["kurtosis"],
                    effective_trials=effective_trials,
                    trial_sharpes=trial_sharpes)
                portfolio["dsr"] = dsr
                portfolio["dsr_benchmark_sharpe"] = benchmark
                portfolio["raw_trial_count"] = raw_trial_count
                portfolio["effective_trial_count"] = effective_trials
            else:
                portfolio = {"sharpe": 0.0, "psr": None, "dsr": None}
            spreads = [
                value["top_minus_bottom_spread"] for value in locked_metrics]
            severe_concentration = any(
                value["temporal_concentration"] > 0.65
                or value["regime_concentration"] > 0.85
                for value in locked_metrics)
            excessive_turnover = any(
                value["turnover"] > 0.75 for value in locked_metrics)
            fdr_values = [
                value.get("fdr_q_value")
                for value in record["discovery"].values()
                if value.get("fdr_q_value") is not None]
            passes = (
                portfolio.get("dsr") is not None
                and portfolio["dsr"] >= 0.95
                and portfolio["sharpe"] > 0
                and sum(value > 0 for value in spreads) >= 2
                and len(stable) >= 2
                and not excessive_turnover
                and not severe_concentration)
            if passes:
                classification = "RETAIN_FACTOR_CANDIDATE"
            elif excessive_turnover:
                classification = "RETIRE_EXCESSIVE_TURNOVER"
            elif severe_concentration:
                classification = "RETIRE_CONCENTRATED"
            elif len(stable) >= 2 and portfolio["sharpe"] > 0:
                classification = "RETAIN_DIAGNOSTIC_ONLY"
                diagnostic_only += 1
            elif len(stable) < 2 or portfolio["sharpe"] <= 0:
                classification = "RETIRE_LOCKED_VERIFICATION_FAILURE"
            else:
                classification = "RETIRE_MULTIPLE_TESTING"
            cluster = clusters.get(record["trial_id"])
            if classification.startswith("RETAIN_") and (
                    cluster is not None and cluster in retained_clusters):
                classification = "RETIRE_REDUNDANT"
            elif classification.startswith("RETAIN_") and cluster is not None:
                retained_clusters.add(cluster)
            retirement_counts[classification] = (
                retirement_counts.get(classification, 0) + 1)
            self.ledger.update_trial(
                record["trial_id"], status="CLASSIFIED",
                classification=classification)
            if classification.startswith("RETAIN_"):
                entry = {
                    "factor_identity": factor_identity(item.node),
                    "canonical_expression": json.loads(canonical_json(item.node)),
                    "plain_language_interpretation":
                        expression_plain_language(item.node),
                    "economic_hypothesis": (
                        "Persistent carry or basis pressure may contain "
                        "state-dependent information about subsequent mark "
                        "returns; this is a research hypothesis, not a signal."),
                    "required_sources": list(item.node.source_groups),
                    "valid_instruments": [item.instrument],
                    "expected_update_frequency": "15m causal research grid",
                    "complexity": item.node.complexity,
                    "turnover": one_hour.get("turnover"),
                    "ic_statistics": {
                        "discovery": record["discovery"],
                        "selection_validation":
                            record["selection_validation"],
                        "locked_verification":
                            record["locked_verification"],
                    },
                    "quantile_statistics": {
                        horizon: value.get("quantile_returns")
                        for horizon, value in
                        record["locked_verification"].items()},
                    "psr": portfolio.get("psr"),
                    "dsr": portfolio.get("dsr"),
                    "fdr_q_values": fdr_values,
                    "correlation_cluster":
                        cluster,
                    "source_family": record["source_family"],
                    "cross_asset_consistency": record[
                        "discovery"]["1H"].get("instrument_consistency"),
                    "cross_asset_instrument_count": record[
                        "discovery"]["1H"].get(
                            "instrument_consistency_count", 0),
                    "asset_specific_economic_justification": (
                        "The bounded beam retained only this instrument form; "
                        "it remains diagnostic-only unless future independent "
                        "cross-asset evidence is collected."
                        if record["discovery"]["1H"].get(
                            "instrument_consistency_count", 0) < 2 else None),
                    "known_limitations": [
                        "locked verification is not forward OOT",
                        "shorter ETH/SOL funding history reduces power",
                        "diagnostic portfolio is non-deployable",
                    ],
                    "current_status": classification,
                    "earliest_permitted_next_action": (
                        "Independent future forward OOT observation only"),
                }
                library.append(entry)
        library.sort(key=lambda entry: (
            entry["current_status"] != "RETAIN_FACTOR_CANDIDATE",
            -(entry["dsr"] or 0.0), entry["complexity"],
            entry["factor_identity"]))
        library = library[:LIBRARY_BUDGET]
        with self.ledger.connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO factor_library VALUES(?,?,?,?)",
                [(self.run_id, entry["factor_identity"],
                  json.dumps(entry, sort_keys=True),
                  entry["current_status"]) for entry in library])

        # PBO blocks are based on discovery daily portfolio returns when enough.
        pbo_vectors = []
        for vector in return_vectors.values():
            blocks = np.array_split(vector[np.isfinite(vector)], 10)
            if all(block.size for block in blocks):
                pbo_vectors.append([float(np.mean(block)) for block in blocks])
        pbo = pbo_from_blocks(
            np.asarray(pbo_vectors, dtype=float)
            if pbo_vectors else np.empty((0, 0)))
        source_results: dict[str, dict[str, int]] = {}
        for family in (
            "funding-only", "basis-only", "funding-x-price-context",
            "basis-x-price-context", "funding-x-basis",
            "price-context-only"):
            source_results[family] = {
                "fully_evaluated": sum(
                    record["source_family"] == family
                    for record in discovery_records),
                "selection_validation": sum(
                    record["source_family"] == family
                    for record in selection_set),
                "locked_verification": sum(
                    record["source_family"] == family
                    for record in locked_set),
                "retained": sum(
                    entry["source_family"] == family
                    for entry in library),
            }
        runtime = time.perf_counter() - started
        counts = self.ledger.counts(self.run_id)
        report = {
            "disclaimer": DISCLAIMER,
            "run_id": self.run_id,
            "versions": {
                "schema": FACTOR_SCHEMA_VERSION,
                "grammar": FACTOR_GRAMMAR_VERSION,
                "search_policy": FACTOR_SEARCH_POLICY_VERSION,
                "identity": FACTOR_IDENTITY_VERSION,
                "evaluation": FACTOR_EVALUATION_VERSION,
                "portfolio": FACTOR_PORTFOLIO_VERSION,
                "trial_ledger": FACTOR_TRIAL_LEDGER_VERSION,
                "multiple_testing": MULTIPLE_TESTING_POLICY_VERSION,
            },
            "dataset_snapshot": {
                "identity": self.snapshot.identity,
                "sha256": self.snapshot.sha256,
                "size_bytes": self.snapshot.size_bytes,
            },
            "eligibility_snapshot": self.eligibility,
            "generation": {
                "semantic_seed": self.seed,
                "workers": self.workers,
                "raw_generated_expressions": len(generated),
                "structural_rejections": structural_rejects,
                "semantic_duplicates": semantic_duplicates,
                "structurally_valid": len(usable),
                "behavior_duplicates": behavior_duplicates,
                "behavior_unique": len(behavior_set),
                "fully_evaluated": len(discovery_records),
                "selection_validation": len(selection_set),
                "locked_verification": len(locked_set),
                "correlation_clusters": cluster_count,
            },
            "multiple_testing": {
                "bh_fdr_complete_family_size": family_size,
                "bh_fdr_family_sizes": bh_family_sizes,
                "raw_trial_count": raw_trial_count,
                "effective_trial_count": effective_trials,
                "pbo": pbo,
            },
            "retained_candidate_count": sum(
                entry["current_status"] == "RETAIN_FACTOR_CANDIDATE"
                for entry in library),
            "diagnostic_only_count": diagnostic_only,
            "retirement_reasons": retirement_counts,
            "source_family_results": source_results,
            "factor_library": library,
            "runtime_seconds": runtime,
            "peak_memory_mb": self._memory_mb(),
            "ledger_counts": counts,
            "safety": {
                "blocked_sources_excluded": list(BLOCKED_TERMINALS),
                "old_ohlcv_holdout_oot_accessed": False,
                "strategy_generated": False,
                "paper_or_live_order_created": False,
                "production_deployment": False,
            },
        }
        self.ledger.set_stage(
            self.run_id, "COMPLETE", status="COMPLETED", report=report)
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True),
                encoding="utf-8")
        if library_path:
            library_path.parent.mkdir(parents=True, exist_ok=True)
            library_path.write_text(
                json.dumps({
                    "version": "factor-library-v1",
                    "disclaimer": DISCLAIMER,
                    "entries": library,
                }, indent=2, sort_keys=True), encoding="utf-8")
        return report
