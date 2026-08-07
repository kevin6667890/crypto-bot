import { formatTime, shortId, safeEnum } from "./formatters";
import type { Presentation } from "./types";

export function ReportHeader({ value, locale, labels }: { value: Presentation; locale: string; labels: Record<string, string> }) {
  const report = value.report!;
  const freshness = labels[value.freshness.status.toLowerCase()] || value.freshness.status;
  return <header className="ama-report-header">
    <div><span className="ama-shadow">{labels.shadow}</span><span className={`ama-fresh ama-${value.freshness.status.toLowerCase()}`}>{freshness}</span></div>
    <h1>{value.instrument} <small>{value.mode}</small></h1><p className="ama-headline">{report.headline}</p>
    <div className="ama-status-grid">
      <span>Audit<strong>{labels.onlyCandidate}</strong></span><span>Freshness<strong>{freshness}</strong></span>
      <span>Bias<strong>{safeEnum(report.directional_bias)}</strong></span><span>Confidence<strong>{safeEnum(report.confidence)}</strong></span>
      <span>Phase<strong>{safeEnum(report.market_phase)}</strong></span><span>Quality<strong>{value.freshness.quality || "UNKNOWN"}</strong></span>
      <span>Decision time<time dateTime={value.decision_time}>{formatTime(value.decision_time, locale)}</time></span>
      <span>Latest confirmed<time>{formatTime(value.latest_confirmed_market_time, locale)}</time></span>
    </div>
    <div className="ama-ids"><code title={value.report_id}>report {shortId(value.report_id)}</code><code title={value.context_id}>context {shortId(value.context_id)}</code><code title={value.audit_id || ""}>audit {shortId(value.audit_id)}</code></div>
  </header>;
}
