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
import math
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from microstructure import (
        INSTRUMENTS, HORIZONS, MINIMUM_SAMPLE_DAYS,
        MICROSTRUCTURE_REPORT_VERSION, MicrostructureStore, now_ms,
    )
except ImportError:
    from .microstructure import (
        INSTRUMENTS, HORIZONS, MINIMUM_SAMPLE_DAYS,
        MICROSTRUCTURE_REPORT_VERSION, MicrostructureStore, now_ms,
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mark_prices(self, instrument: str) -> dict[int, float]:
        """Load mark prices keyed by timestamp_ms for fast lookup."""
        with self.store.connect(readonly=True) as c:
            rows = c.execute(
                """SELECT source_ts_ms, close FROM mark_price_observations
                   WHERE instrument=? AND state='confirmed'
                   ORDER BY source_ts_ms""",
                (instrument,),
            ).fetchall()
        return {int(r["source_ts_ms"]): float(r["close"]) for r in rows}

    def _forward_return(self, mark_prices: dict[int, float],
                        decision_ms: int, horizon_ms: int) -> float | None:
        """Find mark price at decision+horizon and calculate return."""
        target_ms = decision_ms + horizon_ms
        # Find the earliest confirmed mark >= target_ms
        closest_ms = None
        for ts in mark_prices:
            if ts >= target_ms:
                if closest_ms is None or ts < closest_ms:
                    closest_ms = ts
        if closest_ms is None:
            return None
        base_price = mark_prices.get(decision_ms)
        if base_price is None:
            # Use latest mark at or before decision_ms
            candidates = [ts for ts in mark_prices if ts <= decision_ms]
            if not candidates:
                return None
            base_price = mark_prices[max(candidates)]
        if base_price == 0:
            return None
        return (mark_prices[closest_ms] - base_price) / base_price

    def _study_features(self, feature_name: str, observations: list[tuple[int, float]],
                        mark_prices: dict[int, float],
                        instrument: str) -> dict[str, Any]:
        """Run event study on a single feature across all horizons."""
        results: dict[str, Any] = {}
        for horizon_label, horizon_ms in HORIZONS.items():
            features_vals: list[float] = []
            returns_vals: list[float] = []
            for ts_ms, fval in observations:
                fwd = self._forward_return(mark_prices, ts_ms, horizon_ms)
                if fwd is not None and math.isfinite(fval):
                    features_vals.append(fval)
                    returns_vals.append(fwd)
            n = len(features_vals)
            if n < 10:
                results[horizon_label] = {
                    "event_count": n, "insufficient_sample": True
                }
                continue
            quantiles = _quantile_split(features_vals, returns_vals)
            mono = _monotonicity(quantiles)
            pearson_ic = _pearson(features_vals, returns_vals)
            spearman_ic = _spearman(features_vals, returns_vals)
            results[horizon_label] = {
                "event_count": n,
                "pearson_ic": round(pearson_ic, 6) if pearson_ic is not None else None,
                "spearman_ic": round(spearman_ic, 6) if spearman_ic is not None else None,
                "monotonicity": round(mono, 4) if mono is not None else None,
                "quantile_returns": quantiles,
                "mean_return": round(statistics.mean(returns_vals), 8),
                "return_std": round(statistics.stdev(returns_vals), 8) if n > 1 else 0.0,
            }
            # Persist to event_study_results
            self._save_result(feature_name, horizon_label,
                              {**results[horizon_label], "instrument": instrument},
                              n)
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

    # ------------------------------------------------------------------
    # Funding study
    # ------------------------------------------------------------------

    def _funding_features(self, instrument: str) -> dict[str, list[tuple[int, float]]]:
        """Extract funding features from settled funding data."""
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
        """Study settled funding features where coverage >= 14 days."""
        elig = self.store.per_feature_eligibility()
        group = elig.get("feature_groups", {}).get("settled_funding", {})
        if group.get("status") == "EXPLORATORY_ONLY":
            return {
                "exploratory_only": True, "study_type": "funding_settled",
                "skipped": True, "reason": "insufficient_coverage",
                "usable_days": group.get("gap_adjusted_sample_days", 0),
            }

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
            results_by_instrument[instrument] = inst_results

        return {
            "exploratory_only": True,
            "study_type": "funding_settled",
            "report_id": self.report_id,
            "coverage_days": group.get("gap_adjusted_sample_days", 0),
            "instruments": results_by_instrument,
        }

    # ------------------------------------------------------------------
    # Basis study
    # ------------------------------------------------------------------

    def _basis_features(self, instrument: str) -> dict[str, list[tuple[int, float]]]:
        """Extract basis features from basis_aggregates."""
        with self.store.connect(readonly=True) as c:
            rows = c.execute(
                """SELECT bucket_ms, last_basis_pct, expansion
                   FROM basis_aggregates
                   WHERE instrument=? AND resolution='1H'
                   ORDER BY bucket_ms""",
                (instrument,),
            ).fetchall()
        if len(rows) < 10:
            return {}

        features: dict[str, list[tuple[int, float]]] = {
            "basis_level": [],
            "basis_zscore": [],
            "basis_expansion_contraction": [],
        }
        values = [float(r["last_basis_pct"]) for r in rows]
        timestamps = [int(r["bucket_ms"]) for r in rows]

        for i in range(1, len(rows)):
            ts = timestamps[i]
            val = values[i]
            features["basis_level"].append((ts, val))
            features["basis_expansion_contraction"].append(
                (ts, float(rows[i]["expansion"]))
            )
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
        """Study basis features where mark+index coverage >= 14 days."""
        elig = self.store.per_feature_eligibility()
        group = elig.get("feature_groups", {}).get("basis", {})
        if group.get("status") == "EXPLORATORY_ONLY":
            return {
                "exploratory_only": True, "study_type": "basis",
                "skipped": True, "reason": "insufficient_coverage",
                "usable_days": group.get("gap_adjusted_sample_days", 0),
            }

        results_by_instrument: dict[str, dict[str, Any]] = {}
        for instrument in INSTRUMENTS:
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
            results_by_instrument[instrument] = inst_results

        return {
            "exploratory_only": True,
            "study_type": "basis",
            "report_id": self.report_id,
            "coverage_days": group.get("gap_adjusted_sample_days", 0),
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
        if (funding_group.get("status") == "EXPLORATORY_ONLY" or
                basis_group.get("status") == "EXPLORATORY_ONLY"):
            return {
                "exploratory_only": True,
                "study_type": "funding_basis_interaction",
                "skipped": True, "reason": "insufficient_coverage",
            }

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
            "report_id": self.report_id,
            "report_version": MICROSTRUCTURE_REPORT_VERSION,
            "eligibility_snapshot": elig,
            "studies": {},
        }

        groups = elig.get("feature_groups", {})

        if groups.get("settled_funding", {}).get("status") != "EXPLORATORY_ONLY":
            results["studies"]["funding"] = self.run_funding_study()

        if groups.get("basis", {}).get("status") != "EXPLORATORY_ONLY":
            results["studies"]["basis"] = self.run_basis_study()

        if (groups.get("settled_funding", {}).get("status") != "EXPLORATORY_ONLY" and
                groups.get("basis", {}).get("status") != "EXPLORATORY_ONLY"):
            results["studies"]["funding_basis_interaction"] = \
                self.run_funding_basis_interaction()

        return results
