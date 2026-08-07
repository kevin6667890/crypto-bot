# AI-6 Rollback Runbook (Plan Only)

Default rollback is flag closure while retaining immutable evidence. Independently disable, in order needed by the incident: internal frontend entry, `AI_MARKET_ANALYSIS_PRESENTATION_ENABLED`, report worker, audit worker, live provider, and the Paper API presentation route/deployment. Verify the old AI Brief remains available.

Do not delete reports, audits, contexts or registry snapshots. Do not downgrade the old schema, alter Paper orders, Router, collector or aggregation, or remove the old AI Brief. Capture flag snapshots, actor/time, health before/after, last accepted presentation IDs, failure distribution and artifact hashes.

Rollback succeeds only when no Shadow poll or generation runs, old UI remains healthy, order/Router/collector counts are unchanged, and immutable records remain queryable read-only. A failed independent rollback is a hard stop for canary continuation.
