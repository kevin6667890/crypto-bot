-- global-research-registry-schema-v1
-- Runtime migration source of truth is SCHEMA_SQL in
-- dashboard/global_research_registry.py.  This checked-in schema documents the
-- database contract without committing the local .research/*.db artifact.

CREATE TABLE IF NOT EXISTS registry_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs(
    id INTEGER PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    phase TEXT NOT NULL,
    source_run_identity TEXT NOT NULL,
    dataset_identity TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    grammar_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    chronological_segment TEXT NOT NULL,
    run_status TEXT NOT NULL,
    raw_trial_count INTEGER,
    statistical_test_count INTEGER,
    effective_cluster_count INTEGER,
    selection_count INTEGER,
    locked_count INTEGER,
    final_classification TEXT NOT NULL,
    report_path TEXT,
    report_hash TEXT,
    ledger_path TEXT,
    ledger_hash TEXT,
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    import_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    missing_fields TEXT NOT NULL,
    import_state TEXT NOT NULL,
    locked_data_accessed INTEGER,
    unrecoverable_trials TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_runs_phase
    ON research_runs(phase);

CREATE TABLE IF NOT EXISTS research_trials(
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES research_runs(id),
    source_trial_identity TEXT NOT NULL,
    strategy_identity TEXT NOT NULL,
    factor_identity TEXT NOT NULL,
    program_identity TEXT NOT NULL,
    canonical_entity_key TEXT NOT NULL,
    canonical_trial_key TEXT NOT NULL,
    parent_identity TEXT NOT NULL,
    dataset_identity TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    grammar_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    instrument TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    horizon TEXT NOT NULL,
    chronological_segment TEXT NOT NULL,
    trial_status TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    raw_attempt INTEGER NOT NULL CHECK(raw_attempt IN (0,1)),
    statistically_evaluated INTEGER NOT NULL CHECK(statistically_evaluated IN (0,1)),
    effective_cluster_identity TEXT NOT NULL,
    entered_selection INTEGER NOT NULL CHECK(entered_selection IN (0,1)),
    entered_locked INTEGER NOT NULL CHECK(entered_locked IN (0,1)),
    final_classification TEXT NOT NULL,
    created_at TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    import_source TEXT NOT NULL,
    confidence TEXT NOT NULL,
    missing_fields TEXT NOT NULL,
    raw_metadata TEXT NOT NULL,
    UNIQUE(run_id, source_trial_identity)
);

CREATE INDEX IF NOT EXISTS idx_research_trials_canonical
    ON research_trials(canonical_trial_key);
CREATE INDEX IF NOT EXISTS idx_research_trials_status
    ON research_trials(trial_status);

CREATE TABLE IF NOT EXISTS import_receipts(
    source_hash TEXT NOT NULL,
    phase TEXT NOT NULL,
    run_key TEXT NOT NULL,
    source_path TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(source_hash,phase,run_key)
);

INSERT OR REPLACE INTO registry_meta(key,value)
VALUES('registry_version','global-research-registry-v1');
INSERT OR REPLACE INTO registry_meta(key,value)
VALUES('schema_version','global-research-registry-schema-v1');
