"""Source-specific microstructure event studies.

Generates features from single source groups (funding, basis) and measures
forward returns against mark price observations.  Unlike the full
FeatureEngine which requires trades+OI+funding+basis all present, this
module enables research on any feature group that independently meets the
minimum sample threshold.

No strategy construction, ranking, or trading signals are produced.
All outputs are labelled ``exploratory_only = True``.
"""

from __future__ import annotations

import json
import hashlib
import math
import random
import statistics
import uuid
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from typing import Any

try:
    from microstructure import (
        INSTRUMENTS, HORIZONS, MINIMUM_SAMPLE_DAYS,
        MICROSTRUCTURE_REPORT_VERSION, MicrostructureStore, normalize_swap_instrument,
        now_ms,
    )
except ImportError:
    from .microstructure import (
        INSTRUMENTS, HORIZONS, MINIMUM_SAMPLE_DAYS,
        MICROSTRUCTURE_REPORT_VERSION, MicrostructureStore, normalize_swap_instrument,
        now_ms,
    )


def _rank(values: list[float]) -> list[float]:
    """Fractional ranks for Spearman correlation."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 5:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    return _pearson(_rank(xs), _rank(ys))


def _quantile_split(features: list[float], returns: list[float],
                    n_quantiles: int = 5) -> list[dict[str, Any]]:
    """Split observations into n_quantiles and report mean returns."""
    if len(features) < n_quantiles * 2:
        return []
    pairs = sorted(zip(features, returns), key=lambda p: p[0])
    chunk = len(pairs) // n_quantiles
    quantiles = []
    for q in range(n_quantiles):
        start = q * chunk
        end = start + chunk if q < n_quantiles - 1 else len(pairs)
        subset = pairs[start:end]
        rets = [p[1] for p in subset]
        quantiles.append({
            "quantile": q + 1,
            "n": len(subset),
            "feature_range": [subset[0][0], subset[-1][0]],
            "mean_return": statistics.mean(rets) if rets else 0.0,
            "median_return": statistics.median(rets) if rets else 0.0,
        })
    return quantiles


def _monotonicity(quantile_returns: list[dict[str, Any]]) -> float | None:
    """Score monotonicity of quantile mean returns.  1.0 = perfect ascending,
    -1.0 = perfect descending, 0.0 = no pattern."""
    means = [q["mean_return"] for q in quantile_returns]
    if len(means) < 3:
        return None
    ascending = sum(1 for i in range(1, len(means)) if means[i] > means[i - 1])
    total = len(means) - 1
    return (2 * ascending - total) / total


DISCLAIMER = "VALIDATION RESEARCH ONLY — NOT A TRADING SIGNAL"
RESEARCH_SEGMENT = "RESEARCH_CALIBRATION"
VALIDATION_SEGMENT = "LATER_VALIDATION"


def _correlation_p_value(correlation: float | None, count: int) -> float | None:
    """Two-sided Fisher-z normal diagnostic without a significance claim."""
    if correlation is None or count < 5:
        return None
    bounded = max(-0.999999, min(0.999999, correlation))
    z_value = abs(math.atanh(bounded)) * math.sqrt(count - 3)
    return math.erfc(z_value / math.sqrt(2))


def _bootstrap_ic(
    features: list[float], returns: list[float], *, rank: bool, seed: int,
    repetitions: int = 200,
) -> list[float] | None:
    if len(features) < 30:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(repetitions):
        indices = [rng.randrange(len(features)) for _ in features]
        left = [features[index] for index in indices]
        right = [returns[index] for index in indices]
        value = _spearman(left, right) if rank else _pearson(left, right)
        if value is not None:
            estimates.append(value)
    if len(estimates) < 20:
        return None
    estimates.sort()
    return [
        round(estimates[int(0.025 * (len(estimates) - 1))], 6),
        round(estimates[int(0.975 * (len(estimates) - 1))], 6),
    ]


class SourceSpecificEventStudy:
    """Event studies using only source-specific features where coverage permits.

    Unlike the full FeatureEngine which requires all sources, this engine
    generates features from a single source group and measures forward returns
    against mark price observations.

    No strategy construction, ranking, or trading signals are produced.
    """

    def __init__(self, store: MicrostructureStore) -> None:
        self.store = store
        self.report_id = f"source-study-{uuid.uuid4().hex[:8]}"
        self._mark_timestamp_cache: dict[int, list[int]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mark_prices(self, instrument: str) -> dict[int, float]:
        """Load mark prices keyed by timestamp_ms for fast lookup."""
        instrument = normalize_swap_instrument(instrument)
        with self.store.connect(readonly=True) as c:
            rows = c.execute(
                """SELECT source_ts_ms, close FROM mark_price_observations
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY source_ts_ms""",
                (instrument,),
            ).fetchall()
        result = {int(r["source_ts_ms"]): float(r["close"]) for r in rows}
        self._mark_timestamp_cache[id(result)] = list(result)
        return result

    def _forward_return(self, mark_prices: dict[int, float],
                        decision_ms: int, horizon_ms: int,
                        max_label_ms: int | None = None) -> float | None:
        """Find mark price at decision+horizon and calculate return."""
        details = self._forward_return_details(
            mark_prices, decision_ms, horizon_ms, max_label_ms=max_label_ms)
        return details[0] if details is not None else None

    def _forward_return_details(
        self, mark_prices: dict[int, float], decision_ms: int, horizon_ms: int,
        *, max_label_ms: int | None = None,
    ) -> tuple[float, int] | None:
        if not mark_prices:
            return None
        timestamps = self._mark_timestamp_cache.setdefault(
            id(mark_prices), list(mark_prices))
        base_position = bisect_right(timestamps, decision_ms) - 1
        target_ms = decision_ms + horizon_ms
        target_position = bisect_left(timestamps, target_ms)
        if base_position < 0 or target_position >= len(timestamps):
            return None
        base_ms = timestamps[base_position]
        forward_ms = timestamps[target_position]
        if max_label_ms is not None and forward_ms > max_label_ms:
            return None
        if decision_ms - base_ms > 60_000 or forward_ms - target_ms > 60_000:
            return None
        base_price = mark_prices[base_ms]
        if base_price == 0:
            return None
        return (mark_prices[forward_ms] - base_price) / base_price, forward_ms

    def _regime(self, mark_prices: dict[int, float], decision_ms: int) -> str:
        timestamps = self._mark_timestamp_cache.setdefault(
            id(mark_prices), list(mark_prices))
        current_position = bisect_right(timestamps, decision_ms) - 1
        prior_position = bisect_right(timestamps, decision_ms - 86_400_000) - 1
        if current_position < 0 or prior_position < 0:
            return "UNCLASSIFIED"
        prior = mark_prices[timestamps[prior_position]]
        change = mark_prices[timestamps[current_position]] / prior - 1 if prior else 0.0
        if change > 0.005:
            return "UP_24H"
        if change < -0.005:
            return "DOWN_24H"
        return "FLAT_24H"

    @staticmethod
    def _segment_metrics(
        feature_name: str, horizon: str,
        events: list[tuple[int, float, float, int, str]],
    ) -> dict[str, Any]:
        n = len(events)
        if n < 10:
            return {"event_count": n, "insufficient_sample": True}
        features = [event[1] for event in events]
        returns = [event[2] for event in events]
        pearson_ic = _pearson(features, returns)
        spearman_ic = _spearman(features, returns)
        quantiles = _quantile_split(features, returns)
        subperiod_ics = []
        for index in range(3):
            subset = events[index * n // 3:(index + 1) * n // 3]
            value = _spearman(
                [event[1] for event in subset],
                [event[2] for event in subset])
            subperiod_ics.append(value)
        non_null_subperiods = [value for value in subperiod_ics if value is not None]
        reference_sign = 0 if spearman_ic in (None, 0) else (1 if spearman_ic > 0 else -1)
        sign_consistency = (
            sum((1 if value > 0 else -1 if value < 0 else 0) == reference_sign
                for value in non_null_subperiods) / len(non_null_subperiods)
            if non_null_subperiods and reference_sign else 0.0)
        dispersion = (
            statistics.pstdev(non_null_subperiods)
            if len(non_null_subperiods) > 1 else 0.0)
        absolute_sum = sum(abs(value) for value in returns)
        regimes: dict[str, int] = {}
        for event in events:
            regimes[event[4]] = regimes.get(event[4], 0) + 1
        seed = int(hashlib.sha256(
            f"{feature_name}:{horizon}:{events[0][0]}:{n}".encode()
        ).hexdigest()[:16], 16)
        return {
            "event_count": n,
            "event_earliest_ms": events[0][0],
            "event_latest_ms": events[-1][0],
            "label_latest_ms": max(event[3] for event in events),
            "pearson_ic": round(pearson_ic, 6) if pearson_ic is not None else None,
            "spearman_ic": round(spearman_ic, 6) if spearman_ic is not None else None,
            "pearson_p_value_diagnostic": _correlation_p_value(pearson_ic, n),
            "spearman_p_value_diagnostic": _correlation_p_value(spearman_ic, n),
            "pearson_bootstrap_95_ci": _bootstrap_ic(
                features, returns, rank=False, seed=seed),
            "spearman_bootstrap_95_ci": _bootstrap_ic(
                features, returns, rank=True, seed=seed + 1),
            "quantile_returns": quantiles,
            "monotonicity": _monotonicity(quantiles),
            "mean_return": statistics.mean(returns),
            "median_return": statistics.median(returns),
            "return_std": statistics.stdev(returns) if n > 1 else 0.0,
            "sign_consistency": round(sign_consistency, 6),
            "temporal_stability": {
                "subperiod_spearman_ic": subperiod_ics,
                "ic_dispersion": dispersion,
            },
            "concentration": (
                max(abs(value) for value in returns) / absolute_sum
                if absolute_sum else 0.0),
            "regime_distribution": regimes,
        }

    def _study_features(self, feature_name: str, observations: list[tuple[int, float]],
                        mark_prices: dict[int, float],
                        instrument: str) -> dict[str, Any]:
        """Run causal chronological research/later-validation segments."""
        results: dict[str, Any] = {}
        overlapping_timestamps = [
            timestamp for timestamp, value in observations
            if math.isfinite(value)
            and self._forward_return(mark_prices, timestamp, HORIZONS["15m"]) is not None
        ]
        if len(overlapping_timestamps) < 2:
            partition_boundary = None
        else:
            partition_boundary = overlapping_timestamps[
                max(0, int(len(overlapping_timestamps) * 0.70) - 1)]
        for horizon_label, horizon_ms in HORIZONS.items():
            events: list[tuple[int, float, float, int, str]] = []
            for ts_ms, fval in observations:
                details = self._forward_return_details(mark_prices, ts_ms, horizon_ms)
                if details is not None and math.isfinite(fval):
                    events.append((
                        ts_ms, fval, details[0], details[1],
                        self._regime(mark_prices, ts_ms)))
            research_events = [
                event for event in events
                if partition_boundary is not None
                and event[0] <= partition_boundary
                and event[3] <= partition_boundary]
            validation_events = [
                event for event in events
                if partition_boundary is not None and event[0] > partition_boundary]
            overall = self._segment_metrics(feature_name, horizon_label, events)
            results[horizon_label] = {
                **overall,
                "partition_boundary_ms": partition_boundary,
                "segments": {
                    RESEARCH_SEGMENT: self._segment_metrics(
                        feature_name, f"{horizon_label}:{RESEARCH_SEGMENT}",
                        research_events),
                    VALIDATION_SEGMENT: self._segment_metrics(
                        feature_name, f"{horizon_label}:{VALIDATION_SEGMENT}",
                        validation_events),
                },
                "partition_policy": "first 70% calibration; later 30% validation",
                "segments_overlap": False,
                "completed_oot_claim": False,
            }
            self._save_result(feature_name, horizon_label,
                              {**results[horizon_label], "instrument": instrument},
                              len(events))
        return results

    def _save_result(self, feature_name: str, horizon: str,
                     payload: dict[str, Any], event_count: int) -> None:
        with self.store.connect() as c:
            c.execute(
                """INSERT OR REPLACE INTO event_study_results
                   (report_id, feature_name, horizon, payload_json,
                    event_count, created_at_ms)
                   VALUES(?,?,?,?,?,?)""",
                (self.report_id, feature_name, horizon,
                 json.dumps(payload), event_count, now_ms()),
            )

    @staticmethod
    def _apply_multiple_testing(feature_results: dict[str, dict[str, Any]]) -> None:
        tests = []
        for feature, horizons in feature_results.items():
            for horizon, result in horizons.items():
                for segment, metrics in result.get("segments", {}).items():
                    value = metrics.get("spearman_p_value_diagnostic")
                    if value is not None:
                        tests.append((feature, horizon, segment, float(value)))
        test_count = max(1, len(tests))
        for feature, horizon, segment, value in tests:
            metrics = feature_results[feature][horizon]["segments"][segment]
            metrics["multiple_testing"] = {
                "method": "Bonferroni diagnostic",
                "family_test_count": test_count,
                "adjusted_p_value": min(1.0, value * test_count),
                "can_promote_feature": False,
            }

    @staticmethod
    def _redundancy(
        features: dict[str, list[tuple[int, float]]]
    ) -> list[dict[str, Any]]:
        findings = []
        names = sorted(features)
        for left_index, left_name in enumerate(names):
            left = dict(features[left_name])
            for right_name in names[left_index + 1:]:
                right = dict(features[right_name])
                overlap = sorted(set(left) & set(right))
                left_values = [left[timestamp] for timestamp in overlap]
                right_values = [right[timestamp] for timestamp in overlap]
                pearson = _pearson(left_values, right_values)
                spearman = _spearman(left_values, right_values)
                findings.append({
                    "left": left_name, "right": right_name,
                    "overlapping_event_count": len(overlap),
                    "overlapping_event_identity": "exact source timestamp intersection",
                    "pearson_correlation": pearson,
                    "rank_correlation": spearman,
                    "duplicated_transform": bool(
                        (pearson is not None and abs(pearson) >= 0.95)
                        or (spearman is not None and abs(spearman) >= 0.95)),
                })
        return findings

    @staticmethod
    def _classifications(
        feature_results: dict[str, dict[str, Any]],
        redundancy: list[dict[str, Any]],
    ) -> dict[str, str]:
        redundant = {
            name for finding in redundancy if finding["duplicated_transform"]
            for name in (finding["left"], finding["right"])
        }
        classifications = {}
        for feature, horizons in feature_results.items():
            validation = [
                result.get("segments", {}).get(VALIDATION_SEGMENT, {})
                for result in horizons.values()
            ]
            adequate = [item for item in validation if item.get("event_count", 0) >= 30]
            if len(adequate) < max(1, len(horizons) // 2):
                classification = "INSUFFICIENT_SAMPLE"
            elif feature in redundant:
                classification = "REDUNDANT"
            else:
                significant = [
                    item for item in adequate
                    if item.get("multiple_testing", {}).get("adjusted_p_value", 1) <= 0.05
                ]
                signs = [
                    1 if item.get("spearman_ic", 0) > 0 else -1
                    if item.get("spearman_ic", 0) < 0 else 0
                    for item in adequate
                ]
                dominant = max((signs.count(-1), signs.count(1)))
                consistency = dominant / len(signs) if signs else 0.0
                median_ic = statistics.median(
                    abs(float(item.get("spearman_ic") or 0)) for item in adequate)
                if len(significant) >= 3 and consistency >= 0.7:
                    classification = "VALIDATION_PROMISING"
                elif consistency < 0.6:
                    classification = "UNSTABLE"
                elif median_ic < 0.03:
                    classification = "NO_DESCRIPTIVE_RELATIONSHIP"
                else:
                    classification = "CONTINUE_COLLECTING"
            classifications[feature] = classification
        return classifications

    # ------------------------------------------------------------------
    # Funding study
    # ------------------------------------------------------------------

    def _funding_features(self, instrument: str) -> dict[str, list[tuple[int, float]]]:
        """Extract funding features from settled funding data."""
        instrument = normalize_swap_instrument(instrument)
        with self.store.connect(readonly=True) as c:
            rows = c.execute(
                """SELECT funding_time_ms, funding_rate FROM funding_settled
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY funding_time_ms""",
                (instrument,),
            ).fetchall()
        if len(rows) < 10:
            return {}

        features: dict[str, list[tuple[int, float]]] = {
            "funding_level": [],
            "funding_change": [],
            "funding_zscore": [],
        }
        rates = [float(r["funding_rate"]) for r in rows]
        timestamps = [int(r["funding_time_ms"]) for r in rows]

        for i in range(1, len(rows)):
            ts = timestamps[i]
            rate = rates[i]
            features["funding_level"].append((ts, rate))
            features["funding_change"].append((ts, rate - rates[i - 1]))
            # Rolling z-score using last 20 observations
            window = rates[max(0, i - 19):i + 1]
            if len(window) >= 5:
                mean = statistics.mean(window)
                std = statistics.stdev(window) if len(window) > 1 else 1e-10
                features["funding_zscore"].append(
                    (ts, (rate - mean) / std if std > 1e-10 else 0.0)
                )
        return features

    def run_funding_study(self) -> dict[str, Any]:
        """Study every genuine settled-funding/mark overlap."""
        elig = self.store.per_feature_eligibility()
        group = elig.get("feature_groups", {}).get("settled_funding", {})

        results_by_instrument: dict[str, dict[str, Any]] = {}
        for instrument in INSTRUMENTS:
            mark = self._mark_prices(instrument)
            if not mark:
                continue
            features = self._funding_features(instrument)
            if not features:
                continue
            inst_results: dict[str, Any] = {}
            for feat_name, observations in features.items():
                inst_results[feat_name] = self._study_features(
                    f"{feat_name}_{instrument}", observations, mark, instrument
                )
            self._apply_multiple_testing(inst_results)
            redundancy = self._redundancy(features)
            results_by_instrument[instrument] = {
                "features": inst_results,
                "redundancy": redundancy,
                "feature_classifications": self._classifications(
                    inst_results, redundancy),
                "economic_interpretation": (
                    "Funding cost can plausibly affect positioning and later mark returns; "
                    "sign and horizon survival are reported descriptively, not selected."),
                "eligibility": group.get("instruments", {}).get(instrument),
            }

        return {
            "exploratory_only": True,
            "disclaimer": DISCLAIMER,
            "study_type": "funding_settled",
            "report_id": self.report_id,
            "coverage_days": group.get("gap_adjusted_sample_days", 0),
            "source_data_status": group.get("source_data_status"),
            "event_study_status": group.get("event_study_status"),
            "chronological_segments": [RESEARCH_SEGMENT, VALIDATION_SEGMENT],
            "completed_oot_claim": False,
            "oot_status": "NOT_CREATED",
            "instruments": results_by_instrument,
        }

    # ------------------------------------------------------------------
    # Basis study
    # ------------------------------------------------------------------

    def _basis_features(self, instrument: str) -> dict[str, list[tuple[int, float]]]:
        """Extract basis features from basis_aggregates."""
        instrument = normalize_swap_instrument(instrument)
        with self.store.connect(readonly=True) as c:
            rows = c.execute(
                """SELECT bucket_ms,last_basis,last_basis_pct,expansion
                   FROM basis_aggregates
                   WHERE instrument=? AND resolution='1H'
                   ORDER BY bucket_ms""",
                (instrument,),
            ).fetchall()
            funding_rows = c.execute(
                """SELECT funding_time_ms,funding_rate FROM funding_settled
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY funding_time_ms""", (instrument,)).fetchall()
        if len(rows) < 10:
            return {}

        features: dict[str, list[tuple[int, float]]] = {
            "basis_level": [],
            "basis_zscore": [],
            "basis_change": [],
            "basis_expansion_contraction": [],
            "basis_absolute": [],
            "basis_percentage": [],
            "basis_funding_adjusted": [],
        }
        values = [float(r["last_basis_pct"]) for r in rows]
        timestamps = [int(r["bucket_ms"]) for r in rows]
        funding_times = [int(row["funding_time_ms"]) for row in funding_rows]
        funding_values = [float(row["funding_rate"]) for row in funding_rows]

        for i in range(1, len(rows)):
            ts = timestamps[i]
            val = values[i]
            features["basis_level"].append((ts, val))
            features["basis_absolute"].append((ts, float(rows[i]["last_basis"])))
            features["basis_percentage"].append((ts, val))
            features["basis_change"].append((ts, val - values[i - 1]))
            features["basis_expansion_contraction"].append(
                (ts, float(rows[i]["expansion"]))
            )
            funding_position = bisect_right(funding_times, ts) - 1
            if (funding_position >= 0
                    and ts - funding_times[funding_position] <= 28_800_000):
                features["basis_funding_adjusted"].append(
                    (ts, val - funding_values[funding_position]))
            # Rolling z-score with last 24 observations (24 hours at 1H)
            window = values[max(0, i - 23):i + 1]
            if len(window) >= 5:
                mean = statistics.mean(window)
                std = statistics.stdev(window) if len(window) > 1 else 1e-10
                features["basis_zscore"].append(
                    (ts, (val - mean) / std if std > 1e-10 else 0.0)
                )
        return features

    def run_basis_study(self) -> dict[str, Any]:
        """Study every genuine basis/mark overlap."""
        elig = self.store.per_feature_eligibility()
        group = elig.get("feature_groups", {}).get("basis", {})

        results_by_instrument: dict[str, dict[str, Any]] = {}
        for instrument in ("BTC-USDT-SWAP",):
            mark = self._mark_prices(instrument)
            if not mark:
                continue
            features = self._basis_features(instrument)
            if not features:
                continue
            inst_results: dict[str, Any] = {}
            for feat_name, observations in features.items():
                inst_results[feat_name] = self._study_features(
                    f"{feat_name}_{instrument}", observations, mark, instrument
                )
            self._apply_multiple_testing(inst_results)
            redundancy = self._redundancy(features)
            results_by_instrument[instrument] = {
                "features": inst_results,
                "redundancy": redundancy,
                "feature_classifications": self._classifications(
                    inst_results, redundancy),
                "economic_interpretation": (
                    "Perpetual-versus-index basis can plausibly reflect carry and positioning; "
                    "later-segment sign survival is required for any promising label."),
                "eligibility": group.get("instruments", {}).get(instrument),
                "exact_source_label_overlap": {
                    "source_earliest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("source_earliest_ms"),
                    "source_latest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("source_latest_ms"),
                    "label_earliest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("label_earliest_ms"),
                    "label_latest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("label_latest_ms"),
                    "overlap_earliest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("overlap_earliest_ms"),
                    "overlap_latest_ms": group.get("instruments", {}).get(
                        instrument, {}).get("overlap_latest_ms"),
                },
            }

        return {
            "exploratory_only": True,
            "disclaimer": DISCLAIMER,
            "study_type": "basis",
            "report_id": self.report_id,
            "coverage_days": group.get("gap_adjusted_sample_days", 0),
            "source_data_status": group.get("source_data_status"),
            "event_study_status": group.get("event_study_status"),
            "inclusion_policy": (
                "BTC only; ETH and SOL remain excluded until each reaches "
                f"{MINIMUM_SAMPLE_DAYS} genuine overlapping days."),
            "chronological_segments": [RESEARCH_SEGMENT, VALIDATION_SEGMENT],
            "completed_oot_claim": False,
            "oot_status": "NOT_CREATED",
            "instruments": results_by_instrument,
        }

    # ------------------------------------------------------------------
    # Funding + Basis interaction
    # ------------------------------------------------------------------

    def run_funding_basis_interaction(self) -> dict[str, Any]:
        """Study divergence between funding and basis z-scores."""
        elig = self.store.per_feature_eligibility()
        funding_group = elig.get("feature_groups", {}).get("settled_funding", {})
        basis_group = elig.get("feature_groups", {}).get("basis", {})

        results_by_instrument: dict[str, dict[str, Any]] = {}
        for instrument in INSTRUMENTS:
            mark = self._mark_prices(instrument)
            if not mark:
                continue
            funding_feats = self._funding_features(instrument)
            basis_feats = self._basis_features(instrument)
            if not funding_feats.get("funding_zscore") or not basis_feats.get("basis_zscore"):
                continue

            # Build timestamp-indexed lookups
            fz = {ts: v for ts, v in funding_feats["funding_zscore"]}
            bz = {ts: v for ts, v in basis_feats["basis_zscore"]}

            # Find overlapping time windows — use basis timestamps, find
            # nearest funding z-score within 8 hours
            divergence_obs: list[tuple[int, float]] = []
            for ts_b, val_b in basis_feats["basis_zscore"]:
                # Find nearest funding z within ±8H
                best_fz = None
                best_dist = 8 * 3_600_000 + 1
                for ts_f, val_f in funding_feats["funding_zscore"]:
                    dist = abs(ts_b - ts_f)
                    if dist < best_dist:
                        best_dist = dist
                        best_fz = val_f
                if best_fz is not None and best_dist <= 8 * 3_600_000:
                    # Divergence: positive when funding and basis z point opposite
                    divergence = best_fz * -val_b
                    divergence_obs.append((ts_b, divergence))

            if len(divergence_obs) >= 10:
                inst_results = {
                    "funding_basis_divergence": self._study_features(
                        f"funding_basis_divergence_{instrument}",
                        divergence_obs, mark, instrument,
                    )
                }
                results_by_instrument[instrument] = inst_results

        return {
            "exploratory_only": True,
            "study_type": "funding_basis_interaction",
            "report_id": self.report_id,
            "source_data_status": {
                "settled_funding": funding_group.get("source_data_status"),
                "basis": basis_group.get("source_data_status"),
            },
            "event_study_status": {
                "settled_funding": funding_group.get("event_study_status"),
                "basis": basis_group.get("event_study_status"),
            },
            "instruments": results_by_instrument,
        }

    # ------------------------------------------------------------------
    # Combined entry point
    # ------------------------------------------------------------------

    def run_all_eligible(self) -> dict[str, Any]:
        """Run studies for all feature groups meeting minimum sample."""
        elig = self.store.per_feature_eligibility()
        results: dict[str, Any] = {
            "exploratory_only": True,
            "disclaimer": DISCLAIMER,
            "report_id": self.report_id,
            "report_version": MICROSTRUCTURE_REPORT_VERSION,
            "eligibility_snapshot": elig,
            "chronological_partition_policy": (
                "Actual overlapping events split 70% research/calibration and "
                "30% later validation; calibration labels cannot cross the boundary."),
            "completed_oot_claim": False,
            "oot_status": "NOT_CREATED",
            "multiple_testing_can_promote_features": False,
            "studies": {},
        }

        results["studies"]["funding"] = self.run_funding_study()
        results["studies"]["basis"] = self.run_basis_study()
        results["studies"]["funding_basis_interaction"] = \
            self.run_funding_basis_interaction()

        with self.store.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO research_manifests
                   (manifest_id,manifest_type,version,status,payload_json,created_at_ms)
                   VALUES(?,?,?,?,?,?)""",
                (self.report_id, "bounded_microstructure_validation",
                 MICROSTRUCTURE_REPORT_VERSION, "VALIDATION_RESEARCH_ONLY",
                 json.dumps(results), now_ms()))
        return results

    @staticmethod
    def latest_summary(store: MicrostructureStore) -> dict[str, Any]:
        with store.connect(readonly=True) as connection:
            row = connection.execute(
                """SELECT payload_json,created_at_ms FROM research_manifests
                   WHERE manifest_type='bounded_microstructure_validation'
                   ORDER BY created_at_ms DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return {
                "available": False, "disclaimer": DISCLAIMER,
                "completed_oot_claim": False, "oot_status": "NOT_CREATED",
            }
        result = json.loads(row["payload_json"])
        result["available"] = True
        result["created_at_ms"] = int(row["created_at_ms"])
        return result
