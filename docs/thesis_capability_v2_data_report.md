# Thesis Capability V2 data report

Audit cutoff: 2026-08-25 UTC. Source availability, release-candidate snapshots,
and production mounts are reported separately. No derivative clause is removed
when its data gate fails.

## Qualified release-candidate artifacts

| Component | Official source | Qualified UTC range | Rows / coverage | RC readiness |
|---|---|---|---|---|
| OHLCV | OKX canonical confirmed candles | 15m/1H/4H existing qualified history; 1D 2024-01-01 through 2026-08-24 | 104,397 rows across BTC/ETH/SOL and 12 partitions | `READY` |
| Open interest | OKX Open Interest History, canonical value `oiUsd` | 1D 2024-01-01 through 2026-08-24 | 967 rows per BTC/ETH/SOL instrument, no detected daily gaps | `READY` for 1D historical studies only |
| Settled funding | OKX Funding Rate History | 2026-05-23 through 2026-08-24 | 280 rows per instrument | `LIMITED`: 93 days is below the 180-day research gate |
| Basis | OKX confirmed mark and index candles | two-hour source smoke only | exact-timestamp join verified; no broad snapshot | `LIMITED` |
| Native CVD | OKX native taker-side trades | no qualified local archive | never inferred from OHLCV | `OPTIONAL_UNAVAILABLE` |

The immutable OHLCV release-candidate snapshot is
`thesis-historical-v2-okx-20260825`, SHA-256 prefix `3fa4b2a066a7`.
The immutable derivatives release-candidate snapshot is
`thesis-derivatives-v1-a6d04336abef478406d45fe4`, SHA-256 prefix
`a6d04336abef`. Full hashes live in the deployment configuration and adjacent
manifest, not in application defaults. These artifacts are not described as
production-mounted until deployment verification succeeds.

## Source forensic

Open-interest retention depends on resolution. The official 1H endpoint exposed
about 60 days (1,440 rows) for each of BTC, ETH and SOL; requesting earlier data
returned no rows. It is therefore not qualified for a long 1H or 4H study. The
1D lane exposed continuous history from the start of 2024 and is the only OI
timeframe advertised as historically available in this release candidate.

Funding returned 280 settled observations per instrument, about 93 days. The
engine reads actual settlement/publication timestamps and never substitutes
predicted funding. This coverage remains visible as limited.

Basis is defined point-in-time as `(perpetual mark - spot index) / spot index`.
The source smoke used exact matching timestamps and produced 120 of 120 expected
rows without a future join. A broad continuity backfill was not qualified, so
basis remains unavailable for historical execution.

CVD requires native trade-level aggressor side. Although the official recent
trade endpoint exists, no sufficiently broad immutable native archive was
qualified. The capability remains disabled with
`CVD_HISTORICAL_NATIVE_SOURCE_UNAVAILABLE`; candles are never used to fabricate
CVD.

## Snapshot and point-in-time policy

`scripts/backfill_thesis_derivatives.py` implements bounded UTC ranges,
checkpointed resume, retry/backoff, conservative pacing, primary-key dedup,
raw-response provenance, dry-run, verification and immutable snapshot creation.
The snapshot writer closes and flushes SQLite before hashing; a regression test
verifies the published SHA remains stable after the process exits.

At candle close `t`, a derivative observation is eligible only when both its
source timestamp and publication timestamp are `<= t` and its age is within the
feature freshness policy. Missing or stale values become `UNKNOWN`, never zero.
Percentile features use strictly earlier observations, not a full-sample rank.

Mixed studies use `CompositeHistoricalDatasetIdentityV1`. Every component keeps
its own dataset ID and SHA, while the effective range is their qualified
intersection after warmup. A derivative SHA or manifest mismatch blocks only
the derivative-dependent thesis; OHLCV-only studies remain available.

## Production gate

Before advertising a derivative lane in production: mount the immutable file
read-only, verify its manifest and expected SHA, inspect readiness, and confirm
the UI capability response matches the backend gate. OI is currently historical
only: it must be presented as testable on 1D but not trackable until a qualified
current adapter can reproduce the same feature. Funding, basis and CVD remain
closed under their stated limitations.

Official reference: [OKX API documentation](https://www.okx.com/docs-v5/en/).
