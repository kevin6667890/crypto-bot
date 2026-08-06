# Phase AI-4 current path audit

Audit baseline: `origin/main` at `b99e4dccc5ec8b782501d09ef18b022a838426c7`.

Reused unchanged components:

- AI-3 canonical JSON/hash and deterministic `MarketAnalysisContext` builders.
- `BoundedMarketDataReaderV2` and the AI-3 read-only order-flow adapter for bounded, causal store reads.
- Existing SQLite connection conventions, `ADMIN_TOKEN`, HTTP body limit, and in-memory client rate limiter.
- Existing OpenAPI generation/check workflow.

Intentionally unchanged production paths:

- `PaperService._ai_context`, `_create_ai_brief`, `chat`, the `ai_briefs` and `ai_health` tables, and the AI Brief worker/retry/monitor.
- Market-page AI Brief and Copilot UI.
- Paper orders/accounting, collector/realtime aggregation, raw/aggregate semantics, strategy router, research queues, and operations decisions.

The legacy `_ai_context` is a small display/decision summary. It does not carry the AI-3 causal timeline, phased order-flow attribution, stable key-level/scenario identities, frozen position/macro evidence, numeric registry, or citation pointers. Reusing it would discard the grounding required by AI-4.

The legacy brief worker is scheduled from the Paper 60-second cycle and writes `ai_briefs`. Long report generation has independent latency, retries, concurrency and token budgets; coupling it to that cycle would expand the Paper fault domain. AI-4 therefore uses an explicit, isolated SQLite database and standalone worker.

`ai_briefs` remains the existing short operational brief contract and is consumed by the current market UI. AI-4 reports are immutable, evidence-linked, shadow-only and always `audit_status=PENDING`; overwriting `ai_briefs` would erase lineage and expose an unaudited artifact through a trusted existing surface.

Automatic macro HTTP retrieval is `NOT_IMPLEMENTED`: the repository has no maintained, version-pinned source integration meeting the required allowlist, redirect, copyright-versioning and runtime audit constraints. Structured supplied evidence, fixture evidence, causal cutoff, deduplication, freezing and no-macro generation are implemented.

Provider documentation audit used the official DeepSeek Chat Completion, JSON Output, model/pricing and rate-limit documentation current on 2026-08-06. The provider uses configured model names only, JSON Output, a 45-second cap and usage metadata. Live validation and provider pricing remain `REQUIRES_RUNTIME_AUDIT`; no production key is read by tests.
