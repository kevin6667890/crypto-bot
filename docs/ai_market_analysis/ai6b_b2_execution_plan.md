# AI-6B B2 Internal Fake Shadow Canary preparation

Status: **PREPARATION ONLY — DEFAULT OFF — NOT AUTHORIZED FOR B2 EXECUTION**

- Base branch: `origin/ops/ai-market-analysis-production-canary`
- Base commit: `3a788d84ca88fc6ae09f1f170a207cd07b056bcd`
- Base tree: `fa28ac452695c784afc667eb9004912012b578a1`
- Preparation branch: `prep/ai6b-b2-fake-shadow`
Preparation worktree: `C:\Users\ASUS\PycharmProjects\crypto-bot-ai6b-b2-prep`

This package does not authorize or perform SSH, production writes, service restarts, schema migration, flag changes, worker startup, Shadow/Presentation enablement, real DeepSeek calls, secret use, or Paper orders. A production owner may begin the execution section only after B1 has an explicit PASS.

## Fixed B2 scope

- Provider: `fake` only.
- Visibility: authenticated internal Shadow only.
- Instruments: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, `SOL-USDT-SWAP`.
- Modes: `QUICK`, `FULL`.
- Position sources: `NONE`, `PAPER`.
- Forbidden: `USER_DECLARED`, non-Fake providers, live trading, Paper order creation, and Router/Collector/Aggregation changes.
- Both frontend and backend remain disabled by default. The frontend route requires `VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED=true` at build selection; the API additionally requires the runtime `AI_MARKET_ANALYSIS_PRESENTATION_ENABLED=true`. Absence of either gate fails closed.

## Implementation-backed capability inventory

No readiness decision below relies only on a runbook.

| Capability | Status | Implementation evidence |
|---|---|---|
| Fake report provider | ALREADY_READY | `report_provider.py::FakeAIReportProvider`; deterministic result and failure behaviors |
| Report worker | ALREADY_READY | `report_jobs.py::ReportWorker`; registry/prompt verification, bounded retries and budgets |
| Audit worker | ALREADY_READY | `report_audit_jobs.py::AuditWorker`; frozen input and fail-closed error event |
| Presentation API | ALREADY_READY | `paper_api.py` presentation routes; runtime flag, auth, rate limits, sanitized failures |
| Shadow frontend | ALREADY_READY | `App.tsx` route gate and `ShadowMarketAnalysisPage.tsx`; no request before authorization |
| NONE position | ALREADY_READY | `position_context.py::none_position_context` |
| PAPER position | ALREADY_READY | `position_context.py::paper_position_context`; SQLite URI is read-only |
| Audit fail-closed | ALREADY_READY | `presentation.py::_audit_eligibility/_status_only`; PENDING/FAILED/ERROR have `report: null` |
| Registry/context identity | ALREADY_READY | immutable registry snapshot, prompt hash and presentation revalidation |
| Instrument isolation | ALREADY_READY | strict supported enum, report selection match and context lookup by instrument |
| Mode isolation | ALREADY_READY | strict mode selection and report selection match |
| Stale handling | ALREADY_READY | confirmed-market-watermark freshness policy; CURRENT/AGING/STALE/SUPERSEDED/UNKNOWN |
| Warning visibility | ALREADY_READY | presentation forwards `data_warnings`; UI renders an `aria-live` warning section |
| Macro evidence | ALREADY_READY | frozen evidence set, registry references, safe-link frontend coverage |
| i18n | ALREADY_READY | Chinese/English UI labels; audited body remains frozen rather than translated |
| Accessibility | ALREADY_READY | keyboard labels, semantic states and Axe E2E assertion at critical/serious zero |
| Kill-switch | ALREADY_READY | durable independent state file, hard-stop event allowlist, no reset operation |
| B2 deterministic fixtures | NEEDS_PREP → PREPARED | `ai6b_b2_fake_canary_v1.json` and local full-pipeline runner |
| Machine-readable acceptance matrix | NEEDS_PREP → PREPARED | `config/ai6b_b2_acceptance_matrix.json` |
| Minute observation | NEEDS_PREP → PREPARED | read-only `scripts/observe_ai6b_b2.py` plus operations snapshot template |
| Rollback rehearsal | NEEDS_PREP → PREPARED | isolated ON → Fake → OFF verifier; evidence/schema/legacy hashes retained |
| Production B2 activation | MISSING BY DESIGN | requires B1 PASS and production-owner authority; this branch cannot activate it |

## Deterministic Fake fixtures

The positive acceptance grid is exactly 3 instruments × 2 modes × 2 position sources = 12 cases. The runner constructs confirmed deterministic OHLCV and deterministic order-flow input, calls the real context builder, submits through `ReportService`, runs `ReportWorker` with `FakeAIReportProvider`, freezes the audit input, runs `AuditWorker`, and reads through `build_report_presentation`. It never inserts final report JSON directly.

Prepared variants are normal, warning, stale context, missing orderflow, audit failure, registry mismatch, wrong instrument, wrong mode, PENDING, FAILED, ERROR, and legacy audit schema. All variant selection is data-driven by `fixtures/ai_market_analysis/ai6b_b2_fake_canary_v1.json`.

Local matrix command (safe now and after B1; it creates only a new local output directory):

```powershell
python scripts/run_ai6b_b2_fake_canary.py --local-only --output-dir artifacts/ai6b-b2-local-canary
```

Required result: `case_count=12`, `pass_count=12`, `fake_provider_calls=12`, `live_provider_calls=0`, `paper_orders_created=0`.

