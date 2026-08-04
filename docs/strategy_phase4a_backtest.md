# Strategy Phase 4A causal replay and validation

Phase 4A is a research-only, causal replay of `TREND_PULLBACK` and
`MA200_MEAN_REVERSION`, independently for LONG and SHORT. It is not connected
to Paper or live execution and does not produce current trading advice.

## Frozen protocol

The pre-PnL manifest is
`research/phase4a_research_manifest_v1.json`, version
`phase4a-research-manifest-v1`, identity
`e0cd13e743abbda3cc69ddd8ddebd625ce7ede9083e44f6dc81ea4536a1c32ff`.
It was committed before any outcome calculation. It freezes router
`strategy-router-v2`, definitions `strategy-family-definitions-v2.1`, Context
V2, State V2, the 32 parameter sets, data identity, split, sample gates,
next-open entry, `STOP_FIRST`, conservative gaps, fee/slippage, account policy,
selection rules and random seed.

The verified offline SQLite source is
`canonical_ohlcv_2023_2025.db`, SHA-256
`9ae9c4ed5f981120eafe42c483ec956a4796c59269206287a781a136d6aee9d3`,
dataset identity
`e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062`.
It contains complete confirmed BTC, ETH and SOL rows from 2023-03-01 through
2025-12-31: 99,552 15m, 24,888 1H, 6,222 4H and 1,037 1D rows per asset.
The 147 complete 1W bars per asset are causally aggregated from seven closed UTC
daily bars. No official API or production database is used.

After a frozen 240-day warm-up, the segments are:

| Segment | Start UTC | End UTC (exclusive) | Access |
|---|---|---|---|
| DEVELOPMENT | 2023-10-27 00:15 | 2025-02-16 04:45 | used |
| VALIDATION | 2025-02-16 04:45 | 2025-07-25 14:15 | not read because no Development candidate passed |
| LOCKED_FINAL_OOT | 2025-07-25 14:15 | 2026-01-01 00:00 | hard-refused, never read |

Development contains four consecutive folds. Setups and positions cannot cross
segment boundaries; warm-up can precede a segment but cannot create a pre-segment
position.

## Causal replay and execution

`strategy-event-replay-engine-v2` exposes only candles whose explicit close is
at or before the evaluation time. The 1H/4H/1D cursors advance only on completed
candles; 1W uses complete Monday-based seven-day groups. Rolling extrema exclude
the current bar, and no centered rolling or backward fill exists. Every
transition records source candle timestamps, level/setup/evaluation/route
identities, geometry, quality and engine version.

Only `TRIGGER_READY` may create an intent. Execution waits until the next 15m
open and always applies adverse slippage. An open beyond invalidation is
`GAP_INVALIDATED_BEFORE_ENTRY`; an open below frozen structural R is
`GEOMETRY_INVALID_AT_ENTRY`. Entry, stop, target, risk, structural R and maximum
hold are frozen at entry. There is no pyramiding, averaging, trailing stop or
break-even mutation.

The formal intrabar policy is `STOP_FIRST`. `TARGET_FIRST` and
`DROP_AMBIGUOUS_BAR` are diagnostics only. A gap through stop exits at the worse
open; a gap beyond target never receives a fill better than the target. Fees are
0.05% each side and adverse slippage is 0.03% each side; 1.5× and 2× cost runs
are retained. Funding is not synthesized: the local CVD/OI/funding-era data has
zero overlap with this price history.

## Frozen sample gates

| Family | Development pooled / per asset | Validation pooled / per asset |
|---|---|---|
| TREND_PULLBACK | 90 / 20 on at least 2 assets | 30 / 8 on at least 2 assets |
| MA200_MEAN_REVERSION | 30 / 8 on at least 2 assets | 10 / 3 on at least 2 assets |

The gates were frozen after event-frequency auditing and before PnL. They were
not relaxed after results.

## Accepted run and result

The accepted engine is `strategy-backtest-engine-v2.0.4`. Run
`d2a72ac24223320655e7eb08d54dba38d976a5c1e804b83203abfc43b2e6ebed`
used code SHA `8f7cf55675e3adbe9aaae924978b7de80eef4f80`; artifact identity is
`5b757fd8f2ca0b9ed4194de94b104b47382550420cb98432b84f44995fd26d18`.

