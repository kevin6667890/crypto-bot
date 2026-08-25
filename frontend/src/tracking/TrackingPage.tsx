import { ArrowRight, FlaskConical } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "../i18n";
import { expressionLabel } from "../thesis/expressionV2";
import { fetchTrackedTheses } from "./api";
import { conditionExpression, formatStatus, formatUtc, requiredConditionSummary, statusTone } from "./state";
import type { TrackBundle } from "./types";

export default function TrackingPage() {
  const { language } = useLanguage();
  const zh = language === "zh";
  const [tracks, setTracks] = useState<TrackBundle[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => {
    const controller = new AbortController();
    fetchTrackedTheses(controller.signal).then((value) => { setTracks(value.tracks); setState("ready"); })
      .catch((error) => { if ((error as Error).name !== "AbortError") setState("error"); });
    return () => controller.abort();
  }, []);
  return <main className="tracking-page">
    <header className="tracking-hero"><span className="product-eyebrow">{zh ? "保存的确定性定义" : "Saved deterministic definitions"}</span><h1>{zh ? "我在跟踪什么" : "What I'm tracking"}</h1><p>{zh ? "历史基线保持不变；当前状态只使用最新已确认的市场证据。" : "Historical baselines stay fixed. Current status uses only the latest confirmed market evidence."}</p></header>
    {state === "loading" && <section className="product-state" role="status">{zh ? "正在加载跟踪项目…" : "Loading tracked theses…"}</section>}
    {state === "error" && <section className="product-state error" role="alert">{zh ? "跟踪服务暂时不可用。" : "Tracking is temporarily unavailable."}</section>}
    {state === "ready" && !tracks.length && <section className="product-empty"><FlaskConical /><h2>{zh ? "尚未跟踪任何想法" : "No tracked theses yet"}</h2><p>{zh ? "先完成一次有效的历史检验，再保存同一个定义。" : "Complete a valid historical test, then save that exact definition."}</p><a className="product-button primary" href="/test-an-idea">{zh ? "测试一个想法" : "Test an idea"}</a></section>}
    <section className="tracking-grid">{tracks.map(({ track, latest_evaluation: evaluation }) => {
      const baseline = track.historical_baseline.historical_summary;
      return <a className="tracking-card" href={`/tracking/${encodeURIComponent(track.track_id)}`} key={track.track_id}>
        <header><div><span>{track.thesis_spec.instrument} · {track.thesis_spec.timeframe}</span><small>{zh ? "创建于" : "Created"} {formatUtc(track.created_at, language)}</small></div><ArrowRight /></header>
        <div className="tracking-definitions">{track.thesis_spec.version === "thesis-spec-v2"
          ? <span>{expressionLabel(track.thesis_spec.expression, null, language)}</span>
          : track.thesis_spec.required_conditions.map((condition, index) => <span key={`${condition.feature}-${index}`}>{conditionExpression(condition)}</span>)}</div>
        <section className="evidence-current"><span>{zh ? "当前证据" : "CURRENT EVIDENCE"}</span><strong className={`status-badge ${statusTone(evaluation?.overall_status)}`}>{formatStatus(evaluation?.overall_status)}</strong>
          <p>{requiredConditionSummary(evaluation, language)}</p>
          <small>{zh ? "最新已确认证据" : "Latest confirmed evidence"}: {formatUtc(evaluation?.as_of, language)}</small></section>
        <section className="evidence-historical"><span>{zh ? "历史证据" : "HISTORICAL EVIDENCE"}</span><strong>{baseline.independent_event_count.toLocaleString()} {zh ? "个独立事件" : "independent events"}</strong><small>{baseline.sample_quality} · {formatUtc(track.historical_tested_range.end, language)}</small></section>
      </a>;
    })}</section>
  </main>;
}
