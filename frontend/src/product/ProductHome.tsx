import { ArrowRight, Clock3, FlaskConical, History } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "../i18n";
import { fetchThesisChanges } from "../tracking/api";
import type { ChangeBundle } from "../tracking/types";
import { formatStatus, formatUtc } from "../tracking/state";
import { productText } from "./i18n";

export default function ProductHome() {
  const { language } = useLanguage();
  const text = productText(language);
  const [changes, setChanges] = useState<ChangeBundle[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    fetchThesisChanges(controller.signal, 3).then((value) => setChanges(value.changes)).catch(() => undefined);
    return () => controller.abort();
  }, []);
  const entries = [
    { href: "/what-changed", icon: <Clock3 />, title: text.changedTitle, body: text.changedBody },
    { href: "/test-an-idea", icon: <FlaskConical />, title: text.testTitle, body: text.testBody, primary: true },
    { href: "/tracking", icon: <History />, title: text.trackingTitle, body: text.trackingBody },
  ];
  return <main className="product-home">
    <section className="product-hero">
      <span className="product-eyebrow">{text.eyebrow}</span><h1>{text.hero}</h1><p>{text.subtitle}</p>
      <div className="product-actions"><a className="product-button primary" href="/test-an-idea">{text.start}<ArrowRight size={17} /></a><a className="product-button" href="/what-changed">{text.seeChanges}</a></div>
    </section>
    <section className="product-entry-section"><h2>{text.entries}</h2><div className="product-entry-grid">{entries.map((entry) => <a className={entry.primary ? "primary" : ""} href={entry.href} key={entry.href}>{entry.icon}<h3>{entry.title}</h3><p>{entry.body}</p><ArrowRight className="entry-arrow" size={17} /></a>)}</div></section>
    <section className="product-recent"><div><span className="product-eyebrow">{text.recent}</span><a href="/what-changed">{text.viewAll}</a></div>
      {changes.length ? changes.map(({ track, evaluation }) => <a href={`/tracking/${encodeURIComponent(track.track_id)}`} key={evaluation.evaluation_id}><strong>{track.thesis_spec.instrument} · {track.thesis_spec.timeframe}</strong><span>{formatStatus(evaluation.delta?.previous_status)} → {formatStatus(evaluation.overall_status)}</span><time>{formatUtc(evaluation.evaluated_at, language)}</time></a>) : <p>{text.noRecent}</p>}
    </section>
  </main>;
}
