import { EvidenceDrawer } from "./EvidenceDrawer";
import type { Presentation } from "./types";

const ORDER = ["CONCLUSION","MACRO_BACKGROUND","RECENT_PROCESS","MOVE_NATURE","TF_15M","TF_1H","TF_4H","TF_1D","TF_1W","ORDER_FLOW","KEY_LEVELS","SCENARIOS","LIMITATIONS","POSITION_PLAN"];
export function orderedSections<T extends { section_id: string }>(sections: T[]) { return [...sections].sort((a,b) => ORDER.indexOf(a.section_id) - ORDER.indexOf(b.section_id)); }
export function ReportSections({ value, labels }: { value: Presentation; labels: Record<string, string> }) {
  return <section className="ama-sections" aria-label={labels.synthesis}>{orderedSections(value.report!.sections).map((section) =>
    <article key={section.section_id} id={`section-${section.section_id.toLowerCase()}`}>
      <span className="ama-modality ama-modality-ai" aria-label={labels.synthesis}>● {labels.synthesis}</span><h2>{section.title}</h2>
      <p>{section.body}</p>
      {section.uncertainties?.map((item) => <p className="ama-uncertainty" key={item}><span aria-label={labels.uncertainty}>△ {labels.uncertainty}</span> {item}</p>)}
      <EvidenceDrawer section={section} facts={value.referenced_facts} labels={{ evidence: labels.evidence, facts: labels.facts }} />
    </article>)}</section>;
}