| Family / direction | Trigger events across 8 trials | Trades across 8 trials | Expectancy R range | PF range | DD range | Classification |
|---|---:|---:|---:|---:|---:|---|
| Trend Pullback LONG | 2,290 | 1,598 | -0.1227 to 0.0115 | 0.6385 to 0.8015 | 10.49% to 20.01% | 8× RETIRE_NEGATIVE_EXPECTANCY |
| Trend Pullback SHORT | 1,528 | 1,040 | -0.1955 to -0.0953 | 0.6984 to 0.8744 | 6.34% to 9.11% | 8× RETIRE_NEGATIVE_EXPECTANCY |
| MA200 Mean Reversion LONG | 170 | 160 | -0.1156 to 0.2758 | 0.8801 to 1.5580 | 1.42% to 2.37% | 8× INSUFFICIENT_SAMPLE |
| MA200 Mean Reversion SHORT | 89 | 75 | -0.4417 to 0.2193 | 0.5971 to 1.2020 | 0.78% to 1.34% | 8× INSUFFICIENT_SAMPLE |

No candidate passed Development. Consequently Validation was not read or run,
and there are no Validation metrics or `VALIDATION_PASS_RESEARCH_ONLY`
identities. This is a valid zero-selection result; gates were not relaxed.

Across the raw trial ledger (where trades repeat across parameter trials), there
are 2,873 trades, 7,028.01 USDT fee drag, 4,216.81 USDT slippage drag and zero
observed gap drag. No formal trade had ambiguous simultaneous stop/target contact,
so STOP_FIRST, TARGET_FIRST and DROP_AMBIGUOUS have identical results for every
trial and the conclusion is not intrabar-order dependent. All 1.5×/2× results
are present. At 2× costs, the best expectancy ranges by direction are -0.1095,
-0.2664, +0.1028 and -0.0715 R respectively; positive MA200 cases remain
ineligible because their frozen sample gates fail.

The full-capital equal-asset buy-and-hold background return is 242.99%; it is not
an elimination gate. Trial matched-exposure benchmarks use actual exposure
(approximately 0.09%–7.15%) and average notional. Cash and direction-matched
diagnostics are also retained. Bootstrap, block bootstrap, PSR and DSR are stored
per trial; they are diagnostics, not proof. Parameter-neighborhood promotion is
not applicable because there are no Development-pass candidates.

Regime tags are frozen on each trade for HTF up/down/transition, pullback,
high/normal/low volatility and major support/resistance tests. They are used only
for post-trade explanation and did not create a new filter.

## Artifacts and governance

Accepted artifacts are under
`.runtime/strategy-phase4a/d2a72ac24223320655e7eb08d54dba38d976a5c1e804b83203abfc43b2e6ebed/`:

- `manifest.json`
- `trial_ledger.json` (all 32 trials, including losses and insufficient samples)
- `event_ledger.jsonl.gz`
- `trade_ledger.jsonl`
- `aggregate_metrics.json`
- `checkpoint.json`
- `report.json`

Four earlier runs are retained and explicitly marked `INVALIDATED_ENGINE_BUG`;
the defects and version bumps are recorded in `research/phase4a_engine_bug_001.json`
through `_004.json`. None is used for the conclusion.

The accepted run performed 4,406,976 evaluations in 242.63 seconds
(approximately 18,163 evaluations/second), peaked at 1.93 GB traced memory, and
produced 174.73 MB of artifacts. The event-frequency baseline had 254.23 seconds
of summed single-worker work and 129 seconds observed with bounded workers.
Checkpoint size is 96 bytes; a read/identity resume check took 113.5 ms in the
recorded PowerShell fixture. Re-running artifact writes is identity-idempotent.

The implementation imports no Paper, order, decision engine, collector, LLM or
official-history client and writes no database. It does not modify the homepage.
There is no Phase 4A order or deployment route.

## Conclusion and next step

Phase 4A found no candidate eligible for Validation. Trend Pullback failed net
expectancy/PF gates; MA200 Mean Reversion had isolated positive configurations
but insufficient samples and unstable asset/trade concentration, so those results
cannot be promoted.

The only next research step is an independent protocol review of the causal
replay/geometry evidence before deciding whether to preregister a new strategy
version. Do not access final OOT, tune Phase 4A, or advance any candidate to Paper.
