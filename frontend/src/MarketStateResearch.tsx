import { useEffect, useMemo, useState } from "react";
import { useLanguage, type TranslationKey } from "./i18n";

type Evidence = { code: string; timeframe: string; value: unknown; weight: number; quality: string; classification: string };
type FrameState = { primary_state: string; role: string; evidence_strength: number; momentum_state: string; overlays: string[]; quality: { status?: string }; limitations: string[] };
type MarketState = {
  version: string; instrument: string; as_of: number; primary_state_code: string;
  evidence_strength: number; timeframes: Record<string, FrameState>;
  cross_timeframe: { state: string; supporting_timeframes: string[]; conflicting_timeframes: string[]; missing_timeframes: string[]; normal_pullback: boolean };
  level_interactions: Array<{ level_type: string; timeframe: string; interaction_type: string; boundary: number; distance_pct: number; quality: string; current_stage: string }>;
  overlays: string[]; transitions: Array<{ from_state: string; to_state: string; transition_timestamp: number; confirmation_status: string }>;
  evidence: Evidence[]; limitations: string[]; quality: { overall_status?: string; stale_sources?: string[]; partial_sources?: string[]; missing_sources?: string[] };
};

const frames = ["1W", "1D", "4H", "1H", "15m"];
const degradedStatusKeys: Record<string, TranslationKey> = {
  UNKNOWN: "state.waitingData", UNAVAILABLE: "state.unavailableFrame", MISSING: "state.unavailableFrame",
  STALE: "state.staleObservation", INSUFFICIENT_DATA: "state.waitingData", PARTIAL: "state.partialCoverage",
  NO_CLEAR_STATE: "state.noClearStructure", LOADING: "common.loading",
};

function statusLabel(value: string | undefined, t: (key: TranslationKey) => string) {
  if (!value) return t("state.waitingData");
  return degradedStatusKeys[value] ? t(degradedStatusKeys[value]) : value.replace(/_/g, " ").toLowerCase().replace(/^./, (letter: string) => letter.toUpperCase());
}

function statusTone(value: string | undefined) {
  if (!value || ["UNKNOWN", "UNAVAILABLE", "MISSING", "STALE", "INSUFFICIENT_DATA", "PARTIAL", "NO_CLEAR_STATE"].includes(value)) return "degraded";
  return "available";
}

