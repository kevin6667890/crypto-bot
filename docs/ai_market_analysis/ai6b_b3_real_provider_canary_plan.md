# AI-6B B3 Real Provider Smoke + Bounded Canary Plan

Status: **PREPARATION ONLY**. No step in this document authorizes formal B3 or a paid request.

## Frozen preparation identity

- Base branch: `origin/ops/ai-market-analysis-production-canary`
- Base commit: `3a788d84ca88fc6ae09f1f170a207cd07b056bcd`
- Base tree: `fa28ac452695c784afc667eb9004912012b578a1`
- Preparation branch: `prep/ai6b-b3-real-provider-canary`
- Worktree: `C:\Users\ASUS\PycharmProjects\crypto-bot-ai6b-b3-prep`
- If the remote production-canary branch advances, record base drift. Do not automatically rebase. Re-integrate and revalidate before formal B3.

## PRECONDITIONS

Every item must be machine-recorded as `PASSED` before a live reservation:

1. B1 PASSED.
2. B2 PASSED.
3. Rollback rehearsal PASSED.
4. A new human `LIVE_PROVIDER_APPROVAL_ID` is present. Prep value is `MISSING_BY_DESIGN`.
5. Official Provider audit is fresh and unchanged from the price schedule loaded by the cost guard.
6. Token, call, concurrency, queue and USD budgets are valid before the request.
7. Secret isolation is runtime-validated: the mounted secret file is visible only to report-worker; diagnostics print only `SECRET_PRESENT=true/false`.
8. Runtime live flag, CLI `--allow-live-provider`, human approval ID, budget guard pass and clear kill switch all pass. The smoke runner additionally requires explicit `--execute-live`.

Any missing precondition returns a structured block and makes zero Provider calls.

## Budget and model

The selected canary model is `deepseek-v4-flash` in non-thinking mode. This is a local bounded-canary choice, not an official DeepSeek recommendation. The audited endpoint is `POST https://api.deepseek.com/chat/completions` with non-streaming JSON output.

Technical safety limits are: 10 calls/rolling 24h, global concurrency 1, per-instrument concurrency 1, queue 10, request input 12,000, QUICK output 3,000, FULL/POSITION output 8,000, daily input 150,000 and daily output 80,000. Formal B3 allows at most six total paid attempts solely because one historical attempt was charged after output truncation, while successful smoke calls remain capped at five. The exact `Decimal` cost telemetry uses cache-miss input price when cache state is unknown. A stale or unknown price version blocks before reservation.

Do not spend to the 10-call ceiling. Five calls are the initial maximum; the remaining five are held for reviewed recovery or validation.

## Durable attempt protocol

Logical identity contains `context_id`, `registry_snapshot_id`, prompt identity, instrument, report mode, position mode and request ID. Before the HTTP adapter receives the request, an isolated SQLite control ledger uses `BEGIN IMMEDIATE` to create `LIVE_PROVIDER_ATTEMPT_RESERVED`. Only its unguessable reservation owner may transition it.

Transitions:

```text
LIVE_PROVIDER_ATTEMPT_RESERVED
  -> FAILED_BEFORE_CHARGE       (reviewed retry may reserve the next attempt)
  -> REQUEST_SENT
       -> SUCCEEDED
       -> FAILED_AFTER_REQUEST_SENT
       -> UNKNOWN_CHARGE_STATE
```

`SUCCEEDED`, `FAILED_AFTER_REQUEST_SENT`, and `UNKNOWN_CHARGE_STATE` prohibit automatic retry. Worker restart, queue redelivery, frontend clicks, audit retry and presentation refresh encounter the existing logical request or reservation and cannot create a second paid attempt. `UNKNOWN_CHARGE_STATE` requires human review.

## Retry policy

The maximum remains three attempts, but this is not permission for three paid sends. Automatic retry is allowed only when transport evidence proves the body was not sent, or when evidence proves the Provider did not accept it. Timeout/reset/parse failure after send, 429, and 5xx default to `UNKNOWN_CHARGE_STATE` and no retry. 401/403 do not retry. There is no `except: retry` path.

DeepSeek's audited official pages do not establish a server-side idempotency key or whether 429/5xx guarantees non-execution. Both are `UNKNOWN`, so application-level durable reservation is mandatory.

## Smoke 1 → Smoke 5

### Smoke 1 — ETH / QUICK / NONE

Success requires the complete acceptance matrix below, immutable report/audit persistence and a PASSED-only presentation. Failure stops the sequence, displays no report body, preserves evidence and blocks later smokes.

