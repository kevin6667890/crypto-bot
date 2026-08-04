# Realtime aggregation liveness

The collector owns a dedicated supervisor for the realtime CVD/OI aggregation
task. Raw persistence remains independent and has priority whenever the writer
queue is non-empty.

Operational health exposes task state, creation/cycle/heartbeat/success times,
the current await description, the last exception, restart count, raw and
aggregate watermarks, pending buckets, and aggregate lag. An alive task with
normal lag is `HEALTHY`; a task in restart backoff or raw data more than three
minutes ahead of aggregates is `DEGRADED`; exhausted restarts are `FAILED` and
make the collector health endpoint return HTTP 503. Disabled maintenance does
not affect this status.

Raw and aggregate watermarks are tracked independently for CVD and OI. A fresh
OI series therefore cannot mask a missing or stale CVD series in health output.

One instrument failure is rolled back and recorded without stopping other
instruments. It is retried by the next bounded live cycle. The supervisor waits
5 seconds before the first task restart, doubles the delay up to 60 seconds,
and stops after five consecutive failures while raw collection continues.
Normal restart processing is limited to the existing 15-minute live lookback.
The explicit catch-up signal remains limited to 120 minutes and never calls a
remote history endpoint.
