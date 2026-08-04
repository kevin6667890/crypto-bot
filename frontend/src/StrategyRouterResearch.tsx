import { useEffect, useState } from "react";

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

function CandidateCard({ candidate, primary = false }: { candidate: Candidate; primary?: boolean }) {
  return <article className={`router-candidate ${primary ? "primary" : ""}`}>
    <header><div><span>{primary ? "PRIMARY ROUTE" : "ALTERNATIVE"}</span><h2>{candidate.family} · {candidate.direction}</h2></div><strong>{candidate.state}</strong></header>
    <div className="router-scores">
      {dimensions.map((name) => <div key={name}><span>{name}</span><b>{candidate.score_breakdown[name] ?? 0}</b></div>)}
      <div><span>total completion</span><b>{candidate.score}</b></div><div><span>evidence consistency</span><b>{candidate.evidence_strength}</b></div>
    </div>
    <p className="router-note">评分表示条件完成度、证据一致度和数据完整度，不表示胜率或盈利概率。</p>
    <div className="router-columns">
      <section><h3>Supporting evidence</h3>{candidate.supporting_evidence.map((item) => <p key={`${item.code}-${item.timeframe}`}>{item.timeframe} · {item.code} <small>{item.detail}</small></p>)}</section>
      <section><h3>Conflicting evidence / blockers</h3>{candidate.conflicting_evidence.map((item) => <p key={`${item.code}-${item.timeframe}`}>{item.timeframe} · {item.code}</p>)}{candidate.blockers.map((item) => <p key={`${item.code}-${item.timeframe}`}><b>{item.code}</b> · {item.release_condition}</p>)}</section>
      <section><h3>Next confirmation</h3>{candidate.next_confirmation.map((item) => <p key={item}>{item}</p>)}</section>
    </div>
    <section className="router-geometry"><h3>Geometry references</h3><p>Setup: {candidate.geometry.setup_zone.reference || "UNAVAILABLE"} · Trigger: {candidate.geometry.trigger_boundary.reference || "UNAVAILABLE"} · Invalidation: {candidate.geometry.invalidation_reference.reference || "UNAVAILABLE"}</p><p>Stop reference: {candidate.geometry.stop_reference_type} · Targets: {candidate.geometry.target_reference_types.join(", ")}</p><p>Minimum structural R: {candidate.geometry.minimum_structural_reward_risk} · Observed: {candidate.geometry.structural_reward_risk ?? "UNAVAILABLE"} · {candidate.geometry.valid ? "VALID" : "INVALID"}</p></section>
    <details><summary>Strategy identity and source timestamps</summary><code>{candidate.identity.strategy_version} · {candidate.identity.parameter_set_version}</code><p>{candidate.identity.strategy_family_id}</p><p>{candidate.identity.strategy_setup_id}</p><p>{candidate.identity.strategy_evaluation_id}</p><p>{candidate.source_timestamps.join(", ")}</p></details>
    {candidate.limitations.map((item) => <p className="router-limit" key={item}>{item}</p>)}{!primary && <small>{candidate.selection_reason}</small>}
  </article>;
}

export default function StrategyRouterResearch({ instrument }: { instrument: string }) {
  const [route, setRoute] = useState<Route | null>(null); const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); setError("");
    fetch(`/api/strategy/route?instrument=${encodeURIComponent(instrument)}&execution_timeframe=15m`, { signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(setRoute)
      .catch((reason) => { if (reason?.name !== "AbortError") setError(String(reason)); }); return () => controller.abort(); }, [instrument]);
  return <main className="strategy-router-research" data-strategy-router-page>
    <header className="router-heading"><div><span className="eyebrow">STRATEGY ROUTER V2</span><h1>{route?.primary_route ? `${route.primary_route.family} · ${route.primary_route.direction}` : "NO_TRADE"}</h1><p>{route?.version} · {route?.definitions_version}</p></div><strong>研究策略路由，不是实时交易建议，当前未连接Paper或实盘执行。</strong></header>
    {error && <p role="alert" className="state-error">{error}</p>}
    {route && <><section className={`router-no-trade ${route.no_trade.active ? "active" : ""}`}><h2>NO_TRADE · {route.no_trade.active ? "ACTIVE" : "INACTIVE"}</h2>{route.no_trade.reasons.map((item) => <p key={item.code}><b>{item.code}</b> · {item.timeframe} · {item.release_condition} <small>{item.temporary ? "TEMPORARY" : "PERMANENT"}</small></p>)}</section>
      {route.primary_route && <CandidateCard candidate={route.primary_route} primary />}<section><h2>Alternatives</h2><div className="router-list">{route.alternatives.map((item) => <CandidateCard candidate={item} key={item.identity_hash} />)}</div></section>
      <section className="state-panel"><h2>Lifecycle transitions</h2>{route.transitions.length ? route.transitions.map((item, index) => <p key={`${item.from_state}-${index}`}>{item.from_state} → {item.to_state} · {item.reason}</p>) : <p>NO_NEW_TRANSITION</p>}</section></>}
  </main>;
}
