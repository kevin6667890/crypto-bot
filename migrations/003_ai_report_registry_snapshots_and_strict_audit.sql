-- AI-5C explicit, append-only migration for immutable registry snapshots.
-- Existing installations add request/attempt columns through the migration
-- runner's PRAGMA-guarded ALTER statements because SQLite has no portable
-- ADD COLUMN IF NOT EXISTS syntax.
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS ai_report_registry_snapshots(
 registry_snapshot_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL UNIQUE,
 report_context_id TEXT NOT NULL,
 enriched_context_id TEXT NOT NULL,
 snapshot_version TEXT NOT NULL,
 fact_registry_version TEXT NOT NULL,
 numeric_registry_version TEXT NOT NULL,
 fact_registry_json TEXT NOT NULL,
 fact_registry_hash TEXT NOT NULL,
 numeric_registry_json TEXT NOT NULL,
 numeric_registry_hash TEXT NOT NULL,
 prompt_hash TEXT NOT NULL,
 source_versions_json TEXT NOT NULL,
 source_versions_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_registry_snapshot_request ON ai_report_registry_snapshots(request_id);
CREATE INDEX IF NOT EXISTS idx_ai_registry_snapshot_context ON ai_report_registry_snapshots(enriched_context_id,registry_snapshot_id);
CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_update BEFORE UPDATE ON ai_report_registry_snapshots BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END;
CREATE TRIGGER IF NOT EXISTS trg_ai_registry_snapshot_no_delete BEFORE DELETE ON ai_report_registry_snapshots BEGIN SELECT RAISE(ABORT,'REGISTRY_SNAPSHOT_MUTATED'); END;
COMMIT;
