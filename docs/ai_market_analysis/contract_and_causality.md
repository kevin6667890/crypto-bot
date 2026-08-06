# Target contract, causality and report rules

## Deterministic modules

### Market Timeline Reconstruction

The engine identifies an observation window, range build, compression start/end, range low/high, breakout timestamp/direction/candle, breakout volume ratio, impulse extreme, pullback start and current phase. Allowed phases are `RANGE_BUILDING`, `COMPRESSION`, `BREAKOUT_ATTEMPT`, `BREAKOUT_CONFIRMED`, `IMPULSE`, `POST_BREAKOUT_PULLBACK`, `RETEST`, `CONTINUATION`, `FAILED_BREAKOUT`, `REVERSAL`, `UNCLASSIFIED`. Each event stores confirmed source-bar timestamps and an invalidation. This is a sequence model over deterministic events, not an LLM narrative.

### Timeframe Structure

One object is mandatory for each of 15m, 1H, 4H, 1D and 1W. It carries last confirmed close, a separately labelled incomplete candle observation, MA20/30/60/200, EMA20, slopes, price position, MA ordering, swing structure, RSI, Stoch RSI, ATR, volume regime, trend/structure classifications, nearest support/resistance references, invalidation, qualitative confidence, supporting facts, contradicting facts and every source bar timestamp.

### Order Flow Phase Attribution

Attribution is calculated separately for `BEFORE_BREAKOUT`, `BREAKOUT_IMPULSE`, `POST_BREAKOUT_HIGH` and `CURRENT_PULLBACK`. Each phase carries start/end, price change, volume/ratio, CVD delta, OI start/end/change, liquidation, funding, basis, quality/gaps, primary and alternative classification, qualitative confidence, evidence and mandatory counterevidence.

Classification is one of `NEW_LONGS_DOMINANT`, `SHORT_COVERING_DOMINANT`, `NEW_SHORTS_DOMINANT`, `LONG_LIQUIDATION_DOMINANT`, `TWO_SIDED_DELEVERAGING`, `SPOT_BUYING_LIKELY`, `MIXED_POSITIONING`, `INSUFFICIENT_EVIDENCE`. A sign combination is evidence, not sufficient proof by itself. “Spot buying likely” requires positive spot/aggressor evidence and must remain probabilistic when venue coverage is incomplete.

### Key Level Engine

Each level stores a central price and zone, source type, timeframe, first detection, last test, touches, qualitative strength, role (`SUPPORT|RESISTANCE|PIVOT`), state (`ACTIVE|BROKEN|FLIPPED|UNCONFIRMED`), confluences, invalidation rule and evidence pointers. Sources include range edges, breakout platform, prior/swing extremes, MA20/30/60/200, EMA20, VPVR POC/VAH/VAL, psychological levels and merged confluence. A level without evidence is invalid.

### Scenario Tree

Exactly three initial branches are required: bullish continuation, normal retest and failed breakout. Likelihood is only `HIGH|MEDIUM|LOW|UNRANKED`; it is not a fabricated probability. Every branch has trigger, confirmation, expected path, context-linked targets, invalidation, volume/CVD/OI confirmation and contradictory evidence.

### Position Plan Context

`source` is `PAPER`, `USER_DECLARED` or `NONE`. It contains side, entry, average cost, quantity, original thesis/timeframe/stop/targets, realised exits, remaining position, current risk, completion and discipline warning. `NONE` requires null/empty position facts and forbids specific size advice. AI may quote the original plan but may not rewrite it as if the user had supplied the revised plan.

## MarketAnalysisContext v1

Top-level fields are `schema_version`, `context_id`, `instrument`, `generated_at`, `decision_time`, `latest_confirmed_market_time`, `requested_analysis_mode`, `source_versions`, `data_watermarks`, `data_quality`, `timeframe_structures`, `structure_events`, `market_timeline`, `order_flow_phases`, `key_levels`, `scenario_tree`, `position_context`, `macro_context`, `current_core_question`, `unsupported_claims`, and `provenance`.

Every important numeric observation uses `TracedNumber`: `value`, `unit`, `timestamp`, `source`, `status`, `quality`, `derivation`, `version`, plus an optional JSON Pointer. Provenance at only the top level is insufficient.

`context_id` is the deterministic identity of canonical context content and source versions. The future implementation should hash a canonical representation excluding volatile transport metadata. Identical `context_id` must not trigger a duplicate LLM call.

## Causal and quality rules

