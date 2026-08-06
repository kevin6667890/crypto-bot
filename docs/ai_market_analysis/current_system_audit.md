# Current system audit

Audit baseline: `ab698e5940158c01f7fc8c2cfaf32668c14eb0a7` (`origin/main` at audit start). Line numbers below refer to that commit. The audit read the required backend modules, their focused tests and design docs, plus the frontend market, chart, cache, instrument, i18n, replay and flow paths. No runtime database was modified or queried through a production service.

## 1. Current AI call chain (30-point audit)

### Hourly AI Brief

Call chain:

1. `dashboard/paper_api.py:1766-1772` starts the 60-second Paper scheduler and AI workers.
2. `PaperService.cycle()` at `1023-1032` loops configured instruments; `INSTRUMENTS` is only `BTC-USDT`, `ETH-USDT` at `102`.
3. `cycle_instrument()` at `1000-1011` obtains price and flow, runs the legacy decision analysis/order path, then calls `maybe_create_ai_brief(analysis)`.
4. `maybe_create_ai_brief()` at `887-895` enqueues when the instrument is not queued, retry time has passed, and its last success is at least 3,600 seconds old (`111`).
5. `_ai_worker()` at `897-903` is a single unbounded in-memory `queue.Queue` consumer (`300`).
6. `_create_ai_brief()` at `933-952` calls `https://api.deepseek.com/chat/completions`, model `deepseek-chat`, temperature `0.2`, `max_tokens=180`, timeout 25 seconds; it stores only `created_at,instrument,content,source` in `ai_briefs`.
7. `status()` at `1034-1059` selects the latest brief by instrument. `GET /api/status` exposes it; `frontend/src/App.tsx:1300-1322` renders plain text.

Frequency is “eligible once per hour per configured instrument”, evaluated by a scheduler that normally runs every 60 seconds. It is not guaranteed exactly hourly: failures back off and service downtime delays it.

### Copilot

Call chain:

1. `frontend/src/App.tsx:829-839` submits the selected instrument and question.
2. `frontend/src/data.ts:440-455` POSTs `/api/chat`.
3. `dashboard/paper_api.py:1664-1667` applies per-client limits of 3/minute and 20/hour (`_limited`, `1269-1272`).
4. `PaperService.chat()` at `1074-1087` truncates the question to 500 characters, normalizes unsupported instruments to ETH, builds `_ai_context`, then calls `deepseek-chat`, temperature `0.2`, `max_tokens=280`, timeout 30 seconds. There is no Copilot retry, queue, response persistence or structured response.
5. `App.tsx:1340` displays the answer in the current component state only.

The input control permits 1,200 characters (`App.tsx:1334`) but the server silently truncates to 500 (`paper_api.py:1075`).

### Exact current AI context

`PaperService._ai_context()` (`paper_api.py:905-931`) is the sole common context builder. Fields and sources:

| Context field | Source | Timestamp/window | Quality/gap |
|---|---|---|---|
| `instrument` | request / scheduled analysis | none attached | none |
| `as_of` | `analysis.updated_at` | ISO generation time, not a unified data cutoff | none |
| `decision.action,bias,score,entry_allowed,rejection_reason` | legacy `analyze()` / decision engine | analysis cycle | no per-field source time |
| `decision.price,ema20,rsi14,atr14,volume_ratio` | legacy 15m analysis | latest legacy analysis; values lack individual timestamps | no per-field quality |
| `timeframes.{15m,1H,4H,1D}.{trend,close,ma60,ma200,ema20_slope_pct}` | `analysis.timeframes` | each timeframe is fetched during the same cycle but no field timestamp is passed to AI | no `confirmed`, gap or stale status in AI context |
| `flow.cvd_delta` | legacy recent-trades / decision flow payload | current sample; AI receives no start/end | no gap status |
| `flow.oi_change_pct` | 15m decision window | current value only | no gap status |
| `flow.price_oi_state` | `professional.price_oi_state.label` | current professional display window | label only |
| `risk.*` | `risk_state()` from Paper DB | query time, no explicit timestamp | not market quality |
| `ledger.summary` | Paper account/status | query time | no snapshot id |
| `ledger.recent_closed[0:3]` | `paper_trades` | per-trade created/closed times | no original plan object |
| `recent_events[0:3]` | `event_logs` | per-event `created_at` | no causal association to context |

