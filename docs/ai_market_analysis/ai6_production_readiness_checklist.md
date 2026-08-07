# AI-6 Production Readiness Checklist

Overall: **NOT_READY**. AI-6A evidence cannot satisfy AI-6B deployment or 24-hour acceptance.

| Gate | Status | Required evidence / owner |
|---|---|---|
| Database backup and restore verification | NOT_READY | DBA owner and checksum |
| Migration 004 review and explicit approval | NOT_READY | DBA + application owner |
| Disk capacity, pagefile and DB permissions | NOT_READY | Operations snapshot |
| Backend/frontend flags default off | READY_FOR_CODE_REVIEW | Config diff; runtime still NOT_READY |
| Provider secret handling | NOT_READY | Security review; never bundle |
| Token and cost budget | NOT_READY | Approved limits and alert owner |
| Read/write rate limits | NOT_READY | Canary load evidence |
| Worker concurrency/retry bounds | NOT_READY | Runtime evidence |
| Report/audit retention | NOT_READY | Approved immutable retention policy |
| Position privacy | REQUIRES_PRIVACY_REVIEW | Privacy owner sign-off |
| Health alerts and stop conditions | NOT_READY | On-call owner |
| Independent rollback rehearsal | NOT_READY | Timestamped rehearsal artifact |
| Frontend internal canary audience | NOT_READY | Access list |
| Old AI Brief fallback | NOT_READY | AI-6B regression evidence |
| No order path impact | NOT_READY | Diff plus runtime counts |
| No Router/collector/aggregation impact | NOT_READY | Diff plus runtime health |
| 24h evidence directory and manifest | NOT_READY | AI-6B acceptance owner |

Stop immediately for wrong-symbol/mode/context/registry, unaudited body display, position leak, order/Router/collector change, duplicate charge, runaway retries, budget breach, critical warning invisibility, or rollback failure.
