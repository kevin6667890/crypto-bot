# Phase 4A2 forensic audit

Version: `strategy-phase4a-forensic-audit-v1` / `strategy-phase4a-audit-report-v1`

This audit is **DIAGNOSTIC_ONLY**. It read Development evidence only, did not
read Validation or locked OOT, and did not change a Phase 4A classification or
create a parameter/trial.

## Identity gate

- Phase 4A artifact SHA-256: `5b757fd8f2ca0b9ed4194de94b104b47382550420cb98432b84f44995fd26d18`
- dataset identity: `e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062`
- accepted engine: `strategy-backtest-engine-v2.0.4`
- accepted code SHA: `8f7cf55675e3adbe9aaae924978b7de80eef4f80`
- trials: 32; Validation payloads: 0; OOT accessed: false
- all four predecessor runs remain `INVALIDATED_ENGINE_BUG`

Every identity matched its expected value before analysis began.

## Independent replay verdict

The accepted Phase 4A runner did not invoke the frozen
`MarketAnalysisContextV2 -> MarketStateEngineV2 -> StrategyRouterV2` chain. It
created events with its private `_frame` and `_evaluate` implementation.

Using the real V2 chain, the audit rebuilt 896 deterministic samples covering
all four family/direction groups, all 32 parameter sets, BTC/ETH/SOL, all four
Development folds, 25 WATCH/ARMED/TRIGGER_READY samples per group, and all 596
expiry events. Only 124/896 lifecycle stages matched. Combined setup,
evaluation, and level identity matched 0/896. Match rates were:

- Trend Pullback LONG: 30/411 (7.30%)
- Trend Pullback SHORT: 33/335 (9.85%)
- MA200 LONG: 32/75 (42.67%)
- MA200 SHORT: 29/75 (38.67%)

This is an `EVENT_REPLAY_ERROR`, `IDENTITY_ERROR`, and `GEOMETRY_ERROR` in the
formal evidence path. In contrast, an independent raw-15m reconstruction of
104 deterministic/extreme trades matched 104/104 next-open entries, frozen
stop/target processing, STOP_FIRST ordering, fees, slippage, exit, PnL and R.
All 32 trial aggregates also reconciled. The execution arithmetic is sound;
the events fed to it are not the frozen Strategy Router V2 events.

## Descriptive ledger postmortem

The following numbers describe the invalid private evaluator and must not be
used to accept, reject, or revise the frozen strategy families.

| Group | WATCH | ARMED | TRIGGER_READY | Trades | Gross exp. R | Net exp. R |
|---|---:|---:|---:|---:|---:|---:|
| TP LONG | 147,210 | 72,394 | 2,290 | 1,598 | 0.0405 | -0.0653 |
| TP SHORT | 72,986 | 36,934 | 1,528 | 1,040 | -0.0059 | -0.1397 |
| MA200 LONG | 145,152 | 8,612 | 170 | 160 | 0.1142 | -0.0036 |
| MA200 SHORT | 106,443 | 4,897 | 89 | 75 | 0.1086 | -0.0539 |

TP generated 2.078 LONG and 1.387 SHORT triggers per 1,000 evaluated candles.
Its trade-to-unique-instrument-entry density was 5.14 LONG and 5.56 SHORT.
Ledger-only states remained negative even in the nominal HTF continuation
slices: -0.0482R LONG and -0.0933R SHORT. These are symptoms of the private
evaluator, not evidence that Strategy Router V2 overtriggers.

MA200 events split as 112/58 LONG triggers and 55/34 SHORT triggers for 1H/4H.
The leading serialized blockers were `INVALID_GEOMETRY`,
`NO_STRUCTURAL_LEVEL`, and `MA200_TOUCH_WITHOUT_RECLAIM`. Confluence provenance
was not serialized, so a valid confluence distribution cannot be recovered.
Positive trial evidence was fragile: every formally positive trial turned
negative after removing its top two trades, largest-contributing asset, or
largest-contributing fold in at least one leave-one-out test.

Next-open latency itself was small (mean signed adverse movement: -0.002 bps TP
LONG, -0.033 bps TP SHORT, -0.082 bps MA LONG, -0.190 bps MA SHORT). The private
geometry used confirmed-swing stops for TP, MA-zone-opposite-boundary stops for
MA200, and prior-swing targets for every trade. Median structural R was 2.02,
2.42, 3.81, and 5.55 respectively; notional caps applied to 95.0%-100% of
trades. Again, this cannot validate the frozen router geometry because identity
and geometry provenance diverged.

## Classification and decision

All four groups receive the single primary classification
`ENGINE_OR_DATA_INVALID`, with secondary reasons `EVENT_REPLAY_ERROR`,
`IDENTITY_ERROR`, and `GEOMETRY_ERROR`. There are zero admitted new hypotheses:
postmortem slicing of invalid events cannot meet the evidence-admission gate.

Decision: **F. ENGINE_FIX_REQUIRED_BEFORE_ANY_RESEARCH**.

The only next action is an isolated repair/replay phase which makes Phase 4A
consume the real Context V2 -> State V2 -> Router V2 contract, invalidates the
accepted run, and reruns all 32 Development trials from the frozen manifest.
Validation and OOT must remain unopened. Do not create TP v3, MA200 v3, or begin
Phase 4B before that repair establishes trustworthy evidence.

The canonical machine-readable artifact is under
`.runtime/strategy-phase4a-audit/381995fcc2c5b2412a92b881faf170b46ae107371d259705db148a1a5241a3e7/`.
Its aggregate SHA-256 is
`55334ac03fb5e1c47de8edf10a169530a162ad0bac607a42dbe67aa1114800f7`.