The AI receives current scalar/window summaries, not historical windows. It does not receive `MarketAnalysisContextV2`, `MarketStateSnapshotV2`, raw candles, VPVR, funding, basis, liquidation, macro evidence, current open-trade details, the immutable `trade_rationale`, user-entered positions/plans, or any source watermark/quality/gap map.

### Consistency, persistence, health and safety answers

| Question | Finding |
|---|---|
| confirmed candle enforced in AI context? | `PARTIAL`: legacy analysis filters fetched candles in its own path, but the AI contract carries no `confirmed` marker or bar evidence. |
| mixed water levels possible? | `YES`: price/candles, recent REST trades, 15-second OI, professional flow, DB risk and ledger are read at different moments; no common decision cutoff is asserted. |
| CVD/OI gaps included? | `NO`. |
| user position plan included? | `NO`; only summary and three recently closed trades. Open trades are omitted from `_ai_context`. |
| macro evidence included? | `NO / NOT_IMPLEMENTED`. |
| request context persisted? | `NO`. `analysis_snapshots` persists legacy analysis elsewhere, not the exact `_ai_context` sent to DeepSeek. |
| same analysis replayable? | `NO`: prompt/context/model response tuple is not stored. Replay (`1061-1072`) restores decision snapshots and 15m candles, not an AI request. |
| output schema? | `NO`: brief is one content string; Copilot is `{answer}` or `{error}`. |
| fact/consistency audit? | `NO`. |
| old brief reused? | `YES`: `status()` always returns latest per-instrument row with no frontend stale suppression. Health staleness does not remove it. |
| symbol cache mixing? | Brief SQL is instrument-scoped and `App.refresh()` has a request counter plus instrument equality guard (`App.tsx:703,729-748`). Chart localStorage keys include series/instrument/timeframe (`chartState.ts:1-14`). This guards known paths; `UNKNOWN` for every browser race. |
| timeframe switch updates AI? | `NO`: interval only reloads chart/VPVR (`App.tsx:802-809`). Brief and Copilot use backend fixed analysis, independent of selected UI interval. |
| AI affects orders/strategy? | `NO`: order creation occurs before brief enqueue (`paper_api.py:1005-1007`); chat only returns text. |
| current safety boundary | API key stays server-side; health exposes only a boolean (`health_service.py:68-69`); chat is rate-limited; prompts say no real orders/profit promises. There is no output grounding/audit or prompt-injection-specific control. |

Health state is persisted in `ai_health` (`paper_api.py:351-353,400-421`). On success, failure count resets and next retry is one hour (`954-960`). Failures use exponential delays `min(3600, 60*2^(n-1))` (`962-970`). Status is `disabled`, `starting`, `retrying`, `healthy`, or `stale`; stale means last success age over 7,200 seconds (`972-985`). A monitor checks each minute and raises/resolves alerts (`987-998`). A failed HTTP request is not retried inside the same job; eligibility is reconsidered by later Paper cycles. Copilot has no such mechanism.

## 2. Market facts and indicators

### Candles

There are three distinct capabilities and they must not be conflated:

| Path | BTC | ETH | SOL | Intervals | confirmation, history and persistence |
|---|---|---|---|---|---|
| Paper production loop | yes | yes | no (`INSTRUMENTS`, `paper_api.py:102`) | fetch helper accepts 1m/5m/15m/1H/4H/1D, but decision path uses 15m/1H/4H/1D | OKX `/market/candles`, max helper default 300; parses OKX confirm flag (`429-433`). Only 15m rows are persisted by analysis (`665`). Unix seconds, UTC semantics. |
| Browser chart | yes | yes | yes (`marketInstruments.ts:1`) | 1m,5m,15m,1h,4h,1D (`App.tsx:909-918`) | direct OKX current/history pagination, up to 300/page (`data.ts:1132-1169`); confirm flag is discarded, so the latest open candle can appear. localStorage is capped at 10,000 points (`chartState.ts:1-31`). |
| Research Context V2 | conditional on DB rows | conditional | conditional | 15m,1H,4H,1D plus derived 1W (`market_context_v2.py:38-40,637-645`) | reads confirmed `historical_candles` first, legacy persisted rows second, bounded 512/512/512/1500. Strict weekly aggregation requires seven contiguous confirmed UTC days, Monday boundary (`170-193`). |

