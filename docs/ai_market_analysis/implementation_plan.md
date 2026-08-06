# File-level implementation plan (AI-2 through AI-6)

All phases preserve the current Paper order, strategy router, collector and aggregation behavior until AI-6 explicitly authorizes deployment. Filenames are proposed and may be adjusted only through an architecture review that preserves module boundaries.

## Phase AI-2 — multi-timeframe facts and structure engine

- Add: `dashboard/ai_market_contracts.py`, `dashboard/ai_market_facts.py`, `dashboard/ai_market_structure.py`, `dashboard/ai_market_timeline.py`, `tests/test_ai_market_facts.py`, `tests/test_ai_market_structure.py`, `tests/fixtures/ai_structure/*`.
- Modify: `dashboard/market_context_v2.py` only to expose MA20/MA30/raw rolling levels or reusable source-bar evidence without changing existing fields; documentation/OpenAPI development routes if needed.
- Must not modify: collector, `realtime_aggregation.py`, Paper orders, `strategy_router_v2.py`, existing AI prompt/cadence.
- Input: bounded confirmed OHLCV and existing Context/State V2 snapshots at one `decision_time`.
- Output: traced timeframe structures, structure events and market timeline; no trade action.
- Tests: all timeframes, strict 1W constituents, warm-up, causal cutoff, gaps, incomplete candle, swing confirmation, compression/breakout/retest state sequences, property tests for future timestamps.
- Performance: facts/context p95 ≤350 ms warm and ≤800 ms cold for one instrument; pure structure ≤75 ms; maximum 512 bars each 15m/1H/4H and 1,500 1D; no table scan.
- DB/API: no DB change; development-only versioned read endpoint behind feature flag, response ≤350 KB.
- Deploy/rollback: Paper API development component only, flag off by default; rollback disables route/removes module import.
- Acceptance: fixture identities deterministic; zero future timestamps; all required timeframe fields present or explicitly unavailable; existing tests unchanged.

## Phase AI-3 — staged flow attribution, levels and scenarios

- Add: `dashboard/ai_order_flow_attribution.py`, `dashboard/ai_key_levels.py`, `dashboard/ai_scenarios.py`, `dashboard/ai_market_context_builder.py`, corresponding unit/contract fixtures and tests.
- Modify: bounded read adapter in `market_context_v2.py` or a new read-only `ai_microstructure_reader.py`; do not alter aggregation writes.
- Must not modify: `microstructure_collector.py`, `realtime_aggregation.py`, Paper order/Router code, prompts.
- Input: AI-2 events/windows plus bounded CVD/OI/funding/basis/liquidation/VPVR observations and gap metadata.
- Output: complete `MarketAnalysisContext v1` excluding user/macro values, with `position.source=NONE`, macro `NOT_REQUESTED`.
- Tests: all eight attribution classes, mandatory alternative/counterevidence, no cross-gap changes, post-gap OI absolute recovery, same-day CVD partial state, level evidence/touches/roles, exact three scenarios/invalidation.
- Performance: microstructure windows maximum 30 days or 50,000 one-minute rows per lane, whichever is lower; phase query p95 ≤300 ms; full context p95 ≤1.2 s cold, ≤500 ms cached.
- DB/API: no schema change; bounded indexes must be verified with `EXPLAIN QUERY PLAN`; dev API response ≤500 KB.
- Deploy/rollback: read-only Paper API component behind separate flag; cache purge/flag disable is rollback.
- Acceptance: no zero/interpolation/forward fill; all context numbers traced; BTC/ETH/SOL missing coverage returns explicit quality, not fallback to another symbol.

## Phase AI-4 — positions, macro and report generator

- Add: `dashboard/position_context.py`, `dashboard/macro_evidence.py`, `dashboard/ai_report_service.py`, `dashboard/ai_report_repository.py`, versioned prompt templates, migrations for immutable `ai_market_contexts`, `ai_report_requests`, `ai_market_reports` and optional `user_position_plans`; API/type tests.
- Modify: `dashboard/paper_api.py` only to add isolated versioned report endpoints/job scheduling; health service; OpenAPI generator. Existing brief remains unchanged during shadow validation.
- Must not modify: collector/aggregation, strategy decisions, Paper order creation/monitoring, Router behavior.
- Input: frozen context plus explicit Paper or user-declared plan and sourced macro items.
- Output: schema-valid QUICK/FULL/POSITION_AWARE response with citations, initially `audit_status=PENDING`.
- Tests: source boundary, legacy Paper rows, no position assumptions, macro timestamp/source, prompt serialization, idempotent `context_id`, timeouts/retries/rate limits, structured parse failure.
- Performance/cost: context input ≤12k tokens; QUICK output ≤900 tokens, FULL/POSITION ≤4k; generation timeout 45 s; at most one active LLM request per instrument and four globally; same context/mode/prompt/model is idempotent. Cost ceiling configured per day and per instrument; exact currency budget requires provider pricing review (`REQUIRES_RUNTIME_AUDIT`).
- DB/API: append-only tables with unique `(context_id,mode,prompt_version,model)`; no production migration until reviewed. POST report request and GET report/status endpoints.
- Deploy/rollback: separate report worker + Paper API feature flag, shadow-only; rollback stops worker and hides route, retaining immutable audit rows.
- Acceptance: all modes schema-valid; exact request context persisted; no duplicate call for same identity; no AI output enters order/strategy paths.

