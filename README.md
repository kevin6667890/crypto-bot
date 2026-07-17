# Crypto-Bot Research Workspace

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
- `GET|POST /api/shadow-strategies`
- `GET /api/strategy-lifecycle`
- `GET /api/health`
- `GET /api/health/details`
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

---

For educational and research purposes only. Past paper or backtest results do
not predict future performance and are not financial advice.
