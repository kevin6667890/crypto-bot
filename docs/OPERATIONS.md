# Operations and deployment

## Local services

Start the Paper API with:

```bash
python -m dashboard.paper_api
```

It listens on `127.0.0.1:8765` by default and starts the public OKX trade collector and perpetual OI poller. Stop it with `Ctrl+C`; collectors close, flush pending buckets, and release database connections.

Start the frontend separately:

```bash
cd frontend
npm ci
npm run dev
```

Vite proxies `/api` to the local Paper API. Streamlit remains a compatibility wrapper and is not the production frontend.

## Docker Compose

```bash
docker compose up -d --build paper-api frontend
```

The production path is an Nginx-served React build with same-origin `/api/` proxying to the Python service. Runtime environment files, credentials, SQLite databases, and candle caches remain outside Git.

## Data collection and retention

Tick CVD is collected only while the service runs. It is a public taker-delta measure, not complete exchange order flow. Historical CVD or OI is never synthesized.

Raw flow trade buckets, price buckets, and OI snapshots are retained for 90 days. Durable 5-minute, 1-hour, 4-hour, and 1-day aggregates use only persisted observations. Missing periods remain gaps.

CVD aggregate `delta` is observed buy notional minus sell notional in the bucket. Its cumulative value uses a stable anchor so ranges and pages reconcile. OI uses the last confirmed observation and reports observed minimum and maximum.

After taking a verified SQLite snapshot, the resumable and idempotent backfill is:

```bash
python scripts/backfill_flow_history.py --database data_cache/paper_trades.db
```

See [storage lifecycle](storage_lifecycle_v2.md), [live aggregation](live_microstructure_aggregation.md), and [microstructure readiness](microstructure_research_readiness.md) for the detailed contracts.

## Queue, health, and alerts

A persistent SQLite queue runs one heavy research job at a time and supports queue limits, deduplication, cancellation, retry, and restart interruption. Operations exposes sanitized service health, collector freshness, job status, storage protection, and deduplicated persistent alerts.

## Security boundary

Chat and research writes have application and Nginx rate limits, request bodies are bounded, API responses are not cached, and browser requests are same-origin. An optional `ADMIN_TOKEN` comes from the server environment. When the UI uses it, it is kept in session storage; HTTPS is required before treating it as protected in transit.

Research reports use an explicit safe allowlist and exclude hidden holdout results, queue internals, credentials, and local paths:

```bash
python scripts/export_research_report.py --optimization-run 12 --output reports/optimization-run-12.md
python scripts/export_research_report.py --experiment-family 3 --output reports/family-3.md
```
