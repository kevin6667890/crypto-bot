import { useEffect, useMemo, useState } from "react";
import { useLanguage, type TranslationKey } from "./i18n";

type Observation = { observed_at?: number; source_at?: number | null; oldest_at?: number | null; bar_count?: number; required_bar_count?: number; freshness_seconds?: number | null; freshness_limit_seconds?: number; availability?: string; quality?: string; structure_state?: string | null; reason_codes?: string[] };
type Evidence = { code: string; timeframe: string; value: unknown; weight: number; quality: string; classification: string };
type FrameState = { primary_state: string; role: string; evidence_strength: number; momentum_state: string; overlays: string[]; quality: { status?: string }; limitations: string[]; observation?: Observation };
type Level = { level_type: string; timeframe: string; interaction_type: string; boundary: number; distance_pct: number; quality: string; current_stage: string };
type MarketState = {
  version: string; instrument: string; as_of: number; primary_state_code: string; evidence_strength: number;
  timeframes: Record<string, FrameState>;
  cross_timeframe: { state: string; supporting_timeframes: string[]; conflicting_timeframes: string[]; missing_timeframes: string[] };
  level_interactions: Level[]; overlays: string[];
  transitions: Array<{ from_state: string; to_state: string; transition_timestamp: number; confirmation_status: string }>;
  evidence: Evidence[]; limitations: string[]; quality: { overall_status?: string; stale_sources?: string[]; partial_sources?: string[]; missing_sources?: string[] };
};

const frames = ["15m", "1H", "4H", "1D", "1W"];
const degradedStatusKeys: Record<string, TranslationKey> = { UNKNOWN: "state.waitingData", UNAVAILABLE: "state.unavailableFrame", MISSING: "state.unavailableFrame", STALE: "state.staleObservation", INSUFFICIENT_DATA: "state.waitingData", PARTIAL: "state.partialCoverage", NO_CLEAR_STATE: "state.noClearStructure", LOADING: "common.loading" };
const structureZh: Record<string, string> = { TREND_UP: "结构上行", TREND_DOWN: "结构下行", TRANSITION_UP: "向上过渡", TRANSITION_DOWN: "向下过渡", TRANSITION_MIXED: "混合过渡", RANGE_LOW_VOLATILITY: "低波动区间", RANGE_HIGH_VOLATILITY: "高波动区间", NO_CLEAR_STATE: "暂无清晰状态", INSUFFICIENT_DATA: "等待足够数据", UNKNOWN: "等待足够数据" };
const availabilityZh: Record<string, string> = { AVAILABLE: "可用", PARTIAL: "部分", STALE: "陈旧", MISSING: "缺失" };

