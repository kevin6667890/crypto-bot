# Realtime collector pressure policy

The live writer queue is bounded at 20,000 observations. Aggregate work never
enters this queue and already defers whenever live observations are pending.

Operations must evaluate depth together with oldest-item age, raw progress and
trend. A single depth sample is never a stop condition:

- `NORMAL`: depth below 200 and oldest age below 1 second.
- `PRESSURE`: depth at least 200 or oldest age at least 1 second. Record the
  burst; aggregate work remains deferred while live work exists.
- `HIGH_PRESSURE`: depth at least 5,000 or oldest age at least 5 seconds.
  Require repeated 10-second samples before escalation.
- `RECOVERING`: depth and age have cleared after pressure; three clear writer
  observations return the state to `NORMAL`.
- `EMERGENCY`: at least three consecutive 10-second samples with depth at or
  above 18,000, oldest age at or above 30 seconds and increasing, and raw
  timestamps stalled. SQLite failures, an unbounded WAL trend or a container
  restart remain independent emergency evidence.

The thresholds derive from the 2026-08-03 event: 441 pending observations
drained to zero within 508 ms, the final batch contained 153 items, and the
writer queue capacity is 20,000. At least 868 observations per second drained
during shutdown, so 200 and 441 represented about 0.23 and 0.51 seconds of
measured backlog. They are warning evidence, not integrity risk.

Never print expanded Compose environments or container `Config.Env`. Inspect
only an allowlisted variable name and whether it is set; never print its value.
