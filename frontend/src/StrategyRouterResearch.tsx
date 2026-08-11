import { useEffect, useState } from "react";
import { useLanguage, type TranslationKey } from "./i18n";

type Evidence = { code: string; dimension: string; timeframe: string; detail: string; strength: string };
type Blocker = { code: string; timeframe: string; release_condition: string };
type Candidate = {
  family: string; direction: "LONG" | "SHORT"; state: string; score: number;
  score_breakdown: Record<string, number>; evidence_strength: number;
  supporting_evidence: Evidence[]; conflicting_evidence: Evidence[]; blockers: Blocker[];
  next_confirmation: string[]; limitations: string[]; identity_hash: string;
  identity: { strategy_family_id: string; strategy_setup_id: string; strategy_evaluation_id: string; strategy_version: string; parameter_set_version: string };
  source_timestamps: number[];
  geometry: { valid: boolean; setup_zone: { reference?: string; timeframe?: string }; trigger_boundary: { reference?: string }; invalidation_reference: { reference?: string }; stop_reference_type: string; target_reference_types: string[]; minimum_structural_reward_risk: number; structural_reward_risk: number | null; confirmation_rule: string[] };
  selection_reason: string;
};
type Route = {
  version: string; definitions_version: string; instrument: string; as_of: number;
  primary_route: Candidate | null; alternatives: Candidate[]; candidates: Candidate[];
  no_trade: { active: boolean; strategy_version: string; reasons: Array<{ code: string; timeframe: string; evidence: string[]; temporary: boolean; release_condition: string }> };
  quality: { overall_status?: string; degraded?: boolean }; transitions: Array<{ from_state: string; to_state: string; reason: string }>;
  disclaimer: string;
};
const dimensions = ["environment", "structure", "setup", "trigger", "data_quality"];

function technicalLabel(value: string) {
  return value.replace(/_/g, " ").toLowerCase().replace(/^./, (letter: string) => letter.toUpperCase());
}

const familyKeys: Record<string, TranslationKey> = {
  TREND_PULLBACK: "router.family.trendPullback", MA200_MEAN_REVERSION: "router.family.meanReversion",
  BREAKOUT_CONTINUATION: "router.family.breakoutContinuation", FAILED_BREAKOUT_REVERSAL: "router.family.failedBreakout",
};
const explanationKeys: Record<string, TranslationKey> = {
  "price reaches a confirmed structural boundary": "router.explanation.structuralBoundary",
  "confirmed expansion and boundary event": "router.explanation.expansionBoundary",
  "wait for valid trigger, invalidation, and opposing structural references": "router.explanation.validGeometry",
  "next listed confirmation completes": "router.explanation.nextConfirmation",
  "second confirmed close, boundary retest, then 15m continuation; first breach never triggers": "router.explanation.secondClose",
  "CVD unavailable": "router.explanation.cvdUnavailable", "OI unavailable": "router.explanation.oiUnavailable",
  "flow missing; price structure remains evaluable at reduced completeness": "router.explanation.flowMissing",
  "partial sources are weak evidence only": "router.explanation.partialWeak",
  "stale sources are excluded from scoring": "router.explanation.staleExcluded",
  "lower lifecycle/event priority or completion": "router.explanation.lowerPriority",
};

function familyLabel(value: string, t: (key: TranslationKey) => string) { return familyKeys[value] ? t(familyKeys[value]) : technicalLabel(value); }
function explanation(value: string, t: (key: TranslationKey) => string) { return explanationKeys[value] ? t(explanationKeys[value]) : value; }

function directionLabel(direction: Candidate["direction"], t: (key: TranslationKey) => string) {
  return t(direction === "LONG" ? "router.long" : "router.short");
}

