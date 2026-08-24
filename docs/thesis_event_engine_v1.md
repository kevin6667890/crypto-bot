# Thesis / Event Engine V1

## Purpose and boundary

Thesis Engine V1 answers one bounded question: when the same explicitly defined
conditions occurred in confirmed historical data, what followed? It produces
historical conditional evidence. It is not causal proof, a trading signal, a
prediction oracle, or a forward probability guarantee.

The domain core is `dashboard/thesis_event_engine.py`. It has no dependency on
HTTP, LLMs, strategy selection, Paper Trading, order execution, or persistence
writers. `dashboard/paper_api.py` is only a rate-limited JSON adapter.

```text
ThesisSpecV1 -> validation -> CompiledEventDefinition
             -> coverage gate -> bounded confirmed history
             -> causal feature batch -> transition candidates
             -> overlap policy -> forward outcomes -> aggregates
             -> deterministic ThesisTestResultV1
```

## Contract and closed feature registry

`ThesisSpecV1` requires an explicit version, BTC/ETH/SOL instrument identity,
1H/4H timeframe, at least one required condition, 4H/12H/24H horizons, an event
independence policy, and a Unix-seconds `requested_as_of`. Conditions contain
only `{feature, operator, value}`. Python expressions, SQL identifiers, paths,
imports, `eval`, and arbitrary DSL execution are not accepted.

Logical AND conditions are sorted before compilation. The definition hash
includes normalized conditions, thresholds, resolved feature versions, source
groups, transition semantics, horizons, and independence policy. JSON key order
and AND-condition order do not change the hash; a threshold change does.

V1 supports these existing causal price definitions:

- EMA20 and MA60/MA200 price relation; MA200 distance in percentage points
- prior-20 volume ratio and trailing-100 volume percentile
- ATR percentage, trailing-100 Bollinger compression/expansion percentile
- RSI14, 14-bar price momentum, and 14-bar momentum persistence

The batch path reuses `discovery_features.build_features` and the canonical
Market Context percentile helper. Every value at index `i` uses only rows
`0..i`. Percentile rank includes the current observation, uses `<=` ties,
requires 20 observations, and never ranks against the full dataset.

OI/CVD codes are present in the closed registry so requests are recognized and
fail honestly. Native aligned historical flow is not wired in V1; such required
conditions return `THESIS_NOT_TESTABLE_AS_REQUESTED` / `UNAVAILABLE`. There is
no zero fill, forward fill, candle-volume proxy, current-value backfill, or
automatic condition dropping. Optional flow conditions never gate membership;
their availability is exposed in `optional_coverage`, unavailable observations
remain null, and a typed `OPTIONAL_FEATURE_UNAVAILABLE` warning is returned.

MarketStateEngineV2's breakout lifecycle is deliberately not duplicated.
Confirmed breakout/failed-breakout features remain out of this V1 vocabulary
until a batched adapter can preserve its exact versioned transition semantics.
The Thesis Engine does not call the full multi-timeframe context once per bar
and does not turn MarketStateEngineV2 into an unbounded research runner.

## Coverage gate

`thesis-coverage-policy-v1` runs before event detection. Per required feature it
reports requested/available bounds, expected and usable observations, coverage
ratio, gaps, stale/partial flags, qualification, and reason. Supported OHLCV
features require:

- enough source history for their indicator warmup;
- at least 30 evaluable observations and a complete maximum-horizon path;
- at least 95% coverage over the evaluable interval and no gaps;
- a latest observation no more than two timeframe intervals behind the
  requested as-of.

The tested range is the intersection of required features' evaluable ranges,
not the longest price range. A failed required feature prevents scanning. A
`testable_subset` may be reported, but V1 never executes it in place of the
requested thesis. This policy is separately versioned from microstructure
research readiness: the latter's native-event and pending-human-approval gates
serve a different offline research-launch decision.

## Event and independence semantics

Conditions use three values: true, false, unknown. Missing/warmup data is
unknown, never false or zero. A candidate exists only when the composite of all
required conditions changes from an immediately preceding explicit false to
true at a confirmed candle close. Initial true, unknown-to-true, and persistent
true do not create candidates. Optional conditions annotate a candidate and do
not control membership.

`event-independence-max-horizon-v1` greedily accepts chronological candidates.
A candidate strictly before the prior included event's maximum horizon end is
retained as `EXCLUDED / OVERLAPPING_MAX_FORWARD_WINDOW`; one exactly at the
boundary is independent. Results expose raw, independent, and overlap-excluded
counts plus per-event reasons.

## Outcomes and aggregates

The event timestamp is the confirmed candle close and its close is the reference
price. For each horizon H, the engine requires the exact confirmed close at
`event_close + H` and every intervening timeframe bar. It calculates decimal
fractions:

- `forward_return = close_H / reference_close - 1`
- `MFE = max(post-event highs through H) / reference_close - 1`
- `MAE = min(post-event lows through H) / reference_close - 1`

The event candle's high/low is excluded. Missing paths and end-of-sample events
are censored per horizon; no nearest close or extrapolation is used. MFE/MAE are
neutral post-event upside/downside excursions, not a long-trade recommendation.

Each horizon returns eligible/censored N, positive/zero/negative counts,
historical positive rate, mean, median, P25/P75, min/max, and median MFE/MAE.
Positive means strictly greater than zero; zero remains in the denominator.
Quantiles use deterministic linear interpolation (Hyndman-Fan type 7). Sample
quality policy V1 is: N 0-9 `INSUFFICIENT`, 10-29 `LOW`, 30-99 `MODERATE`, and
100+ `ADEQUATE`. Raw statistics remain visible at small N.

## Anti-lookahead and identity guarantees

All source candles must explicitly be confirmed and have both open and close
timestamps at or before `requested_as_of`; future, unconfirmed, or conflicting
duplicate rows fail closed. Detection is completed from causal features before
forward outcomes are attached. Tests mutate future OHLCV and extreme volume and
assert earlier membership and percentile values remain identical.

`bounded-ohlcv-dataset-identity-v1` hashes ordered bounded timestamps, OHLCV,
confirmed state, source, source version, source store, required/optional native
flow values, and quality for source groups actually used by the definition. The result hash covers
the compiled definition, engine/feature versions, tested range/as-of, coverage,
dataset identity, independence/outcome policies, event records, and aggregates.
Runtime, request UUIDs, temporary paths, creation wall-clock, and provenance-only
Thesis metadata do not affect result identity.

## API

`POST /api/research/thesis/test` accepts structured JSON only:

```json
{
  "version": "thesis-spec-v1",
  "instrument": "BTC",
  "timeframe": "4H",
  "required_conditions": [
    {"feature": "VOLUME_RATIO", "operator": "gte", "value": 1.2}
  ],
  "optional_conditions": [],
  "forward_horizons": ["4H", "12H", "24H"],
  "requested_as_of": 1767225600
}
```

The reader is SQLite read-only, instrument/time bounded, and capped at 20,000
rows. Coverage failure is a typed HTTP 200 research result because the request is
valid but not testable. Invalid schemas return a sanitized 400; unexpected
failures return a generic 500 without a stack trace.

## Limitations and next phase

V1 does not yet expose canonical MarketState breakout/failed-breakout events or
native historical OI/CVD evaluation. It has no subgroup search, causality claims,
LLM parsing, tracking, UI, or artifact persistence. Phase 2 should add a tightly
validated natural-language-to-ThesisSpec adapter and one Test-an-Idea frontend
vertical slice against this endpoint. It must preserve the structured request as
the executable source of truth and use historical-rate wording.