function statusLabel(value: string | undefined, t: (key: TranslationKey) => string, zh = false) {
  if (!value) return t("state.waitingData");
  if (zh && structureZh[value]) return structureZh[value];
  return degradedStatusKeys[value] ? t(degradedStatusKeys[value]) : value.replace(/_/g, " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}
function statusTone(value: string | undefined) { return !value || ["UNKNOWN", "UNAVAILABLE", "MISSING", "STALE", "INSUFFICIENT_DATA", "PARTIAL", "NO_CLEAR_STATE"].includes(value) ? "degraded" : "available"; }
function age(value: number | null | undefined, zh: boolean) { if (value == null) return zh ? "无时间" : "No timestamp"; if (value < 120) return zh ? `${value} 秒前` : `${value}s ago`; if (value < 7200) return zh ? `${Math.round(value / 60)} 分钟前` : `${Math.round(value / 60)}m ago`; if (value < 172800) return zh ? `${Math.round(value / 3600)} 小时前` : `${Math.round(value / 3600)}h ago`; return zh ? `${Math.round(value / 86400)} 天前` : `${Math.round(value / 86400)}d ago`; }
function stamp(value: number | null | undefined) { return value ? new Date(value * 1000).toLocaleString() : "—"; }
function levelType(value: string, zh: boolean) { const map: Record<string, string> = { CONFLUENCE_ZONE: zh ? "共振位" : "Confluence", SWING_HIGH: zh ? "摆动高点" : "Swing high", SWING_LOW: zh ? "摆动低点" : "Swing low", MA200: "MA200", MA60: "MA60", EMA20: "EMA20", PSYCHOLOGICAL_ROUND: zh ? "整数位" : "Round level" }; return map[value] || value.replace(/_/g, " "); }
function interaction(value: string, zh: boolean) { const map: Record<string, string> = { UNKNOWN: zh ? "观察中" : "Observing", TOUCHING: zh ? "正在测试" : "Touching", INSIDE_ZONE: zh ? "区间内测试" : "Inside zone", APPROACHING: zh ? "接近" : "Approaching", BROKEN: zh ? "已突破" : "Broken", RECLAIMED: zh ? "已收复" : "Reclaimed", REJECTED: zh ? "已拒绝" : "Rejected", INVALIDATED: zh ? "已失效" : "Invalidated" }; return map[value] || value.replace(/_/g, " "); }

export default function MarketStateResearch({ instrument }: { instrument: string }) {
  const { t } = useLanguage(); const zh = t("state.title") === "市场结构";
  const [state, setState] = useState<MarketState | null>(null); const [error, setError] = useState(""); const [filter, setFilter] = useState("ALL");
  useEffect(() => { const controller = new AbortController(); setError(""); setState(null); fetch(`/api/market/state?instrument=${encodeURIComponent(instrument)}&execution_timeframe=15m`, { signal: controller.signal }).then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(setState).catch((reason) => { if (reason?.name !== "AbortError") setError(String(reason)); }); return () => controller.abort(); }, [instrument]);
  const supporting = useMemo(() => state?.evidence.filter((item) => item.classification === "supporting") ?? [], [state]);
  const conflicting = useMemo(() => state?.evidence.filter((item) => item.classification === "conflicting") ?? [], [state]);
  const levels = useMemo(() => (state?.level_interactions || []).filter((item) => filter === "ALL" || filter === "TESTING" && ["TOUCHING", "INSIDE_ZONE", "APPROACHING"].includes(item.interaction_type) || filter === "SUPPORT" && /LOW|VAL/.test(item.level_type) || filter === "RESISTANCE" && /HIGH|VAH/.test(item.level_type) || filter === "BREAKOUT" && ["BROKEN", "RECLAIMED"].includes(item.interaction_type) || filter === "INVALID" && item.interaction_type === "INVALIDATED"), [state, filter]);
  return <main className="market-state-research" data-market-state-page>
    <header className="market-state-heading"><div><span className="eyebrow">{t("state.eyebrow")}</span><h1>{t("state.title")}</h1><p>{t("state.description")}</p></div><span className="state-disclaimer">{t("state.disclaimer")}</span></header>
    {error && <section role="alert" className="degraded-notice"><strong>{t("state.temporarilyUnavailable")}</strong><span>{t("state.retryHint")}</span></section>}
    {!state && !error && <section className="degraded-notice" role="status"><strong>{t("state.loadingTitle")}</strong><span>{t("state.loadingHelp")}</span></section>}
    {state && <>
      <section className="state-summary-grid primary-three">
        <article><span>{zh ? "市场状态" : "Market state"}</span><b className={`human-status ${statusTone(state.primary_state_code)}`}>{statusLabel(state.primary_state_code, t, zh)}</b><small>{zh ? "证据一致度" : "Evidence consistency"} {state.evidence_strength.toFixed(1)} / 100 · {t("state.notProbability")}</small></article>
        <article><span>{t("state.crossTimeframe")}</span><b className={`human-status ${statusTone(state.cross_timeframe.state)}`}>{statusLabel(state.cross_timeframe.state, t, zh)}</b><small>{t("state.coverageSummary", { count: state.cross_timeframe.missing_timeframes.length })}</small></article>
        <article><span>{t("state.dataQuality")}</span><b className={`human-status ${statusTone(state.quality.overall_status)}`}>{zh ? availabilityZh[state.quality.overall_status || ""] || statusLabel(state.quality.overall_status, t, zh) : statusLabel(state.quality.overall_status, t)}</b><small>{t("state.freshnessTracked")}</small></article>
      </section>
      <section className="state-panel"><div className="state-panel-heading"><div><span className="eyebrow">{t("state.multiTimeframe")}</span><h2>{t("state.timeframeStates")}</h2></div><p>{zh ? "结构状态与数据可用性分别呈现；规则趋势信号请在工作台查看。" : "Structural regimes and data availability are separate; rule trend signals remain in Workspace."}</p></div><div className="state-frame-grid">
        {frames.map((name) => { const item = state.timeframes[name]; const technical = item?.primary_state || "UNKNOWN"; const observation = item?.observation || {}; const available = observation.availability || item?.quality?.status || "MISSING"; return <article key={name} title={`${statusLabel(technical, t, zh)} · ${statusLabel(available, t, zh)}`}><div className="frame-card-head"><b>{name}</b><span className={`availability-badge ${available.toLowerCase()}`}>{zh ? availabilityZh[available] || available : available}</span></div><strong className={`human-status ${statusTone(technical)}`}>{statusLabel(technical, t, zh)}</strong><span>{age(observation.freshness_seconds, zh)}</span><small>{zh ? "更新" : "Updated"} {stamp(observation.source_at)}</small><small>{observation.bar_count ?? 0} / {observation.required_bar_count ?? 200} {zh ? "根K线" : "bars"}</small></article>; })}
      </div></section>
      <section className="state-panel"><div className="state-panel-heading"><div><span className="eyebrow">{t("state.structureEvidence")}</span><h2>{t("state.keyLevels")}</h2></div><p>{zh ? "压缩展示当前距离和互动状态。" : "Compact view of distance and interaction state."}</p></div>
        <div className="level-filters" role="group" aria-label={zh ? "关键位置筛选" : "Key level filters"}>{[["ALL","全部","All"],["TESTING","正在测试","Testing"],["SUPPORT","支撑","Support"],["RESISTANCE","压力","Resistance"],["BREAKOUT","突破","Breakout"],["INVALID","失效","Invalid"]].map(([key,cn,en]) => <button className={filter === key ? "active" : ""} key={key} onClick={() => setFilter(key)}>{zh ? cn : en}</button>)}</div>
        <div className="level-table"><div className="level-table-head"><span>{zh ? "周期" : "Timeframe"}</span><span>{zh ? "位置" : "Level"}</span><span>{zh ? "类型" : "Type"}</span><span>{zh ? "价格" : "Price"}</span><span>{zh ? "距离" : "Distance"}</span><span>{zh ? "状态" : "State"}</span></div>{levels.length ? levels.map((item, index) => <div key={`${item.level_type}-${item.timeframe}-${index}`}><b>{item.timeframe}</b><span>{levelType(item.level_type, zh)}</span><span>{item.quality === "AVAILABLE" ? (zh ? "已确认" : "Confirmed") : availabilityZh[item.quality] || item.quality}</span><span>{item.boundary.toFixed(2)}</span><span className={item.distance_pct >= 0 ? "positive" : "negative"}>{item.distance_pct.toFixed(3)}%</span><strong>{interaction(item.interaction_type, zh)}</strong></div>) : <p className="empty-state">{t("state.noLevelInteraction")}</p>}</div>
      </section>
      <details className="state-diagnostics"><summary><span><strong>{t("state.diagnostics")}</strong><small>{t("state.diagnosticsHelp")}</small></span><span>{t("state.expand")}</span></summary><div className="state-diagnostics-body"><section className="state-columns"><article className="state-panel"><h2>{t("state.supportingEvidence")}</h2>{supporting.length ? supporting.map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> <code>{item.code}</code> <small>{item.weight}</small></p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article><article className="state-panel"><h2>{t("state.conflictingEvidence")}</h2>{conflicting.length ? conflicting.map((item, index) => <p key={`${item.code}-${index}`}><b>{item.timeframe}</b> <code>{item.code}</code> <small>{item.weight}</small></p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article><article className="state-panel"><h2>{t("state.limitations")}</h2>{state.limitations.length ? state.limitations.map((item) => <p key={item}>{item}</p>) : <p className="empty-state">{t("state.noneRecorded")}</p>}</article></section><p className="engine-metadata">{t("state.engineMetadata")}: <code>{state.version}</code> · {state.instrument}</p></div></details>
    </>}
  </main>;
}
