# AI-6B Production Canary Runbook (B0 Candidate Only)

Status: **B0 REMEDIATION/REVALIDATION ONLY; B1 NOT ENTERED**. No command in this runbook is authorized for production until the owner separately starts B1. The sole audience/on-call/stop authority is the project owner. The candidate frontend binds only `127.0.0.1:8443` and is reached through the owner's SSH tunnel with TLS; no B0 production network change is allowed.

Execute strictly in this order:

1. Back up the report database and prove restore/checksum.
2. Explicitly apply the reviewed migration; capture operator, time and hash.
3. Deploy report/audit workers disabled.
4. Deploy Paper API with every new flag off.
5. Verify sanitized health, schema, storage and rollback controls.
   Before any live call, verify `python scripts/ai6b_kill_switch.py status` reports enabled=false. Any hard stop is a single command: `python scripts/ai6b_kill_switch.py trip --event <APPROVED_EVENT> --evidence-id <ID>`. There is intentionally no reset command.
6. Enable audit read-only interfaces for the internal audience.
7. Enable Shadow Presentation API.
8. Enable the internal Shadow frontend.
9. Exercise only Fake Provider or designated immutable test reports.
10. Run one separately approved, budget-capped real-provider smoke test.
11. Allow a small bounded set of real Shadow generations.
12. Observe latency, payload, mismatch, privacy, retries, token/cost and legacy behavior.
13. Consider a later step only after every threshold passes and owners sign.

Never open the frontend before its backend, label `PASSED_SHADOW_ONLY` production-ready, change order/Router/collector paths, run unbounded workers, put an admin token in a URL, or retry persistent 401/429 responses. Any stop condition trips the durable live-provider kill switch before rollback.