1. Only observations confirmed and available at or before `decision_time` may contribute to deterministic conclusions.
2. An incomplete candle is a live observation only. It cannot confirm a breakout, breakdown, swing, retest or invalidation.
3. Higher timeframes derived from lower timeframes require every complete constituent. 1W is Monday 00:00 UTC through the next Monday and requires seven contiguous confirmed 1D bars.
4. Never compute cumulative CVD change across a CVD gap.
5. Never compute OI change, z-score or acceleration across an OI gap.
6. OI absolute value may resume from the first genuine observation after a gap; dependent change fields remain unavailable until a new continuous window is complete.
7. If a UTC-day CVD sequence has a gap, later same-day anchored cumulative values are `PARTIAL_AFTER_GAP` until the next valid anchor reset. They are not `VALID`.
8. Do not zero-fill, interpolate or forward-fill missing microstructure. Null means unavailable, not neutral.
9. Every different source watermark is recorded. Material mismatch is a `watermark_mismatch` and downgrades affected claims.
10. Timeframe mapping is exact: a 15m request cannot silently read/return 1H facts. Case aliases are normalized only at the API boundary and recorded.
11. Every structure judgment stores all bar timestamps it used.
12. Every level stores its evidence paths and first-detection time.
13. Every attribution includes counterevidence and an alternative classification.
14. Insufficient inputs return `INSUFFICIENT_EVIDENCE`, never a directional guess.
15. The LLM must preserve `UNKNOWN`, `MISSING`, `PARTIAL` and `INSUFFICIENT_EVIDENCE`; wording cannot promote them into facts.
16. The LLM may not introduce a number absent from the context. All report numeric claims require context JSON Pointer citations.
17. Macro items require source URI, publication timestamp, retrieval timestamp, relevance and the bounded claim extracted from that source.
18. When no real position is user-declared, the system must not imply one. Paper and user positions are never merged silently.

Semantic rules that exceed JSON Schema expressiveness are enforced by a future audit library; Phase AI-1 demonstrates them in static tests. This includes timestamp ordering, gap/change incompatibility, pointer resolution, request/response identity and position-guidance constraints.

## Gap state transitions

| Lane | Before gap | At gap | After first genuine observation | Fully usable again |
|---|---|---|---|---|
| CVD delta | `VALID` if continuous | change unavailable | raw minute delta is observed; UTC anchored cumulative is `PARTIAL_AFTER_GAP` | next clean anchor/window according to metric definition |
| OI absolute | `VALID` | missing | absolute OI may be `VALID` from genuine observation | immediately for absolute only |
| OI change/zscore/acceleration | `VALID` if continuous window | unavailable | unavailable; no bridge to pre-gap value | after a complete post-gap lookback |
| funding/basis/liquidation | lane-specific | missing/partial | genuine event only | after each metric's documented completeness window; liquidations never become a complete global ledger |

## Report request and response

`AIReportRequest` freezes `request_id`, `context_id`, mode, request time, language, the complete context and generation policy (`may_create_numbers=false`, `must_preserve_unknown=true`, input/output token caps).

Modes:

- `QUICK`: 300–500 Chinese characters; core conclusion, current phase, two supports, two resistances, three most important order-flow facts and one invalidation.
- `FULL`: fixed sections: synthesis; recent timeline; move attribution; 15m; 1H; 4H; daily; weekly; volume/CVD/OI/funding/basis/liquidation; levels; three paths; limitations/invalidation.
- `POSITION_AWARE`: FULL plus current position, original-plan completion, current reward/risk, staged handling logic, timeframe-upgrade warning and thesis invalidation. It is valid only when position source is not `NONE`.

`AIReportResponse` stores `headline`, `market_phase`, `directional_bias`, qualitative `confidence`, structured `sections`, context references for `key_levels` and `scenarios`, nullable `position_guidance`, `unsupported_claims`, `data_warnings`, claim citations, `generated_text`, `model`, `prompt_version`, `context_id` and `audit_status`. Model output remains `PENDING` until the fact auditor passes it; a failed report is not promoted to the normal UI.

## Fact classes

The UI and report renderer must distinguish:

- data fact: direct traced observation;
- program derivation: deterministic and versioned;
- AI synthesis: prose combining cited facts;
- uncertainty: explicit quality/confidence limitation;
- counterevidence: facts weakening the primary conclusion;
- missing data: explicit source/field with no usable observation.

No layer may relabel a lower-confidence class as a higher-confidence class without new evidence.
