# Research API

The React application uses same-origin `/api` requests. In local development, Vite proxies these requests to the Python Paper API on port `8765`; in Docker, Nginx provides the same boundary.

## Paper and market state

- `GET /api/health`
- `GET /api/health/details`
- `GET /api/market/state`
- `GET /api/strategy/route`
- `GET /api/paper/flow/health`
- `GET /api/paper/flow/history/v1`

The flow-history endpoint returns explicit requested/available bounds, resolution, source, freshness, gaps, and pagination metadata. Missing periods remain missing; they are not filled with zeroes.

## Backtest and research

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

## Validation and governance

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
- `GET /api/validation-suites`
- `GET /api/validation-suites/{id}`
- `GET|POST /api/shadow-strategies`
- `GET /api/strategy-lifecycle`

Experiment-family ranges are locked. Revealing a holdout is explicit and durable; later search-space changes are recorded as contaminated evidence. Holdout, final OOT, and transfer results are excluded from optimization ranking.

## Operations

- `GET /api/operations/summary`
- `GET /api/jobs`
- `POST /api/jobs/{id}/cancel`
- `POST /api/jobs/{id}/retry`
- `GET /api/alerts`
- `POST /api/alerts/{id}/acknowledge`

Write endpoints enforce request-size and rate limits. Optional administrator protection is configured server-side; secrets and local paths are excluded from exported public research reports.
