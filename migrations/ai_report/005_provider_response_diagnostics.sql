-- ai-report-attempt-diagnostics-v1
-- Sanitized response evidence is isolated from immutable report promotion records.
CREATE TABLE IF NOT EXISTS ai_report_attempt_diagnostics(
 attempt_id TEXT PRIMARY KEY,
 request_id TEXT NOT NULL,
 raw_response_hash TEXT NOT NULL,
 sanitized_raw_response TEXT NOT NULL,
 normalized_response_json TEXT,
 parse_diagnostic_json TEXT NOT NULL,
 validation_diagnostic_json TEXT NOT NULL,
 sanitizer_version TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_attempt_diagnostics_request
 ON ai_report_attempt_diagnostics(request_id,attempt_id);
