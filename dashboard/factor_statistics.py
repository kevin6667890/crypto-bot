"""Dependency-light statistical diagnostics for factor AutoResearch."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


MULTIPLE_TESTING_POLICY_VERSION = "factor-multiple-testing-v1"


def benjamini_hochberg(p_values: Iterable[float | None]) -> list[float | None]:
    """Complete-family BH-FDR q-values with monotone correction."""
    values = list(p_values)
    valid = [(index, float(value)) for index, value in enumerate(values)
             if value is not None and math.isfinite(float(value))]
    count = len(valid)
    result: list[float | None] = [None] * len(values)
    if not count:
        return result
    ordered = sorted(valid, key=lambda item: (item[1], item[0]))
    adjusted = [0.0] * count
    running = 1.0
    for position in range(count - 1, -1, -1):
        value = ordered[position][1] * count / (position + 1)
        running = min(running, value)
        adjusted[position] = min(1.0, running)
    for (index, _), value in zip(ordered, adjusted):
        result[index] = value
    return result


def bonferroni(p_value: float | None, family_size: int) -> float | None:
    if p_value is None:
        return None
    return min(1.0, float(p_value) * max(1, family_size))


def normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value / math.sqrt(2.0))


def moments(returns: Iterable[float]) -> tuple[int, float, float, float, float]:
    data = np.asarray(list(returns), dtype=float)
    data = data[np.isfinite(data)]
    count = int(data.size)
    if count < 2:
        return count, 0.0, 0.0, 0.0, 3.0
    mean = float(np.mean(data))
    std = float(np.std(data, ddof=1))
    if std == 0:
        return count, mean, std, 0.0, 3.0
    centered = (data - mean) / std
    skew = float(np.mean(centered ** 3))
    kurtosis = float(np.mean(centered ** 4))
    return count, mean, std, skew, kurtosis


def probabilistic_sharpe_ratio(
    observed_sharpe: float, benchmark_sharpe: float, sample_length: int,
    skew: float, kurtosis: float,
) -> float | None:
    """Bailey/López de Prado PSR using actual non-NaN sample moments."""
    if sample_length < 3 or not all(math.isfinite(value) for value in (
            observed_sharpe, benchmark_sharpe, skew, kurtosis)):
        return None
    denominator_term = (
        1.0 - skew * observed_sharpe
        + ((kurtosis - 1.0) / 4.0) * observed_sharpe ** 2)
    if denominator_term <= 0:
        return None
    statistic = (
        (observed_sharpe - benchmark_sharpe)
        * math.sqrt(sample_length - 1)
        / math.sqrt(denominator_term))
    return normal_cdf(statistic)


def expected_maximum_sharpe(
    effective_trials: float, trial_sharpe_std: float,
) -> float:
    """Expected maximum of independent zero-mean Sharpe trials."""
    trials = max(1.0, float(effective_trials))
    if trials <= 1.0 or trial_sharpe_std <= 0:
        return 0.0
    euler_gamma = 0.5772156649015329
    first = normal_inverse_cdf(1.0 - 1.0 / trials)
    second = normal_inverse_cdf(
        1.0 - 1.0 / (trials * math.e))
    return trial_sharpe_std * (
        (1.0 - euler_gamma) * first + euler_gamma * second)


def deflated_sharpe_ratio(
    observed_sharpe: float, sample_length: int, skew: float, kurtosis: float,
    *, effective_trials: float, trial_sharpes: Iterable[float],
) -> tuple[float | None, float]:
    trial_values = np.asarray(
        [value for value in trial_sharpes if math.isfinite(value)], dtype=float)
    trial_std = float(np.std(trial_values, ddof=1)) if trial_values.size > 1 else 0.0
    benchmark = expected_maximum_sharpe(effective_trials, trial_std)
    return (
        probabilistic_sharpe_ratio(
            observed_sharpe, benchmark, sample_length, skew, kurtosis),
        benchmark,
    )


def normal_inverse_cdf(probability: float) -> float:
    """Acklam inverse-normal approximation (absolute error < 1.2e-9)."""
    p = min(1.0 - 1e-15, max(1e-15, probability))
    a = (-39.6968302866538, 220.946098424521, -275.928510446969,
         138.357751867269, -30.6647980661472, 2.50662827745924)
    b = (-54.4760987982241, 161.585836858041, -155.698979859887,
         66.8013118877197, -13.2806815528857)
    c = (-0.00778489400243029, -0.322396458041136,
         -2.40075827716184, -2.54973253934373, 4.37466414146497,
         2.93816398269878)
    d = (0.00778469570904146, 0.32246712907004,
         2.445134137143, 3.75440866190742)
    low = 0.02425
    high = 1.0 - low
    if p < low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q
                 + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > high:
        return -normal_inverse_cdf(1.0 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r
             + a[4]) * r + a[5]) * q / (
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r
          + b[4]) * r + 1))


def correlation_clusters(
    vectors: dict[str, Iterable[float]], *, threshold: float = 0.85,
) -> tuple[dict[str, int], int]:
    """Deterministic single-link correlation clustering."""
    keys = sorted(vectors)
    parent = {key: key for key in keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    arrays = {key: np.asarray(list(vectors[key]), dtype=float) for key in keys}
    for left_index, left in enumerate(keys):
        for right in keys[left_index + 1:]:
            x, y = arrays[left], arrays[right]
            length = min(x.size, y.size)
            if length < 5:
                continue
            x, y = x[:length], y[:length]
            valid = np.isfinite(x) & np.isfinite(y)
            if int(valid.sum()) < 5:
                continue
            if np.std(x[valid]) == 0 or np.std(y[valid]) == 0:
                similarity = 1.0 if np.array_equal(x[valid], y[valid]) else 0.0
            else:
                similarity = abs(float(np.corrcoef(x[valid], y[valid])[0, 1]))
            if similarity >= threshold:
                union(left, right)
    roots = sorted({find(key) for key in keys})
    root_id = {root: index + 1 for index, root in enumerate(roots)}
    assignments = {key: root_id[find(key)] for key in keys}
    return assignments, len(roots)


def pbo_from_blocks(
    return_matrix: np.ndarray, *, minimum_blocks: int = 8,
) -> dict[str, float | int | str]:
    """Chronological block PBO diagnostic; unavailable when power is absent."""
    if return_matrix.ndim != 2 or return_matrix.shape[1] < minimum_blocks:
        return {
            "status": "UNAVAILABLE_INSUFFICIENT_BLOCKS",
            "independent_blocks": (
                int(return_matrix.shape[1]) if return_matrix.ndim == 2 else 0),
        }
    # Adjacent block pairs preserve chronology. Select in the earlier block,
    # rank the selected trial in the later block, and count below-median ranks.
    logits: list[float] = []
    trials, blocks = return_matrix.shape
    if trials < 2:
        return {"status": "UNAVAILABLE_INSUFFICIENT_BLOCKS",
                "independent_blocks": blocks}
    for index in range(blocks - 1):
        in_sample = return_matrix[:, index]
        out_sample = return_matrix[:, index + 1]
        if not np.any(np.isfinite(in_sample)) or not np.any(np.isfinite(out_sample)):
            continue
        selected = int(np.nanargmax(in_sample))
        ordered = np.argsort(np.nan_to_num(out_sample, nan=-np.inf))
        rank = int(np.where(ordered == selected)[0][0]) + 1
        relative = rank / (trials + 1.0)
        logits.append(math.log(relative / (1.0 - relative)))
    if len(logits) < minimum_blocks - 1:
        return {"status": "UNAVAILABLE_INSUFFICIENT_BLOCKS",
                "independent_blocks": blocks}
    return {
        "status": "AVAILABLE",
        "independent_blocks": blocks,
        "pbo": sum(value <= 0 for value in logits) / len(logits),
        "comparisons": len(logits),
    }
