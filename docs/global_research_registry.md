# Global research registry and DSR trial accounting

Registry version: `global-research-registry-v1`

The local registry is an append-preserving audit index for already-produced
research evidence. Its default location is
`.research/global_research_registry.db`; the database, WAL, and SHM files are
local artifacts and are not committed. Importing is read-only with respect to
source reports and ledgers and never starts a research runner.

## Phase 6A–6G timeline and current evidence

| Phase | Research scope and chronological data boundary | Historical attempt evidence in this revision | Statistical tests | Locked data access | Ledger gap |
|---|---|---|---|---|---|
| 6A | Automatic strategy-program discovery on development data only | The result schema preserves structural rejection, semantic/behavior duplicate, event rejection, elimination, and retained classifications. No completed report or trial ledger is committed. | Unknown until a report is supplied | Report contract says false | Full historical trial ledger unavailable |
| 6B | Temporal strategy-program discovery on development data only | The result schema preserves generated programs, diagnostic sparse items, event rejects, classifications, and checkpoint progress. No completed report or ledger is committed. | Unknown until a report is supplied | Report contract says false | Full historical trial/checkpoint ledger unavailable |
| 6C | Official public-source microstructure collection and exploratory event studies | Repository documentation describes source scope; no completed run report or trial ledger is committed. | Unknown | No completed holdout/OOT claim | Run and trial history unavailable |
| 6D | Corrected source coverage and source-specific event-study pipeline | Only implementation/tests are present at the baseline; tests are engineering fixtures and are excluded from all accounting. | Unknown | Not established by a committed run artifact | Run and trial history unavailable |
| 6E | Bounded microstructure readiness validation with a chronological 70/30 research/validation split | Narrative report is importable as `PARTIAL_METADATA_ONLY`; it does not contain a complete per-test ledger. | Unknown | No completed holdout or OOT period was created or claimed | Per-feature/per-horizon trial ledger unavailable |
| 6F | Deterministic factor expression search with 60/20/20 discovery, selection-validation, and once-opened locked verification | Narrative documentation is importable only as partial metadata. Generated SQLite ledgers/reports are explicitly local and absent from Git. | Unknown without the ledger/report | Yes, locked verification is described as once opened | Raw rows, statuses, rejection reasons, and evaluation multiplicity require the local ledger |
| 6G | Statistical validity audit of the immutable 6F manifest using source-native events | Documentation explicitly states that 2,500 6F trial identities were mapped, but the audit ledger/report is not committed. The document is imported as partial run metadata, never as 2,500 fabricated rows. | Unknown without the audit ledger; pure structural invalidity must be excluded | It preserves 6F locked membership and does not open new sources | Per-trial mappings, applicable test families, and effective clusters require the local audit ledger |

Counts in this table are intentionally `Unknown` unless an existing report or
ledger states them. Budgets, test fixtures, and implementation constants are
not historical outcomes.

## Registry model

`research_runs` stores Phase, immutable run and dataset/snapshot identities,
source scope, grammar/schema/evaluation versions, instrument, timeframe,
horizon, chronological segment, run status, declared raw/statistical/effective
counts, selection/locked counts, final classification, report/ledger paths and
SHA-256 hashes, source/import timestamps, confidence, missing fields, and
unrecoverable-history notes.

`research_trials` preserves every available success, failure, elimination,
structural invalidity, budget truncation, duplicate, insufficient-sample, and
diagnostic record. Original strategy/factor/program/trial identities remain
unchanged. Missing values are stored as `UNKNOWN`; aggregate counts never cause
synthetic trial rows.

## Canonical identity

The registry adds keys without replacing source identities:

1. `canonical_entity_key` hashes the normalized expression/AST when available,
   otherwise the original factor/program/strategy identity plus normalized
   parameters.
2. `canonical_trial_key` combines the entity key with dataset identity,
   snapshot hash, normalized parameters, instrument, timeframe, horizon, and
   chronological segment.
3. Phase names, report/ledger paths, import time, creation/update timestamps,
   runtime duration, and other metadata timestamps are excluded.
4. Parent identities are preserved for lineage. Identical expressions and
   identical dataset/parameter attempts therefore reconcile across ledgers,
   while duplicate source-attempt rows and actual repeats remain historical
   attempts. Canonical keys are indexed for reconciliation, not used to delete
   genuine duplicate attempts.

Canonical identity version is
`global-research-canonical-identity-v1`.

## DSR accounting policy

The CLI exposes three simultaneous views:

- `RAW_ATTEMPT_COUNT`: genuine search attempts, including failure,
  elimination, duplicates, insufficient samples, and budget-truncated real
  attempts.
- `STATISTICALLY_EVALUATED_COUNT`: explicit statistical evaluations/tests.
  Pure structural invalidity with no outcome and engineering fixtures are
  excluded.
- `EFFECTIVE_CORRELATION_CLUSTER_COUNT`: ledger-declared effective cluster
  count first; otherwise distinct explicit cluster identities, with evaluated
  canonical trials as a conservative fallback.

Run-level declared totals remain visible when trial rows are missing. The
output separately lists `partial_metadata_risks`, included phases, excluded
phases, and every caller-supplied exclusion reason. Missing ledgers can
understate effective clusters and prevent auditing whether every applicable
factor/horizon/segment test was counted. They can also conceal correlations
between historical failures, so partial counts must not be represented as a
complete DSR family.

Accounting policy version is `global-research-dsr-accounting-v1`.

## Registering an existing or future phase

Initialize, auto-discover safe repository artifacts, import extra local
ledgers/reports, and print accounting:

```powershell
python scripts/audit_global_research_trials.py `
  --artifact 6A=fixtures/research/phase6a-report.json `
  --artifact 6F=fixtures/research/phase6f-ledger.db `
  --artifact 6G=fixtures/research/phase6g-audit.db
```

New phases must provide a stable run identity, dataset identity and snapshot
hash, complete raw-attempt rows (including terminal failures), explicit
statistical applicability, cluster identity/count, selection and locked
membership, versions, chronological segment, source scope, and artifact
hashes. If a legacy source cannot provide those fields, import its known
run-level metadata and keep it `PARTIAL_METADATA_ONLY`; never reconstruct
trials from budgets, summaries, or fixtures.

Auto-discovery is limited to `docs`, `reports`, and `.research`, and refuses
paths marked holdout/OOT. It reads JSON, Markdown, and supported SQLite tables
directly. It does not import or call strategy generation, research execution,
activation, paper/live orders, exchange clients, or deployment code.
