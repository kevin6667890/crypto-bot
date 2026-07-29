# Storage lifecycle v2

This design preserves orders, accounting, decision reasons, canonical lineage,
research manifests, trial ledgers, and non-reproducible outputs. It removes
repeated canonical input series from live analysis snapshots and moves verified
cold raw data to off-host archives. An off-host archive is never a runtime
dependency of the Paper API or collector.

## Retention classes

- `PERMANENT_LEDGER`: compact decision result and audit metadata. Long input
  series are prohibited.
- `REPRODUCIBILITY_MANIFEST`: source ranges, watermarks, row counts,
  fingerprints, dataset identity, parameters, code commit, and model versions.
- `DEBUG_SAMPLE`: optional compressed artifact, at most one per
  instrument/timeframe/UTC day and seven-day retention. Full artifacts are
  disabled by default.
- `ERROR_FORENSIC`: optional compressed minimal failure evidence with 30-day
  retention.
- `RESEARCH_ARTIFACT`: belongs in the separate research manifest/ledger, never
  as a large `analysis_snapshots` payload.

Canonical candles, CVD/OI aggregates, funding, basis, gap ledgers, source
summaries, coverage, and readiness remain in their canonical tables. They are
referenced by identity and time range rather than copied into each snapshot.

## Analysis snapshot contract

`analysis-snapshot-storage-v2` has a default inline limit of 32 KiB. The
recursive validator enforces total serialized size, nesting depth, sequence
length, string-keyed mappings, and JSON-compatible types. It does not rely on a
field-name denylist.

The Paper API still writes the core evaluation, signal, order/accounting, and
lineage ledgers if an input analysis is oversized. The compact snapshot stores
the final action, classification, reason, confidence, scalar features,
watermarks, row counts, source fingerprints, dataset identity, code/model
versions, risk/accounting/order references, and original artifact hash.
Oversized-input telemetry records what was stripped.

Legacy inline payloads remain readable. Compact payloads are returned directly
by replay endpoints. Historical full payloads are restored only through the
offline restore tool.

## Snapshot archive and compact rebuild

Run these steps only against an SQLite-consistent off-host source copy:

```text
python scripts/archive_analysis_snapshots.py \
  --source-database <consistent-paper-copy> \
  --archive-directory <offhost-snapshot-archive> \
  --compression zstd --report <archive-report>

python scripts/restore_analysis_snapshot.py \
  --archive <monthly-bundle> --snapshot-id <id> --verify --payload

python scripts/build_compact_paper_database.py \
  --source-database <consistent-paper-copy> \
  --archive-directory <offhost-snapshot-archive> \
  --output-database <new-compact-database> --report <rebuild-report>
```

Monthly SQLite bundles deduplicate exact payload hashes and store individually
compressed payload blobs. Each bundle includes source DB hash, snapshot identity,
original/compressed sizes, codec, archive time, and restore instructions.
Verification enumerates every member, checks SQLite integrity, decompresses
every payload, checks byte length/SHA/row identity, and performs deterministic
sample restores.

The compact builder works on a temporary off-host copy and uses offline
`VACUUM INTO` to create a new database. It never vacuums the production DB. It
verifies schema compatibility, `user_version`, foreign keys, every snapshot ID
and basic metadata, archive mapping completeness, and full canonical hashes for
every non-snapshot table.

## Raw trade lifecycle

`microstructure-raw-retention-v1` keeps seven complete UTC days hot. A cold
archive is one UTC day and instrument, containing exact sorted
`trade_flow_observations`, an embedded manifest, and a compressed shard. The
manifest records time/trade-ID ranges, row count, buy/sell/signed notional,
price range, source fingerprint, gap summary, and reconciliation against 1m CVD
aggregates.

Pruning is dry-run by default. Apply requires a cryptographically matched
off-host ACK, passing aggregate reconciliation, no unresolved archive-window
gap, zero current critical live gaps, low queue/writer lag, and an interval
outside the hot window. Each invocation deletes at most the configured batch
and records resume state. It never runs `VACUUM`; free pages are reused by
future inserts.

Confirmed archive coverage is exposed as `ARCHIVED_CONFIRMED`. It extends
historical coverage/readiness through the archive manifest but never changes
the current live-source freshness timestamp and is never classified as a live
gap.

## Disk protection and logs

Thresholds are environment configuration:

- `WARNING`: free space below 20%, or projected 85% use within 14 days.
- `CRITICAL`: free space below 12%, or projected 90% use within 7 days.
- `EMERGENCY`: free space below 5% or below the configured safety byte floor.

Core orders, accounting, lineage, and aggregates are always allowed.
`WARNING` and above disable optional/debug artifacts. No level automatically
deletes unarchived data or restarts the host.

Compose uses `json-file`, `max-size=20m`, and `max-file=5` without changing bind
mount semantics. Cleanup must use an explicit inventory and preserve current
images, at least two rollback images per service, all volumes, configuration,
credentials, and verified off-host backup records.

## Production switch and rollback

Before switching, stop every paper DB writer/reader, checkpoint, verify there
are no active SQLite file descriptors, transfer the current DB off-host, and
verify size, SHA, SQLite header, and `quick_check`. If production resumes writes
after the copy, discard the candidate and repeat from a new consistent copy.

Upload the verified compact DB to a temporary server path, fsync it, verify SHA,
then atomically rename while services are stopped. Keep the old server DB until
the new DB has passed the observation window and the off-host source/archive
remain verified.

Rollback stops new services, atomically restores the old DB filename, restores
the previous images/configuration, starts services, and rechecks orders,
accounting, lineage, timestamps, gaps, queue, and WAL. Failed candidates and
off-host backups are retained for investigation.
