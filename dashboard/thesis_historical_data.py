"""Versioned, fail-closed historical dataset selection for thesis studies.

The selector deliberately chooses one physical store/table/source partition.  It
never unions a frozen research database with a recent/live database and never
joins source versions merely because their timestamps happen to touch.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

try:
    from market_context_v2 import TIMEFRAME_SECONDS
    from signal_identity import canonical_json
except ImportError:
    from .market_context_v2 import TIMEFRAME_SECONDS
    from .signal_identity import canonical_json


HISTORICAL_DATA_SELECTION_POLICY_VERSION = "historical-data-selection-policy-v1"
HISTORICAL_DATASET_IDENTITY_VERSION = "historical-thesis-dataset-v1"
MINIMUM_RESEARCH_SPAN_SECONDS = 180 * 86_400
MINIMUM_RESEARCH_SPAN_POLICY_VERSION = "thesis-minimum-research-span-v1"
MAX_HISTORICAL_ROWS = 30_000


class HistoricalDataSelectionError(ValueError):
    """A stable, user-safe failure to select qualified historical evidence."""


@dataclass(frozen=True)
class HistoricalStoreV1:
    path: Path
    role: str
    priority: int
    expected_file_sha256: str | None = None
    declared_dataset_id: str | None = None


@dataclass(frozen=True)
class HistoricalDataSelectionV1:
    policy_version: str
    dataset_identity_version: str
    dataset_id: str
    source_label: str
    source_type: str
    source_name: str
    source_version: str | None
    store_role: str
    table: str
    instrument: str
    timeframe: str
    raw_start: int
    raw_end: int
    row_count: int
    continuity: str
    gap_count: int
    point_in_time_safe: bool
    confirmed_semantics: str
    content_sha256: str
    immutable_store_sha256: str
    immutable_store_verification: str
    declared_dataset_id: str | None
    effective_as_of: int
    minimum_research_span_seconds: int
    minimum_research_span_policy_version: str
    span_days: int
    breadth_qualification: str

    def public_dict(self) -> dict[str, Any]:
        # No filesystem path is part of this public/auditable contract.
        return asdict(self)


@dataclass(frozen=True)
class SelectedHistoricalDatasetV1:
    selection: HistoricalDataSelectionV1
    rows: tuple[Mapping[str, Any], ...]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _source_label(source: str) -> str:
    upper = source.upper()
    if "OKX" in upper:
        return "Canonical OKX OHLCV"
    if "BINANCE" in upper:
        return "Canonical Binance OHLCV"
    if source == "persisted_confirmed_market_candles":
        return "Recent canonical OHLCV"
    return "Canonical historical OHLCV"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_digest(rows: Sequence[Mapping[str, Any]], *, policy_version: str,
                    instrument: str, timeframe: str, source: str,
                    source_version: str | None, store_role: str, table: str) -> str:
    payload = {
        "policy_version": policy_version,
        "instrument": instrument,
        "timeframe": timeframe,
        "source": source,
        "source_version": source_version,
        "store_role": store_role,
        "table": table,
        "rows": [{key: row.get(key) for key in (
            "ts", "candle_close_ts", "open", "high", "low", "close", "volume", "confirmed"
        )} for row in rows],
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


class HistoricalDataSelectionPolicyV1:
    """Choose the longest qualified single-source partition deterministically."""

    version = HISTORICAL_DATA_SELECTION_POLICY_VERSION

    def __init__(self, stores: Iterable[HistoricalStoreV1]) -> None:
        unique: dict[tuple[str, str], HistoricalStoreV1] = {}
        for store in stores:
            resolved = Path(store.path).resolve()
            unique[(str(resolved), store.role)] = HistoricalStoreV1(
                resolved, str(store.role), int(store.priority), store.expected_file_sha256,
                store.declared_dataset_id,
            )
        self.stores = tuple(sorted(unique.values(), key=lambda item: (item.priority, item.role)))

    def select(self, instrument: str, timeframe: str, requested_as_of: int,
               required_source_groups: Sequence[str]) -> SelectedHistoricalDatasetV1:
        if set(required_source_groups) - {"OHLCV"}:
            raise HistoricalDataSelectionError(
                "native aligned historical data is unavailable for one or more required source groups"
            )
        if timeframe not in TIMEFRAME_SECONDS:
            raise HistoricalDataSelectionError("unsupported historical timeframe")
        candidates: list[tuple[int, int, str, SelectedHistoricalDatasetV1]] = []
        rejected_gaps = 0
        frozen_stores = tuple(store for store in self.stores if store.role == "frozen_research")
        considered_stores = frozen_stores or self.stores
        for store in considered_stores:
            if not store.path.is_file():
                if store.role == "frozen_research":
                    raise HistoricalDataSelectionError("configured frozen historical store is unavailable")
                continue
            file_sha256 = _file_sha256(store.path)
            if store.expected_file_sha256 and file_sha256 != store.expected_file_sha256:
                raise HistoricalDataSelectionError("configured frozen historical store failed immutable SHA-256 verification")
            try:
                selected, gaps = self._store_candidates(store, instrument, timeframe, requested_as_of, file_sha256)
            except sqlite3.Error:
                continue
            rejected_gaps += gaps
            if store.role == "frozen_research" and not selected:
                raise HistoricalDataSelectionError(
                    "configured frozen historical store has no continuous confirmed partition for this request"
                    + (" because the available partition contains a gap" if gaps else "")
                )
            for item in selected:
                span = item.selection.raw_end - item.selection.raw_start
                # Longest continuous evidence wins. Store priority only breaks ties.
                candidates.append((-span, store.priority, item.selection.dataset_id, item))
        if not candidates:
            suffix = " because every available partition contains a gap" if rejected_gaps else ""
            raise HistoricalDataSelectionError(f"no continuous confirmed historical OHLCV dataset is available{suffix}")
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _store_candidates(self, store: HistoricalStoreV1, instrument: str,
                          timeframe: str, requested_as_of: int, file_sha256: str
                          ) -> tuple[list[SelectedHistoricalDatasetV1], int]:
        uri = f"file:{store.path.as_posix()}?mode=ro"
        output: list[SelectedHistoricalDatasetV1] = []
        rejected_gaps = 0
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            if _table_exists(connection, "historical_candles"):
                columns = _columns(connection, "historical_candles")
                source_expr = "COALESCE(source, 'canonical_historical_ohlcv')" if "source" in columns else "'canonical_historical_ohlcv'"
                version_expr = "source_version" if "source_version" in columns else "NULL"
                partitions = connection.execute(
                    f"""SELECT {source_expr} source_name, {version_expr} source_version
                         FROM historical_candles
                         WHERE instrument=? AND timeframe=? AND confirmed=1 AND ts<=?
                         GROUP BY source_name, source_version""",
                    (instrument, timeframe, requested_as_of),
                ).fetchall()
                for partition in partitions:
                    source, version = str(partition["source_name"]), partition["source_version"]
                    version_clause = "source_version IS NULL" if version is None and "source_version" in columns else "source_version=?"
                    parameters: list[Any] = [instrument, timeframe, requested_as_of, source]
                    if "source" not in columns:
                        source_clause = "1=1"
                        parameters.pop()
                    else:
                        source_clause = "COALESCE(source, 'canonical_historical_ohlcv')=?"
                    if "source_version" not in columns:
                        version_clause = "1=1"
                    elif version is not None:
                        parameters.append(version)
                    rows = connection.execute(
                        f"""SELECT ts,open,high,low,close,volume,confirmed
                             FROM historical_candles WHERE instrument=? AND timeframe=?
                               AND confirmed=1 AND ts<=? AND {source_clause} AND {version_clause}
                             ORDER BY ts LIMIT ?""",
                        (*parameters, MAX_HISTORICAL_ROWS),
                    ).fetchall()
                    item = self._qualify_rows(store, "historical_candles", source,
                                              None if version is None else str(version),
                                              instrument, timeframe, requested_as_of, rows, file_sha256)
                    if item is None:
                        rejected_gaps += 1
                    else:
                        output.append(item)
            if _table_exists(connection, "market_candles"):
                columns = _columns(connection, "market_candles")
                bar_column = "bar" if "bar" in columns else "timeframe"
                rows = connection.execute(
                    f"""SELECT ts,open,high,low,close,volume,1 confirmed
                         FROM market_candles WHERE instrument=? AND {bar_column}=? AND ts<=?
                         ORDER BY ts LIMIT ?""",
                    (instrument, timeframe, requested_as_of, MAX_HISTORICAL_ROWS),
                ).fetchall()
                if rows:
                    item = self._qualify_rows(
                        store, "market_candles", "persisted_confirmed_market_candles", None,
                        instrument, timeframe, requested_as_of, rows, file_sha256,
                    )
                    if item is None:
                        rejected_gaps += 1
                    else:
                        output.append(item)
        return output, rejected_gaps

    def _qualify_rows(self, store: HistoricalStoreV1, table: str, source: str,
                      source_version: str | None, instrument: str, timeframe: str,
                      requested_as_of: int, db_rows: Sequence[sqlite3.Row], file_sha256: str
                      ) -> SelectedHistoricalDatasetV1 | None:
        width = TIMEFRAME_SECONDS[timeframe]
        rows = [{**dict(row), "candle_close_ts": int(row["ts"]) + width,
                 "source": source, "source_version": source_version,
                 "_source_store": table} for row in db_rows
                if int(row["ts"]) + width <= requested_as_of]
        if not rows:
            return None
        timestamps = [int(row["ts"]) for row in rows]
        gaps = sum(right - left != width for left, right in zip(timestamps, timestamps[1:]))
        if gaps:
            return None
        digest = _content_digest(
            rows, policy_version=self.version, instrument=instrument, timeframe=timeframe,
            source=source, source_version=source_version, store_role=store.role, table=table,
        )
        raw_start, raw_end = int(rows[0]["candle_close_ts"]), int(rows[-1]["candle_close_ts"])
        span_seconds = max(0, raw_end - raw_start)
        identity_payload = {
            "version": HISTORICAL_DATASET_IDENTITY_VERSION,
            "policy_version": self.version,
            "instrument": instrument, "timeframe": timeframe,
            "source": source, "source_version": source_version,
            "store_role": store.role, "table": table,
            "raw_start": raw_start, "raw_end": raw_end,
            "row_count": len(rows), "content_sha256": digest,
            "immutable_store_sha256": file_sha256,
            "declared_dataset_id": store.declared_dataset_id,
        }
        dataset_id = hashlib.sha256(canonical_json(identity_payload).encode()).hexdigest()
        selection = HistoricalDataSelectionV1(
            policy_version=self.version,
            dataset_identity_version=HISTORICAL_DATASET_IDENTITY_VERSION,
            dataset_id=dataset_id,
            source_label=_source_label(source),
            source_type="FROZEN_CANONICAL" if store.role == "frozen_research" else "CANONICAL_STORE",
            source_name=source,
            source_version=source_version,
            store_role=store.role,
            table=table,
            instrument=instrument,
            timeframe=timeframe,
            raw_start=raw_start,
            raw_end=raw_end,
            row_count=len(rows),
            continuity="CONTINUOUS",
            gap_count=0,
            point_in_time_safe=True,
            confirmed_semantics="CONFIRMED_CANDLE_CLOSE_ONLY",
            content_sha256=digest,
            immutable_store_sha256=file_sha256,
            immutable_store_verification=("VERIFIED_EXPECTED_SHA256" if store.expected_file_sha256
                                          else "CONTENT_HASHED_NO_EXPECTED_DIGEST"),
            declared_dataset_id=store.declared_dataset_id,
            effective_as_of=raw_end,
            minimum_research_span_seconds=MINIMUM_RESEARCH_SPAN_SECONDS,
            minimum_research_span_policy_version=MINIMUM_RESEARCH_SPAN_POLICY_VERSION,
            span_days=span_seconds // 86_400,
            breadth_qualification=("SUFFICIENT_SPAN" if span_seconds >= MINIMUM_RESEARCH_SPAN_SECONDS
                                   else "LIMITED_HISTORICAL_SPAN"),
        )
        return SelectedHistoricalDatasetV1(selection, tuple(rows))
