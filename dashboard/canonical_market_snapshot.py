"""Immutable, causal market-fact snapshot shared by independent consumers.

``MarketAnalysisContextV2`` remains the compatibility/read boundary.  This
module turns that boundary payload into a small, versioned evidence ledger so
state engines, reports, and future consumers do not have to treat an
interpretation lens as canonical truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping


CANONICAL_MARKET_SNAPSHOT_VERSION = "canonical-market-snapshot-v1"
CANONICAL_MARKET_FACT_VERSION = "canonical-market-fact-v1"


class FactProvenanceClass(str, Enum):
    """Allowed semantic layers; higher layers never masquerade as input data."""

    RAW = "RAW"
    CANONICAL_OBSERVATION = "CANONICAL_OBSERVATION"
    DERIVED_FACT = "DERIVED_FACT"
    DETERMINISTIC_INTERPRETATION = "DETERMINISTIC_INTERPRETATION"


PROVENANCE_CLASSES = tuple(item.value for item in FactProvenanceClass)
FACT_QUALITY_STATES = (
    "AVAILABLE", "PARTIAL", "STALE", "MISSING", "WARMUP", "UNAVAILABLE",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _quality(item: Mapping[str, Any]) -> str:
    if not item.get("available") or item.get("value") is None:
        return "MISSING"
    if item.get("stale"):
        return "STALE"
    if item.get("partial"):
        return "PARTIAL"
    if not item.get("warmup_complete", True):
        return "WARMUP"
    return "AVAILABLE"


def _provenance(value: Any, default: str) -> str:
    candidate = str(value or default)
    if candidate not in PROVENANCE_CLASSES:
        raise ValueError(f"unknown fact provenance class: {candidate}")
    return candidate


@dataclass(frozen=True)
class CanonicalMarketEvidence:
    """A content-addressed reference to evidence used by one canonical fact."""

    evidence_id: str
    path: str
    source_timestamp: int | None
    provenance_class: str
    quality: str

    @classmethod
    def create(cls, *, path: str, source_timestamp: int | None,
               provenance_class: str, quality: str) -> "CanonicalMarketEvidence":
        body = {
            "version": CANONICAL_MARKET_FACT_VERSION,
            "path": path,
            "source_timestamp": source_timestamp,
            "provenance_class": provenance_class,
            "quality": quality,
        }
        return cls(_stable_hash(body), path, source_timestamp,
                   provenance_class, quality)


@dataclass(frozen=True)
class CanonicalMarketFact:
    """One immutable scalar fact. Missing is represented by ``value=None``."""

    fact_id: str
    path: str
    value: bool | int | float | str | None
    source_timestamp: int | None
    quality: str
    provenance_class: str
    source: str
    calculation_version: str
    evidence: tuple[CanonicalMarketEvidence, ...]

    @classmethod
    def create(cls, *, path: str, value: bool | int | float | str | None,
               source_timestamp: int | None, quality: str,
               provenance_class: str, source: str,
               calculation_version: str,
               evidence_paths: Iterable[str] = ()) -> "CanonicalMarketFact":
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite canonical fact at {path}")
        if quality not in FACT_QUALITY_STATES:
            raise ValueError(f"unknown fact quality: {quality}")
        provenance_class = _provenance(provenance_class,
                                       FactProvenanceClass.DERIVED_FACT.value)
        refs = tuple(CanonicalMarketEvidence.create(
            path=item, source_timestamp=source_timestamp,
            provenance_class=(FactProvenanceClass.RAW.value
                              if provenance_class == FactProvenanceClass.CANONICAL_OBSERVATION.value
                              else FactProvenanceClass.CANONICAL_OBSERVATION.value),
            quality=quality,
        ) for item in sorted(set(evidence_paths)))
        body = {
            "version": CANONICAL_MARKET_FACT_VERSION,
            "path": path,
            "value": value,
            "source_timestamp": source_timestamp,
            "quality": quality,
            "provenance_class": provenance_class,
            "source": source,
            "calculation_version": calculation_version,
            "evidence_ids": [item.evidence_id for item in refs],
        }
        return cls(_stable_hash(body), path, value, source_timestamp, quality,
                   provenance_class, source, calculation_version, refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id, "path": self.path, "value": self.value,
            "source_timestamp": self.source_timestamp, "quality": self.quality,
            "provenance_class": self.provenance_class, "source": self.source,
            "calculation_version": self.calculation_version,
            "evidence": [item.__dict__.copy() for item in self.evidence],
        }


@dataclass(frozen=True)
class CanonicalInstrumentIdentity:
    venue: str
    instrument: str
    base_asset: str | None
    quote_asset: str | None
    product_type: str


@dataclass(frozen=True)
class CanonicalPriceObservation:
    identity: CanonicalInstrumentIdentity
    value: float | None
    source_timestamp: int | None
    causal_cutoff: int
    quality: str
    missing_reason: str | None
    provenance_class: str
    source: str


@dataclass(frozen=True)
class CanonicalGap:
    start: int | None
    end: int | None
    missing_bars: int | None
    reason: str


@dataclass(frozen=True)
class CanonicalTimeframeObservation:
    timeframe: str
    causal_cutoff: int
    confirmed: bool
    source: str
    source_timestamp: int | None
    last_open: float | None
    last_high: float | None
    last_low: float | None
    last_close: float | None
    last_volume: float | None
    quality: str
    coverage_state: str
    gap_state: str
    gaps: tuple[CanonicalGap, ...]
    missing_reason: str | None
    provenance_class: str
    input_provenance_class: str


@dataclass(frozen=True)
class CanonicalMicrostructureObservation:
    series: str
    identity: CanonicalInstrumentIdentity
    value: float | None
    window_start: int | None
    window_end: int | None
    source_timestamp: int | None
    causal_cutoff: int
    freshness_seconds: int | None
    expected_buckets: int | None
    observed_buckets: int | None
    coverage_ratio: float | None
    has_gaps: bool | None
    quality: str
    missing_reason: str | None
    provenance_class: str
    source: str
    contract_version: str


@dataclass(frozen=True)
class CanonicalMarketSnapshot:
    """Versioned immutable facts available at one inclusive causal cutoff."""

    version: str
    decision_time: int
    as_of: int
    instrument: str
    price_identity: CanonicalInstrumentIdentity
    causal_cutoff: int
    execution_timeframe: str
    context_version: str
    context_identity: str | None
    price: CanonicalPriceObservation
    timeframes: tuple[CanonicalTimeframeObservation, ...]
    microstructure: tuple[CanonicalMicrostructureObservation, ...]
    facts: tuple[CanonicalMarketFact, ...]
    quality: tuple[tuple[str, Any], ...]
    snapshot_identity: str

    @property
    def facts_by_path(self) -> Mapping[str, CanonicalMarketFact]:
        return MappingProxyType({item.path: item for item in self.facts})

    def fact(self, path: str) -> CanonicalMarketFact | None:
        return self.facts_by_path.get(path)

    def timeframe(self, timeframe: str) -> CanonicalTimeframeObservation | None:
        return next((item for item in self.timeframes if item.timeframe == timeframe), None)

    def microstructure_observation(
        self, series: str, product_type: str, *, venue: str = "OKX",
    ) -> CanonicalMicrostructureObservation | None:
        return next((item for item in self.microstructure
                     if item.series == series and
                     item.identity.product_type == product_type and
                     item.identity.venue == venue), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "decision_time": self.decision_time,
            "as_of": self.as_of, "instrument": self.instrument,
            "price_identity": self.price_identity.__dict__.copy(),
            "causal_cutoff": self.causal_cutoff,
            "execution_timeframe": self.execution_timeframe,
            "context_version": self.context_version,
            "context_identity": self.context_identity,
            "price": {**self.price.__dict__, "identity": self.price.identity.__dict__.copy()},
            "timeframes": [
                {**item.__dict__, "gaps": [gap.__dict__.copy() for gap in item.gaps]}
                for item in self.timeframes
            ],
            "microstructure": [
                {**item.__dict__, "identity": item.identity.__dict__.copy()}
                for item in self.microstructure
            ],
            "facts": [item.to_dict() for item in self.facts],
            "quality": {key: value for key, value in self.quality},
            "snapshot_identity": self.snapshot_identity,
        }


def _instrument_identity(instrument: str, *, venue: str = "OKX",
                         product_type: str | None = None) -> CanonicalInstrumentIdentity:
    parts = instrument.upper().split("-")
    inferred = "SWAP" if parts and parts[-1] == "SWAP" else "SPOT"
    base = parts[0] if len(parts) >= 2 else None
    quote = parts[1] if len(parts) >= 2 else None
    return CanonicalInstrumentIdentity(venue, instrument.upper(), base, quote,
                                       product_type or inferred)


def _timeframe_contracts(context: Mapping[str, Any], cutoff: int) -> tuple[CanonicalTimeframeObservation, ...]:
    frames = context.get("timeframes") if isinstance(context.get("timeframes"), Mapping) else {}
    result: list[CanonicalTimeframeObservation] = []
    for timeframe in ("15m", "1H", "4H", "1D", "1W"):
        frame = frames.get(timeframe) if isinstance(frames, Mapping) else None
        frame = frame if isinstance(frame, Mapping) else {}
        observation = frame.get("observation")
        observation = observation if isinstance(observation, Mapping) else {}
        quality = frame.get("quality")
        quality = quality if isinstance(quality, Mapping) else {}
        gaps = tuple(CanonicalGap(
            int(item["start"]) if item.get("start") is not None else None,
            int(item["end"]) if item.get("end") is not None else None,
            int(item["missing_bars"]) if item.get("missing_bars") is not None else None,
            str(item.get("reason") or "MISSING_CONFIRMED_BAR"),
        ) for item in quality.get("gaps", ()) if isinstance(item, Mapping))
        source_timestamp = observation.get("source_at", frame.get("candle_close_ts"))
        source_timestamp = int(source_timestamp) if source_timestamp is not None else None
        confirmed = bool(frame.get("confirmed")) and source_timestamp is not None
        missing_reason = observation.get("missing_reason")
        if not confirmed and not missing_reason:
            missing_reason = "NO_CONFIRMED_CANDLE_AT_OR_BEFORE_CUTOFF"
        result.append(CanonicalTimeframeObservation(
            timeframe, cutoff, confirmed,
            str(observation.get("confirmed_ohlcv_source") or observation.get("source_store") or "unknown"),
            source_timestamp,
            float(observation["last_open"]) if observation.get("last_open") is not None else None,
            float(observation["last_high"]) if observation.get("last_high") is not None else None,
            float(observation["last_low"]) if observation.get("last_low") is not None else None,
            float(observation["last_close"]) if observation.get("last_close") is not None else None,
            float(observation["last_volume"]) if observation.get("last_volume") is not None else None,
            str(quality.get("status") or "MISSING"),
            str(observation.get("coverage_state") or "MISSING"),
            str(observation.get("gap_state") or ("GAPPED" if gaps else "MISSING" if not confirmed else "CONTIGUOUS")),
            gaps, str(missing_reason) if missing_reason else None,
            str(observation.get("provenance_class") or "CANONICAL_OBSERVATION"),
            str(observation.get("input_provenance_class") or "RAW"),
        ))
    return tuple(result)


def _microstructure_contracts(context: Mapping[str, Any], cutoff: int,
                              price_identity: CanonicalInstrumentIdentity) -> tuple[CanonicalMicrostructureObservation, ...]:
    """Normalize the evidence adapter payload, or expose explicit V2 fallback gaps."""
    supplied = context.get("microstructure_evidence")
    items: list[Mapping[str, Any]] = []
    if isinstance(supplied, Mapping):
        items = [item for item in supplied.values() if isinstance(item, Mapping)]
    elif isinstance(supplied, (list, tuple)):
        items = [item for item in supplied if isinstance(item, Mapping)]
    result: list[CanonicalMicrostructureObservation] = []
    for item in items:
        identity_payload = item.get("identity") if isinstance(item.get("identity"), Mapping) else {}
        identity = _instrument_identity(
            str(identity_payload.get("instrument") or price_identity.instrument),
            venue=str(identity_payload.get("venue") or "OKX"),
            product_type=str(identity_payload.get("product_type") or price_identity.product_type),
        )
        window = item.get("window") if isinstance(item.get("window"), Mapping) else {}
        coverage = item.get("coverage") if isinstance(item.get("coverage"), Mapping) else {}
        source_ms = item.get("source_end_ms")
        result.append(CanonicalMicrostructureObservation(
            str(item.get("series") or "unknown"), identity,
            float(item["value"]) if item.get("value") is not None else None,
            int(window["start_ms"] // 1000) if window.get("start_ms") is not None else None,
            int(window["end_ms"] // 1000) if window.get("end_ms") is not None else None,
            int(source_ms // 1000) if source_ms is not None else None, cutoff,
            int(item["freshness_ms"] // 1000) if item.get("freshness_ms") is not None else None,
            int(coverage["expected_buckets"]) if coverage.get("expected_buckets") is not None else None,
            int(coverage["observed_buckets"]) if coverage.get("observed_buckets") is not None else None,
            float(coverage["ratio"]) if coverage.get("ratio") is not None else None,
            bool(coverage["has_gaps"]) if coverage.get("has_gaps") is not None else None,
            str(item.get("quality") or "MISSING"),
            str(item["missing_reason"]) if item.get("missing_reason") else None,
            "CANONICAL_OBSERVATION",
            str((item.get("provenance") or {}).get("source") or "microstructure evidence adapter"),
            str(item.get("contract_version") or "microstructure-evidence-v1"),
        ))
    if result:
        swap_instrument = (price_identity.instrument if price_identity.product_type == "SWAP"
                           else f"{price_identity.instrument}-SWAP")
        required_identities = (
            _instrument_identity(swap_instrument, product_type="SWAP"),
            _instrument_identity(swap_instrument.removesuffix("-SWAP"), product_type="SPOT"),
        )
        present = {(item.series, item.identity.product_type) for item in result}
        for series in ("cvd", "oi"):
            for identity in required_identities:
                if (series, identity.product_type) in present:
                    continue
                result.append(CanonicalMicrostructureObservation(
                    series, identity, None, None, None, None, cutoff, None,
                    None, None, 0.0, True, "MISSING",
                    ("NOT_APPLICABLE_TO_SPOT" if series == "oi" and
                     identity.product_type == "SPOT" else "EVIDENCE_SERIES_NOT_SUPPLIED"),
                    "CANONICAL_OBSERVATION", "microstructure evidence adapter",
                    "microstructure-evidence-v1",
                ))
        return tuple(sorted(result, key=lambda value: (
            value.series, value.identity.product_type, value.identity.instrument)))

    # Compatibility path until MarketContextServiceV2 is directly wired to the
    # evidence adapter. It is deliberately SWAP-qualified and creates explicit
    # SPOT nulls rather than substituting the SWAP series.
    flow = context.get("flow") if isinstance(context.get("flow"), Mapping) else {}
    swap_instrument = (price_identity.instrument if price_identity.product_type == "SWAP"
                       else f"{price_identity.instrument}-SWAP")
    swap_identity = _instrument_identity(swap_instrument, product_type="SWAP")
    spot_identity = _instrument_identity(swap_instrument.removesuffix("-SWAP"), product_type="SPOT")
    for series in ("cvd", "oi"):
        group = flow.get(series) if isinstance(flow, Mapping) and isinstance(flow.get(series), Mapping) else {}
        current = group.get("current") if isinstance(group.get("current"), Mapping) else {}
        combination = flow.get(f"price_{series}_combination") if isinstance(flow, Mapping) else {}
        combination = combination if isinstance(combination, Mapping) else {}
        timestamp = current.get("source_timestamp")
        timestamp = int(timestamp) if timestamp is not None else None
        value = float(current["value"]) if current.get("value") is not None else None
        partial = bool(current.get("partial"))
        stale = bool(current.get("stale"))
        quality = "MISSING" if value is None else "STALE" if stale else "PARTIAL" if partial else "AVAILABLE"
        result.append(CanonicalMicrostructureObservation(
            series, swap_identity, value,
            int(combination["start_timestamp"]) if combination.get("start_timestamp") is not None else None,
            int(combination["end_timestamp"]) if combination.get("end_timestamp") is not None else None,
            timestamp, cutoff, max(0, cutoff - timestamp) if timestamp is not None else None,
            None, None, None, partial if value is not None else True, quality,
            "NO_CONFIRMED_OBSERVATION" if value is None else None,
            str(current.get("provenance_class") or "CANONICAL_OBSERVATION"),
            str(current.get("source") or "MarketAnalysisContextV2 compatibility boundary"),
            str(current.get("calculation_version") or "market-analysis-context-v2"),
        ))
        result.append(CanonicalMicrostructureObservation(
            series, spot_identity, None, None, None, None, cutoff, None,
            None, None, 0.0, True, "MISSING",
            "SOURCE_PRODUCT_NOT_COLLECTED" if series == "cvd" else "NOT_APPLICABLE_TO_SPOT",
            "CANONICAL_OBSERVATION", "MarketAnalysisContextV2 compatibility boundary",
            "market-analysis-context-v2",
        ))
    return tuple(sorted(result, key=lambda value: (
        value.series, value.identity.product_type, value.identity.instrument)))


def _timestamp_violations(value: Any, cutoff: int, path: str = "context") -> list[str]:
    result: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if (key.endswith("_timestamp") or key.endswith("_ts") or key.endswith("_ms") or
                    key in {"as_of", "observed_at", "source_at", "oldest_at"}):
                if item is not None:
                    try:
                        boundary = cutoff * 1000 if key.endswith("_ms") else cutoff
                        if int(item) > boundary:
                            result.append(child)
                    except (TypeError, ValueError):
                        result.append(child)
            else:
                result.extend(_timestamp_violations(item, cutoff, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            result.extend(_timestamp_violations(item, cutoff, f"{path}[{index}]"))
    return result


def _indicator_fact(path: str, item: Mapping[str, Any], *,
                    default_provenance: str = "DERIVED_FACT") -> CanonicalMarketFact:
    timestamp = item.get("source_timestamp")
    timestamp = int(timestamp) if timestamp is not None else None
    provenance = _provenance(item.get("provenance_class"), default_provenance)
    evidence_paths = item.get("evidence_paths") or ()
    return CanonicalMarketFact.create(
        path=path, value=item.get("value"), source_timestamp=timestamp,
        quality=_quality(item), provenance_class=provenance,
        source=str(item.get("source") or "MarketAnalysisContextV2"),
        calculation_version=str(item.get("calculation_version") or "unknown"),
        evidence_paths=evidence_paths,
    )


def build_canonical_market_snapshot(context: Mapping[str, Any]) -> CanonicalMarketSnapshot:
    """Validate and freeze a Context V2 payload into a canonical fact ledger."""

    cutoff = int(context.get("as_of", 0))
    if cutoff <= 0:
        raise ValueError("context as_of must be a positive causal cutoff")
    violations = _timestamp_violations(context, cutoff)
    if violations:
        raise ValueError("fact timestamp later than causal cutoff: " + ", ".join(sorted(violations)[:8]))

    facts: list[CanonicalMarketFact] = []
    instrument = str(context.get("instrument") or "")
    price_identity = _instrument_identity(instrument)
    price = context.get("price")
    if isinstance(price, Mapping):
        facts.append(_indicator_fact("/price", price,
                                    default_provenance="CANONICAL_OBSERVATION"))
    else:
        facts.append(CanonicalMarketFact.create(
            path="/price", value=None, source_timestamp=None, quality="MISSING",
            provenance_class="CANONICAL_OBSERVATION", source="MarketAnalysisContextV2",
            calculation_version="unknown", evidence_paths=("/raw/candles",),
        ))
    price_fact = facts[-1]
    price_contract = CanonicalPriceObservation(
        price_identity,
        float(price_fact.value) if isinstance(price_fact.value, (int, float)) else None,
        price_fact.source_timestamp, cutoff, price_fact.quality,
        "NO_CONFIRMED_EXECUTION_PRICE" if price_fact.value is None else None,
        price_fact.provenance_class, price_fact.source,
    )
    timeframe_contracts = _timeframe_contracts(context, cutoff)
    microstructure_contracts = _microstructure_contracts(context, cutoff, price_identity)

    frames = context.get("timeframes") if isinstance(context.get("timeframes"), Mapping) else {}
    for timeframe in ("15m", "1H", "4H", "1D", "1W"):
        frame = frames.get(timeframe) if isinstance(frames, Mapping) else None
        frame = frame if isinstance(frame, Mapping) else {}
        for group in ("trend", "momentum", "volatility", "structure", "volume"):
            values = frame.get(group)
            if not isinstance(values, Mapping) or not values:
                facts.append(CanonicalMarketFact.create(
                    path=f"/timeframes/{timeframe}/{group}", value=None,
                    source_timestamp=None, quality="MISSING",
                    provenance_class="DERIVED_FACT", source="MarketAnalysisContextV2",
                    calculation_version="unknown",
                    evidence_paths=(f"/raw/candles/{timeframe}",),
                ))
                continue
            for name, item in sorted(values.items()):
                if isinstance(item, Mapping):
                    facts.append(_indicator_fact(
                        f"/timeframes/{timeframe}/{group}/{name}", item))

    flow = context.get("flow") if isinstance(context.get("flow"), Mapping) else {}
    for group in ("cvd", "oi", "funding", "basis", "vpvr"):
        values = flow.get(group) if isinstance(flow, Mapping) else None
        if not isinstance(values, Mapping) or not values:
            facts.append(CanonicalMarketFact.create(
                path=f"/flow/{group}", value=None, source_timestamp=None,
                quality="MISSING", provenance_class="CANONICAL_OBSERVATION",
                source="MarketAnalysisContextV2", calculation_version="unknown",
                evidence_paths=(f"/raw/{group}",),
            ))
            continue
        for name, item in sorted(values.items()):
            if isinstance(item, Mapping):
                facts.append(_indicator_fact(f"/flow/{group}/{name}", item,
                                             default_provenance="CANONICAL_OBSERVATION"))

    for name in ("price_oi_combination", "price_cvd_combination"):
        item = flow.get(name) if isinstance(flow, Mapping) else None
        item = item if isinstance(item, Mapping) else {}
        timestamp = item.get("end_timestamp")
        quality = str(item.get("data_quality") or "MISSING")
        if quality not in FACT_QUALITY_STATES:
            quality = "UNAVAILABLE"
        facts.append(CanonicalMarketFact.create(
            path=f"/flow/{name}/state", value=item.get("state"),
            source_timestamp=int(timestamp) if timestamp is not None else None,
            quality=quality, provenance_class="DETERMINISTIC_INTERPRETATION",
            source="MarketAnalysisContextV2",
            calculation_version=str(item.get("calculation_version") or "price-flow-combination-v2"),
            evidence_paths=("/price", f"/flow/{'oi' if 'oi' in name else 'cvd'}"),
        ))

    for index, level in enumerate(context.get("levels") or ()):
        if not isinstance(level, Mapping):
            continue
        timestamp = level.get("source_timestamp")
        facts.append(CanonicalMarketFact.create(
            path=f"/levels/{index}/value", value=level.get("value"),
            source_timestamp=int(timestamp) if timestamp is not None else None,
            quality="AVAILABLE" if level.get("confirmed") else "PARTIAL",
            provenance_class=_provenance(level.get("provenance_class"), "DERIVED_FACT"),
            source=str(level.get("source") or "MarketAnalysisContextV2"),
            calculation_version=str(level.get("calculation_version") or "unknown"),
            evidence_paths=level.get("evidence_paths") or (),
        ))

    for item in microstructure_contracts:
        micro_path = (f"/microstructure/{item.identity.venue}/{item.identity.product_type}/"
                      f"{item.identity.instrument}/{item.series}")
        fact_quality = item.quality
        if fact_quality == "VALID":
            fact_quality = "AVAILABLE"
        elif fact_quality not in FACT_QUALITY_STATES:
            fact_quality = "PARTIAL" if item.value is not None else "MISSING"
        facts.append(CanonicalMarketFact.create(
            path=micro_path, value=item.value,
            source_timestamp=item.source_timestamp, quality=fact_quality,
            provenance_class=item.provenance_class, source=item.source,
            calculation_version=item.contract_version,
            evidence_paths=(f"/raw/microstructure/{item.identity.product_type.lower()}/{item.series}",),
        ))

    facts.sort(key=lambda item: item.path)
    if len({item.path for item in facts}) != len(facts):
        raise ValueError("canonical fact paths must be unique")
    quality_payload = context.get("quality") if isinstance(context.get("quality"), Mapping) else {}
    quality = tuple((key, quality_payload[key]) for key in sorted(quality_payload)
                    if isinstance(quality_payload[key], (str, int, float, bool, type(None))))
    identity_body = {
        "version": CANONICAL_MARKET_SNAPSHOT_VERSION,
        "decision_time": cutoff,
        "as_of": cutoff,
        "instrument": instrument,
        "price_identity": price_identity.__dict__,
        "causal_cutoff": cutoff,
        "execution_timeframe": str(context.get("execution_timeframe") or ""),
        "context_version": str(context.get("version") or ""),
        "context_identity": context.get("context_identity"),
        "price": {**price_contract.__dict__, "identity": price_identity.__dict__},
        "timeframes": [
            {**item.__dict__, "gaps": [gap.__dict__ for gap in item.gaps]}
            for item in timeframe_contracts
        ],
        "microstructure": [
            {**item.__dict__, "identity": item.identity.__dict__}
            for item in microstructure_contracts
        ],
        "facts": [item.to_dict() for item in facts],
        "quality": {key: value for key, value in quality},
    }
    return CanonicalMarketSnapshot(
        CANONICAL_MARKET_SNAPSHOT_VERSION, cutoff, cutoff,
        identity_body["instrument"], price_identity, cutoff,
        identity_body["execution_timeframe"], identity_body["context_version"],
        context.get("context_identity"), price_contract, timeframe_contracts,
        microstructure_contracts, tuple(facts), quality,
        _stable_hash(identity_body),
    )


canonical_market_snapshot = build_canonical_market_snapshot
