-- ai-market-report-db-v1
-- Isolated AI report database. This file is the schema source of truth.
CREATE TABLE IF NOT EXISTS ai_report_migrations(
 migration_key TEXT PRIMARY KEY,
 schema_version TEXT NOT NULL,
 file_sha256 TEXT NOT NULL,
 completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_market_contexts(
 context_id TEXT PRIMARY KEY,base_context_id TEXT NOT NULL,schema_version TEXT NOT NULL,instrument TEXT NOT NULL,
 decision_time TEXT NOT NULL,position_fingerprint TEXT NOT NULL,macro_evidence_set_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,source_versions_json TEXT NOT NULL,quality TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_position_plans(
 plan_id TEXT PRIMARY KEY,plan_version TEXT NOT NULL,instrument TEXT NOT NULL,source TEXT NOT NULL,effective_at TEXT NOT NULL,
 supersedes_plan_id TEXT,status TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_macro_evidence_sets(
 evidence_set_id TEXT PRIMARY KEY,decision_time TEXT NOT NULL,item_count INTEGER NOT NULL,categories_json TEXT NOT NULL,
 payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,quality TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_requests(
 request_id TEXT PRIMARY KEY,request_identity TEXT NOT NULL UNIQUE,context_id TEXT NOT NULL,instrument TEXT NOT NULL,
 mode TEXT NOT NULL,language TEXT NOT NULL,prompt_version TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,
 max_output_tokens INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_request_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_report_attempts(
 attempt_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,attempt_number INTEGER NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,
 started_at TEXT NOT NULL,completed_at TEXT,latency_ms INTEGER,http_status INTEGER,input_tokens INTEGER,output_tokens INTEGER,
 total_tokens INTEGER,finish_reason TEXT,raw_response_hash TEXT,parse_status TEXT,validation_status TEXT,failure_code TEXT,
 sanitized_error TEXT,cost_status TEXT NOT NULL,currency TEXT,price_schedule_version TEXT,estimated_cost REAL,
 UNIQUE(request_id,attempt_number));
CREATE TABLE IF NOT EXISTS ai_market_reports(
 report_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,context_id TEXT NOT NULL,mode TEXT NOT NULL,language TEXT NOT NULL,
 response_json TEXT NOT NULL,response_hash TEXT NOT NULL,model TEXT NOT NULL,provider TEXT NOT NULL,prompt_version TEXT NOT NULL,
 generated_text TEXT NOT NULL,audit_status TEXT NOT NULL CHECK(audit_status='PENDING'),created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_report_events_request ON ai_report_request_events(request_id,event_id);
CREATE INDEX IF NOT EXISTS idx_reports_latest ON ai_market_reports(context_id,created_at DESC);