Actual earliest/latest timestamps and row counts for BTC/ETH/SOL are `REQUIRES_RUNTIME_AUDIT`; they cannot be inferred from source code. Gap semantics in Context V2 are missing adjacent open-time buckets (`250-263`). All backend external timestamps are Unix seconds at this boundary; microstructure storage is Unix milliseconds and Context V2 converts at read time. Browser chart uses Unix seconds. Timezone is UTC by API/candle definition, independent of local display formatting.

### Indicator inventory

The strongest reusable implementation is `MarketIndicatorRegistryV2.calculate()` (`market_context_v2.py:266-369`), built on causal `discovery_features.build_features()` (`discovery_features.py:14-45`). It uses confirmed candles selected at or before `as_of`.

| Indicator | Current state | Parameters / implementation | Warm-up and reuse verdict |
|---|---|---|---|
| MA20 | `PARTIAL` | base feature builder can generate SMA20 but Context V2 registers EMA20, MA60, MA200 only (`281-283,325-333`) | add MA20 to registry/contract |
| MA30 | `NOT_IMPLEMENTED` in Context V2 | no registered MA30 | add |
| MA60/MA200 | `EXISTS` | SMA, 60/200 | needs 60/200 closed bars; reuse |
| EMA20 and slope | `EXISTS` | recursive EMA20; four-bar percent slope (`293-298`) | EMA seeds at first closed value; slope needs 5 feature rows; reuse with documented seed |
| RSI | `EXISTS` | RSI14 in feature builder | 15 closes; causal; reuse |
| Stoch RSI | `EXISTS` | RSI14, stochastic 14, K3, D3 (`196-227`) | null until dependent windows complete; reuse |
| ATR | `EXISTS` | Wilder ATR14 | 15 bars; reuse |
| Bollinger bands/bandwidth | `EXISTS` | 20, population SD, 2σ; bandwidth percent | 20 bars; reuse |
| volume ratio | `EXISTS` | current volume / previous 20 mean | 21 bars; reuse |
| rolling volatility | `EXISTS` | population SD of 20 log returns × √20 ×100 (`315-316`) | 21 closes; reuse; naming/annualisation must remain explicit |
| body/wicks | `EXISTS` | percent of candle high-low (`319-324`) | one non-zero-range confirmed candle |
| rolling high/low | `PARTIAL` | distance to prior 20 high/low; raw value is not exposed | add raw traced levels |
| swing high/low | `EXISTS` | confirmed 2-left/2-right fractal (`236-247`) | 5 bars minimum; pivot timestamp is right-side confirmation |
| trend strength | `PARTIAL` | Market State evidence strength, not a universal price trend-strength indicator | reuse only with its exact semantics |
| normalized distance | `PARTIAL` | price-to-EMA20/MA60/MA200 percent; level state also ATR-normalizes | add required MA20/30 and formal units |
| volume expansion/contraction | `PARTIAL` | volume ratio plus state threshold 1.20; no standalone regime field in Context V2 | deterministic regime module needed |
| compression/expansion | `EXISTS/PARTIAL` | causal rank of current BB width among up to last 100; at least 20 widths (`317-349`) | reusable input; timeline start/end not implemented |

No indicator in Context V2 uses the live incomplete candle. Browser-only EMA/signal calculations do not preserve OKX confirmation and are not acceptable as formal facts.

## 3. Microstructure inventory

The durable store is `MicrostructureStore` (`dashboard/microstructure.py`). Native forward raw trades, 1m CVD and 1m OI are aggregated by `RealtimeAggregationEngine` (`realtime_aggregation.py:233-317`), with higher 5m/15m/1H/4H/1D resolutions derived only from complete 1m constituents (`332-469`). Flow history has bounded range queries and gap runs (`flow_history.py:111-130,177-374`).

