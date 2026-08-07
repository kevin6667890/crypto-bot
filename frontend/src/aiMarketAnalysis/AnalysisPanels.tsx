import { isSafeHttpUrl } from "./types";
import { safeEnum, valueText } from "./formatters";
import type { Presentation } from "./types";

const factValue = (value: Presentation, category: string) => value.referenced_facts.filter((fact) => fact.category === category);
export function TimeframeMatrix({ value, title }: { value: Presentation; title: string }) {
  const rows = ["TF15_SUMMARY","TF1H_SUMMARY","TF4H_SUMMARY","TF1D_SUMMARY","TF1W_SUMMARY"].map((id) => value.referenced_facts.find((f) => f.fact_id === id));
  return <section className="ama-panel"><h2>{title}</h2><div className="ama-table-scroll" tabIndex={0} role="region" aria-label={title}><table><caption>{title}</caption><thead><tr><th>Timeframe</th><th>Trend / structure / close / MA / momentum / volume / levels / invalidation / quality</th></tr></thead><tbody>{rows.map((fact,index) => <tr key={index}><th>{["15m","1H","4H","1D","1W"][index]}</th><td>{fact ? valueText(fact.value) : "MISSING_DATA"}<small>{fact?.quality}</small></td></tr>)}</tbody></table></div></section>;
}
export function Timeline({ value, title }: { value: Presentation; title: string }) {
  const events = factValue(value, "EVENT").concat(value.referenced_facts.filter((f) => f.fact_id.startsWith("EVENT_")));
  return <section className="ama-panel"><h2>{title}</h2><div className="ama-card-grid">{events.map((event) => <article key={event.fact_id}><span className="ama-modality ama-modality-derived">■ DETERMINISTIC</span><strong>{event.label || event.fact_id}</strong><p>{valueText(event.value)}</p><small>{event.timestamp} · {event.quality}</small></article>)}</div></section>;
}
export function OrderFlow({ value, title }: { value: Presentation; title: string }) {
  const phases = value.referenced_facts.filter((f) => f.fact_id.startsWith("FLOW_PHASE"));
  return <section className="ama-panel"><h2>{title}</h2><div className="ama-card-grid">{phases.map((phase) => <article key={phase.fact_id}><span className="ama-modality ama-modality-derived">■ DETERMINISTIC</span><strong>{phase.label || phase.fact_id}</strong><pre>{valueText(phase.value)}</pre>{/GAP|PARTIAL/.test(valueText(phase.value)) && <p className="ama-warning">MISSING_DATA · GAP/PARTIAL</p>}</article>)}</div></section>;
}
export function Levels({ value, title }: { value: Presentation; title: string }) {
  return <section className="ama-panel"><h2>{title}</h2><div className="ama-card-grid">{value.referenced_levels.map((level,index) => <article className={`ama-level ama-strength-${String(level.asserted_strength || "UNKNOWN").toLowerCase()}`} key={String(level.level_id || index)}><strong>{safeEnum(level.asserted_role)} · {safeEnum(level.asserted_state)}</strong><p>{String(level.analysis_text || "")}</p><small>{level.asserted_dynamic ? "Dynamic position" : "Static zone"} · {safeEnum(level.asserted_strength)} · {safeEnum(level.asserted_timeframe)}</small></article>)}</div></section>;
}
export function Scenarios({ value, title }: { value: Presentation; title: string }) {
  return <section className="ama-panel"><h2>{title}</h2>{value.referenced_scenarios.map((scenario,index) => <details className="ama-scenario" key={String(scenario.scenario_id || index)}><summary><strong>{safeEnum(scenario.scenario_type)}</strong><span>{safeEnum(scenario.direction)} · evidence tendency {safeEnum(scenario.likelihood)}</span></summary><dl>{["trigger_text","confirmation_text","expected_path_text","target_level_refs","invalidation_text","invalidation_timeframe","confirmed_close_required","volume_confirmation_text","cvd_confirmation_text","oi_confirmation_text","funding_basis_confirmation_text","contradicting_evidence_text"].map((key) => <div key={key}><dt>{safeEnum(key)}</dt><dd>{valueText(scenario[key])}</dd></div>)}</dl></details>)}</section>;
}
export function Macro({ value, title, empty }: { value: Presentation; title: string; empty: string }) {
  return <section className="ama-panel"><h2>{title}</h2>{value.referenced_macro.length ? value.referenced_macro.map((item,index) => <article key={index}><strong>{String(item.title || item.publisher || "Evidence")}</strong><p>{String(item.factual_summary || "")}</p>{isSafeHttpUrl(item.url) && <a href={item.url} target="_blank" rel="noopener noreferrer">Verified source (opens new tab)</a>}</article>) : <p>{empty}</p>}</section>;
}
export function AuditPanel({ value, title }: { value: Presentation; title: string }) {
  const audit=value.audit_summary!; return <section className="ama-panel"><h2>{title}</h2><strong className="ama-score">{audit.overall_score ?? "—"}</strong><p>{audit.status} · Shadow only · policy {audit.policy_version}</p>{audit.hard_failure_count > 0 && <div className="ama-critical" role="alert">Hard failures: {audit.hard_failure_count} · {audit.hard_failures.join(", ")}</div>}<dl className="ama-ratios">{Object.entries(audit.ratios || {}).map(([key,val]) => <div key={key}><dt>{safeEnum(key)}</dt><dd>{String(val)}</dd></div>)}</dl></section>;
}
