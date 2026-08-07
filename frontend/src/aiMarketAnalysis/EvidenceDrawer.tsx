import { valueText } from "./formatters";
import type { Fact, Section } from "./types";

export function EvidenceDrawer({ section, facts, labels }: { section: Section; facts: Fact[]; labels: { evidence: string; facts: string } }) {
  const visible = facts.filter((fact) => section.fact_refs.includes(fact.fact_id));
  return <details className="ama-evidence">
    <summary aria-label={`${labels.evidence}: ${section.title}`}>{labels.evidence} <span>({visible.length})</span></summary>
    <ul aria-label={labels.facts}>{visible.map((fact) => <li key={fact.fact_id}>
      <div><span className="ama-modality ama-modality-fact" aria-label={labels.facts}>◆ {labels.facts}</span><code>{fact.fact_id}</code></div>
      <strong>{fact.label || fact.category || fact.fact_id}</strong><span>{valueText(fact.display_value ?? fact.value)} {fact.unit || ""}</span>
      <small>{[fact.timestamp, fact.quality, fact.source, fact.context_pointer].filter(Boolean).join(" · ")}</small>
    </li>)}</ul>
  </details>;
}