export default function MarketStateResearch({ instrument }: { instrument: string }) {
  const { t } = useLanguage();
  const [state, setState] = useState<MarketState | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    fetch(`/api/market/state?instrument=${encodeURIComponent(instrument)}&execution_timeframe=15m`, { signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then(setState)
      .catch((reason) => { if (reason?.name !== "AbortError") setError(String(reason)); });
    return () => controller.abort();
  }, [instrument]);

  const supporting = useMemo(() => state?.evidence.filter((item) => item.classification === "supporting") ?? [], [state]);
  const conflicting = useMemo(() => state?.evidence.filter((item) => item.classification === "conflicting") ?? [], [state]);
  return <main className="market-state-research" data-market-state-page>
    <header className="market-state-heading">
      <div><span className="eyebrow">{t("state.eyebrow")}</span><h1>{t("state.title")}</h1><p>{t("state.description")}</p></div>
      <span className="state-disclaimer">{t("state.disclaimer")}</span>
    </header>
    {error && <section role="alert" className="degraded-notice"><strong>{t("state.temporarilyUnavailable")}</strong><span>{t("state.retryHint")}</span><code>{error}</code></section>}
    {!state && !error && <section className="degraded-notice" role="status"><strong>{t("state.loadingTitle")}</strong><span>{t("state.loadingHelp")}</span></section>}
    {state && <>
      <section className="state-summary-grid">
        <article><span>{t("state.currentStructure")}</span><b className={`human-status ${statusTone(state.primary_state_code)}`}>{statusLabel(state.primary_state_code, t)}</b><small>{t("state.technicalStatus")}: <code>{state.primary_state_code}</code></small></article>
        <article><span>{t("state.evidenceConsistency")}</span><b>{state.evidence_strength.toFixed(1)} / 100</b><small>{t("state.notProbability")}</small></article>
        <article><span>{t("state.crossTimeframe")}</span><b className={`human-status ${statusTone(state.cross_timeframe.state)}`}>{statusLabel(state.cross_timeframe.state, t)}</b><small>{t("state.coverageSummary", { count: state.cross_timeframe.missing_timeframes.length })}</small></article>
        <article><span>{t("state.dataQuality")}</span><b className={`human-status ${statusTone(state.quality.overall_status)}`}>{statusLabel(state.quality.overall_status, t)}</b><small>{t("state.freshnessTracked")}</small></article>
      </section>
      <section className="state-panel"><div className="state-panel-heading"><div><span className="eyebrow">{t("state.multiTimeframe")}</span><h2>{t("state.timeframeStates")}</h2></div><p>{t("state.timeframeHelp")}</p></div><div className="state-frame-grid">
        {frames.map((name) => { const item = state.timeframes[name]; const technical = item?.primary_state || "UNKNOWN"; return <article key={name}><b>{name}</b><strong className={`human-status ${statusTone(technical)}`}>{statusLabel(technical, t)}</strong><span>{item?.role ? item.role.replace(/_/g, " ").toLowerCase() : t("state.awaitingContext")}</span><small>{t("state.technicalStatus")}: <code>{technical}</code>{item?.quality?.status ? ` · ${item.quality.status}` : ""}</small><div>{item?.overlays.map((overlay) => <code key={overlay}>{overlay}</code>)}</div></article>; })}
      </div></section>
      <section className="state-panel"><div className="state-panel-heading"><div><span className="eyebrow">{t("state.structureEvidence")}</span><h2>{t("state.keyLevels")}</h2></div><p>{t("state.keyLevelsHelp")}</p></div><div className="state-table">
        {state.level_interactions.length ? state.level_interactions.map((item, index) => <div key={`${item.level_type}-${item.timeframe}-${index}`}><b>{item.timeframe} · {item.level_type.replace(/_/g, " ")}</b><span>{item.interaction_type === "UNKNOWN" ? t("state.unclassifiedInteraction") : statusLabel(item.interaction_type, t)}</span><span>{item.boundary.toFixed(4)}</span><small>{item.distance_pct.toFixed(3)}% · {item.current_stage.replace(/_/g, " ").toLowerCase()}</small></div>) : <p className="empty-state">{t("state.noLevelInteraction")}</p>}
      </div></section>
      <details className="state-diagnostics"><summary><span><strong>{t("state.diagnostics")}</strong><small>{t("state.diagnosticsHelp")}</small></span><span>{t("state.expand")}</span></summary><div className="state-diagnostics-body">
        <section className="state-columns">
          <article className="state-panel"><h2>{t("state.supportingEvidence")}</h2>{supporting.length ? supporting.map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> <code>{item.code}</code> <small>{item.weight}</small></p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article>
          <article className="state-panel"><h2>{t("state.conflictingEvidence")}</h2>{conflicting.length ? conflicting.map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> <code>{item.code}</code> <small>{item.weight}</small></p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article>
          <article className="state-panel"><h2>{t("state.limitations")}</h2>{state.limitations.length ? state.limitations.map((item) => <p key={item}>{item}</p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article>
        </section>
        <section className="state-panel"><h2>{t("state.overlays")}</h2><div className="state-tags">{state.overlays.map((item) => <code key={item}>{item}</code>)}</div></section>
        <section className="state-panel"><h2>{t("state.transitions")}</h2>{state.transitions.length ? state.transitions.map((item, index) => <p key={`${item.transition_timestamp}-${index}`}><code>{item.from_state}</code> → <code>{item.to_state}</code> · {statusLabel(item.confirmation_status, t)}</p>) : <p className="empty-state">{t("state.noTransition")}</p>}</section>
        <p className="engine-metadata">{t("state.engineMetadata")}: <code>{state.version}</code> · {state.instrument}</p>
      </div></details>
    </>}
  </main>;
}
