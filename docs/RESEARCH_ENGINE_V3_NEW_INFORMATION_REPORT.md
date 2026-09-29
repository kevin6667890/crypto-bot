# Research Engine V3: New Information Report

## Decision

Pure OHLCV indicator and combination expansion remains frozen. V3 only admitted a
new, causally aligned cross-asset information path. Derivatives features remain
`UNAVAILABLE`; no synthetic series, gap fill, or proxy substitution was used.

## Cross-asset causal coverage

The development window is 2024-01-01 through 2024-12-31 UTC at 15 minutes.

| Primary | Context | Confirmed rows each | Exact aligned rows | Missing context bars | Duplicate timestamps | Coverage | Status |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| ETH-USDT | BTC-USDT | 35,136 | 35,136 | 0 | 0 | 100.0% | COMPLETE |
| SOL-USDT | BTC-USDT | 35,136 | 35,136 | 0 | 0 | 100.0% | COMPLETE |

Alignment accepts only exact same-timestamp confirmed bars. It does not forward
fill, backward fill, shift timestamps, or expose a later BTC candle to an alt
signal. The adapter and its fail-closed audit live in
`research_engine/cross_asset.py`.

## Derivatives data admission audit

The new `research_engine/derivatives.py` registry requires source, symbol,
exchange, timestamp coverage, resolution, missing ratio, validation state, and
a deterministic dataset version before a feature can be admitted.

| Dataset | Local observed state | Decision |
| --- | --- | --- |
| Funding | No continuous, versioned historical series aligned to the 2024 development window | UNAVAILABLE |
| Basis | No validated mark/index historical aggregate aligned to the development window | UNAVAILABLE |
| OI | Canonical 1m archive begins in 2026; 28,262 `UNRECOVERABLE_RAW_GAP` rows and 2,260 `MISSING` rows | UNAVAILABLE |
| Liquidations | No complete historical ledger; exchange websocket collection is not an archive | UNAVAILABLE |
| CVD / true VPVA | Canonical 1m archive begins in 2026; 70,244 `PARTIAL_AFTER_GAP` and 25,647 `MISSING` rows | UNAVAILABLE |

The local OI/CVD snapshot is real collected data, but it neither overlaps the
2024 development period nor meets continuity rules. It is therefore not
backfilled or scored. The feature registry exposes candidate definitions for
funding, basis, OI, liquidations, and true VPVA/CVD while retaining their
availability as fail-closed.

Potential source route: OKX documents public funding history, mark/index data,
open interest and recent trades, while its historical-data product advertises
tick trade history from September 2021. This needs a separate licensed/exported
coverage audit, not an assumption based on endpoint existence. Binance's OI
statistics documentation says only the latest 30 days are available, so it is
not sufficient for this historical research window. [OKX API documentation](https://www.okx.com/docs-v5/en/) and [OKX historical-data page](https://www.okx.com/en-sg/historical-data) describe the available routes; [Binance OI documentation](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Info) documents the short OI-history limitation. Historical OKX liquidation REST access was retired in 2023, so websocket observations must not be misrepresented as full history. [OKX change log](https://www.okx.com/docs-v5/log_en/)

## Feature-level information diagnostics

Diagnostics use causal 16-bar (four-hour) inputs and forward 16-bar asset
returns. Correlation and sign rates are descriptive only; they are not an
optimization objective.

| Feature | ETH correlation / sign hit | SOL correlation / sign hit | Assessment |
| --- | --- | --- | --- |
| Relative strength vs BTC | 0.006 / 49.4% | -0.047 / 49.2% | No persistent directional value |
| Asset + BTC momentum | 0.030 / 50.6% | 0.036 / 51.0% | Weak and not independently convincing |
| Leader / laggard vs peer | 0.022 / 50.8% | -0.034 / 49.5% | Inconsistent across assets |
| BTC regime context | 0.033 / 51.0% | 0.056 / 51.3% | Small conditional indication; insufficient as edge |
| Cross-sectional rank | 0.001 / 50.2% | -0.005 / 49.2% | No information value |

The diagnostics imply short holding periods and high signal frequency, which is
confirmed by the execution results below. No feature is promoted as a verified
information source.

## Bounded V3 research cycle

`research_cycle_v3_new_information` tested 20 hypotheses and 50 candidates:
two assets (ETH and SOL) times five explicit cross-asset ideas times five
pre-declared thresholds. It did not use an OHLCV-only grid, legacy optimizer,
composite score, or holdout data. All tests use the canonical next-open model
with 0.05% fees and 0.03% slippage.

| Stage | Pass | Fail / unavailable |
| --- | ---: | ---: |
| Stage 0: causal/data validation | 50 | 0 |
| Stage 1: development screen | 0 | 50 |
| Stage 2–5 | 0 | 0 (not reached) |
| Stage 6: holdout | 0 | 0 (not accessed) |

All fifty candidates were rejected for `NO_RAW_EDGE`, `NEGATIVE_RETURN`,
`NEGATIVE_SHARPE`, `MAXIMUM_DRAWDOWN`, and `EXCESSIVE_TURNOVER`.

| Family | Candidates | Mean gross return | Mean net return | Mean MaxDD | Trades/day | Mean cost / abs(gross PnL) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Relative strength | 10 | -33.97% | -91.96% | 92.05% | 4.50 | 1.73x |
| Asset + BTC momentum | 10 | -36.56% | -93.39% | 93.58% | 4.58 | 1.57x |
| Leader / laggard | 10 | -33.84% | -93.91% | 94.00% | 4.79 | 1.85x |
| BTC regime context | 10 | -38.91% | -89.40% | 89.44% | 3.67 | 1.37x |
| Cross-sectional rank | 10 | -36.00% | -89.55% | 89.73% | 3.82 | 1.55x |

ETH candidates averaged -34.16% gross / -90.55% net, while SOL candidates
averaged -37.55% gross / -92.73% net. The gross results are negative before
fees, so this is not classified merely as execution fragility. Separate
slippage drag remains unavailable without counterfactual fills.

## Governance and outcome

- Holdout access: **0**.
- All 50 candidates have deterministic identities and Stage 0/1 append-only
  ledger records under `.research/research_cycle_v3_new_information/`.
- No candidate reached parameter robustness, OOS, ablation, bootstrap, or cost
  stress; these costly tests were correctly early-stopped.
- All five V3 cross-asset families are `FAMILY_EXHAUSTED` for this cycle. They
  cannot be reopened by threshold changes alone.
- Survivors: **0**. No hypothesis qualifies as
  `PROMISING_FOR_FURTHER_RESEARCH`.

## Highest-value next work

1. Acquire a licensed/exported, timestamp-verifiable multi-year funding series
   for the exact perpetual contracts and rerun the coverage audit.
2. Acquire matching mark/index or dated-futures data for a true basis series.
3. Backfill a continuous OI archive before enabling price/OI interactions.
4. Obtain an archival trade/liquidation feed with aggressor-side convention and
   coverage manifest for true CVD/VPVA work.
5. Only after a dataset passes validation, run standalone information-value
   diagnostics before any bounded signal-plus-derivatives hypotheses.
