-- AI-5 explicit append-only migration for the isolated AI report database.
CREATE TABLE IF NOT EXISTS ai_report_audit_inputs(
 audit_input_id TEXT PRIMARY KEY,report_id TEXT NOT NULL UNIQUE,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_audits(
 audit_id TEXT PRIMARY KEY,report_id TEXT NOT NULL,request_id TEXT NOT NULL,context_id TEXT NOT NULL,report_hash TEXT NOT NULL,
 context_hash TEXT NOT NULL,audit_version TEXT NOT NULL,policy_version TEXT NOT NULL,status TEXT NOT NULL,
 overall_score REAL NOT NULL,promotion_eligible INTEGER NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_audit_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,audit_id TEXT NOT NULL,report_id TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_evaluation_runs(
 evaluation_run_id TEXT PRIMARY KEY,evaluation_version TEXT NOT NULL,policy_version TEXT NOT NULL,manifest_json TEXT NOT NULL,
 manifest_hash TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT,payload_hash TEXT,created_at TEXT NOT NULL,completed_at TEXT);
CREATE TABLE IF NOT EXISTS ai_evaluation_cases(
 evaluation_run_id TEXT NOT NULL,case_id TEXT NOT NULL,manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(evaluation_run_id,case_id));
CREATE TABLE IF NOT EXISTS ai_evaluation_results(
 evaluation_run_id TEXT NOT NULL,case_id TEXT NOT NULL,audit_id TEXT NOT NULL,expected_status TEXT NOT NULL,actual_status TEXT NOT NULL,
 payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(evaluation_run_id,case_id));
CREATE INDEX IF NOT EXISTS idx_ai_audits_report_latest ON ai_report_audits(report_id,created_at DESC,audit_id DESC);
CREATE INDEX IF NOT EXISTS idx_ai_audit_events_identity ON ai_report_audit_events(audit_id,event_id);
CREATE INDEX IF NOT EXISTS idx_ai_audit_events_report ON ai_report_audit_events(report_id,event_id);
CREATE INDEX IF NOT EXISTS idx_ai_evaluation_results_run ON ai_evaluation_results(evaluation_run_id,case_id);
