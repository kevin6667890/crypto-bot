# Live microstructure aggregation

The collector has two independent controls:

- `MICROSTRUCTURE_MAINTENANCE_ENABLED=false` keeps historical maintenance,
  retention, repair, and historical funding polling disabled.
- `MICROSTRUCTURE_REALTIME_AGGREGATION_ENABLED=true` enables only completed
  live CVD/OI buckets.

The normal loop revisits at most 15 completed UTC minutes and uses the existing
`(instrument, source_ts_ms)` raw indexes. It writes through the collector's
single SQLite writer, two minutes per transaction, and advances the
`realtime_aggregation` checkpoint only in the same successful transaction.

`SIGUSR1` requests one local-only catch-up bounded to the preceding 120
completed minutes. The fixed order is ETH, BTC, SOL. Each instrument is paused
for 30 seconds and each two-minute transaction is separated by two seconds.
The signal does not call a network API.

Source fingerprints are stored in `realtime_aggregate_fingerprints`. A changed
fingerprint never overwrites an existing aggregate: that instrument transitions
to `CONFLICT` and pauses. Missing raw minutes create metadata only; they do not
create zero or synthetic aggregate rows. 5m, 15m, and 1H values are derived
only when every required 1m bucket is valid.

The read-only inventory command is:

```text
python scripts/realtime_aggregation_dry_run.py --database PATH \
  --instrument ETH-USDT-SWAP --start EPOCH_MS --end EPOCH_MS
```

The command rejects ranges greater than 120 minutes and never writes the
database.
