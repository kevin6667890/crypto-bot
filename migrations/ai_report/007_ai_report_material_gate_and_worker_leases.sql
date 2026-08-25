-- ai-report-production-hardening-v1
-- Automatic generation decisions are isolated from immutable report/audit rows.
CREATE TABLE IF NOT EXISTS ai_report_generation_decisions(
 decision_id TEXT PRIMARY KEY,
 instrument TEXT NOT NULL,
 confirmed_4h_close TEXT NOT NULL,
 material_fingerprint TEXT NOT NULL,
 material_fingerprint_version TEXT NOT NULL,
 canonical_snapshot_identity TEXT,
 facts_as_of TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('EVALUATING','QUEUED','SKIPPED_NO_MATERIAL_CHANGE','ERROR')),
 request_id TEXT,
 claim_owner TEXT,
 claim_until TEXT,
 detail_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(instrument,confirmed_4h_close)
);
CREATE INDEX IF NOT EXISTS idx_ai_generation_decisions_latest
 ON ai_report_generation_decisions(instrument,confirmed_4h_close DESC);
ALTER TABLE ai_report_requests ADD COLUMN worker_claim_owner TEXT;
ALTER TABLE ai_report_requests ADD COLUMN worker_claim_until TEXT;
CREATE INDEX IF NOT EXISTS idx_ai_report_requests_worker_claim
 ON ai_report_requests(worker_claim_until,instrument,created_at);
