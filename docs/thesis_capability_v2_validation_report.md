# Thesis Capability V2 release-candidate validation

Validation date: 2026-08-25 UTC. All studies below used immutable, SHA-verified
release-candidate snapshots. Counts are observations from real OKX data, not
hard-coded expectations.

## Historical smokes

| Smoke | Definition | Effective UTC epoch range | Raw / independent events | Runtime | Result hash prefix |
|---|---|---:|---:|---:|---|
| A | BTC 4H `rolling high(20) AND volume percentile >= 90`; 12H/24H outcomes | 1721880000–1782864000 | 74 / 64 | 0.3215s | `3cf0ac2ed3c0` |
| B | BTC 4H failed breakout, lookback 20, failure window 3; 12H/24H outcomes | 1721880000–1782864000 | 92 / 84 | 0.2365s | `5c14025e9f78` |
| OI | BTC 1D OI change percentile >= 90; 3D/7D outcomes | 1706832000–1787616000 | 63 / 46 | 0.2128s | `21ae6475ba0a` |

The OHLCV studies used composite dataset identity prefix `a4fc6b5117a5`.
The OI study used component-preserving composite identity prefix
`5e75e5df42ef`; the OI component was not presented as OHLCV.

Three failed-breakout events were manually inspected:

| Failure confirmation | Original breakout | Reference level |
|---:|---:|---:|
| 1722110400 | 1722096000 | 68278.8 |
| 1724155200 | 1724140800 | 60887.0 |
| 1724284800 | 1724270400 | 61386.0 |

In every case the event timestamp is the later failure-confirmation candle and
outcomes begin after that timestamp. Future-data mutation tests preserve all
earlier breakout and failure memberships.

The requested 4H compound OI demo is intentionally unavailable: the official
OKX 1H OI endpoint exposes only about 60 days, below the research gate. The
qualified 1D OI lane demonstrates the data and PIT path. An OHLCV-only 4H
compound equivalent using `ANY(volume percentile >= 90, RSI >= 70)` and
`RSI <= 80` remains executable, but it is not represented as an OI result.

Funding (93 days) and basis (bounded source smoke only) remain below their
historical qualification gates. CVD remains disabled because a sufficiently
broad immutable native taker-side archive was not qualified.

## Current evidence and tracking

Rolling structure, OHLCV indicators, nested expression trees, V2 track
persistence, and leaf/group/overall What Changed deltas pass deterministic
tests. Current and historical dataset IDs are asserted unequal.

The current OI adapter reads confirmed official live OI whose ingestion time is
not later than the evaluation as-of, samples only the reviewed 15:55-16:00 UTC
daily window, and combines one value per UTC day with frozen causal history.
Readiness calls the real feature computation and requires recent live samples
for BTC, ETH and SOL. The local release-candidate store does not have a complete
qualified live lane, so capability discovery truthfully reports 1D OI as
historical-only. Frontend and backend both reject Track creation for a
historical-only leaf.

## Automated gates

- Focused backend after red-team fixes: 157 passed.
- Frontend: 32 files, 235 tests passed.
- TypeScript/Vite production build and bundle budgets passed.
- Thesis browser E2E: 8 passed, including rolling OR/NOT and Track,
  failed-breakout markers, OI data-blocked UX, and V1 regression cases.
- Python compileall and `git diff --check` passed.
- Full Python suite: 2171 passed, 2 skipped, 19 failed. The remaining failures
  are unchanged unrelated/environmental AI-report fixtures,
  candidate compose expectations, and an absent external phase4 audit artifact;
  no modified Thesis V2 file appears in those tracebacks.
- Docker Compose rendering was not executed locally because Docker is not
  installed on the validation host. It remains a release-host gate.

## Red-team status

The first independent review found one CRITICAL and nine HIGH issues. Seven
subsequent adversarial rounds added per-clause feature/number/operator binding,
closed residual-semantic accounting, feature-specific vocabulary, NOT polarity,
ASCII/full-width parenthesis and BETWEEN-aware grouping verification,
hash-visible failure windows, derivative density/max-gap/manifest gates,
publication-level causal ranking, current OI publication/cadence checks,
localized reasons, and frontend parameter-key validation. Production release
candidate review now reports `CRITICAL=0` and `HIGH=0`.
