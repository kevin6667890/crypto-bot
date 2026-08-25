# Thesis Capability Expansion V2

Thesis V2 keeps the V1 trust boundary: AI may map language into a closed,
validated definition, but deterministic domain code alone decides event
membership and statistics.

## Expression contract

`ThesisExpressionV2` contains only `CONDITION`, `ALL`, `ANY`, and `NOT` nodes.
Depth is capped at 3, leaf conditions at 10, and group children at 8. Conditions
select a registered feature, operator, value, and schema-validated parameters;
unknown nodes, parameters, SQL, code, loops, and functions are rejected.

Leaves evaluate to `TRUE`, `FALSE`, or `UNKNOWN`. `ALL` is false if any child is
false, true only when all are true, otherwise unknown. `ANY` is true if any child
is true, false only when all are false, otherwise unknown. `NOT` preserves
unknown. `between` is deterministically compiled into inclusive `gte` and `lte`
conditions.

An ordinary event occurs only when a fully qualified expression moves from
non-true to true. All required sources must be qualified at the current and
necessary lookback timestamps, so missing coverage cannot manufacture an event.
The V1 overlap embargo remains authoritative.

Stored `ThesisSpecV1` definitions and historical baselines are never rewritten.
`legacy-v1-expression-adapter-v1` maps V1 required conditions to `ALL` only for
evaluation compatibility; V1 hashes remain unchanged.

## Semantic presets and parsing

Parser V3 receives features, operators, parameters, timeframes, data
availability, and presets from the capability registry. It cannot add a feature
or threshold outside that contract. Explicit user numbers override presets.
Versioned presets cover reviewed phrases such as previous high, obvious volume
surge, OI surge, RSI overbought, and RSI oversold. Every applied preset is shown
before execution, editable, stored in the spec, and included in the definition
hash.

Parser results distinguish ready, ready with assumptions, needs input, partially
supported, unsupported, and error. Unsupported clauses remain in the result and
must be explicitly edited or removed by the user; they are never silently
dropped or substituted.

## Price structure semantics

`ROLLING_HIGH_BREAKOUT_CONFIRMED` compares the current confirmed close with the
maximum high of the previous `lookback_bars` confirmed candles. The current
candle is excluded. `ROLLING_LOW_BREAKDOWN_CONFIRMED` symmetrically uses the
minimum previous low. A wick alone is not confirmation. Lookback is bounded
from 5 to 500 bars.

`FAILED_BREAKOUT_CONFIRMED` begins with a confirmed rolling-high breakout and is
true only when a later confirmed close returns below its original reference
within `failure_window_bars` (1 to 20, preset 3). Its event timestamp is the
failure confirmation timestamp, never the original breakout timestamp. Failed
breakdown is symmetric. Outcomes start after failure confirmation.

Canonical MarketState support/resistance features remain deferred because the
repository does not retain enough point-in-time canonical level history for an
exact replay. Rolling structure is exposed under separate names and is not
presented as equivalent to MarketState semantics.

## Historical and current data

OHLCV, OI, funding and basis retain separate component identities. The usable
range is their qualified intersection after warmup. Derivatives are joined only
from timestamps and publication times at or before candle close, with bounded
staleness. Percentiles are causal. Missing is `UNKNOWN`, not false or zero.

The current evaluator uses the latest confirmed candle and returns both the
expression tree and leaf results. Tracking V2 stores the expression, definition
hash, feature and preset versions, historical component identities, and current
evaluation policy. What Changed reports leaf, group, overall, quality and source
identity transitions. Historical and current dataset identities remain
separate.

A feature is trackable only when capability metadata marks its current evaluator
available. Historical-only features are rejected by both UI and backend track
creation. A derivative snapshot failure cannot remove a condition or block an
unrelated OHLCV-only thesis.

## Product boundary

Capabilities are the single source for parser context, the manual builder,
examples, labels, timeframes, operators, parameters, historical/current
availability and reasons. The UI renders nested expressions and assumptions,
but it does not recompute feature values, event levels, or research statistics.
Breakout event context supplies the reference level; failed events also supply
the original breakout and failure-confirmation timestamps.

AI explanations receive an audited fact set and cannot generate research
numbers. No code in this capability touches trading execution, strategy
selection, position sizing, or risk behavior.
