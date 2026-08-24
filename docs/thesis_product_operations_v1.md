# Thesis product operations v1

Historical research evidence and current tracking evidence use separate data lifecycles.

## Required stores

Production historical testing should set `THESIS_HISTORICAL_REQUIRE_IMMUTABLE=true` plus `THESIS_HISTORICAL_DB_PATH`, `THESIS_HISTORICAL_DB_SHA256`, and `THESIS_HISTORICAL_DATASET_ID`. A missing or mismatched file fails the historical thesis endpoint closed; it never falls back to recent current candles. The database is read-only to the thesis engine.

The immutable database must expose a one-row `thesis_dataset_manifest` (or `historical_dataset_manifest`) table with a `dataset_id` column. Readiness compares that value to `THESIS_HISTORICAL_DATASET_ID`; the partition-level dataset identity produced by the historical engine remains a separate content-derived identity.

Saved theses use the independent SQLite file at `THESIS_TRACKING_DB_PATH`. Put it on a persistent volume and include that file in normal SQLite-safe backups. Schema initialization is idempotent and never changes Paper trading tables.

Current evaluation reads `PAPER_DB_PATH` through the canonical bounded market reader. It requires the latest confirmed candle from `market_candles`; historical rows may provide warmup only. UTC candle timestamps determine idempotency.

## Scheduler

The worker is disabled by default. Set `THESIS_TRACKING_SCHEDULER_ENABLED=true` and optionally `THESIS_TRACKING_SCHEDULER_CADENCE_SECONDS=900`, then run `python -m scripts.run_thesis_tracking_scheduler`.

Docker users can start the isolated `thesis-tracking` profile. Repeated ticks on the same source candle are database no-ops. The worker calls the domain service directly and never calls the public HTTP endpoint or the trading scheduler.

## Optional AI

AI credentials are not a site-wide requirement. Without parser credentials, the manual thesis builder remains available. `THESIS_EXPLANATION_ENABLED=false` keeps deterministic evidence explanations and tracking fully usable.

## Health and recovery

`GET /api/research/thesis/readiness` reports sanitized status for historical data, current data, the tracking database, optional AI, and scheduler configuration. It never returns paths or secrets.

Back up the tracking SQLite file together with its WAL/SHM files using a SQLite-aware snapshot or a stopped process. Restore the file at the configured persistent path before starting the API and worker.
