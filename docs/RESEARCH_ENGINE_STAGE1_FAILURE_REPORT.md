# Research Engine V1 — Stage 1 Failure Diagnostic

Source: append-only ledger, `research_cycle_v1_final`; 12 distinct candidates,
confirmed BTC 15m development data, next-open fills, 5bp fee and 3bp slippage.

## Candidate-level evidence

| Family | Candidates | Return range | Sharpe range | MaxDD range | Trades | Fee burden* | Main result |
|---|---:|---:|---:|---:|---:|---:|---|
| Trend | 4 | -9.66% to -56.88% | -0.43 to -6.94 | 23.48% to 57.00% | 268–500 | 37.6%–68.0% | 3 failed drawdown; the least bad candidate then stopped because Stage 2 stability evidence did not exist. |
| Momentum / breakout | 4 | -38.03% to -44.89% | -2.24 to -3.57 | 45.34% to 47.34% | 328–413 | 38.6%–40.4% | All failed drawdown. |
| Mean reversion | 4 | -26.09% to -37.39% | -4.37 to -5.59 | 26.09% to 38.10% | 135–178 | 27.2%–31.8% | All failed drawdown. |

\*Fee burden is `fees / (abs(net profit) + fees)`, an explanatory proxy only;
it is not a formal gate and is not gross-PnL cost ratio.

## Interpretation

The failures are not primarily a minimum-trade-count issue: every candidate
had materially more than the 30-trade floor. Nor is there evidence that the
drawdown gate alone manufactured the outcome: all reported returns and Sharpes
are negative before the gate is applied.

Costs are economically material, especially for high-turnover momentum/trend
variants, but cannot by themselves explain the negative gross result without a
separate gross-PnL decomposition. The next Engine adapter will record turnover,
gross PnL and formal cost/gross-PnL ratio before using a cost-ratio gate.

The appropriate provisional conclusion is **no evidence of edge for these
bounded hypotheses in this development window**, not a claim that all trend,
breakout or mean-reversion ideas are false. It is not a reason to relax gates.

## Stage progression

* Stage 0: three formal OHLCV families were available; VPVA_PROXY/Boll was
  registered but deliberately had no expandable adapter.
* Stage 1: 11 candidates failed maximum drawdown; one Trend candidate passed
  Stage 1 but was stopped at Stage 2 because no parameter-neighbour evidence
  was available. No result reached OOS, cross-asset, bootstrap or holdout.
* Holdout accesses: 0.

## Required breadth expansion policy

The next cycle must add strategy *definitions*, not a wider local grid:

1. volatility contraction/expansion;
2. volume-confirmed trend;
3. time-series momentum;
4. breakout;
5. regime-filtered variants;
6. cross-asset relative strength only after causal aligned inputs exist.

Each family has a new cycle budget. `TRUE_VPVA`, CVD, OI, funding and basis
remain UNAVAILABLE and cannot be substituted by proxies.
