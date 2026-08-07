# AI-6B Production Canary Runbook (Plan Only)

Status: **NOT_EXECUTED / NOT_PRODUCTION_READY**. Commands require separate AI-6B authorization and named owners.

Execute strictly in this order:

1. Back up the report database and prove restore/checksum.
2. Explicitly apply the reviewed migration; capture operator, time and hash.
3. Deploy report/audit workers disabled.
4. Deploy Paper API with every new flag off.
5. Verify sanitized health, schema, storage and rollback controls.
6. Enable audit read-only interfaces for the internal audience.
7. Enable Shadow Presentation API.
8. Enable the internal Shadow frontend.
9. Exercise only Fake Provider or designated immutable test reports.
10. Run one separately approved, budget-capped real-provider smoke test.
11. Allow a small bounded set of real Shadow generations.
12. Observe latency, payload, mismatch, privacy, retries, token/cost and legacy behavior.
13. Consider a later step only after every threshold passes and owners sign.

Never open the frontend before its backend, label `PASSED_SHADOW_ONLY` production-ready, change order/Router/collector paths, or run unbounded workers. Any stop condition invokes the rollback runbook.
