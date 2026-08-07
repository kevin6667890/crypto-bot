-- ai-shadow-presentation-indexes-v1
CREATE INDEX IF NOT EXISTS idx_ai_requests_presentation
 ON ai_report_requests(instrument,mode,language,created_at DESC,request_id DESC);
CREATE INDEX IF NOT EXISTS idx_ai_reports_presentation
 ON ai_market_reports(mode,language,created_at DESC,report_id DESC);
CREATE INDEX IF NOT EXISTS idx_ai_contexts_watermark
 ON ai_market_contexts(instrument,decision_time DESC,context_id DESC);
