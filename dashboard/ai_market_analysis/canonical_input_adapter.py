"""Canonical factual input boundary for AI market analysis.

AI deterministic intelligence deliberately remains an independent tactical
lens.  This adapter guarantees that lens and ``CanonicalMarketSnapshot`` are
built from the same bounded candle rows, instrument and causal cutoff.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dashboard.market_context_v2 import BoundedMarketDataReaderV2, MarketContextServiceV2
from dashboard.microstructure_evidence import (
    CanonicalMicrostructureEvidenceAdapter,
    canonical_market_evidence_set,
)

from .context_adapter import build_market_analysis_context
from .readonly_adapter import MAX_ORDERFLOW_QUERY_SECONDS, ReadOnlyOrderflowAdapter
from .versions import SUPPORTED_TIMEFRAMES


class _FrozenCanonicalReader:
    """Replay one bounded read set through Market Context without re-fetching."""

    def __init__(self, datasets: dict[str, list[dict[str, Any]]],
                 source: BoundedMarketDataReaderV2) -> None:
        self._datasets = datasets
        self._source = source

    def candles(self, _instrument: str, timeframe: str, _as_of: int,
                limit: int) -> list[dict[str, Any]]:
        rows = self._datasets.get(timeframe, [])
        return rows[-int(limit):]

    def flow(self, instrument: str, as_of: int,
             execution_timeframe: str) -> dict[str, dict[str, Any]]:
        # The AI order-flow adapter below owns canonical-history evidence.
        # Market Context must not reinterpret a legacy aggregate schema while
        # producing the shared price/candle snapshot.
        return {name: {} for name in (
            "cvd", "oi", "funding_settled", "funding_predicted", "basis",
        )}


def _orderflow(datasets: dict[str, list[dict[str, Any]]], instrument: str,
               decision: int, micro_db: str | Path | None) -> dict[str, Any] | None:
    canonical_path = Path(os.getenv(
        "CANONICAL_MICROSTRUCTURE_HISTORY_DB_PATH", str(micro_db or "")
    ))
    if ReadOnlyOrderflowAdapter.available(canonical_path):
        raw_start = min(
            (int(row["ts"]) for rows in datasets.values() for row in rows),
            default=decision - 30 * 86_400,
        )
        start = max(raw_start, decision - MAX_ORDERFLOW_QUERY_SECONDS)
        return ReadOnlyOrderflowAdapter(
            canonical_path, supplemental_path=micro_db
        ).read(instrument, start, decision, "4H")
    if micro_db:
        # An aggregate or live DB is not silently substituted for canonical
        # history.  Missing remains explicit input evidence.
        return {
            "cvd": [], "oi": [], "basis": [], "funding": [],
            "liquidation": [], "liquidation_complete": False,
            "canonical_metadata": {
                "source_contract": "UNAVAILABLE", "synthetic_data": False,
                "interpolation": False,
            },
        }
    return None


def build_canonical_ai_context(*, instrument: str, decision: int, mode: str,
                               paper_db: str | Path,
                               micro_db: str | Path | None) -> dict[str, Any]:
    """Build canonical facts and the AI lens from one immutable read set."""

    reader = BoundedMarketDataReaderV2(paper_db, micro_db)
    datasets = {
        timeframe: reader.candles(
            instrument, timeframe, decision,
            1_500 if timeframe == "1D" else 512,
        )
        for timeframe in SUPPORTED_TIMEFRAMES
    }
    frozen_reader = _FrozenCanonicalReader(datasets, reader)
    microstructure_evidence = None
    if micro_db:
        canonical_path = Path(os.getenv(
            "CANONICAL_MICROSTRUCTURE_HISTORY_DB_PATH", str(micro_db)
        ))
        evidence_adapter = CanonicalMicrostructureEvidenceAdapter(
            micro_db, canonical_path if canonical_path.is_file() else None,
        )
        microstructure_evidence = canonical_market_evidence_set(
            evidence_adapter, instrument, decision,
        )
    snapshot = MarketContextServiceV2(frozen_reader).canonical_snapshot(
        instrument, as_of=decision, execution_timeframe="15m",
        microstructure_evidence=microstructure_evidence,
    )
    return build_market_analysis_context(
        datasets, instrument, decision, mode,
        orderflow=_orderflow(datasets, instrument, decision, micro_db),
        canonical_snapshot=snapshot.to_dict(),
    )