## Phase AI-5 — fact auditor and replay evaluation

- Add: `dashboard/ai_report_audit.py`, `dashboard/ai_report_evaluation.py`, `scripts/evaluate_ai_reports.py`, expanded golden/history fixtures, `tests/test_ai_report_audit.py`, `tests/test_ai_report_replay.py`.
- Modify: report repository/status transition only; no market/strategy mutation.
- Must not modify: collectors, aggregation, Paper orders, Router, context facts during replay.
- Input: frozen request/context/response plus golden semantic assertions and causally bounded historical snapshots.
- Output: `AIReportAudit v1`, scorecards, regression artifacts; only `PASSED` reports eligible for normal UI.
- Tests: numeric grounding, repetitions, unsupported facts, contradictions, invalidation/data-quality/scenario coverage, `UNKNOWN` preservation, pointer resolution, Chinese number/unit normalization, deterministic replay IDs.
- Performance: audit p95 ≤250 ms/report; offline replay bounded batches, no live provider call by default; model comparison is opt-in and budgeted.
- DB/API: append-only audit rows linked to report/context; evaluation-run metadata. GET audit/result endpoints.
- Deploy/rollback: auditor can fail closed while old brief remains; rollback disables new report promotion, never deletes evidence.
- Acceptance: golden passes thresholds; every injected anti-pattern fails the intended gate; zero uncited numeric claims; historical evaluation has no lookahead.

## Phase AI-6 — frontend, production deployment and 24-hour acceptance

- Add: `frontend/src/aiMarketAnalysis/types.ts`, `api.ts`, `hooks.ts`, `MarketReport.tsx`, `DataWarnings.tsx`, `ScenarioTree.tsx`, `PositionContext.tsx` and tests; deployment/acceptance runbooks.
- Modify: `frontend/src/App.tsx`, `data.ts`, `i18n.tsx`, generated API types, health/operations panels, production compose only after gates pass.
- Must not modify: collector calculations, realtime aggregation semantics, Paper order/strategy behavior.
- Input: audited report API and health/stale metadata.
- Output: fact/derivation/synthesis/uncertainty/counterevidence/missing-data presentation; correct instrument/mode/timeframe selection; old audited report visibly marked stale rather than silently current.
- Tests: request race/cache isolation, symbol/timeframe switch, stale/failed audit, accessibility/i18n, component/unit/e2e, production smoke and 24-hour evidence.
- Performance: report JSON compressed ≤250 KB target/500 KB hard cap; cached page render ≤100 ms main-thread work; API p95 ≤800 ms excluding generation; poll no faster than current minute cadence unless separately approved.
- DB/API: apply reviewed append-only migrations with backup; activate versioned endpoints. No destructive migration.
- Deploy/rollback: report worker → Paper API flag → frontend canary; rollback frontend/flag/worker independently, preserve tables and old brief.
- Acceptance: 24 hours with no wrong-symbol/timeframe report, no duplicate context charge, schema/audit success ≥99%, no order/strategy behavior change, resource/cost budgets met, all warnings visible.

## Cross-phase cache, storage and retention budget

- Cache key: `(schema_version,instrument,decision_time,source_versions,source_watermarks,analysis_mode)`; immutable `context_id` is the content identity. Invalidate on any source watermark/version/position/macro change, never only on wall-clock TTL.
- Compute cadence: facts may refresh once per confirmed 15m bar per instrument; live incomplete observation may refresh every 60 seconds but cannot change confirmed structure. 1H/4H/1D/1W recompute only on their close or dependency change.
- Storage: canonical context target ≤500 KB uncompressed; request metadata ≤32 KB excluding the referenced/deduplicated context; report ≤128 KB; audit ≤32 KB. Store context once and reference by ID.
- Retention proposal: contexts/reports/audits hot 90 days; compressed immutable archive 365 days; golden/evaluation manifests retained indefinitely. User-declared plans follow explicit privacy/deletion policy and are not embedded redundantly in every archive. Final retention requires storage/privacy review.
- No unbounded/full-table query. CVD/OI maximum online window is 30 days/50k minute rows per lane; downsampled summaries only enter the LLM.
- Cost controls: no repeat call for identical identity, per-client API rate limit, per-instrument single flight, global concurrency four, daily token/currency circuit breaker, and explicit usage persisted per report.
- Token budgets: deterministic context ≤12k input tokens; QUICK ≤900 output tokens; FULL/POSITION ≤4k output tokens. The LLM never receives multi-year/raw full series.

## Integration procedure

At the end of each phase: fetch/prune, record new `origin/main`, create a clean integration worktree from it, merge feature with a normal non-force merge, run targeted + full backend + compileall + frontend tests/build where applicable, and record the integration commit. Never reuse a dirty user worktree or force-push.
