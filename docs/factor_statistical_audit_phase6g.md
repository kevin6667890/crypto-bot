# Phase 6G factor statistical validity audit

Phase 6G reads the immutable Phase 6F SQLite ledger as the generated-expression
manifest. It verifies the frozen dataset, run, manifest, and all 2,500 trial
identities before mapping each trial to a versioned Phase 6G reevaluation
identity. It never imports or calls the factor generator.

Dense 15-minute results are retained as descriptive diagnostics. Formal
inference uses source-native events:

- funding factors: genuine settlement timestamps;
- basis factors: confirmed one-hour basis updates and a fixed one-hour causal
  rebalance schedule;
- price/context factors: the existing 15-minute causal research grid.

Each result reports dense rows, source events, factor-value changes,
non-overlapping labels, effective observations, duplicated exposure duration,
and the unchanged-source rate. Forward-filled funding rows do not create
independent events.

For every horizon, Phase 6G calculates deterministic non-overlapping outcomes,
Newey-West errors with lag `ceil(horizon / native spacing) - 1`, and a
fixed-seed moving-block bootstrap when at least four blocks and twenty
observations exist. Native-event HAC p-values, not dense IID p-values, feed
formal FDR families.

Diagnostic portfolios create at most one position per native event and retain
only non-overlapping close returns. Costs apply to event-to-event exposure
turnover. Sharpe uses long-run dependency-adjusted volatility; annualization
uses the median realized return spacing. PSR and DSR use per-period Sharpe and
effective return observations.

Local BH families are explicitly keyed by source family, instrument, horizon,
expression lineage, and chronological segment. Global BH includes every
statistically applicable attempted test, assigning `p=1` to failed or
insufficient outcomes. Pure structural invalidity is mapped but is not counted
as a statistical test.

The original discovery, selection-validation, and locked-verification
membership is preserved. Locked results do not change expressions, generation,
budgets, or thresholds. The audit produces no strategy, order, or deployable
signal.
