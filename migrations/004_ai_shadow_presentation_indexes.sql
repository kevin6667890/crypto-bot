-- AI-6A: bounded read indexes only. Applying this migration is explicit and is
-- not part of the Shadow UI build or test startup path.
CREATE INDEX IF NOT EXISTS idx_ai_requests_presentation
 ON ai_report_requests(instrument,mode,language,created_at DESC,request_id);
CREATE INDEX IF NOT EXISTS idx_ai_reports_presentation
 ON ai_market_reports(mode,language,created_at DESC,report_id);
CREATE INDEX IF NOT EXISTS idx_ai_contexts_watermark
 ON ai_market_contexts(instrument,decision_time DESC,context_id);
