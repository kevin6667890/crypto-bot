"""Phase 6G dependency-aware audit of the frozen Phase 6F experiment.

The Phase 6F generator is deliberately not imported. This module reads the
frozen trial manifest and snapshot in SQLite read-only mode, maps every trial
to a new audit identity, and performs source-native inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Iterable

import numpy as np

from .factor_autoresearch import (
    BLOCKED_TERMINALS,
    GRID_MS,
    HORIZONS_MS,
    MIN_EVENTS,
    PORTFOLIO_COST_PER_TURNOVER,
    FactorData,
    _causal_exposure,
    _family,
    chronological_segments,
)
from .factor_expression import FactorNode, factor_identity
from .factor_statistics import (
    benjamini_hochberg,
    deflated_sharpe_ratio,
    expected_maximum_sharpe,
    moments,
    probabilistic_sharpe_ratio,
)


FACTOR_STATISTICAL_AUDIT_VERSION = "factor-statistical-validity-audit-v1"
NATIVE_EVENT_POLICY_VERSION = "factor-native-event-inference-v1"
DEPENDENCE_POLICY_VERSION = "factor-return-dependence-v1"
FDR_FAMILY_AUDIT_VERSION = "factor-fdr-family-audit-v1"
PHASE6F_DATASET_IDENTITY = (
    "d9859fe023cd4aa6675cfa351ce93d616ecaf2c625e16e907cc6170d0958c8e7")
PHASE6F_RUN_ID = (
    "f438f23a2d5c5f28865cff773c1e66d954dd64b6b3c9250f6e2bc46fd22a9444")
PHASE6F_MANIFEST_IDENTITY = (
    "b6303fd351386f4d6c8baa62778f7c3e2906d3f9daea722f13c77bc098a9188a")
PHASE6F_RAW_TRIALS = 2500
PHASE6F_EFFECTIVE_CLUSTERS = 175
BLOCK_BOOTSTRAP_SEED = 20260727
BLOCK_BOOTSTRAP_REPETITIONS = 200
YEAR_MS = 365.25 * 86_400_000
DISCLAIMER = (
    "STATISTICAL VALIDITY AUDIT ONLY - NOT A STRATEGY OR TRADING SIGNAL")


def stable_hash(*parts: object) -> str:
    return hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


@dataclass(frozen=True)
class FrozenTrial:
    sequence: int
    trial_id: str
    factor_id: str
    instrument: str
    node: FactorNode
    lineage: str
    parent_expressions: tuple[str, ...]
    structural_status: str
    phase6f_status: str
    phase6f_classification: str | None

    @property
    def statistically_applicable(self) -> bool:
        return self.structural_status != "STRUCTURALLY_INVALID"


@dataclass(frozen=True)
class FrozenExperiment:
    ledger_path: Path
    snapshot_path: Path
    report_path: Path
    run_id: str
    dataset_identity: str
    dataset_sha256: str
    manifest_identity: str
    ledger_sha256: str
    trials: tuple[FrozenTrial, ...]
    selection_trial_ids: frozenset[str]
    locked_trial_ids: frozenset[str]
    phase6f_report: dict[str, Any]

    @classmethod
    def load(
        cls, ledger_path: Path | str, snapshot_path: Path | str,
        report_path: Path | str,
    ) -> "FrozenExperiment":
        ledger = Path(ledger_path).resolve()
        snapshot = Path(snapshot_path).resolve()
        report_file = Path(report_path).resolve()
        report = json.loads(report_file.read_text(encoding="utf-8"))
        connection = sqlite3.connect(
            f"file:{ledger.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        run = connection.execute(
            "SELECT * FROM factor_runs WHERE run_id=?", (PHASE6F_RUN_ID,)
        ).fetchone()
        if run is None:
            raise ValueError("Phase 6F run identity is absent from the ledger")
        if str(run["dataset_identity"]) != PHASE6F_DATASET_IDENTITY:
            raise ValueError("Phase 6F dataset identity mismatch")
        if report["dataset_snapshot"]["identity"] != PHASE6F_DATASET_IDENTITY:
            raise ValueError("Phase 6F report dataset identity mismatch")
        dataset_sha = str(run["dataset_sha256"])
        if file_sha256(snapshot) != dataset_sha:
            raise ValueError("Phase 6F snapshot bytes no longer match")
        rows = connection.execute(
            """SELECT sequence,trial_id,factor_identity,instrument,
                      expression_ast,canonical_expression,trial_family,
                      parent_expressions,structural_status,status,
                      classification
               FROM factor_trials WHERE run_id=? ORDER BY sequence""",
            (PHASE6F_RUN_ID,)).fetchall()
        digest = hashlib.sha256()
        trials: list[FrozenTrial] = []
        for row in rows:
            manifest_row = {
                key: row[key] for key in (
                    "sequence", "trial_id", "factor_identity", "instrument",
                    "expression_ast", "canonical_expression", "trial_family",
                    "parent_expressions", "structural_status")}
            digest.update(stable_json(manifest_row).encode("utf-8"))
            digest.update(b"\n")
            node = FactorNode.from_dict(json.loads(row["expression_ast"]))
            if factor_identity(node) != row["factor_identity"]:
                raise ValueError(
                    f"Phase 6F factor identity changed at sequence "
                    f"{row['sequence']}")
            trials.append(FrozenTrial(
                int(row["sequence"]), str(row["trial_id"]),
                str(row["factor_identity"]), str(row["instrument"]), node,
                str(row["trial_family"]),
                tuple(json.loads(row["parent_expressions"])),
                str(row["structural_status"]), str(row["status"]),
                row["classification"],
            ))
        selection = frozenset(row[0] for row in connection.execute(
            """SELECT DISTINCT trial_id FROM factor_evaluations
               WHERE run_id=? AND segment='SELECTION_VALIDATION'""",
            (PHASE6F_RUN_ID,)))
        locked = frozenset(row[0] for row in connection.execute(
            """SELECT DISTINCT trial_id FROM factor_evaluations
               WHERE run_id=? AND segment='LOCKED_VERIFICATION'""",
            (PHASE6F_RUN_ID,)))
        connection.close()
        identity = digest.hexdigest()
        if len(trials) != PHASE6F_RAW_TRIALS:
            raise ValueError(
                f"expected 2500 frozen expressions, found {len(trials)}")
        if len({trial.sequence for trial in trials}) != PHASE6F_RAW_TRIALS:
            raise ValueError("Phase 6F sequences are not unique")
        if len({trial.trial_id for trial in trials}) != PHASE6F_RAW_TRIALS:
            raise ValueError("Phase 6F trial identities are not unique")
        if identity != PHASE6F_MANIFEST_IDENTITY:
            raise ValueError(
                f"Phase 6F manifest mismatch: {identity}")
        if int(report["multiple_testing"]["effective_trial_count"]) != (
                PHASE6F_EFFECTIVE_CLUSTERS):
            raise ValueError("Phase 6F effective cluster count mismatch")
        return cls(
            ledger, snapshot, report_file, PHASE6F_RUN_ID,
            PHASE6F_DATASET_IDENTITY, dataset_sha, identity,
            file_sha256(ledger), tuple(trials), selection, locked, report)


class StatisticalAuditLedger:
    """Persistent Phase 6G identity mapping and audit results."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        result = sqlite3.connect(self.path, timeout=60)
        result.row_factory = sqlite3.Row
        result.execute("PRAGMA journal_mode=WAL")
        result.execute("PRAGMA synchronous=NORMAL")
        result.execute("PRAGMA busy_timeout=60000")
        return result

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS statistical_audit_runs(
                    audit_run_id TEXT PRIMARY KEY,
                    audit_version TEXT NOT NULL,
                    phase6f_run_id TEXT NOT NULL,
                    dataset_identity TEXT NOT NULL,
                    manifest_identity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    report_json TEXT);
                CREATE TABLE IF NOT EXISTS phase6f_phase6g_identity_map(
                    audit_run_id TEXT NOT NULL,
                    phase6f_sequence INTEGER NOT NULL,
                    phase6f_trial_id TEXT NOT NULL,
                    phase6f_factor_identity TEXT NOT NULL,
                    phase6g_reevaluation_identity TEXT NOT NULL,
                    statistically_applicable INTEGER NOT NULL,
                    exclusion_reason TEXT,
                    PRIMARY KEY(audit_run_id,phase6f_sequence),
                    UNIQUE(audit_run_id,phase6f_trial_id),
                    UNIQUE(audit_run_id,phase6g_reevaluation_identity));
                CREATE TABLE IF NOT EXISTS statistical_audit_evaluations(
                    audit_run_id TEXT NOT NULL,
                    reevaluation_identity TEXT NOT NULL,
                    phase6f_trial_id TEXT NOT NULL,
                    factor_identity TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    source_family TEXT NOT NULL,
                    lineage TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    granularity_json TEXT NOT NULL,
                    dense_metrics_json TEXT NOT NULL,
                    native_metrics_json TEXT NOT NULL,
                    portfolio_json TEXT NOT NULL,
                    formal_p_value REAL NOT NULL,
                    local_family TEXT,
                    local_family_size INTEGER,
                    local_rank INTEGER,
                    local_fdr_q REAL,
                    global_family_size INTEGER,
                    global_rank INTEGER,
                    global_fdr_q REAL,
                    local_bonferroni REAL,
                    global_bonferroni REAL,
                    PRIMARY KEY(audit_run_id,reevaluation_identity,segment,horizon));
                CREATE TABLE IF NOT EXISTS statistical_audit_portfolio_returns(
                    audit_run_id TEXT NOT NULL,
                    reevaluation_identity TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    exposure REAL NOT NULL,
                    turnover REAL NOT NULL,
                    gross_return REAL NOT NULL,
                    cost_drag REAL NOT NULL,
                    net_return REAL NOT NULL,
                    source_event_id INTEGER NOT NULL,
                    PRIMARY KEY(
                        audit_run_id,reevaluation_identity,segment,horizon,
                        timestamp_ms));
                CREATE TABLE IF NOT EXISTS statistical_audit_classifications(
                    audit_run_id TEXT NOT NULL,
                    phase6f_trial_id TEXT NOT NULL,
                    reevaluation_identity TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    PRIMARY KEY(audit_run_id,phase6f_trial_id));
                """
            )

    def start(self, experiment: FrozenExperiment) -> str:
        audit_run_id = stable_hash(
            FACTOR_STATISTICAL_AUDIT_VERSION,
            experiment.run_id, experiment.dataset_identity,
            experiment.manifest_identity)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO statistical_audit_runs VALUES(?,?,?,?,?,?,?,NULL,NULL)
                   ON CONFLICT(audit_run_id) DO UPDATE SET status='RUNNING'""",
                (audit_run_id, FACTOR_STATISTICAL_AUDIT_VERSION,
                 experiment.run_id, experiment.dataset_identity,
                 experiment.manifest_identity, "RUNNING", utc_now()))
            for trial in experiment.trials:
                reevaluation_id = stable_hash(
                    FACTOR_STATISTICAL_AUDIT_VERSION,
                    experiment.manifest_identity, trial.trial_id,
                    trial.factor_id, trial.instrument)
                connection.execute(
                    """INSERT OR IGNORE INTO phase6f_phase6g_identity_map
                       VALUES(?,?,?,?,?,?,?)""",
                    (audit_run_id, trial.sequence, trial.trial_id,
                     trial.factor_id, reevaluation_id,
                     int(trial.statistically_applicable),
                     None if trial.statistically_applicable
                     else "PURE_STRUCTURAL_INVALIDITY_NO_OUTCOME"))
        return audit_run_id

    def reevaluation_identity(
        self, audit_run_id: str, trial_id: str,
    ) -> str:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT phase6g_reevaluation_identity
                   FROM phase6f_phase6g_identity_map
                   WHERE audit_run_id=? AND phase6f_trial_id=?""",
                (audit_run_id, trial_id)).fetchone()
        if row is None:
            raise KeyError(trial_id)
        return str(row[0])

    def save_evaluation(
        self, audit_run_id: str, result: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO statistical_audit_evaluations(
                   audit_run_id,reevaluation_identity,phase6f_trial_id,
                   factor_identity,instrument,source_family,lineage,segment,
                   horizon,granularity_json,dense_metrics_json,
                   native_metrics_json,portfolio_json,formal_p_value,
                   local_family,local_family_size,local_rank,local_fdr_q,
                   global_family_size,global_rank,global_fdr_q,
                   local_bonferroni,global_bonferroni)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (audit_run_id, result["reevaluation_identity"],
                 result["phase6f_trial_id"], result["factor_identity"],
                 result["instrument"], result["source_family"],
                 result["lineage"], result["segment"], result["horizon"],
                 stable_json(result["granularity"]),
                 stable_json(result["dense"]),
                 stable_json(result["native"]),
                 stable_json(result["portfolio"]),
                 result["formal_p_value"], result.get("local_family"),
                 result.get("local_family_size"), result.get("local_rank"),
                 result.get("local_fdr_q"),
                 result.get("global_family_size"),
                 result.get("global_rank"), result.get("global_fdr_q"),
                 result.get("local_bonferroni"),
                 result.get("global_bonferroni")))

    def save_returns(
        self, audit_run_id: str, result: dict[str, Any],
    ) -> None:
        rows = result.get("portfolio_rows", [])
        if not rows:
            return
        with self.connect() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO statistical_audit_portfolio_returns
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [(audit_run_id, result["reevaluation_identity"],
                  result["segment"], result["horizon"], *row)
                 for row in rows])

    def save_results(
        self, audit_run_id: str, results: Iterable[dict[str, Any]],
    ) -> None:
        with self.connect() as connection:
            for result in results:
                connection.execute(
                    """INSERT OR REPLACE INTO statistical_audit_evaluations(
                       audit_run_id,reevaluation_identity,phase6f_trial_id,
                       factor_identity,instrument,source_family,lineage,segment,
                       horizon,granularity_json,dense_metrics_json,
                       native_metrics_json,portfolio_json,formal_p_value,
                       local_family,local_family_size,local_rank,local_fdr_q,
                       global_family_size,global_rank,global_fdr_q,
                       local_bonferroni,global_bonferroni)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (audit_run_id, result["reevaluation_identity"],
                     result["phase6f_trial_id"], result["factor_identity"],
                     result["instrument"], result["source_family"],
                     result["lineage"], result["segment"],
                     result["horizon"], stable_json(result["granularity"]),
                     stable_json(result["dense"]),
                     stable_json(result["native"]),
                     stable_json(result["portfolio"]),
                     result["formal_p_value"], result["local_family"],
                     result["local_family_size"], result["local_rank"],
                     result["local_fdr_q"], result["global_family_size"],
                     result["global_rank"], result["global_fdr_q"],
                     result["local_bonferroni"],
                     result["global_bonferroni"]))
                if result.get("persist_portfolio_rows"):
                    connection.executemany(
                        """INSERT OR REPLACE INTO
                           statistical_audit_portfolio_returns
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        [(audit_run_id, result["reevaluation_identity"],
                          result["segment"], result["horizon"], *row)
                         for row in result.get("portfolio_rows", [])])

    def save_classification(
        self, audit_run_id: str, trial_id: str, reevaluation_id: str,
        classification: str, diagnostics: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO statistical_audit_classifications
                   VALUES(?,?,?,?,?)""",
                (audit_run_id, trial_id, reevaluation_id, classification,
                 stable_json(diagnostics)))

    def complete(self, audit_run_id: str, report: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE statistical_audit_runs SET status='COMPLETE',
                   completed_at=?,report_json=? WHERE audit_run_id=?""",
                (utc_now(), stable_json(report), audit_run_id))

    def counts(self, audit_run_id: str) -> dict[str, int]:
        with self.connect() as connection:
            return {
                "identity_mappings": int(connection.execute(
                    """SELECT COUNT(*) FROM phase6f_phase6g_identity_map
                       WHERE audit_run_id=?""", (audit_run_id,)).fetchone()[0]),
                "statistical_evaluations": int(connection.execute(
                    """SELECT COUNT(*) FROM statistical_audit_evaluations
                       WHERE audit_run_id=?""", (audit_run_id,)).fetchone()[0]),
                "portfolio_return_rows": int(connection.execute(
                    """SELECT COUNT(*) FROM statistical_audit_portfolio_returns
                       WHERE audit_run_id=?""", (audit_run_id,)).fetchone()[0]),
                "classifications": int(connection.execute(
                    """SELECT COUNT(*) FROM statistical_audit_classifications
                       WHERE audit_run_id=?""", (audit_run_id,)).fetchone()[0]),
            }


def horizon_hac_lag(
    horizon_ms: int, event_timestamps: Iterable[int],
) -> int:
    timestamps = np.asarray(list(event_timestamps), dtype=np.int64)
    if timestamps.size < 2:
        return 0
    spacing = float(np.median(np.diff(np.unique(timestamps))))
    if spacing <= 0:
        return 0
    return max(0, int(math.ceil(horizon_ms / spacing)) - 1)


def deterministic_non_overlapping_indices(
    timestamps: Iterable[int], horizon_ms: int,
) -> np.ndarray:
    values = np.asarray(list(timestamps), dtype=np.int64)
    if values.size == 0:
        return np.asarray([], dtype=np.int64)
    selected: list[int] = []
    next_available = -2**63
    for index, timestamp in enumerate(values):
        if int(timestamp) >= next_available:
            selected.append(index)
            next_available = int(timestamp) + int(horizon_ms)
    return np.asarray(selected, dtype=np.int64)


def hac_mean_standard_error(
    values: Iterable[float], lag: int,
) -> tuple[float | None, float | None, float | None]:
    data = np.asarray(list(values), dtype=float)
    data = data[np.isfinite(data)]
    count = int(data.size)
    if count < 2:
        return None, None, None
    mean = float(np.mean(data))
    centered = data - mean
    gamma_zero = float(centered @ centered / count)
    naive = math.sqrt(max(0.0, gamma_zero) / count)
    bounded_lag = min(max(0, int(lag)), count - 1)
    long_run = gamma_zero
    for offset in range(1, bounded_lag + 1):
        covariance = float(
            centered[offset:] @ centered[:-offset] / count)
        weight = 1.0 - offset / (bounded_lag + 1.0)
        long_run += 2.0 * weight * covariance
    long_run = max(long_run, 1e-30)
    hac = math.sqrt(long_run / count)
    return mean, naive, hac


def effective_sample_size(
    values: Iterable[float], lag: int | None = None,
) -> float:
    data = np.asarray(list(values), dtype=float)
    data = data[np.isfinite(data)]
    count = int(data.size)
    if count < 2:
        return float(count)
    centered = data - np.mean(data)
    gamma_zero = float(centered @ centered / count)
    if gamma_zero <= 0:
        return float(count)
    bounded_lag = min(
        count - 1,
        int(lag) if lag is not None else max(1, int(count ** (1 / 3))))
    inflation = 1.0
    for offset in range(1, bounded_lag + 1):
        autocorrelation = float(
            centered[offset:] @ centered[:-offset] /
            ((count - offset) * gamma_zero))
        weight = 1.0 - offset / (bounded_lag + 1.0)
        inflation += 2.0 * weight * autocorrelation
    return float(min(count, max(1.0, count / max(1.0, inflation))))


def moving_block_bootstrap_ci(
    values: Iterable[float], *, block_length: int, seed: int,
    repetitions: int = BLOCK_BOOTSTRAP_REPETITIONS,
) -> list[float] | None:
    data = np.asarray(list(values), dtype=float)
    data = data[np.isfinite(data)]
    count = int(data.size)
    length = min(max(2, int(block_length)), count)
    if count < max(20, length * 4):
        return None
    block_sums = (
        np.cumsum(np.r_[0.0, data])[length:]
        - np.cumsum(np.r_[0.0, data])[:-length])
    if block_sums.size < 2:
        return None
    blocks_needed = int(math.ceil(count / length))
    rng = np.random.default_rng(seed)
    starts = rng.integers(
        0, block_sums.size, size=(repetitions, blocks_needed))
    estimates = np.mean(block_sums[starts] / length, axis=1)
    return [
        float(np.quantile(estimates, 0.025)),
        float(np.quantile(estimates, 0.975)),
    ]


def _rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    preliminary = np.empty(values.size, dtype=float)
    preliminary[order] = np.arange(values.size, dtype=float)
    _, inverse, counts = np.unique(
        values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=preliminary)
    return sums[inverse] / counts[inverse]


def _correlation_influence(
    left: np.ndarray, right: np.ndarray, *, rank: bool,
) -> tuple[float | None, np.ndarray]:
    if left.size < 3:
        return None, np.asarray([], dtype=float)
    x = _rank(left) if rank else left
    y = _rank(right) if rank else right
    x_std = float(np.std(x))
    y_std = float(np.std(y))
    if x_std == 0 or y_std == 0:
        return 0.0, np.zeros(left.size)
    standardized = (
        (x - np.mean(x)) / x_std) * ((y - np.mean(y)) / y_std)
    return float(np.mean(standardized)), standardized


def _two_sided_normal_p(estimate: float | None, standard_error: float | None) -> float:
    if estimate is None or standard_error is None:
        return 1.0
    if standard_error <= 0:
        return 0.0 if estimate != 0 else 1.0
    return float(math.erfc(
        abs(float(estimate) / float(standard_error)) / math.sqrt(2.0)))


def _quantile_spread_influence(
    scores: np.ndarray, returns: np.ndarray,
) -> tuple[list[float], float | None, np.ndarray]:
    if scores.size < 10:
        return [], None, np.asarray([], dtype=float)
    order = np.argsort(scores, kind="mergesort")
    groups = np.array_split(order, 5)
    quantiles = [
        float(np.mean(returns[group])) if group.size else 0.0
        for group in groups]
    low = groups[0]
    high = groups[-1]
    influence = np.zeros(scores.size)
    influence[high] = returns[high] * scores.size / max(1, high.size)
    influence[low] = -returns[low] * scores.size / max(1, low.size)
    return quantiles, quantiles[-1] - quantiles[0], influence


def _maximum_drawdown(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    cumulative = np.cumsum(returns)
    peaks = np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
    return abs(float(np.min(cumulative - peaks)))


def _primary_source(node: FactorNode) -> str:
    groups = set(node.source_groups)
    if "funding" in groups:
        return "settled_funding"
    if "basis" in groups:
        return "basis"
    return "price_context"


def is_formally_auditable(trial: FrozenTrial) -> bool:
    return (
        trial.statistically_applicable
        and not any(terminal in BLOCKED_TERMINALS
                    for terminal in trial.node.terminals))


def dependency_adjusted_dsr_sensitivity(
    observed_sharpe: float, effective_observations: float,
    skew: float, kurtosis: float, attempted_trial_sharpes: Iterable[float],
) -> dict[str, dict[str, float | int | None]]:
    sharpes = list(attempted_trial_sharpes)
    result: dict[str, dict[str, float | int | None]] = {}
    for label, trials in (
            ("phase6f_effective_clusters", 175),
            ("behavior_unique_upper_bound", 400),
            ("raw_phase6f_trials", 2500)):
        probability, benchmark = deflated_sharpe_ratio(
            observed_sharpe,
            max(3, int(math.floor(effective_observations))),
            skew, kurtosis, effective_trials=trials,
            trial_sharpes=sharpes)
        result[label] = {
            "trial_count": trials,
            "applicable_sharpe_input_count": len(sharpes),
            "expected_maximum_sharpe": benchmark,
            "dsr_probability": probability,
        }
    return result


def _source_event_ids(
    data: FactorData, primary_source: str,
) -> tuple[np.ndarray, np.ndarray]:
    if primary_source == "settled_funding":
        source_times = data._funding_ts  # frozen Phase 6F source arrays
    elif primary_source == "basis":
        source_times = data._basis_ts
    else:
        return data.timestamps.copy(), np.ones(
            data.timestamps.size, dtype=bool)
    positions = np.searchsorted(
        source_times, data.timestamps, side="right") - 1
    event_ids = np.full(data.timestamps.size, -1, dtype=np.int64)
    valid = positions >= 0
    event_ids[valid] = source_times[positions[valid]]
    native = valid & np.isin(data.timestamps, source_times)
    return event_ids, native


def _dense_metrics(scores: np.ndarray, labels: np.ndarray) -> dict[str, Any]:
    valid = np.isfinite(scores) & np.isfinite(labels)
    x, y = scores[valid], labels[valid]
    pearson, _ = _correlation_influence(x, y, rank=False)
    spearman, _ = _correlation_influence(x, y, rank=True)
    quantiles, spread, _ = _quantile_spread_influence(x, y)
    return {
        "row_count": int(x.size),
        "pearson_ic": pearson,
        "spearman_ic": spearman,
        "quantile_returns": quantiles,
        "top_minus_bottom_spread": spread,
        "inference_status": "DESCRIPTIVE_ONLY_DENSE_DEPENDENT_ROWS",
    }


def _native_metrics(
    timestamps: np.ndarray, scores: np.ndarray, labels: np.ndarray,
    horizon_ms: int, *, bootstrap_seed: int,
) -> tuple[dict[str, Any], np.ndarray]:
    valid = np.isfinite(scores) & np.isfinite(labels)
    ts, x, y = timestamps[valid], scores[valid], labels[valid]
    count = int(x.size)
    if count < MIN_EVENTS:
        return ({
            "status": "INSUFFICIENT_SAMPLE",
            "event_count": count,
            "pearson_ic": None,
            "spearman_ic": None,
            "hac_lag": horizon_hac_lag(horizon_ms, ts),
            "naive_standard_error": None,
            "hac_standard_error": None,
            "block_bootstrap_confidence_interval": None,
            "effective_sample_size": float(count),
            "formal_p_value": 1.0,
            "non_overlapping_count": int(
                deterministic_non_overlapping_indices(
                    ts, horizon_ms).size),
        }, np.asarray([], dtype=np.int64))
    pearson, _ = _correlation_influence(x, y, rank=False)
    spearman, influence = _correlation_influence(x, y, rank=True)
    lag = horizon_hac_lag(horizon_ms, ts)
    _, naive_se, hac_se = hac_mean_standard_error(influence, lag)
    block_length = max(2, lag + 1, int(math.sqrt(count)))
    bootstrap = moving_block_bootstrap_ci(
        influence, block_length=block_length, seed=bootstrap_seed)
    nonoverlap = deterministic_non_overlapping_indices(ts, horizon_ms)
    nonoverlap_ic, _ = _correlation_influence(
        x[nonoverlap], y[nonoverlap], rank=True)
    quantiles, spread, spread_influence = _quantile_spread_influence(x, y)
    _, _, spread_hac_se = hac_mean_standard_error(spread_influence, lag)
    spread_bootstrap = moving_block_bootstrap_ci(
        spread_influence, block_length=block_length,
        seed=bootstrap_seed ^ 0x5F3759DF)
    return ({
        "status": "EVALUATED",
        "event_count": count,
        "pearson_ic": pearson,
        "spearman_ic": spearman,
        "quantile_returns": quantiles,
        "top_minus_bottom_spread": spread,
        "naive_standard_error": naive_se,
        "hac_standard_error": hac_se,
        "hac_lag": lag,
        "block_length": block_length,
        "block_bootstrap_confidence_interval": bootstrap,
        "spread_hac_standard_error": spread_hac_se,
        "spread_block_bootstrap_confidence_interval": spread_bootstrap,
        "effective_sample_size": effective_sample_size(influence, lag),
        "formal_p_value": _two_sided_normal_p(spearman, hac_se),
        "non_overlapping_count": int(nonoverlap.size),
        "non_overlapping_spearman_ic": nonoverlap_ic,
    }, nonoverlap)


def _portfolio_metrics(
    data: FactorData, scores: np.ndarray, event_indices: np.ndarray,
    labels: np.ndarray, horizon_ms: int,
) -> tuple[dict[str, Any], list[tuple[int, float, float, float, float, float, int]]]:
    if event_indices.size == 0:
        return ({
            "status": "INSUFFICIENT_SAMPLE",
            "raw_native_positions": 0,
            "non_overlapping_positions": 0,
        }, [])
    timestamps = data.timestamps[event_indices]
    nonoverlap = deterministic_non_overlapping_indices(
        timestamps, horizon_ms)
    chosen = event_indices[nonoverlap]
    exposure_all = _causal_exposure(scores)
    exposure = exposure_all[chosen]
    valid = np.isfinite(labels[chosen])
    chosen, exposure = chosen[valid], exposure[valid]
    if chosen.size == 0:
        return ({
            "status": "INSUFFICIENT_SAMPLE",
            "raw_native_positions": int(event_indices.size),
            "non_overlapping_positions": 0,
        }, [])
    turnover = np.abs(exposure - np.r_[0.0, exposure[:-1]])
    gross = exposure * labels[chosen]
    costs = turnover * PORTFOLIO_COST_PER_TURNOVER
    net = gross - costs
    count, mean, raw_std, skew, kurtosis = moments(net)
    dependency_lag = max(1, int(max(1, count) ** (1 / 3)))
    _, _, mean_hac_se = hac_mean_standard_error(net, dependency_lag)
    long_run_std = (
        mean_hac_se * math.sqrt(count)
        if mean_hac_se is not None else raw_std)
    per_period_sharpe = (
        mean / long_run_std if long_run_std and long_run_std > 0 else 0.0)
    effective_count = effective_sample_size(net, dependency_lag)
    spacings = np.diff(data.timestamps[chosen])
    median_spacing = (
        float(np.median(spacings)) if spacings.size else float(horizon_ms))
    annualization = math.sqrt(YEAR_MS / max(1.0, median_spacing))
    annual_sharpe = per_period_sharpe * annualization
    psr = probabilistic_sharpe_ratio(
        per_period_sharpe, 0.0, max(3, int(math.floor(effective_count))),
        skew, kurtosis)
    psr_variance_term = max(
        0.0, 1.0 - skew * per_period_sharpe
        + ((kurtosis - 1.0) / 4.0) * per_period_sharpe ** 2)
    sharpe_se = math.sqrt(
        psr_variance_term / max(1.0, effective_count - 1.0))
    rows = [
        (int(data.timestamps[index]), float(exposure[position]),
         float(turnover[position]), float(gross[position]),
         float(costs[position]), float(net[position]),
         int(data.timestamps[index]))
        for position, index in enumerate(chosen)
    ]
    return ({
        "status": "EVALUATED" if count >= MIN_EVENTS
        else "INSUFFICIENT_SAMPLE",
        "construction": (
            "one position per native source event; non-overlapping close "
            "returns; no mark-to-market rows"),
        "raw_native_positions": int(event_indices.size),
        "non_overlapping_positions": int(count),
        "same_source_multiple_positions": False,
        "positions_overlap_label_horizon": False,
        "returns_marked_every_bar": False,
        "turnover": float(np.mean(turnover)) if turnover.size else 0.0,
        "cost_per_unit_turnover": PORTFOLIO_COST_PER_TURNOVER,
        "gross_return": float(np.sum(gross)),
        "cost_drag": float(np.sum(costs)),
        "net_return": float(np.sum(net)),
        "raw_return_observations": count,
        "effective_return_observations": effective_count,
        "dependency_lag": dependency_lag,
        "per_period_dependency_adjusted_sharpe": per_period_sharpe,
        "dependency_adjusted_annual_sharpe": annual_sharpe,
        "sharpe_standard_error": sharpe_se,
        "annualization_factor": annualization,
        "annualization_basis_ms": median_spacing,
        "skew": skew,
        "kurtosis": kurtosis,
        "maximum_drawdown": _maximum_drawdown(net),
        "time_in_market": float(np.mean(exposure != 0))
        if exposure.size else 0.0,
        "psr": psr,
    }, rows)


def assign_multiple_testing(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assign explicit local-family and global BH/Bonferroni diagnostics."""
    local_groups: dict[str, list[int]] = {}
    for index, result in enumerate(results):
        local_family = "|".join((
            result["source_family"], result["instrument"],
            result["horizon"], result["lineage"], result["segment"]))
        result["local_family"] = local_family
        local_groups.setdefault(local_family, []).append(index)
    local_sizes: dict[str, int] = {}
    for family, indices in sorted(local_groups.items()):
        ordered = sorted(
            indices, key=lambda index: (
                results[index]["formal_p_value"],
                results[index]["reevaluation_identity"]))
        q_values = benjamini_hochberg(
            [results[index]["formal_p_value"] for index in ordered])
        local_sizes[family] = len(ordered)
        for rank, (index, q_value) in enumerate(
                zip(ordered, q_values), start=1):
            result = results[index]
            result["local_family_size"] = len(ordered)
            result["local_rank"] = rank
            result["local_fdr_q"] = q_value
            result["local_bonferroni"] = min(
                1.0, result["formal_p_value"] * len(ordered))
    ordered_global = sorted(
        range(len(results)), key=lambda index: (
            results[index]["formal_p_value"],
            results[index]["reevaluation_identity"],
            results[index]["segment"], results[index]["horizon"]))
    global_q = benjamini_hochberg(
        [results[index]["formal_p_value"] for index in ordered_global])
    global_size = len(ordered_global)
    for rank, (index, q_value) in enumerate(
            zip(ordered_global, global_q), start=1):
        result = results[index]
        result["global_family_size"] = global_size
        result["global_rank"] = rank
        result["global_fdr_q"] = q_value
        result["global_bonferroni"] = min(
            1.0, result["formal_p_value"] * global_size)
    return {
        "version": FDR_FAMILY_AUDIT_VERSION,
        "local_family_dimensions": [
            "source_family", "instrument", "target_horizon",
            "expression_lineage", "chronological_segment"],
        "local_family_count": len(local_groups),
        "local_family_sizes": local_sizes,
        "global_family_size": global_size,
        "failed_or_insufficient_attempt_policy": (
            "statistically applicable attempted hypotheses receive p=1; "
            "pure structural invalidity is excluded"),
    }


def _native_update_frequencies(snapshot_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{snapshot_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    def spacing(query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = connection.execute(query, parameters).fetchall()
        values = [int(row[0]) for row in rows]
        differences = np.diff(np.asarray(values, dtype=np.int64))
        return {
            "observations": len(values),
            "median_native_spacing_ms": (
                int(np.median(differences)) if differences.size else None),
            "minimum_native_spacing_ms": (
                int(np.min(differences)) if differences.size else None),
            "maximum_native_spacing_ms": (
                int(np.max(differences)) if differences.size else None),
        }

    funding = {}
    basis = {}
    mark = {}
    index = {}
    for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
        funding[instrument] = spacing(
            """SELECT funding_time_ms FROM funding_settled
               WHERE instrument=? ORDER BY funding_time_ms""", (instrument,))
        basis[instrument] = spacing(
            """SELECT bucket_ms FROM basis_aggregates
               WHERE instrument=? AND resolution='1H' ORDER BY bucket_ms""",
            (instrument,))
        mark[instrument] = spacing(
            """SELECT source_ts_ms FROM mark_price_observations
               WHERE instrument=? AND state='confirmed'
               ORDER BY source_ts_ms""", (instrument,))
        index[instrument] = spacing(
            """SELECT source_ts_ms FROM index_price_observations
               WHERE instrument=? AND state='confirmed'
               ORDER BY source_ts_ms""",
            (instrument.removesuffix("-SWAP"),))
    connection.close()
    return {
        "settled_funding": {
            "native_event": "genuine exchange settlement",
            "expected_frequency": "normally 8H",
            "instruments": funding,
        },
        "basis": {
            "native_event": "confirmed 1H basis aggregate",
            "formal_rebalance_frequency": "1H fixed causal schedule",
            "instruments": basis,
        },
        "mark_index_returns": {
            "raw_source_frequency": "approximately 1m confirmed observations",
            "factor_decision_frequency": "15m",
            "mark": mark,
            "index": index,
        },
        "volatility_context": {
            "input_frequency": "15m causal mark/index grid",
            "factor_update_frequency": "15m rolling update",
        },
    }


def _original_multiple_testing_reconciliation(
    experiment: FrozenExperiment,
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(
        f"file:{experiment.ledger_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    reconciliations: list[dict[str, Any]] = []
    for entry in experiment.phase6f_report["factor_library"]:
        factor_id = entry["factor_identity"]
        instrument = entry["valid_instruments"][0]
        trial = connection.execute(
            """SELECT trial_id,trial_family,expression_ast FROM factor_trials
               WHERE run_id=? AND factor_identity=? AND instrument=?""",
            (experiment.run_id, factor_id, instrument)).fetchone()
        evaluation = connection.execute(
            """SELECT raw_p_value,fdr_q_value,bonferroni_p
               FROM factor_evaluations
               WHERE run_id=? AND trial_id=? AND segment='DISCOVERY'
                     AND horizon='1H'""",
            (experiment.run_id, trial["trial_id"])).fetchone()
        family = _family(
            FactorNode.from_dict(json.loads(trial["expression_ast"])))
        family_rows = []
        for row in connection.execute(
                """SELECT e.trial_id,e.horizon,e.raw_p_value,t.expression_ast
                   FROM factor_evaluations e
                   JOIN factor_trials t ON t.trial_id=e.trial_id
                   WHERE e.run_id=? AND e.segment='DISCOVERY'
                         AND e.raw_p_value IS NOT NULL""",
                (experiment.run_id,)):
            node = FactorNode.from_dict(json.loads(row["expression_ast"]))
            if _family(node) == family:
                family_rows.append((
                    float(row["raw_p_value"]), str(row["trial_id"]),
                    str(row["horizon"])))
        family_rows.sort(key=lambda item: (item[0], item[1], item[2]))
        rank = next(
            index + 1 for index, item in enumerate(family_rows)
            if item[1] == trial["trial_id"] and item[2] == "1H")
        raw_p = float(evaluation["raw_p_value"])
        phase6f_global_bonferroni_family = int(
            experiment.phase6f_report["multiple_testing"][
                "bh_fdr_complete_family_size"])
        reconciliations.append({
            "factor_identity": factor_id,
            "instrument": instrument,
            "phase6f_raw_p_value": raw_p,
            "phase6f_bh_family_definition": (
                f"discovery {family} factor/horizon tests"),
            "phase6f_bh_family_size": len(family_rows),
            "phase6f_bh_rank": rank,
            "phase6f_unconstrained_rank_adjustment":
                raw_p * len(family_rows) / rank,
            "phase6f_stored_bh_q_value":
                float(evaluation["fdr_q_value"]),
            "phase6f_bonferroni_family_definition": (
                "all 1,400 successful discovery factor/horizon tests"),
            "phase6f_bonferroni_family_size":
                phase6f_global_bonferroni_family,
            "phase6f_bonferroni_calculation": min(
                1.0, raw_p * phase6f_global_bonferroni_family),
            "phase6f_stored_bonferroni":
                float(evaluation["bonferroni_p"]),
            "mathematically_compatible": True,
            "explanation": (
                "BH divides the family-size multiplier by the p-value rank "
                "and applies a monotone minimum; Bonferroni applies the full "
                "global multiplier. A small BH q and Bonferroni=1 are "
                "therefore compatible, although the family definitions were "
                "not aligned."),
        })
    connection.close()
    return reconciliations


def _segment_name_masks(masks: Any) -> dict[str, np.ndarray]:
    return {
        "DISCOVERY": masks.discovery,
        "SELECTION_VALIDATION": masks.selection_validation,
        "LOCKED_VERIFICATION": masks.locked_verification,
    }


class FactorStatisticalAudit:
    """Dependency-aware reevaluation of the immutable Phase 6F manifest."""

    def __init__(
        self, experiment: FrozenExperiment, audit_ledger_path: Path | str,
    ) -> None:
        self.experiment = experiment
        self.ledger = StatisticalAuditLedger(audit_ledger_path)
        self.data = {
            instrument: FactorData(experiment.snapshot_path, instrument)
            for instrument in (
                "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")}

    def _evaluate_trial(
        self, audit_run_id: str, trial: FrozenTrial,
    ) -> list[dict[str, Any]]:
        data = self.data[trial.instrument]
        reevaluation_id = self.ledger.reevaluation_identity(
            audit_run_id, trial.trial_id)
        try:
            scores = data.evaluate(trial.node)
        except Exception as error:
            # A statistically applicable expression that fails evaluation
            # remains an attempted hypothesis with p=1 in every permitted
            # segment/horizon family.
            scores = np.full(data.timestamps.size, np.nan)
            evaluation_error = type(error).__name__
        else:
            evaluation_error = None
        one_hour = data.labels(HORIZONS_MS["1H"])
        usable = np.isfinite(scores) & np.isfinite(one_hour)
        masks = chronological_segments(data.timestamps, usable)
        segment_masks = _segment_name_masks(masks)
        primary_source = _primary_source(trial.node)
        source_event_ids, native_schedule = _source_event_ids(
            data, primary_source)
        allowed_segments = {"DISCOVERY"}
        if trial.trial_id in self.experiment.selection_trial_ids:
            allowed_segments.add("SELECTION_VALIDATION")
        if trial.trial_id in self.experiment.locked_trial_ids:
            allowed_segments.add("LOCKED_VERIFICATION")
        results: list[dict[str, Any]] = []
        for segment in (
                "DISCOVERY", "SELECTION_VALIDATION", "LOCKED_VERIFICATION"):
            if segment not in allowed_segments:
                continue
            segment_mask = segment_masks[segment]
            for horizon, horizon_ms in HORIZONS_MS.items():
                labels = data.labels(horizon_ms)
                dense_valid = (
                    segment_mask & np.isfinite(scores) & np.isfinite(labels))
                dense_indices = np.flatnonzero(dense_valid)
                native_valid = dense_valid & native_schedule
                native_indices = np.flatnonzero(native_valid)
                dense = _dense_metrics(
                    scores[dense_valid], labels[dense_valid])
                seed = int(stable_hash(
                    BLOCK_BOOTSTRAP_SEED, trial.trial_id,
                    segment, horizon)[:16], 16) % (2**32)
                native, nonoverlap = _native_metrics(
                    data.timestamps[native_valid],
                    scores[native_valid], labels[native_valid],
                    horizon_ms, bootstrap_seed=seed)
                regimes = data.terminal(FactorNode.term(
                    "causal_regime_code", lookback=32))[native_valid]
                finite_regimes = regimes[np.isfinite(regimes)]
                native["regime_concentration"] = (
                    max(int(np.sum(finite_regimes == value))
                        for value in (-1.0, 0.0, 1.0))
                    / finite_regimes.size
                    if finite_regimes.size else 0.0)
                event_ids = source_event_ids[dense_indices]
                source_valid = event_ids >= 0
                valid_event_ids = event_ids[source_valid]
                repeated = (
                    int(np.sum(valid_event_ids[1:] == valid_event_ids[:-1]))
                    if valid_event_ids.size > 1 else 0)
                dense_scores = scores[dense_indices]
                finite_changes = np.abs(np.diff(dense_scores))
                material_scale = (
                    float(np.nanmedian(np.abs(dense_scores)))
                    if dense_scores.size else 0.0)
                tolerance = max(1e-12, material_scale * 1e-9)
                value_changes = (
                    1 + int(np.sum(finite_changes > tolerance))
                    if dense_scores.size else 0)
                portfolio, portfolio_rows = _portfolio_metrics(
                    data, scores, native_indices, labels, horizon_ms)
                granularity = {
                    "primary_source": primary_source,
                    "raw_row_count": int(dense_indices.size),
                    "unique_source_event_count": int(
                        np.unique(valid_event_ids).size),
                    "unique_factor_value_change_count": value_changes,
                    "native_event_row_count": int(native_indices.size),
                    "unique_non_overlapping_label_count":
                        int(nonoverlap.size),
                    "effective_sample_size":
                        native["effective_sample_size"],
                    "duplicated_exposure_duration_ms":
                        repeated * GRID_MS,
                    "unchanged_source_row_count": repeated,
                    "unchanged_source_row_percentage": (
                        repeated / valid_event_ids.size
                        if valid_event_ids.size else 0.0),
                    "funding_forward_fill_is_independent": False,
                    "evaluation_error": evaluation_error,
                }
                results.append({
                    "reevaluation_identity": reevaluation_id,
                    "phase6f_trial_id": trial.trial_id,
                    "factor_identity": trial.factor_id,
                    "instrument": trial.instrument,
                    "source_family": _family(trial.node),
                    "lineage": trial.lineage,
                    "segment": segment,
                    "horizon": horizon,
                    "granularity": granularity,
                    "dense": dense,
                    "native": native,
                    "portfolio": portfolio,
                    "portfolio_rows": portfolio_rows,
                    "persist_portfolio_rows":
                        trial.trial_id in self.experiment.locked_trial_ids,
                    "formal_p_value": float(native["formal_p_value"]),
                })
        return results

    def _classify_locked(
        self, audit_run_id: str, results_by_key: dict[tuple[str, str, str], dict[str, Any]],
        trial_sharpes: list[float],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        locked_trials = [
            trial for trial in self.experiment.trials
            if trial.trial_id in self.experiment.locked_trial_ids]
        classifications: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for trial in locked_trials:
            consistent_horizons = 0
            locked_positive_spreads = 0
            segment_results: dict[str, Any] = {}
            for horizon in HORIZONS_MS:
                horizon_results = {
                    segment: results_by_key.get(
                        (trial.trial_id, segment, horizon))
                    for segment in (
                        "DISCOVERY", "SELECTION_VALIDATION",
                        "LOCKED_VERIFICATION")}
                segment_results[horizon] = horizon_results
                signs = []
                for segment in horizon_results.values():
                    value = (
                        segment["native"].get("spearman_ic")
                        if segment else None)
                    if value not in (None, 0):
                        signs.append(int(np.sign(value)))
                if len(signs) == 3 and len(set(signs)) == 1:
                    consistent_horizons += 1
                locked = horizon_results["LOCKED_VERIFICATION"]
                if (locked and
                        (locked["native"].get(
                            "top_minus_bottom_spread") or 0) > 0):
                    locked_positive_spreads += 1
            one_hour = results_by_key.get(
                (trial.trial_id, "LOCKED_VERIFICATION", "1H"))
            if one_hour is None:
                classification = "INSUFFICIENT_SAMPLE"
                diagnostics = {"reason": "NO_LOCKED_ONE_HOUR_RESULT"}
            else:
                portfolio = one_hour["portfolio"]
                observed = float(portfolio.get(
                    "per_period_dependency_adjusted_sharpe", 0.0))
                effective_n = float(portfolio.get(
                    "effective_return_observations", 0.0))
                skew = float(portfolio.get("skew", 0.0))
                kurtosis = float(portfolio.get("kurtosis", 3.0))
                dsr_sensitivity = dependency_adjusted_dsr_sensitivity(
                    observed, effective_n, skew, kurtosis, trial_sharpes)
                primary_dsr = dsr_sensitivity[
                    "phase6f_effective_clusters"]["dsr_probability"]
                locked_native = one_hour["native"]
                excessive_turnover = (
                    float(portfolio.get("turnover", 0.0)) > 0.75)
                severe_concentration = (
                    float(locked_native.get(
                        "regime_concentration", 0.0)) > 0.85)
                passes = (
                    portfolio.get("status") == "EVALUATED"
                    and primary_dsr is not None and primary_dsr >= 0.95
                    and float(portfolio.get(
                        "dependency_adjusted_annual_sharpe", 0.0)) > 0
                    and locked_positive_spreads >= 2
                    and consistent_horizons >= 2
                    and not excessive_turnover
                    and not severe_concentration)
                if passes:
                    classification = "RETAIN_FACTOR_CANDIDATE"
                elif excessive_turnover:
                    classification = "RETIRE_EXCESSIVE_TURNOVER"
                elif severe_concentration:
                    classification = "RETIRE_CONCENTRATED"
                elif (
                        consistent_horizons >= 2
                        and float(portfolio.get(
                            "dependency_adjusted_annual_sharpe", 0.0)) > 0):
                    classification = "RETAIN_DIAGNOSTIC_ONLY"
                elif portfolio.get("status") != "EVALUATED":
                    classification = "INSUFFICIENT_SAMPLE"
                else:
                    classification = "RETIRE_LOCKED_VERIFICATION_FAILURE"
                diagnostics = {
                    "observed_per_period_sharpe": observed,
                    "dependency_adjusted_annual_sharpe": portfolio.get(
                        "dependency_adjusted_annual_sharpe"),
                    "raw_return_observations": portfolio.get(
                        "raw_return_observations"),
                    "effective_return_observations": effective_n,
                    "skew": skew,
                    "kurtosis": kurtosis,
                    "psr": portfolio.get("psr"),
                    "raw_trial_count": PHASE6F_RAW_TRIALS,
                    "applicable_trial_count": len(trial_sharpes),
                    "effective_trial_count": PHASE6F_EFFECTIVE_CLUSTERS,
                    "dsr_sensitivity": dsr_sensitivity,
                    "consistent_horizons": consistent_horizons,
                    "locked_positive_spread_horizons":
                        locked_positive_spreads,
                    "turnover": portfolio.get("turnover"),
                }
            counts[classification] = counts.get(classification, 0) + 1
            reevaluation_id = self.ledger.reevaluation_identity(
                audit_run_id, trial.trial_id)
            self.ledger.save_classification(
                audit_run_id, trial.trial_id, reevaluation_id,
                classification, diagnostics)
            classifications.append({
                "phase6f_trial_id": trial.trial_id,
                "factor_identity": trial.factor_id,
                "instrument": trial.instrument,
                "canonical_expression": trial.node.to_dict(),
                "classification": classification,
                "diagnostics": diagnostics,
                "segment_results": segment_results,
            })
        return classifications, counts

    def run(
        self, *, report_path: Path | str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        self.ledger.initialize()
        audit_run_id = self.ledger.start(self.experiment)
        results: list[dict[str, Any]] = []
        mapped = 0
        evaluated_trials = 0
        for trial in self.experiment.trials:
            mapped += 1
            if not trial.statistically_applicable:
                continue
            if not is_formally_auditable(trial):
                raise ValueError(
                    "blocked source entered statistically applicable trials")
            evaluated_trials += 1
            results.extend(self._evaluate_trial(audit_run_id, trial))
        fdr = assign_multiple_testing(results)
        self.ledger.save_results(audit_run_id, results)
        results_by_key = {
            (result["phase6f_trial_id"], result["segment"],
             result["horizon"]): result
            for result in results}
        trial_sharpes = []
        for trial in self.experiment.trials:
            if not trial.statistically_applicable:
                continue
            result = results_by_key.get(
                (trial.trial_id, "DISCOVERY", "1H"))
            trial_sharpes.append(
                float(result["portfolio"].get(
                    "per_period_dependency_adjusted_sharpe", 0.0))
                if result else 0.0)
        classifications, classification_counts = self._classify_locked(
            audit_run_id, results_by_key, trial_sharpes)

        # Ten blocks are rejected unless every locked trial has at least ten
        # non-overlapping native returns per discovery block.
        block_minimums = []
        for trial in self.experiment.trials:
            if trial.trial_id not in self.experiment.locked_trial_ids:
                continue
            result = results_by_key.get(
                (trial.trial_id, "DISCOVERY", "24H"))
            if not result:
                block_minimums.append(0)
                continue
            observations = int(
                result["native"].get("non_overlapping_count", 0))
            block_minimums.append(observations // 10)
        minimum_per_block = min(block_minimums) if block_minimums else 0
        pbo = {
            "status": "UNAVAILABLE_INSUFFICIENT_INDEPENDENT_BLOCKS",
            "requested_chronological_blocks": 10,
            "minimum_native_non_overlapping_observations_per_block":
                minimum_per_block,
            "required_minimum_per_block": 10,
            "reason": (
                "The shortest locked factor histories do not provide ten "
                "independent native, non-overlapping outcomes in each of ten "
                "chronological blocks."),
        }
        frequencies = _native_update_frequencies(
            self.experiment.snapshot_path)
        source_granularity: dict[str, dict[str, float]] = {}
        for source in ("settled_funding", "basis", "price_context"):
            values = [
                result["granularity"] for result in results
                if result["segment"] == "DISCOVERY"
                and result["horizon"] == "1H"
                and result["granularity"]["primary_source"] == source]
            source_granularity[source] = {
                "factor_count": len(values),
                "raw_rows": sum(item["raw_row_count"] for item in values),
                "native_event_rows": sum(
                    item["native_event_row_count"] for item in values),
                "non_overlapping_labels": sum(
                    item["unique_non_overlapping_label_count"]
                    for item in values),
                "mean_unchanged_source_row_percentage": (
                    statistics.mean(
                        item["unchanged_source_row_percentage"]
                        for item in values) if values else 0.0),
            }
        diagnostic_ids = {
            entry["factor_identity"]
            for entry in self.experiment.phase6f_report["factor_library"]}
        diagnostic_reconciliation = [
            item for item in classifications
            if item["factor_identity"] in diagnostic_ids]
        report = {
            "disclaimer": DISCLAIMER,
            "audit_run_id": audit_run_id,
            "versions": {
                "audit": FACTOR_STATISTICAL_AUDIT_VERSION,
                "native_event": NATIVE_EVENT_POLICY_VERSION,
                "dependence": DEPENDENCE_POLICY_VERSION,
                "fdr": FDR_FAMILY_AUDIT_VERSION,
            },
            "frozen_experiment": {
                "phase6f_run_id": self.experiment.run_id,
                "dataset_identity": self.experiment.dataset_identity,
                "dataset_sha256": self.experiment.dataset_sha256,
                "ledger_sha256": self.experiment.ledger_sha256,
                "manifest_identity": self.experiment.manifest_identity,
                "raw_trial_count": len(self.experiment.trials),
                "statistically_applicable_trial_count": evaluated_trials,
                "pure_structural_invalid_count":
                    len(self.experiment.trials) - evaluated_trials,
                "phase6f_effective_clusters": PHASE6F_EFFECTIVE_CLUSTERS,
                "identity_mapping_count": mapped,
                "selection_trial_count":
                    len(self.experiment.selection_trial_ids),
                "locked_trial_count": len(self.experiment.locked_trial_ids),
            },
            "native_update_frequencies": frequencies,
            "observation_granularity_summary": source_granularity,
            "overlapping_return_policy": {
                "non_overlapping_subsamples": True,
                "hac_lag_policy": (
                    "ceil(horizon/native median event spacing)-1"),
                "block_bootstrap": (
                    f"deterministic moving-block, seed "
                    f"{BLOCK_BOOTSTRAP_SEED}, "
                    f"{BLOCK_BOOTSTRAP_REPETITIONS} repetitions"),
                "formal_inference_source": "native-event results only",
                "dense_results_status": "descriptive only",
            },
            "portfolio_dependence_policy": {
                "one_position_per_source_event": True,
                "non_overlapping_close_returns": True,
                "mark_every_bar": False,
                "cost_per_unit_turnover":
                    PORTFOLIO_COST_PER_TURNOVER,
                "annualization": (
                    "sqrt(milliseconds per year / median realized "
                    "non-overlapping return spacing)"),
                "psr_dsr_sample": "dependency-adjusted effective observations",
            },
            "multiple_testing": fdr,
            "phase6f_bh_bonferroni_reconciliation":
                _original_multiple_testing_reconciliation(self.experiment),
            "pbo": pbo,
            "locked_classification_counts": classification_counts,
            "locked_factors": classifications,
            "phase6f_diagnostic_factor_reconciliation":
                diagnostic_reconciliation,
            "corrected_retained_candidate_count":
                classification_counts.get("RETAIN_FACTOR_CANDIDATE", 0),
            "corrected_diagnostic_only_count":
                classification_counts.get("RETAIN_DIAGNOSTIC_ONLY", 0),
            "any_factor_survived_all_controls":
                classification_counts.get(
                    "RETAIN_FACTOR_CANDIDATE", 0) > 0,
            "proven_phase6f_statistical_defects": [
                "Forward-filled funding rows were used as dense IC events.",
                "Formal IC p-values used an IID Fisher approximation despite "
                "overlapping labels and repeated sources.",
                "PSR used annualized Sharpe with the raw non-overlapping row "
                "count rather than per-period Sharpe and dependency-adjusted "
                "effective observations.",
                "PBO block independence was not established before reporting "
                "a numeric result.",
                "BH and Bonferroni used mathematically valid but different "
                "family definitions without explicit reconciliation.",
            ],
            "corrections": [
                "Source-native event sampling",
                "Deterministic non-overlapping outcome subsamples",
                "Horizon/native-frequency Newey-West HAC errors",
                "Deterministic moving-block bootstrap confidence intervals",
                "Dependency-adjusted portfolio Sharpe, PSR and DSR inputs",
                "Explicit local and global FDR families",
                "PBO unavailable when independent blocks are inadequate",
            ],
            "runtime_seconds": time.perf_counter() - started,
            "ledger_counts": self.ledger.counts(audit_run_id),
            "safety": {
                "new_expressions_generated": False,
                "grammar_or_budget_changed": False,
                "new_data_sources_opened": False,
                "cvd_oi_formally_evaluated": False,
                "strategy_or_order_api_called": False,
                "production_deployment": False,
            },
        }
        self.ledger.complete(audit_run_id, report)
        if report_path is not None:
            target = Path(report_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(report, indent=2, sort_keys=True),
                encoding="utf-8")
        return report
