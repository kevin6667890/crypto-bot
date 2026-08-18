# AI-6B Production Readiness Checklist

Overall B0 result: **AI6B_B0_READY_FOR_B1** on 2026-08-07. This is a B0 candidate/readiness result only: B1 was not entered, production migration was not run, production AI remained disabled, and no 24-hour Canary acceptance window has started.

| B0 gate | Status | Evidence |
|---|---|---|
| 30-day hot retention and 31–365 archive | PASS | `capacity-revalidation.json`; retention/archive tests |
| Database backup and isolated restore | PASS | `backup-restore-revalidation.json`; production-state backup manifest |
| Migration governance and exact hashes | PASS | `migration-revalidation.json`; production apply remains prohibited |
| Permissions, resource limits, secret isolation | PASS_CANDIDATE | Candidate Compose static and isolation tests; not deployed |
| HTTP headers, query/log privacy, access topology | PASS_CANDIDATE | TLS loopback/owner SSH tunnel candidate; no production network change |
| Approved budgets, concurrency, retry and rates | PASS | `config/ai6b_canary_policy.json`; B0 tests |
| Frontend error budget | PASS_CANDIDATE | Frozen policy plus frontend contract/error handling tests |
| Alerts and durable kill switch | PASS | Local/isolated trip and fail-closed tests; 60-second policy SLA |
| NONE/PAPER privacy | APPROVED | Internal Shadow owner-only; USER_DECLARED remains not approved and disabled |
| Runtime image drift | EXPLAINED | `runtime-image-drift.json`; immutable staging source matches running commit |
| SOL old AI Brief | EXPECTED_INACTIVITY | `sol-legacy-classification.json`; running scheduler only selects BTC/ETH |
| 30-minute production baseline | PASS | 1803.386 seconds, 30 samples, no failed probes or production mutations |
| Backend/frontend regression | PASS | 1538 backend passed, 1 skipped; 147 frontend passed; build/bundle PASS |
| Production AI writes / migrations / live calls | PASS_ZERO | DB absent, flags off, migrations 0, real DeepSeek calls 0 |

AI-6B acceptance artifacts and golden/evaluation fixtures are retained indefinitely. The 24-hour window thresholds are frozen but have not yet been exercised. Provider official pricing must be revalidated before B3.

Stop immediately for wrong symbol/mode/context/audit/registry, unaudited body display, position leak, secret exposure, duplicate charge, budget or retry/queue runaway, hidden critical warning, order/Router/Collector/Aggregation change, database corruption, or critical disk pressure.
