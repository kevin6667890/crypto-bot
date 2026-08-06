# Phase 4A6B Full Development Lifecycle Identity Gate

- Integration SHA: `5312ee256d8465db2d1a000813a6988c252a6d07`
- Dataset identity: `e8b0c73430a41e5e8696b0319e887b26222c8c6705bef2a32f726da632840062`
- Development contexts: 137718
- Candidate evaluations / Router evaluations: 4406976
- WATCH / ARMED / TRIGGER_READY: 2560549 / 163746 / 537
- Setup anchors: 1023472 (unexpected changes: 0)
- Level continuity changes: 1441272 legitimate, 0 unexpected
- Duplicate TRIGGER_READY: 0
- Confirmation lineage / geometry provenance: 100.000000% / 100.000000%
- Lifecycle setup-key propagation: FAIL — 368,800 evaluations promoted an INELIGIBLE fallback identity into a non-null setup anchor/raw lifecycle key.
- Checkpoint compatibility: FAIL — feature checkpoints have no enforced schema version and old code may silently accept new fields.
- Identity gate: FAIL
- Primary conclusion: `LIFECYCLE_PROPAGATION_FAILURE`
- Secondary failed hard gate: `CHECKPOINT_IDENTITY_FAILURE`

This run evaluated Development identity and lifecycle continuity only. It did not run a profitability backtest, create trades, calculate PnL, read Validation/OOT, or touch Paper/collector/frontend/production.