### Smoke 2 — ETH / FULL / NONE

May start only after Smoke 1 passes. Success additionally requires all FULL sections, levels, scenarios and warnings. Failure handling is identical to Smoke 1.

### Smoke 3 — BTC / QUICK / NONE

May start only after both Smoke 1 and Smoke 2 pass. It proves symbol isolation. Any ETH/SOL reference or registry mismatch is a hard stop.

### Smoke 4 — SOL / QUICK / NONE

May start only after both gate smokes pass and Smoke 3 does not activate a stop condition. It proves a second cross-symbol boundary. Any ETH/BTC reference is a hard stop.

### Smoke 5 — ETH / FULL / PAPER

May start only after Smokes 1–4 pass. The frozen PAPER position must match the expected instrument and position fingerprint. Unexpected position data, NONE/PAPER mode crossing, position leakage or any order side effect is a hard stop. `USER_DECLARED` is never accepted.

## Per-smoke acceptance matrix

All rows must be `PASSED`: context freeze; Context ID; Registry Snapshot identity/hash; prompt identity/hash; durable Provider reservation; Provider response; strict schema parse; numeric grounding; reference support; level coverage; scenario coverage; warning coverage; deterministic audit; immutable persistence; PASSED-only presentation.

Every factual numeric claim—price, percentage, RSI, MA, ATR, CVD, OI, funding, basis, liquidation, level, target, stop and any schema-approved probability—must map to a frozen Numeric Registry entry with value and unit agreement, or be explicitly non-factual. Every other factual claim must map to Fact Registry, Numeric Registry or Macro Evidence. Plausibility is not support.

If any row fails, audit fails and presentation returns status only with no body. A presentation body is legal only after the stored report, context, registry hashes and PASSED audit all match.

## Fake/stub negative matrix

The 19 fixtures cover invalid JSON, markdown wrappers, missing schema fields, unsupported numbers, wrong symbol, wrong timeframe, stale context, hallucinated levels, hallucinated macro facts, unsupported probabilities, truncation, timeout, 401, 403, 429, 500, connection reset, duplicate response and delayed response. All require failed audit, no body and no automatic retry. These fixtures never instantiate the live adapter.

## Secret isolation

The B3 key is a mounted file supplied only to report-worker. It is forbidden in frontend, audit-worker, paper-api, collector, Router, static files, artifacts, logs, process arguments, Git and plaintext environment values. The scanner reports file/line locations only and never matched text. Runtime checks must not print a prefix, suffix, hash or value.

## Kill switch and stop conditions

Any budget breach, duplicate-charge risk, wrong symbol/mode, context or registry mismatch, unsupported numeric claim, reference support failure, audit mismatch, secret exposure, unexpected position data, runaway retry or schema corruption stops new reservations immediately without deleting evidence.

Formal B3 also stops on one occurrence of: unsupported numeric/reference failure displayed as body; unaudited body; position leak; duplicate paid generation; automatic retry from unknown charge; budget exceed; order side effect; Router side effect; Collector side effect; or Aggregation side effect.

## Observability

The control ledger exposes: Provider requests, successes/failures, reservations, prevented duplicates, unknown charge states, input/output tokens, predicted/reconciled cost, 401/403/429/5xx, timeout, retry, queue depth, audit pass/fail, presentation pass/fail, budget blocks and kill-switch events. Predicted usage is immutable and is never overwritten by Provider-reported usage. Missing Provider fields remain `UNKNOWN`.

## Formal execution command contract

`scripts/run_ai6b_b3_smoke.py` defaults to dry-run. Without `--approval-id`, it returns `LIVE_PROVIDER_APPROVAL_REQUIRED`. Live execution additionally needs `--execute-live`, runtime `AI_REPORT_LIVE_PROVIDER_ENABLED=true`, `--allow-live-provider`, a preconditions file, smoke number, isolated report database, request ID, control ledger, kill-switch path and mounted secret file. The preparation phase does not provide or invent an approval ID.

Before the command, re-fetch the production-canary remote. If it differs from the preparation base, record drift and re-integrate/retest; do not auto-rebase. Do not execute formal B3 until B1 and B2 evidence is PASSED.

## Deferred validation

Full pytest, large frontend build and Router benchmarks are `DEFERRED_UNTIL_B1_AGENT_IDLE`. The preparation phase runs only provider adapter, budget, cost, usage reconciliation, duplicate reservation, retry state machine, secret isolation, schema/grounding/reference, fixture, kill-switch and dry-run script tests.