| Lane | Source/status | Resolution/history/gaps | Paper API / current AI |
|---|---|---|---|
| raw trades | OKX public trades WebSocket; forward-only | raw durable observations; actual earliest/latest `REQUIRES_RUNTIME_AUDIT`; genuine gaps recorded | Paper chart/decision has a separate local collector; raw stream is not sent to AI |
| CVD | signed trade delta; UTC anchored cumulative | native 1m, derived 5m/15m/1H/4H/1D; do not treat across-gap cumulative as fully valid; actual coverage `REQUIRES_RUNTIME_AUDIT` | flow-history API/chart exposed; Context V2 reads only a four-bar window at requested 15m/1H/4H; AI gets one scalar without quality |
| OI | OKX SWAP public OI, forward samples | native 1m snapshots, derived resolutions; gaps prohibit change/zscore across them; absolute observation may resume after gap | chart/history and Context V2 current/change exposed; AI gets one percent change without gap status |
| settled funding | official funding history | normally settlement schedule, paginated 400/page; limited history (`docs/okx_microstructure_source_audit.md:10`) | research chart + Context V2 latest; not AI |
| predicted funding | current funding endpoint | provisional, forward-only (`source_audit.md:11`) | Context V2 latest; not AI |
| basis/mark/index | OKX mark/index/ticker inputs; basis aggregate | 1m and derived store support; actual coverage `REQUIRES_RUNTIME_AUDIT` | research chart + Context V2 latest basis; mark/index are not independent AI fields |
| liquidation | official `liquidation-orders` WS | capped feed, forward-only, explicitly not a complete ledger (`source_audit.md:15`) | health/count research surfaces exist; Context V2/Paper AI do not expose phase liquidation |
| VPVR | trade-price profile when forward trades cover range; otherwise OHLCV uniform-range approximation | requested interval config 1m through 1D (`paper_api.py:121`), 24h display window; method/coverage reported | `/api/vpvr` and frontend render it; current AI context omits it |

The repository provides explicit stale/partial/gap fields in professional/research APIs and Context V2 (`IndicatorValueV2`, `DataQualityV2`, `market_context_v2.py:57-76`). The legacy AI context strips them. “Earliest/latest”, full BTC/ETH/SOL coverage, retention currently present, and collector liveness at this date remain `REQUIRES_RUNTIME_AUDIT`.

## 4. Positions and plans

Paper storage (`paper_api.py:335-376,784-796,835-871`) has side, simulated entry/fill, stop, target, position quantity, mark, one full exit, realised/net PnL, and immutable machine-generated rationale with strategy timeframe/version and evidence timestamps. Current unrealised PnL can be derived from entry/mark/quantity but is not a stored named field. Average cost equals the single-entry fill; multi-fill average cost and partial exits are `NOT_IMPLEMENTED`. Original rationale exists for new Paper rows, but the AI context does not include it. There are no manual notes or user-entered plan fields.

User real positions, exchange account positions, user original thesis/timeframe/stop/targets, manual notes, partial reductions, plan-completed state, and emotion/discipline history are `NOT_IMPLEMENTED`. They must never be inferred from Paper positions. The target contract therefore requires `source=PAPER|USER_DECLARED|NONE`, with nullable details when `NONE`.

## 5. Frontend entry and cache audit

- AI Brief: `App.tsx:1300-1322`; latest stored plain text only, button disabled.
- Copilot: `App.tsx:1323-1341`; answer is ephemeral state and reset on instrument change (`752-755`).
- Replay: `App.tsx:841-853,1220-1260`; restores legacy analysis/candles/outcome, not AI request/report.
- Flow chart: `charts.tsx:235-493`; selection guards and instrument/timeframe cache keys prevent known cross-selection reuse. Flow gaps are displayed via gap-aware series.
- Instrument universe: public UI BTC/ETH/SOL, but Paper/AI only BTC/ETH. SOL AI requests normalize to ETH on the server, so enabling SOL Copilot without a contract fix is unsafe.
- Timeframe changes refresh chart and VPVR, not AI context. The brief label “hourly” is accurate cadence intent; the UI does not expose health/staleness beside the brief.

## 6. Required code/tests/docs reviewed

Backend: `paper_api.py`, `microstructure.py`, `realtime_aggregation.py`, `market_context_v2.py`, `market_state_v2.py`, `strategy_router_v2.py`, `volume_profile.py`, `flow_history.py`, `discovery_features.py`, `health_service.py`, `rate_limit.py`, `analysis_snapshot_archive.py`, snapshot storage/lifecycle modules and repository data access.

Frontend: `App.tsx`, `data.ts`, `charts.tsx`, `chartState.ts`, `candleHistory.ts`, `flowHistory.ts`, `marketInstruments.ts`, `i18n.tsx`, market-state/router/research components and API contracts.

Tests: AI-adjacent operations/health, Context V2, State V2, Router V2, CVD/OI flow history, realtime aggregation, Paper flow/provenance, snapshot lifecycle, replay-related API/frontend contracts, candle history, flow history, chart state, instrument and product contract tests.

Docs: repository README plus market context/state, microstructure source/forward/readiness, live aggregation, router, analysis snapshot lifecycle, retention/storage architecture and flow-history-relevant design. The current baseline contains no dedicated structured AI report contract or macro evidence design.
