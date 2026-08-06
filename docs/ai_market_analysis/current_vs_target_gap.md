# Current vs target gap

Baseline statuses describe repository capability, not live data coverage. Actual row ranges remain `REQUIRES_RUNTIME_AUDIT`.

| Target capability | Current | Current implementation | Limitation / missing fields | Recommended location | Dependency / risk | Phase |
|---|---|---|---|---|---|---|
| five-timeframe indicators | `PARTIAL` | `market_context_v2.py:266-369,614-701` | no registered MA20/MA30; formal output differs from target; AI does not consume it | extend a new `ai_market_facts.py` adapter over V2, preserving V2 | confirmed OHLCV; duplicate-algorithm risk | AI-2 |
| strict 1W | `EXISTS` | `aggregate_confirmed_daily_to_weekly`, `170-193` | availability depends on contiguous 1D; AI omits it | reuse unchanged through facts adapter | 1D gaps/warm-up | AI-2 |
| Stoch RSI | `EXISTS` | `stoch_rsi_series`, `196-227` | AI omits it | reuse registry output | dependent warm-up | AI-2 |
| swing structure | `PARTIAL` | latest confirmed 2x2 high/low, `236-247`; state engine | no HH/HL sequence model or full bar evidence list | `dashboard/ai_market_structure.py` | pivot repaint prevention | AI-2 |
| compression timeline | `PARTIAL` | BB width causal percentiles; state overlay | no compression start/end/range lifecycle | `dashboard/ai_market_timeline.py` | stable thresholds/versioning | AI-2 |
| breakout/retest timeline | `PARTIAL` | `MarketStateEngineV2` level interactions/compare | no complete range→breakout→impulse→pullback reconstruction | `dashboard/ai_market_timeline.py` | multiple timeframe event identity | AI-2 |
| candle quality/watermarks | `PARTIAL` | Context V2 per-value source timestamp/status/gaps | target needs ISO per-field source/unit/derivation and all watermarks | `dashboard/ai_market_contracts.py`, facts adapter | timestamp-unit mistakes | AI-2 |
| staged OI/CVD attribution | `NOT_IMPLEMENTED` | current four-bar sign combinations only (`market_context_v2.py:529-543`) | phases, funding/basis/liquidation, counterevidence and alternative absent | `dashboard/ai_order_flow_attribution.py` | gap causality, venue ambiguity | AI-3 |
| key-level engine | `PARTIAL` | V2 candidates/confluence, `546-612`; State interactions | MA20/30, range platform, role/state/first-last test/invalidation incomplete | `dashboard/ai_key_levels.py` | level proliferation | AI-3 |
| scenario tree | `NOT_IMPLEMENTED` | none | all branch fields absent | `dashboard/ai_scenarios.py` | deterministic triggers vs prose | AI-3 |
| liquidation phase facts | `PARTIAL` store only | `microstructure.py` observation/health tables | Context V2 and AI omit it; feed incomplete | bounded reader + attribution module | forward-only incomplete ledger | AI-3 |
| user position context | `NOT_IMPLEMENTED` | Paper positions/rationale only | no user-declared store, notes, partial exits, completion | `dashboard/position_context.py` plus new DB migration later | privacy/source confusion | AI-4 |
| Paper position adapter | `PARTIAL` | `paper_trades`, rationale/accounting | no multi-fill average/partials; AI omits open trade/rationale | `dashboard/position_context.py` | legacy nullable rows | AI-4 |
| macro evidence | `NOT_IMPLEMENTED` | none | source/publish/retrieve timestamps absent | `dashboard/macro_evidence.py` and bounded connector | stale/news licensing/injection | AI-4 |
| structured request/output | `NOT_IMPLEMENTED` | plain prompt/string in `paper_api.py:933-952,1074-1087` | no request/context persistence, sections, citations, IDs | `dashboard/ai_report_service.py`; API routes after review | migration/backward compatibility | AI-4 |
| QUICK/FULL/POSITION modes | `NOT_IMPLEMENTED` | one ~120-char brief + free chat | all mode contracts absent | report service/prompt templates | token/cost control | AI-4 |
| exact context/report persistence | `NOT_IMPLEMENTED` | `ai_briefs` stores text only; analysis snapshot is not exact AI input | no reproducibility | `dashboard/ai_report_repository.py`, future migration | storage growth/PII | AI-4 |
| fact audit | `NOT_IMPLEMENTED` | none | numeric grounding, pointer resolution, contradictions absent | `dashboard/ai_report_audit.py` | Chinese numeric/token parsing | AI-5 |
| golden/history replay evaluation | `PARTIAL` decision replay only | `PaperService.replay`, snapshots | no frozen AI context/model/prompt/audit | `dashboard/ai_report_evaluation.py`, `scripts/evaluate_ai_reports.py` | model nondeterminism/lookahead | AI-5 |
| AI health/retry | `PARTIAL` | brief queue/backoff/health | Copilot lacks retry; no idempotency/context key; stale brief still displayed | report service/job repository | duplicate cost and thundering herd | AI-4/6 |
| frontend structured report | `NOT_IMPLEMENTED` | plain brief and plain Copilot answer | no fact classes, quality, levels, scenarios, audit status | `frontend/src/aiMarketAnalysis/*` | information overload | AI-6 |
| frontend selection correctness | `PARTIAL` | request guards/cache keys for chart/status | selected timeframe does not affect AI; SOL normalizes to ETH backend | typed report request hook | wrong-symbol analysis | AI-6 |

## Core gap summary

The system already has much of the deterministic indicator substrate. The decisive gap is orchestration and evidence preservation: the production LLM bypasses V2 facts/state, no engine reconstructs the full timeline or phase attribution, and no immutable report contract/auditor exists. Increasing prompt size or output tokens would amplify ambiguity rather than close this gap.

Highest-risk gaps are (1) different source watermarks hidden from AI, (2) no CVD/OI gap state in AI context, (3) SOL UI/backend universe mismatch, (4) no exact AI request persistence/replay, (5) no user-vs-Paper position boundary, and (6) no fact audit before display.
