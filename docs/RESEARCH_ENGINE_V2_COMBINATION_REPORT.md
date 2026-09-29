# Research Engine V2 Combination Cycle

## Scope and controls

`research_cycle_v2_combinations_final` evaluated 20 explicitly registered hypotheses: ten economic recipes, each with a pre-declared FAST or SLOW mode. It tested 100 deterministic, unique candidates (five bounded thresholds per hypothesis). No cartesian indicator search, legacy optimizer, composite score, or protected-holdout data was used. Signals use confirmed BTC-USDT 15-minute OHLCV from 2024 and the canonical next-open execution model (0.05% fee and 0.03% slippage assumptions).

Artifacts are in `.research/research_cycle_v2_combinations_final/`, including `experiment_ledger.sqlite` and `summary.json`.

## Hypotheses and candidates

| Combination family | Candidates | Result |
| --- | ---: | --- |
| Trend + momentum | 10 | Stage 1 fail |
| Trend + volume | 10 | Stage 1 fail |
| Trend + volatility regime | 10 | Stage 1 fail |
| Momentum + trend regime | 10 | Stage 1 fail |
| Momentum + volume | 10 | Stage 1 fail |
| Breakout + volume | 10 | Stage 1 fail |
| Breakout + volatility contraction | 10 | Stage 1 fail |
| Mean reversion + range regime | 10 | Stage 1 fail |
| Mean reversion + volatility filter | 10 | Stage 1 fail |
| Base trend + BTC regime | 10 | Unavailable |

Every recipe has no more than three core components. The BTC-context family was stopped as `CAUSAL_ALIGNMENT_COVERAGE_UNAVAILABLE`; no proxy was substituted.

## Stage outcomes

| Stage | Passed | Failed / unavailable | Notes |
| --- | ---: | ---: | --- |
| Stage 0: data / hypothesis validation | 90 | 10 unavailable | Cross-asset context stopped fail-closed. |
| Stage 1: development screen | 0 | 90 | Every evaluated candidate stopped here. |
| Stage 2: parameter neighbourhood | 0 | 0 | Not applicable after early stop. |
| Stage 3: walk-forward / OOS | 0 | 0 | Not applicable. |
| Stage 4: cross-asset / timeframe | 0 | 0 | Not applicable. |
| Stage 5: cost stress / ablation / bootstrap | 0 | 0 | Not applicable. |
| Stage 6: protected holdout | 0 | 0 | No access. |

There are **no survivors**. This is a fail-closed outcome, not a reason to weaken a gate.

## Rejections and failure mechanism

Each of the 90 candidates actually backtested received `NO_RAW_EDGE`, `NEGATIVE_RETURN`, `NEGATIVE_SHARPE`, `MAXIMUM_DRAWDOWN`, and `EXCESSIVE_TURNOVER`. The remaining ten records are cross-asset `UNAVAILABLE` records.

Gross return, measured before fees but after the canonical price/slippage execution path, was negative for every evaluated candidate: **-43.43% to -27.18%**. Costs worsen an already negative result. `EXECUTION_FRAGILE` was not assigned because no candidate had positive gross PnL and negative net PnL.

## Cost, turnover, and holding period

Net returns ranged from **-93.32% to -78.83%**. Fee drag ranged from 1.15x to 2.24x absolute pre-fee gross PnL. Separate slippage drag is intentionally unavailable: it requires a counterfactual execution rerun and is not inferred.

| Family group | Mean gross return | Mean net return | Mean fees | Trades/day | Median holding | Mean MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Breakout variants | -33.70% | -81.08% | 4,738 | 2.69 | 0.25 h | 81.18% |
| Mean-reversion variants | -31.42% | -85.68% | 5,426 | 3.32 | 0.75 h | 85.69% |
| Momentum / trend-momentum variants | -32.59% | -89.46% | 5,688 | 3.93 | 0.70 h | 89.65% |
| Trend-volume / trend-volatility variants | -39.17% | -91.92% | 5,275 | 3.99 | 0.75 h | 91.99% |

Turnover is excessive for this horizon, but the negative gross results mean that lower turnover alone is not evidence of an edge.

## Regime, ablation, and interactions

Formal regime-level performance, parameter stability, ablation, cost stress, bootstrap, and walk-forward metrics are **not applicable**: the scheduler prevented expensive tests after the development gate. No positive incremental interaction is evidenced and no hypothesis is `PROMISING_FOR_FURTHER_RESEARCH`.

The nine evaluated combination families are `FAMILY_EXHAUSTED` for this cycle: each has ten tests and the same negative-gross, negative-Sharpe, high-turnover, drawdown pattern. They are frozen unless a new data source, timeframe, execution model, regime definition, or materially different hypothesis is introduced.

## Governance and conclusion

- Holdout access: **0**.
- Candidate identities: **100 unique** deterministic IDs.
- All Stage 0 and Stage 1 events are in the append-only ledger.
- The legacy early-holdout optimizer was not invoked.

No verified or promising edge was found. The main failure mechanism is a negative raw signal compounded by high turnover and fees, not a gate that is marginally too strict. The next high-value step is a causal cross-asset coverage audit plus real continuous funding, basis, open-interest, or trade/order-flow data before a new family cycle.
