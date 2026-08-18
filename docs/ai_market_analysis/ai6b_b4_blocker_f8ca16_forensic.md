# AI6B B4 Blocker Forensic — request_f8ca16

Sanitized record. No provider secrets or raw provider payloads are included.

## Request identity

- `request_id`: `request_f8ca16d56ff327769640159a11f1d8d5247e66ec59ec9d829e0d148679d2a6cb`
- instrument `BTC-USDT-SWAP`, mode `FULL`, provider `deepseek`, model `deepseek-v4-flash`
- created `2026-08-16T06:22:38Z`, queued via the B4 qualifier stack for candidate `1994627`

## Reconstructed lifecycle (all local evidence is read-only)

| UTC | event | source |
|---|---|---|
| 06:22:38 | QUEUED | `ai_report_request_events` |
| 06:22:39 | RUNNING (attempt 1) | `ai_report_request_events` |
| ~06:23:0x | HTTP response headers received; chunked body interrupted at 12267 bytes (`http.client.IncompleteRead`), usage never received | report-worker container log (image `sha256:3b42ba…` = candidate 1994627) |
| 06:23:10 | worker container restarted (restart policy on-failure, RestartCount=1) | `docker inspect` |
| 06:23:11 | `recover()` → INTERRUPTED (worker_restart); guard tripped kill switch `DUPLICATE_PROVIDER_CHARGE`; FAILED_FINAL `INTERRUPTED_LIVE_CALL_CHARGE_UNCERTAIN` | request events + kill-switch state file |
| 06:23:57 | report/audit workers externally SIGKILLed (exit 137) — fail-closed rollback | `docker inspect` |

## Charge-state determination

- Sent: **YES** (provider accepted; response streaming began).
- Response headers: **YES** received. Body: truncated mid-stream. Usage: **NOT** received.
- Provider-side per-request reconciliation: **NOT OFFICIALLY AVAILABLE** — the B3 official-docs
  audit (`artifacts/ai6b/b3-prep/provider-official-audit.json`) documents no usage/billing/
  request-history lookup, no x-request-id header, no idempotency key. Official pricing pages
  document only per-token deduction, not interrupted-stream billing.
- Local balance snapshots around the call: none exist, so balance-delta reconciliation cannot
  attribute this single call among the seven live calls of 2026-08-16.

Conclusion: **UNKNOWN_CHARGE_STATE_UNRESOLVABLE** — neither CONFIRMED_CHARGED nor
CONFIRMED_NOT_CHARGED can be proven. Automatic retry: 0. Duplicate charge confirmed: NO
(the kill-switch trip was preventive duplicate-charge risk, not evidence of a duplicate).

Canonical mechanisms in force (tested): unknown-charge quarantine (kill switch, no reset),
worst-case charge reservation (control ledger predicted-cost ceilings), and the manual
reconciliation policy from `ai6b_b3_real_provider_canary_plan.md`:
`UNKNOWN_CHARGE_STATE requires human review`.

## Fixes landed (candidate lineage, after this record)

1. `PROVIDER_RESPONSE_INTERRUPTED` — independent transport-failure classification
   (`IncompleteRead` / `ConnectionResetError` during body read), charge_state
   `UNKNOWN_CHARGE_STATE`, never auto-retried. Transport failure and billing
   uncertainty are now two separate dimensions.
2. Paid-attempt identity persisted at the send boundary (`lifecycle_state SUBMITTED`)
   and progressively updated: `RESPONSE_HEADERS_RECEIVED` → `BODY_STREAMING` →
   `USAGE_RECONCILED` → `SUCCEEDED` / `FAILED` / `UNKNOWN`; `charge_state` tracked
   separately. Interrupted live requests always leave an attempt record with
   `UNKNOWN_CHARGE_STATE`.
3. Migration `006_ai_report_attempt_lifecycle.sql` (non-destructive, isolated AI
   report DB only).

Regression: 1734 passed, 2 skipped (full pytest, single run).

## Required human governance decision

The kill switch intentionally has no reset and `UNKNOWN_CHARGE_STATE requires human
review`. The owner must decide whether to accept the unresolvable charge with its
worst-case reservation and authorize B4 to restart from zero. Until then the blocker
stands: `AI6B_B4_BLOCKED — UNKNOWN_CHARGE_STATE_UNRESOLVABLE`.
