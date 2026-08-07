# AI-6A Shadow UI

Status: **SHADOW CANDIDATE ONLY — NOT_PRODUCTION_READY**. This phase does not deploy, enable a live provider, or complete AI-6.

## Architecture and boundaries

The admin-only route `/shadow/ai-market-analysis` is absent from normal navigation and requires `VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED=true`. Its only primary data source is the atomic `GET /api/ai-market-analysis/v1/presentations/latest`; the backend requires `AI_MARKET_ANALYSIS_PRESENTATION_ENABLED=true` and the existing bearer `ADMIN_TOKEN`. Both flags default off. Health may be projected in the same sanitized payload. Debug report/audit APIs are not composed by the browser.

The projection reads report, request, current-policy latest audit, frozen context and registry snapshot in one SQLite read transaction. Only `AUDIT_PASSED_SHADOW_ONLY` includes `report`. Other eligibility states return identity, queue/audit status and a null body. The latest generated report and latest displayable passed report are separate fields.

Initial position projection omits quantity, average cost, stop and targets. The explicit “show details” action makes a separate authenticated, report/instrument/mode-bound request. Tokens remain in component memory and are never placed in URLs or web storage.

Freshness policy `ai-market-freshness-policy-v1` compares the frozen report watermark to the newest frozen context watermark. Zero confirmed 15m bars is CURRENT, one or two is AGING, more than two is STALE, a newer passed report is SUPERSEDED, and unavailable watermarks are UNKNOWN. Freshness never changes report or audit identity.

## Local Shadow runbook

Use an isolated temporary report SQLite. Explicitly apply migrations 001/002/003/004 to that temporary file only, seed synthetic fixtures with `FakeAIReportProvider`, and set all report/audit/live-provider workers disabled. Set a synthetic `ADMIN_TOKEN`, then explicitly set both Shadow flags. Run the backend on loopback only and the Vite preview on loopback. Never point `AI_MARKET_REPORT_DB_PATH`, Paper DB or microstructure DB at production or user data.

Verify disabled flag, 401, empty state, all eligibility states, passed/current/stale/superseded, old-passed plus new-pending, instrument/mode races, position sources, macro/no-macro, warnings, evidence, scorecard, provenance, responsive layouts, keyboard use and sanitized failures. Stop only PIDs started by this run whose cwd is this worktree.

## Audit conclusions

- Pre-AI-6A report and latest-audit reads were separate connections and could form a mixed snapshot: **yes**.
- Pre-AI-6A latest report was instrument/language-bound but mode was optional: **unsafe for the Shadow primary path**.
- A report could be paired with a different context when composed client-side: **possible; now rejected**.
- Full Registry was not returned by report API; frozen audit input contained it: **Presentation returns only referenced compact evidence**.
- USER_DECLARED data through general debug reads lacked a uniform admin read gate: **Presentation and position detail are admin-only**.
- No prior Shadow cache/race controller existed: **new key includes schema/instrument/mode/language/latest-or-ID/auth scope and sequence rejection**.
- General exception strings are not exposed by Presentation: **stable code plus generic message only**.

## Performance reproduction

Run `python scripts/benchmark_ai6a_presentation.py --iterations 200` against its temporary synthetic SQLite; it prints p50/p95/max and payload bytes. Run `npm test` for parse/cache/sequence micro-bench assertions and browser Performance tooling for cached render, first render, section focus, evidence and scenario expand at 1440/1024/768/390 widths. Record results in the AI-6B evidence manifest; do not invent missing runtime measurements.
