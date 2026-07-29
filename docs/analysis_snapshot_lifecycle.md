# Analysis snapshot lifecycle

`analysis_snapshots` is decision evidence, not an order ledger. Its row ID,
creation time, instrument, decision lineage, and non-reproducible result must
remain addressable. Large embedded flow histories are nevertheless suitable
for verified cold storage because the canonical flow observations remain in
their source tables and the snapshot payload can be restored byte-for-byte.

The lifecycle tool is:

```text
python scripts/manage_analysis_snapshot_lifecycle.py \
  --database <offline-copy.db> \
  --report <dry-run.json> \
  --older-than-days 30 \
  --archive-directory <cold-storage>
```

Dry-run is the default. `--apply` is required to change an offline copy and is
rejected for production roots and any path listed in
`CRYPTO_BOT_PRODUCTION_DB_PATHS`.

## Archive contract

Payloads are stored by SHA-256 under
`blobs/sha256/<prefix>/<digest>.json.gz`. Gzip output is deterministic. Before
any SQLite update, the tool:

1. writes each blob atomically;
2. decompresses it and checks original length and SHA-256;
3. checks a row identity over snapshot ID, creation time, instrument, and
   payload hash;
4. publishes and verifies an immutable manifest;
5. verifies the restore adapter against the inline payload.

Apply then replaces only `analysis_snapshots.payload` with a small archive
stub. It retains the snapshot row and therefore also retains any logical or
foreign-key reference. It never deletes snapshot rows, never touches order,
accounting, or decision-lineage tables, and never runs `VACUUM`. Freed SQLite
pages enter the freelist and are reused by later writes; physical file
shrinking is deliberately outside this workflow.

`--max-rows` and `--max-bytes` bound a batch. `--checkpoint` records committed
snapshot IDs. `--resume` verifies the same manifest and treats already-stubbed
rows as completed, so interruption can leave only harmless verified blobs or
a committed batch; it cannot leave a partially written payload.

`dashboard.analysis_snapshot_archive.read_archived_payload` accepts an inline
payload or an archive stub, prevents path traversal, decompresses the blob,
and verifies its length and hash before returning JSON text.

## Retention classes

- Permanent SQLite metadata: order/accounting rows, canonical decision
  lineage, snapshot identity, decision reason, and evidence that cannot be
  reproduced.
- Compressible archive: large JSON payloads, embedded flow histories, and
  completed research-input copies.
- Cleanup candidates after a separate approval: exact duplicate payloads and
  unreferenced temporary copies whose archive and restore have been verified.
  The current tool reports duplicates but does not delete them.

## Production rollout sequence

1. Run dry-run against a recent consistent offline copy.
2. Archive a bounded batch from another disposable copy and restore every
   payload through the adapter.
3. Provision durable cold storage and a separate database disk.
4. Schedule a maintenance window for a schema/reader rollout only after backup
   and rollback rehearsals pass.
5. Observe at least 24 continuous hours after deployment before permitting a
   larger batch.

No production schema change or production apply is part of this repository
change.