The production daily output cap is 25,000 tokens, while worst-case reservation for six QUICK and six FULL cases is 29,400. The production owner must not raise or bypass that cap. The machine matrix therefore defines two UTC-day batches: all QUICK plus FULL/NONE (17,400 maximum reserved output tokens), then FULL/PAPER after the UTC budget reset (12,000). The local runner's isolated test-only budget exists solely to validate the full Cartesian set in one local invocation and is not a production setting.

## Acceptance procedure after B1 PASS

Only the production owner executes this section.

1. Record B1 PASS evidence, deployed commit/image digests, pre-canary report/audit/Paper-order counters, Router/Collector/Aggregation status, old AI Brief status, and frontend error baseline.
2. Verify all AI flags are still OFF and `AI_REPORT_LIVE_PROVIDER_ENABLED=false`. Verify the durable kill-switch state is known. Do not expose or read a provider key for B2.
3. Validate the 12-case fixture locally against the exact deployed commit. Reject any result containing a provider other than `fake`, a source outside NONE/PAPER, or any failed identity assertion.
4. Turn on only the approved internal Fake Shadow gates. Keep live provider and USER_DECLARED disabled. Do not change Router, Collector, Aggregation, Paper scheduler, or legacy AI Brief.
5. Submit the matrix in the two machine-defined UTC-day batches, never raising or bypassing configured queue/token budgets. Freeze and audit every generated report. Presentation reads are permitted only after the matching audit passes.
6. Run all negative cases. PENDING, FAILED, ERROR and legacy schema must return status-only payloads. Wrong instrument/mode, context or registry identity must reject rather than fall back to another report.
7. Sample every minute with the observation script. Stop immediately if live provider calls or Paper-order delta becomes nonzero, provider type is not Fake, warnings disappear, identity mismatches occur, or Router/Collector/Aggregation/old AI Brief deviates from baseline.
8. Perform Shadow OFF rollback. Do not delete the AI database, reports, audits, registry snapshots, or schema. Verify immutable evidence and legacy hashes remain unchanged.

## Presentation acceptance

Backend targeted tests cover PENDING/FAILED/ERROR/audit-not-passed body suppression, legacy schema suppression, wrong instrument/mode, registry/context/hash tampering, stale watermark handling, warning propagation, and NONE/PAPER position labels. `paper_api.py` rejects `USER_DECLARED` report submission for this canary, and `AI6B_PRIVACY_SCOPE_ENFORCED=true` rejects both presentation projection and position-detail access for non-NONE/PAPER sources.

Frontend E2E coverage exercises Chinese and English UI, audited Chinese-body preservation, warnings, freshness states, selection races, desktop/mobile 390 px, keyboard navigation, and Axe. The Shadow route remains OFF unless explicitly selected, while `/` remains the legacy route.

## Observation command

The sampler opens databases with SQLite read-only URIs and consumes an operator-exported JSON snapshot; it makes no network connection. Capture the Paper baseline before B2.

```powershell
python scripts/observe_ai6b_b2.py `
  --ai-db <AI_DB_READONLY_PATH> `
  --paper-db <PAPER_DB_READONLY_PATH> `
  --paper-orders-baseline <PRE_B2_COUNT> `
  --operations-json config/ai6b_b2_operations_snapshot.template.json `
  --output artifacts/ai6b-b2-observations.jsonl `
  --samples 60 --interval-seconds 60
```

It exits nonzero on any live-provider call or Paper-order delta. The operator snapshot must be refreshed by the production owner's approved read-only telemetry export so Router, Collector, Aggregation, old AI Brief, presentation calls, stale and frontend errors are current.

## Rollback rehearsal

Local command:

```powershell
python scripts/verify_ai6b_b2_rollback.py --local-only --output-dir artifacts/ai6b-b2-rollback-local
```

The verifier performs local Shadow ON → 12-case Fake canary → Shadow OFF and asserts: AI DB retained; contexts, registry snapshots, reports and audits retained; schema migration ledger unchanged; immutable hashes unchanged; legacy frontend and old AI Brief route tokens unchanged; live calls zero; Paper orders zero. It deliberately implements no delete, downgrade, restore, or production flag operation.

## Tests

Safe targeted preparation tests:

```powershell
python -m pytest -q tests/ai_market_analysis/test_ai6b_b2_preparation.py
python -m pytest -q tests/ai_market_analysis/test_ai6a_presentation.py tests/ai_market_analysis/test_ai6ac_presentation_closure.py
python -m pytest -q tests/ai_market_analysis/test_ai6a_frontend_security_contract.py
cd frontend
npx playwright test e2e/shadow-market-analysis.spec.ts --project=chromium
```

The Playwright file includes desktop and 390 px checks and requires Axe critical = 0 and serious = 0.

### DEFERRED_UNTIL_B1_AGENT_IDLE

Do not run these while B1 deployment/verification is active:

```powershell
python -m pytest -q
cd frontend
npm run build
npm run test:e2e
```

Router performance benchmarks, large bundle benchmarks, and any other CPU-heavy suites are also deferred.

## Hard-stop and evidence rules

- Any non-Fake provider attempt, live-provider count > 0, provider secret use, Paper-order delta, USER_DECLARED access, identity mismatch, unaudited body, hidden critical warning, or Router/Collector/Aggregation change is an immediate B2 failure and rollback trigger.
- Rollback means Shadow OFF only. Evidence and schema remain append-only and intact.
- The legacy `/` frontend and old AI Brief remain available throughout.
- B2 is not entered by preparing, merging, deploying, or locally running this package. It begins only on explicit production-owner activation after B1 PASS.
