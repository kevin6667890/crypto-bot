import { Activity, ArrowRight, BrainCircuit, Gauge, RadioTower, TerminalSquare } from "lucide-react";
import { useLanguage } from "../i18n";

const entries = [
  { id: "workspace", icon: TerminalSquare },
  { id: "market", icon: Activity },
  { id: "research", icon: BrainCircuit },
  { id: "microstructure", icon: RadioTower },
  { id: "operations", icon: Gauge },
] as const;

export default function AdvancedLanding() {
  const { t } = useLanguage();
  const copy = (key: string) => t(key as never);

  return <main className="advanced-landing">
    <section className="advanced-hero">
      <span className="eyebrow">{copy("advanced.eyebrow")}</span>
      <h1>{copy("advanced.title")}</h1>
      <p>{copy("advanced.description")}</p>
    </section>
    <nav className="advanced-entry-grid" aria-label={copy("advanced.secondaryNav")}>
      {entries.map(({ id, icon: Icon }) => <a className="advanced-entry-card" href={`/advanced#${id}`} key={id}>
        <span className="advanced-entry-icon" aria-hidden="true"><Icon size={20} /></span>
        <h2>{copy(`advanced.${id}.title`)}</h2>
        <p>{copy(`advanced.${id}.description`)}</p>
        <ArrowRight size={17} aria-hidden="true" />
      </a>)}
    </nav>
  </main>;
}
