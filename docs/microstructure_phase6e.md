# Phase 6E microstructure readiness and validation

All validation produced by this phase is permanently labelled:

> VALIDATION RESEARCH ONLY — NOT A TRADING SIGNAL

## Collector semantics

- BTC, ETH, and SOL trades use independently supervised `trades-all` sockets.
- A trade socket reconnects after 30 seconds without source messages. Health
  writes run outside the event loop and cannot terminate a source worker.
- Live observations use the serialized writer first. Historical backfill
  pauses when the live queue is non-empty or trade persistence is stale, uses
  at most 50 rows per write, and retains its official cursor when paused.
- Trade health exposes received, persisted, and aggregated timestamps
  separately. Writer queue depth and write latency are separate backlog
  indicators.
- Settled funding is polled from OKX funding history every five minutes and is
  kept separate from provisional/current-period funding. Health compares the
  latest settlement with the exchange schedule and reports the next expected
  settlement; generic seconds-lag is not used.
- Liquidation health is based on connection heartbeat/message state. The last
  genuine event and event count are displayed separately because the official
  stream is sparse and is not a complete liquidation ledger.

## Gap classification

No gap is filled with zeroes, interpolation, or synthetic observations.
Recorded raw-source gaps and open live gaps use these deterministic classes:

- `CRITICAL_LIVE_GAP`
- `RECOVERABLE_BACKFILL_GAP`
- `HISTORICAL_SOURCE_LIMIT`
- `EXPECTED_EVENT_SPARSE`
- `LEGACY_BOUNDARY`
- `FALSE_POSITIVE`
- `RESOLVED`

OI intervals of at most 60 seconds are bounded poll/retry jitter, not outages.
Historical OI gaps are source-limited because OKX has no official historical
OI endpoint. Trade and mark gaps inside official retention are backfill
eligible. A gap containing later-arriving genuine observations is resolved.

## Eligibility

Eligibility is reported for every feature group and instrument. Each row
includes genuine source span and rows, gap-adjusted days, genuine mark-label
span, exact overlap, labelable event count, independent source/event-study
status, next eligibility date, and blocking reason.

Aggregate fields remain for compatibility and use the strict BTC/ETH/SOL
intersection. They are explicitly documented as aggregate-only: ETH or SOL
coverage never reduces the BTC instrument row.

## Bounded validation

Settled funding is evaluated for BTC, ETH, and SOL using level, change, and a
rolling z-score at 15m, 30m, 1H, 2H, 4H, 8H, and 24H horizons.

Basis validation is BTC-only until ETH and SOL independently meet the minimum
sample. It evaluates level, z-score, change, expansion/contraction, absolute
basis, percentage basis, and funding-adjusted basis.

Actual source/mark overlap is split chronologically: the first 70% is
research/calibration and the later 30% is validation. A calibration label may
not cross the boundary. No completed holdout or OOT period is created or
claimed.

Outputs include event count, Pearson and Spearman IC, quantile returns,
monotonicity, sign consistency, temporal stability, concentration, regime
distribution, bootstrap confidence intervals when sample size permits, and
Bonferroni-adjusted significance as a diagnostic. Multiple-testing diagnostics
cannot promote features. Classifications are limited to:

- `CONTINUE_COLLECTING`
- `VALIDATION_PROMISING`
- `UNSTABLE`
- `REDUNDANT`
- `NO_DESCRIPTIVE_RELATIONSHIP`
- `INSUFFICIENT_SAMPLE`

The validation path has no order client, strategy construction, automatic
strategy discovery, activation control, or feature-promotion operation.
