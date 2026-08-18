-- ai-report-attempt-lifecycle-v1
-- Paid-call observability: attempt identity is persisted at the send boundary and
-- progressively updated. Transport failure and billing uncertainty stay separate:
-- lifecycle_state tracks SUBMITTED/RESPONSE_HEADERS_RECEIVED/BODY_STREAMING/
-- USAGE_RECONCILED/SUCCEEDED/FAILED/UNKNOWN; charge_state stays a second dimension.
ALTER TABLE ai_report_attempts ADD COLUMN lifecycle_state TEXT;
ALTER TABLE ai_report_attempts ADD COLUMN charge_state TEXT;
