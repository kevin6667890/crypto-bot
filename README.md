# Crypto-Bot Research Workspace

[![CI](https://github.com/kevin6667890/crypto-bot/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/kevin6667890/crypto-bot/actions/workflows/ci.yml)

Crypto-Bot is a paper-trading and historical strategy-research workspace for
BTC-USDT, ETH-USDT and SOL-USDT. The production UI is React + TypeScript served
by Nginx; the Python Paper API stores runtime and research data in SQLite.

The project does not place exchange orders. OKX public endpoints provide market
data, deterministic rules control all paper decisions, and DeepSeek is limited
to descriptive market summaries and user-requested explanations.

## Production architecture

- React, TypeScript and Vite frontend
- Nginx static hosting with `/api/` reverse-proxied to the Paper API
- Python `ThreadingHTTPServer` application on internal port 8765
- SQLite persistence under `data_cache/`
- Docker Compose services `frontend` and `paper-api`
- OKX public spot candles, trades, ticker and perpetual open interest

Streamlit remains only as a compatibility wrapper and is not the production
frontend.

## Workspaces

### Market Analysis

- Live BTC, ETH and SOL ticker and confirmed candles
- 1m, 5m, 15m, 1H, 4H and 1D charts with MA60 and MA200
- Multi-timeframe trend, EMA20 slope, recent-trade CVD proxy and swap OI
- Explainable deterministic score, risk controls and SQLite paper ledger
- Historical decision replay and DeepSeek Market Copilot

CVD is a proxy calculated from the latest public taker-trade sample. It is not a
complete historical order-flow measure and is labelled accordingly.

### Strategy Research

- Real OKX `history-candles` data with pagination, confirmation filtering,
  deduplication, gap reporting and SQLite caching
- BTC-USDT, ETH-USDT and SOL-USDT on 15m, 1H and 4H
- Causal indicators with complete Slow MA warm-up
- Signal confirmation at candle close and next-candle-open execution
- Configurable adverse slippage, two-sided fees, ATR stops, reward targets,
  cooldown and long/short controls
- Persisted strategy configurations, runs, trades, equity and walk-forward data
- IS/OOS validation, rolling walk-forward stability and Paper reconciliation
- Responsive equity, drawdown, candle/execution, R-distribution, monthly-return
  and long/short diagnostics
- Gate funnel statistics from complete canonical decision payloads, explainable
  near misses and conservative counterfactual outcomes
- Bounded OAT/2D sensitivity, comparable benchmarks, reproducible Monte Carlo
  and bootstrap stress tests in the persistent single-worker queue
- Isolated restart-safe Shadow candidates and an evidence-based, audited
  strategy lifecycle with manual-only Active promotion and rollback
- Causal deterministic market regimes stored with new decisions
- Optimization run history/comparison, explicit experiment-family holdout locking,
  contamination audit flags and anonymized research-report export
- Persistent multi-stage out-of-time validation suites. BTC holdout/final-OOT and
  ETH/SOL transfer evidence are descriptive only and never influence ranking.

Historical CVD and OI are not reconstructed because the required historical
samples are not reliably available from the endpoints used here. The backtest
does not synthesize them. If a candle touches stop and target in the same bar,
the engine conservatively records the stop first.

### Operations and lineage

- Paper and historical research call the same canonical `evaluate_decision`
  implementation; 15m research uses strict confirmed 1H/4H as-of context.
- Canonical JSON configuration hashes and SHA-256 signal IDs make decisions
  reproducible without rewriting legacy rows.
- Exact reconciliation classifies matched, missing, divergent, risk-blocked,
  service-gap and legacy executions instead of inferring matches.
- Portfolio research processes BTC, ETH and SOL in one chronological stream
  with shared cash, exposure, position and risk limits.
- A persistent SQLite queue runs one heavy job at a time and supports queue
  limits, deduplication, cancellation, retries and restart interruption.
- Operations exposes sanitized health, collector freshness, job monitoring and
  deduplicated persistent alerts. Logs rotate on disk.

Chat and research writes have application and Nginx rate limits. API bodies are
limited to 64 KiB, API responses are not cached, and browser API calls are
same-origin. Optional `ADMIN_TOKEN` protection uses a server environment value;
when used by the UI it belongs in `sessionStorage`. Plain HTTP does not protect
that token in transit, so HTTPS remains required before treating it as secure.

## Local development

```bash
python -m pip install -r requirements.txt
python dashboard/paper_api.py
```

### Paper Flow collector

The supported local command is `python -m dashboard.paper_api`. It listens on
port `8765` by default and starts the public OKX `trades-all` WebSocket and the
public SWAP OI poller with the Paper API. Check it with:

```bash
curl http://127.0.0.1:8765/api/paper/flow/health
```

Stop it with `Ctrl+C`; the collectors close, flush pending buckets, and release
their database connections. Runtime flow data is stored in
`data_cache/paper_trades.db`. Tick CVD is collected only while this service is
running: an empty restart starts with partial coverage and needs six hours for a
complete display window. No historical tick CVD or OI is synthesized.

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local Paper API.

## Tests and production build

```bash
pytest -q
cd frontend
npm run build
```

The backtest tests cover causal/no-future indicators, MA warm-up, fees,
slippage, long and short execution, stop/target handling, drawdown, Profit
Factor and empty-trade results.

## Research API

- `POST /api/backtest/run`
- `GET /api/backtest/{id}`
- `GET /api/backtest/{id}/trades`
- `GET /api/backtest/{id}/equity`
- `GET /api/backtest/history`
- `GET|POST /api/strategies`
- `PUT|DELETE /api/strategies/{id}`
- `POST /api/strategies/{id}/duplicate`
- `POST /api/compare`
- `POST /api/walk-forward`
- `GET /api/reconciliation?run_id={id}`
- `POST /api/portfolio/run`
- `GET /api/portfolio/{id}`
- `GET /api/validation/gates`
- `POST /api/validation/gates/run`
- `GET /api/near-misses`
- `POST /api/sensitivity/run`
- `POST /api/benchmarks/run`
- `POST /api/robustness/run`
- `GET|POST /api/optimization/families`
- `GET /api/optimization/families/{id}`
- `POST /api/optimization/run`
- `GET /api/optimization/history`
- `GET /api/optimization/{id}` (holdout hidden by default)
- `POST /api/optimization/{id}/reveal-holdout`
- `POST /api/optimization/compare`
- `POST /api/validation-suites/run`
- `GET /api/validation-suites`, `GET /api/validation-suites/{id}`
- `GET|POST /api/shadow-strategies`
- `GET /api/strategy-lifecycle`
- `GET /api/health`
- `GET /api/health/details`
- `GET /api/paper/flow/health`
- `GET /api/paper/flow/history/v1?instrument=BTC-USDT&series=cvd&start={unix}&end={unix}&max_points=1200&cursor={opaque}`
- `GET /api/jobs`
- `POST /api/jobs/{id}/cancel`
- `POST /api/jobs/{id}/retry`
- `GET /api/alerts`
- `POST /api/alerts/{id}/acknowledge`

## Docker deployment

Build the frontend before starting the services because Nginx mounts the
generated `frontend/dist` directory:

```bash
cd frontend && npm ci && npm run build && cd ..
docker compose up -d --build paper-api frontend
```

Runtime `.env` files, keys, SQLite databases and candle caches must remain
outside Git.

### Flow-history retention and aggregation

Raw `flow_trade_buckets`, `flow_price_buckets`, and `oi_snapshots` remain
bounded to 90 days. Durable aggregates use only persisted observations and are
retained indefinitely at 5 minute, 1 hour, 4 hour, and 1 day resolutions.
Missing periods remain absent and are reported as gaps; they are never filled
with zeroes.

CVD aggregate `delta` is the sum of observed buy notional minus sell notional
inside the bucket. The API returns a globally anchored cumulative `value`, so
different ranges, resolutions, and pages keep consistent CVD semantics. OI
uses the last confirmed observation in a bucket and also returns the observed
minimum and maximum. Legacy `flow_snapshots` OI is admitted only before the
first raw OI snapshot for that instrument; its rolling CVD is not used.

The versioned response includes requested and available bounds, latest
timestamp, raw/returned counts, selected resolution, stale/history flags,
bidirectional coverage flags, an opaque older-page cursor, source, gap
metadata, and `flow-retention-v2`. Run the transactional, resumable,
idempotent backfill after making a verified SQLite snapshot:

```bash
python scripts/backfill_flow_history.py --database data_cache/paper_trades.db
```

## Research report export

Export only sanitized persisted evidence; generated reports are ignored by Git.

```bash
python scripts/export_research_report.py --optimization-run 12 --output reports/optimization-run-12.md
python scripts/export_research_report.py --experiment-family 3 --output reports/family-3.md
```

Experiment families lock instrument, timeframe, development, primary holdout and
optional final OOT periods. Revealing a holdout is explicit and durable. Later
parameter or search-space changes are flagged as contaminated rather than
silently treated as untouched validation. Optimization ranking is based only on
development/validation metrics: holdout, final OOT and cross-asset transfer
results never affect it. The workspace remains paper/research only: no real
orders, no AI parameter search, no automatic parameter activation or promotion.

---

For educational and research purposes only. Past paper or backtest results do
not predict future performance and are not financial advice.
# Experiment-family research governance

The Optimization Lab supports governed experiment families: configure a 15m BTC-USDT, ETH-USDT, or SOL-USDT development period, a primary holdout, and an optional final OOT period. Family ranges are locked; any gap between development and holdout is excluded from research. Start optimization from the selected family to retain its fingerprint and evidence lineage.

Primary holdout metrics remain hidden until **Reveal Primary Holdout** is explicitly confirmed. Later changed base parameters or search space are marked as contaminated evidence. Final OOT and cross-asset validation are recorded as validation suites and never affect optimization ranking or strategy activation. Retrying a failed, cancelled, or interrupted suite creates a new suite and job while preserving the original evidence.

Public Markdown/JSON reports use a safe allowlist and exclude hidden holdout results, queue internals, secrets, and local paths. This project is paper/research only: it sends no live exchange orders, automatically activates no parameters, and does not use AI parameter search.

CI checks: `pytest -q`, `python -m compileall dashboard tests scripts`, and `cd frontend && npm ci && npm run build`.
# Strategy Discovery Lab

The lab is paper/research only: it places no live orders, never automatically
activates a strategy, and has no online self-learning.  Prepare the fixed cache
with `python scripts/prepare_discovery_dataset.py --start 2024-01-01 --end
2026-01-01 --instruments BTC-USDT ETH-USDT SOL-USDT --timeframes 15m 1H 4H 1D`.
Its range is start-inclusive/end-exclusive (`[2024-01-01, 2026-01-01)`), and
uses confirmed OKX public candles.  The primary engine is causal next-bar-open.
Price-only templates are Trend Pullback, Volatility Breakout, Mean Reversion and
Trend Breakout, searched with a bounded seeded sampler and development-only
walk-forward folds. CVD/OI are never fabricated; flow overlays require verified
coverage. Historical discovery does not prove future profitability and final
selection remains manual. See `docs/strategy_discovery_architecture.md`.
