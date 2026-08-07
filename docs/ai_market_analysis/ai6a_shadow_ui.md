# AI-6A Shadow UI

Status: **AI-6A SHADOW CANDIDATE — NOT_PRODUCTION_READY**. This phase does not deploy, enable a live provider, apply a production migration, or complete AI-6B.

## Architecture and safety boundary

The admin-only route `/shadow/ai-market-analysis` is absent from normal navigation and requires `VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED=true`. Its primary source is the atomic `GET /api/ai-market-analysis/v1/presentations/latest`; the backend separately requires `AI_MARKET_ANALYSIS_PRESENTATION_ENABLED=true` and the existing bearer `ADMIN_TOKEN`. Both flags default to false.

The projection reads report, request, current-policy latest audit, frozen context and registry snapshot in one SQLite read transaction. Only `AUDIT_PASSED_SHADOW_ONLY` includes a report body. The initial position projection omits quantity, average cost, stop and targets. Position details use a separate authenticated request bound to report, instrument and mode. Tokens stay in component memory.

Freshness policy `ai-market-freshness-policy-v1` uses confirmed context watermarks rather than browser wall-clock age. Audit status and freshness remain independent.

## AI-6AC enum and semantic closure

The UI enum source is `ai-market-ui-enum-manifest-v1`, generated deterministically by `scripts/generate_ai_market_ui_enum_manifest.py` from backend constants and JSON Schema contracts. The generated JSON and TypeScript mirror contain every supported value; tests require non-empty zh/en entries and fail if a known value reaches the explicit unknown fallback. `safeEnum` remains only as a developer diagnostic and is not imported by a business component.

Every evidence-bearing panel uses the same six-value semantic model:

- `FACT`: frozen facts, macro factual summaries and original position facts.
- `DETERMINISTIC_DERIVATION`: timeline, timeframe structure, flow attribution, levels, scenarios and audit calculations.
- `AI_SYNTHESIS`: audited report headline and structured section bodies.
- `UNCERTAINTY`: uncertainty lists, likelihood and unconfirmed states.
- `COUNTEREVIDENCE`: scenario/flow counterevidence, opposing conditions and hard failures.
- `MISSING_DATA`: gaps, partial/stale/unavailable quality, missing macro/position and unknown freshness.

Each semantic badge has visible text, a non-color glyph/shape, a stable class, an ARIA label and a screen-reader explanation in both languages. Component tests exercise the semantics in real report panels, not only the badge primitive.

Health is rendered as localized Feature, Queue, Outcomes, Resources and Versions groups. Raw health JSON is not rendered. Key Level and Scenario panels project every contracted field with localized labels and explicit missing-data handling.

## Reproducible performance evidence

Run from `frontend`:

```text
npm run benchmark:ai-presentation-parse
npx playwright test e2e/ai6ac-performance.spec.ts
```

The parse benchmark uses `fixtures/aiPresentationFullGolden.json`, warms up 50 times and measures 500 iterations for both the current approximately 52KB FULL payload and an expanded approximately 245KB payload. Each iteration includes `JSON.parse`, the runtime contract guard and presentation normalization.

The cached benchmark warms both FULL and POSITION_AWARE keys, performs five warmups and 30 measured real React structured renders. It records request count after cache warmup and requires zero network requests plus p95 at most 100ms. First-render evidence starts after parse/normalization, covers desktop FULL, desktop POSITION_AWARE, a critical-warning report and 390px mobile FULL, and requires each maximum at most 300ms.

Artifacts are written to:

- `artifacts/ai6a/presentation_parse_benchmark.json`
- `artifacts/ai6a/cached_render_benchmark.json`
- `artifacts/ai6a/first_render_benchmark.json`

Final AI-6AC integration evidence on tested commit `943eb77d77bf6c6a84d13abda5179ad9624830af`:

- 52,507-byte parse: p50 0.0406ms, p95 0.0515ms, max 0.3836ms (50 warmups, 500 measurements).
- 245,376-byte parse: p50 0.1795ms, p95 0.2166ms, max 0.3412ms (50 warmups, 500 measurements).
- cached structured render: p50 7.6ms, p95 11.9ms, max 12.0ms (5 warmups, 30 measurements, zero network requests).
- first structured render p95: desktop FULL 15.7ms, POSITION_AWARE 7.5ms, critical warning 8.7ms, 390px mobile FULL 7.4ms.
- backend Presentation matrix: 30/30 required nodes passed; 16/16 additional security nodes passed.
- full backend: 1507 passed and 1 skipped in each of two serial runs.
- frontend unit/component: 147 passed; Playwright: 48 passed; Axe critical/serious findings: zero.

Performance measurements are evidence only and never enter report, audit or presentation identity hashes.

## Presentation backend matrix

`tests/ai_market_analysis/presentation_test_matrix_v1.json` maps each of the original 30 Presentation requirements to one unique pytest node ID, a named input fixture, its core assertion and recorded result. The matrix covers fail-closed eligibility, strict selection, all identity mismatches, latest audit, old-passed/new-pending, freshness, redaction, bounded evidence/payload, authorization/rate limiting/flag closure, query plan, external storage and network isolation, sanitization, identity stability and immutable historical content. Sixteen additional security nodes cover wrong language, forged identities, payload hashes, position selection, query tokens, deep/long payloads, unsupported enums, bounded failure summaries, sanitized health and explicit transactions.

## Local Shadow runbook

Use an isolated temporary report SQLite and the reviewed 001/002/003/004 migrations only. Seed synthetic fixtures with `FakeAIReportProvider`; keep report/audit/live-provider workers disabled. Use a synthetic admin token, loopback backend and loopback Vite server. Never point report, Paper or microstructure paths at production or user data. Stop only processes created by the current shell whose PID and command path prove ownership.

## Production readiness remains open

Backup/restore, migration approval, capacity, permissions, provider secrets, cost budgets, alerting, retention, privacy sign-off, production audience, rollback rehearsal and 24-hour evidence all remain `NOT_READY` or `REQUIRES_PRIVACY_REVIEW` in the production readiness checklist. AI-6AC closing code on main does not authorize production use.