function CandidateCard({ candidate, primary = false }: { candidate: Candidate; primary?: boolean }) {
  const { t } = useLanguage();
  return <article className={`router-candidate ${primary ? "primary" : ""}`}>
    <header><div><span className="eyebrow">{primary ? t("router.selectedRoute") : t("router.alternative")}</span><h2>{familyLabel(candidate.family, t)} · {directionLabel(candidate.direction, t)}</h2><small className="technical-id">{candidate.family} · {candidate.direction}</small></div><span className={`route-status ${candidate.state.toLowerCase()}`}>{t(`enum.${candidate.state}` as TranslationKey)}</span></header>
    <div className="router-scores">
      {dimensions.map((name) => <div key={name}><span>{t(`router.dimension.${name}` as TranslationKey)}</span><b>{candidate.score_breakdown[name] ?? 0}</b></div>)}
      <div className="emphasis"><span>{t("router.completion")}</span><b>{candidate.score}</b></div><div className="emphasis"><span>{t("router.consistency")}</span><b>{candidate.evidence_strength}</b></div>
    </div>
    <p className="router-note">{t("router.scoreHelp")}</p>
    <div className="router-columns">
      <section><h3>{t("router.supporting")}</h3>{candidate.supporting_evidence.length ? candidate.supporting_evidence.map((item) => <p key={`${item.code}-${item.timeframe}`}><b>{item.timeframe}</b> · <code>{item.code}</code><small>{item.detail}</small></p>) : <p className="empty-state">{t("router.noSupporting")}</p>}</section>
      <section><h3>{t("router.blockers")}</h3>{candidate.conflicting_evidence.map((item) => <p key={`${item.code}-${item.timeframe}`}><b>{item.timeframe}</b> · <code>{item.code}</code></p>)}{candidate.blockers.map((item) => <p key={`${item.code}-${item.timeframe}`}><code>{item.code}</code><small>{explanation(item.release_condition, t)}</small></p>)}{!candidate.conflicting_evidence.length && !candidate.blockers.length && <p className="empty-state">{t("router.noBlockers")}</p>}</section>
      <section><h3>{t("router.nextConfirmation")}</h3>{candidate.next_confirmation.length ? candidate.next_confirmation.map((item) => <p key={item}>{explanation(item, t)}</p>) : <p className="empty-state">{t("router.noConfirmation")}</p>}</section>
    </div>
    <details className="router-technical"><summary>{t("router.technicalDetails")}</summary><section className="router-geometry"><h3>{t("router.geometry")}</h3><p>{t("router.setup")}: {candidate.geometry.setup_zone.reference || t("common.notAvailable")} · {t("router.trigger")}: {candidate.geometry.trigger_boundary.reference || t("common.notAvailable")} · {t("router.invalidation")}: {candidate.geometry.invalidation_reference.reference || t("common.notAvailable")}</p><p>{t("router.minimumStructuralR")}: {candidate.geometry.minimum_structural_reward_risk} · {t("router.observed")}: {candidate.geometry.structural_reward_risk ?? t("common.notAvailable")} · {candidate.geometry.valid ? t("router.valid") : t("router.waitingGeometry")}</p></section><code>{candidate.identity.strategy_version} · {candidate.identity.parameter_set_version}</code><p>{candidate.identity.strategy_family_id}</p><p>{candidate.identity.strategy_setup_id}</p><p>{candidate.identity.strategy_evaluation_id}</p></details>
    {candidate.limitations.length > 0 && <details className="router-limit"><summary>{t("router.limitations", { count: candidate.limitations.length })}</summary>{candidate.limitations.map((item) => <p key={item}>{explanation(item, t)}</p>)}</details>}{!primary && <small>{explanation(candidate.selection_reason, t)}</small>}
  </article>;
}

export default function StrategyRouterResearch({ instrument }: { instrument: string }) {
  const { t } = useLanguage();
  const [route, setRoute] = useState<Route | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); setError("");
    fetch(`/api/strategy/route?instrument=${encodeURIComponent(instrument)}&execution_timeframe=15m`, { signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(setRoute)
      .catch((reason) => { if (reason?.name !== "AbortError") setError(String(reason)); }); return () => controller.abort(); }, [instrument]);
  return <main className="strategy-router-research" data-strategy-router-page>
    <header className="router-heading"><div><span className="eyebrow">{t("router.eyebrow")}</span><h1>{t("router.title")}</h1><p>{t("router.description")}</p></div><span className="state-disclaimer">{t("router.disclaimer")}</span></header>
    {error && <section role="alert" className="degraded-notice"><strong>{t("router.temporarilyUnavailable")}</strong><span>{t("state.retryHint")}</span><code>{error}</code></section>}
    {!route && !error && <section className="degraded-notice" role="status"><strong>{t("router.loadingTitle")}</strong><span>{t("router.loadingHelp")}</span></section>}
    {route && <>
      <section className={`router-no-trade ${route.no_trade.active ? "active" : "clear"}`}><div><span className="eyebrow">{t("router.routeStatus")}</span><h2>{route.no_trade.active ? t("router.noTradeActive") : t("router.routeEligible")}</h2><p>{route.no_trade.active ? t("router.noTradeHelp") : t("router.eligibleHelp")}</p></div><span className={`route-status ${route.no_trade.active ? "blocked" : "eligible"}`}>{route.no_trade.active ? t("router.paused") : t("router.eligible")}</span>{route.no_trade.reasons.length > 0 && <div className="router-reasons">{route.no_trade.reasons.map((item) => <p key={`${item.code}-${item.timeframe}`}><b>{item.timeframe}</b><code>{item.code}</code><span>{explanation(item.release_condition, t)}</span><small>{item.temporary ? t("router.temporary") : t("router.persistent")}</small></p>)}</div>}</section>
      {route.primary_route ? <CandidateCard candidate={route.primary_route} primary /> : <section className="degraded-notice"><strong>{t("router.noSelectedRoute")}</strong><span>{t("router.noSelectedHelp")}</span></section>}
      {route.alternatives.length > 0 && <details className="router-alternatives"><summary>{t("router.alternatives", { count: route.alternatives.length })}</summary><div className="router-list">{route.alternatives.map((item) => <CandidateCard candidate={item} key={item.identity_hash} />)}</div></details>}
      <details className="state-diagnostics"><summary><span><strong>{t("router.lifecycle")}</strong><small>{t("router.lifecycleHelp")}</small></span><span>{t("state.expand")}</span></summary><div className="state-panel">{route.transitions.length ? route.transitions.map((item, index) => <p key={`${item.from_state}-${index}`}><code>{item.from_state}</code> → <code>{item.to_state}</code> · {item.reason}</p>) : <p className="empty-state">{t("state.noTransition")}</p>}<p className="engine-metadata">{t("state.engineMetadata")}: <code>{route.version}</code> · <code>{route.definitions_version}</code></p></div></details>
    </>}
  </main>;
}
