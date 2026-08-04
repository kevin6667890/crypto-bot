import { useEffect, useState } from "react";

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

export default function MarketStateResearch({ instrument }: { instrument: string }) {
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

  return <main className="market-state-research" data-market-state-page>
    <header className="market-state-heading">
      <div><span className="eyebrow">MARKET STATE ENGINE V2</span><h1>{state?.primary_state_code || "LOADING"}</h1></div>
      <strong className="state-disclaimer">市场状态识别，不是交易信号。</strong>
    </header>
    {error && <p role="alert" className="state-error">{error}</p>}
    {state && <>
      <section className="state-summary-grid">
        <article><span>证据一致度与完整度</span><b>{state.evidence_strength.toFixed(1)} / 100</b><small>不是概率</small></article>
        <article><span>跨周期</span><b>{state.cross_timeframe.state}</b><small>{state.cross_timeframe.normal_pullback ? "NORMAL_PULLBACK" : "STRUCTURE_OBSERVED"}</small></article>
        <article><span>数据质量</span><b>{state.quality.overall_status || "UNKNOWN"}</b><small>{state.version}</small></article>
      </section>
      <section className="state-panel"><h2>五周期独立状态</h2><div className="state-frame-grid">
        {frames.map((name) => { const item = state.timeframes[name]; return <article key={name}><b>{name}</b><strong>{item?.primary_state || "UNKNOWN"}</strong><span>{item?.role}</span><small>{item?.momentum_state} · {item?.quality?.status || "UNKNOWN"}</small><div>{item?.overlays.map((overlay) => <code key={overlay}>{overlay}</code>)}</div></article>; })}
      </div></section>
      <section className="state-panel"><h2>关键位置互动</h2><div className="state-table">
        {state.level_interactions.length ? state.level_interactions.map((item, index) => <div key={`${item.level_type}-${item.timeframe}-${index}`}><b>{item.timeframe} · {item.level_type}</b><span>{item.interaction_type}</span><span>{item.boundary.toFixed(4)}</span><small>{item.distance_pct.toFixed(3)}% · {item.current_stage} · {item.quality}</small></div>) : <p>NO_LEVEL_INTERACTION</p>}
      </div></section>
      <section className="state-columns">
        <article className="state-panel"><h2>Supporting evidence</h2>{state.evidence.filter((item) => item.classification === "supporting").map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> {item.code} <small>{item.weight}</small></p>)}</article>
        <article className="state-panel"><h2>Conflicting evidence</h2>{state.evidence.filter((item) => item.classification === "conflicting").map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> {item.code} <small>{item.weight}</small></p>)}</article>
        <article className="state-panel"><h2>Unavailable / limitations</h2>{state.limitations.map((item) => <p key={item}>{item}</p>)}</article>
      </section>
      <section className="state-panel"><h2>Overlays</h2><div className="state-tags">{state.overlays.map((item) => <code key={item}>{item}</code>)}</div></section>
      <section className="state-panel"><h2>State transitions</h2>{state.transitions.length ? state.transitions.map((item, index) => <p key={`${item.transition_timestamp}-${index}`}>{item.from_state} → {item.to_state} · {item.confirmation_status}</p>) : <p>NO_NEW_TRANSITION</p>}</section>
    </>}
  </main>;
}
