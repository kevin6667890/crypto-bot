import { useEffect, useState } from "react";
import { useLanguage } from "../i18n";
import { fetchThesisChanges } from "./api";
import { formatObserved, formatSemanticState, formatStatus, formatUtc } from "./state";
import type { ChangeBundle, MarketStateChange } from "./types";
import { V2Delta } from "./TrackingExpression";

export default function WhatChangedPage() {
  const { language } = useLanguage(); const zh = language === "zh";
  const [changes, setChanges] = useState<ChangeBundle[]>([]); const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [marketChanges, setMarketChanges] = useState<MarketStateChange[]>([]);
  useEffect(() => { const controller = new AbortController(); fetchThesisChanges(controller.signal).then((value) => { setChanges(value.changes); setMarketChanges(value.market_state_changes || []); setState("ready"); }).catch((error) => { if ((error as Error).name !== "AbortError") setState("error"); }); return () => controller.abort(); }, []);
  return <main className="changes-page"><header className="tracking-hero"><span className="product-eyebrow">{zh ? "确定性的证据变化" : "Deterministic evidence changes"}</span><h1>{zh ? "发生了什么变化？" : "What changed?"}</h1><p>{zh ? "这里只展示已跟踪条件、质量或证据身份的实质变化。" : "Only material changes in tracked conditions, quality, or evidence identity appear here."}</p></header>
    {state === "loading" && <section className="product-state" role="status">{zh ? "正在加载变化…" : "Loading changes…"}</section>}
    {state === "error" && <section className="product-state error" role="alert">{zh ? "变化记录暂时不可用。" : "Changes are temporarily unavailable."}</section>}
    {state === "ready" && !changes.length && !marketChanges.length && <section className="product-empty"><h2>{zh ? "没有实质变化" : "No material changes"}</h2><p>{zh ? "自上次确认更新后，没有实质证据变化。" : "No material evidence changes since the last confirmed update."}</p></section>}
    <section className="changes-feed">{changes.map(({ track, evaluation }) => <a href={`/tracking/${encodeURIComponent(track.track_id)}`} key={evaluation.evaluation_id}><header><strong>{track.thesis_spec.instrument} · {track.thesis_spec.timeframe}</strong><time>{formatUtc(evaluation.evaluated_at, language)}</time></header>{evaluation.delta?.status_changed && <h2>{formatStatus(evaluation.delta.previous_status)} → {formatStatus(evaluation.overall_status)}</h2>}{evaluation.delta && (evaluation.delta.overall_change || evaluation.delta.leaf_changes?.length || evaluation.delta.group_changes?.length)
      ? <V2Delta delta={evaluation.delta} zh={zh} />
      : <div className="delta-list">{evaluation.delta?.condition_changes.map((item) => <p key={item.feature}><b>{item.feature.replace(/_/g, " ")}</b><span>{item.from} → {item.to}</span><small>{formatObserved(item.previous_observed_value)} → {formatObserved(item.current_observed_value)}</small></p>)}{evaluation.delta?.quality_changes.map((item) => <p key={`${item.feature}-quality`}><b>{item.feature.replace(/_/g, " ")}</b><span>{item.from} → {item.to}</span></p>)}</div>}<span className="change-link">{zh ? "查看跟踪项目 →" : "View thesis →"}</span></a>)}</section>
    <section className="market-changes-feed">{marketChanges.map((item, index) => <article key={`${item.current_as_of}-${index}`}><header><strong>{item.instrument} · {item.timeframe}</strong><time>{formatUtc(item.transition.transition_timestamp, language)}</time></header><span className="product-eyebrow">{zh ? "市场结构变化" : "MARKET STRUCTURE CHANGED"}</span><h2>{formatSemanticState(item.transition.from_state)} → {formatSemanticState(item.transition.to_state)}</h2><p>{item.transition.trigger_evidence.map((evidence) => evidence.replace(/_/g, " ")).join(" · ")}</p><small>{zh ? "现有 MarketState V2 的已确认语义变化" : "Confirmed semantic transition from the existing MarketState V2 engine"}</small></article>)}</section>
    </main>;
}
