import { Archive, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLanguage } from "../i18n";
import { archiveTrackedThesis, evaluateTrackedThesis, fetchTrackedThesis } from "./api";
import { conditionExpression, conditionTone, formatCode, formatObserved, formatStatus, formatUtc, requiredConditionSummary, statusTone } from "./state";
import type { CurrentEvaluation, TrackDetail } from "./types";

function Identity({ label, value }: { label: string; value: string | null | undefined }) {
  return <div><dt>{label}</dt><dd><code>{value || "—"}</code></dd></div>;
}

export default function TrackDetailPage({ trackId }: { trackId: string }) {
  const { language } = useLanguage(); const zh = language === "zh";
  const [detail, setDetail] = useState<TrackDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const mutationPending = useRef(false);
  useEffect(() => { const controller = new AbortController(); fetchTrackedThesis(trackId, controller.signal).then((value) => { setDetail(value); setState("ready"); }).catch((error) => { if ((error as Error).name !== "AbortError") setState("error"); }); return () => controller.abort(); }, [trackId]);
  async function refresh() {
    if (mutationPending.current) return; mutationPending.current = true; setMessage("");
    try {
      const result = await evaluateTrackedThesis(trackId);
      setDetail((current) => current ? { ...current, track: result.track, latest_evaluation: result.latest_evaluation,
        evaluation_history: result.evaluation_created ? [result.latest_evaluation!, ...current.evaluation_history.filter((item) => item.evaluation_id !== result.latest_evaluation?.evaluation_id)] : current.evaluation_history } : current);
      setMessage(result.outcome === "NO_CHANGE" ? (zh ? "已检查最新确认的证据；没有新增实质变化记录。" : "Latest confirmed evidence checked. No material change record was added.") : (zh ? "当前证据已更新。" : "Current evidence updated."));
    } catch { setMessage(zh ? "刷新失败。" : "Refresh failed."); } finally { mutationPending.current = false; }
  }
  async function archive() {
    if (mutationPending.current) return; mutationPending.current = true;
    try { await archiveTrackedThesis(trackId); window.location.assign("/tracking"); }
    catch { setMessage(zh ? "归档失败。" : "Archive failed."); mutationPending.current = false; }
  }
  if (state === "loading") return <main className="product-message" role="status">{zh ? "正在加载…" : "Loading…"}</main>;
  if (state === "error" || !detail) return <main className="product-message" role="alert">{zh ? "无法读取这个跟踪项目。" : "This tracked thesis could not be loaded."}</main>;
  const { track, latest_evaluation: evaluation, evaluation_history: history } = detail;
  const baseline = track.historical_baseline;
  return <main className="track-detail-page">
    <header className="track-detail-header"><a href="/tracking">← {zh ? "返回跟踪" : "Back to tracking"}</a><span className="product-eyebrow">{track.thesis_spec.instrument} · {track.thesis_spec.timeframe}</span><h1>{track.original_text || track.thesis_spec.required_conditions.map((condition) => conditionExpression(condition, language)).join(" · ")}</h1><div><button className="product-button" onClick={refresh}><RefreshCw size={16} />{zh ? "刷新" : "Refresh"}</button><button className="product-button subtle" onClick={archive}><Archive size={16} />{zh ? "归档" : "Archive"}</button></div>{message && <p className="track-action-message" role="status">{message}</p>}</header>
    <section className="track-evidence-columns">
      <article className="track-evidence-panel current"><span className="product-eyebrow">{zh ? "当前证据" : "CURRENT EVIDENCE"}</span><h2 className={`status-text ${statusTone(evaluation?.overall_status)}`}>{formatStatus(evaluation?.overall_status, language)}</h2><p>{requiredConditionSummary(evaluation, language)}</p><small>{zh ? "截至最新已确认 K 线" : "As of latest confirmed candle"}: {formatUtc(evaluation?.as_of, language)}</small>
        <div className="condition-evidence">{evaluation?.conditions.map((condition, index) => <div className={condition.requirement === "OPTIONAL" ? "optional" : ""} key={`${condition.feature}-${index}`}><span>{formatCode(condition.feature, language)}<small>{formatCode(condition.requirement, language)}</small></span><strong>{formatObserved(condition.observed_value, language)}</strong><b className={`condition-badge ${conditionTone(condition.state)}`}>{formatCode(condition.state, language)}</b>{condition.limitation && <small>{formatCode(condition.limitation, language)}</small>}</div>)}</div>
        {evaluation?.limitations.length ? <ul className="evidence-limitations">{evaluation.limitations.map((item) => <li key={item}>{formatCode(item, language)}</li>)}</ul> : null}
      </article>
      <article className="track-evidence-panel historical"><span className="product-eyebrow">{zh ? "历史证据 · 保存时基线" : "HISTORICAL EVIDENCE · SAVED BASELINE"}</span><h2>{baseline.historical_summary.independent_event_count.toLocaleString()} <small>{zh ? "个独立事件" : "independent events"}</small></h2><p>{baseline.historical_summary.sample_quality} {zh ? "样本" : "sample"}</p><small>{formatUtc(baseline.historical_tested_range.start, language)} → {formatUtc(baseline.historical_tested_range.end, language)}</small><p className="baseline-note">{zh ? "这份历史基线在保存后不会自动重算。" : "This historical baseline is not silently recalculated after saving."}</p></article>
    </section>
    <section className="track-section"><h2>{zh ? "发生了什么变化" : "What changed"}</h2>{evaluation?.delta?.material_change ? <Delta evaluation={evaluation} language={language} /> : <p className="quiet-state">{zh ? "没有实质证据变化。" : "No material evidence change."}</p>}</section>
    <section className="track-section"><h2>{zh ? "评估历史" : "Evaluation history"}</h2><div className="evaluation-timeline">{history.filter((item) => item.delta?.initial_evaluation || item.delta?.material_change).map((item) => <article key={item.evaluation_id}><time>{formatUtc(item.evaluated_at, language)}</time><strong>{formatStatus(item.overall_status, language)}</strong><span>{requiredConditionSummary(item, language)}</span>{item.delta?.material_change && <Delta evaluation={item} language={language} />}</article>)}</div></section>
    <details className="track-section track-audit"><summary>{zh ? "证据身份与审计详情" : "Evidence identities and audit details"}</summary><dl><Identity label={zh ? "历史证据来源" : "Historical evidence source"} value={track.historical_dataset_identity} /><Identity label={zh ? "当前证据来源" : "Current evidence source"} value={evaluation?.current_dataset_identity?.dataset_id} /><Identity label={zh ? "历史结果" : "Historical result"} value={track.historical_result_hash} /><Identity label={zh ? "定义" : "Definition"} value={track.definition_hash} /><Identity label={zh ? "历史引擎" : "Historical engine"} value={track.historical_engine_version} /><Identity label={zh ? "当前评估策略" : "Current evaluation policy"} value={track.current_evaluation_policy_version} /></dl></details>
  </main>;
}

function Delta({ evaluation, language }: { evaluation: CurrentEvaluation; language: "en" | "zh" }) {
  const zh = language === "zh";
  const delta = evaluation.delta;
  if (!delta) return null;
  return <div className="delta-list">{delta.status_changed && <p><b>{zh ? "整体" : "OVERALL"}</b><span>{formatStatus(delta.previous_status, language)} → {formatStatus(delta.current_status, language)}</span></p>}{delta.condition_changes.map((item) => <p key={`${item.feature}-${item.from}-${item.to}`}><b>{formatCode(item.feature, language)}</b><span>{formatCode(item.from, language)} → {formatCode(item.to, language)}</span><small>{formatObserved(item.previous_observed_value, language)} → {formatObserved(item.current_observed_value, language)}</small></p>)}{delta.quality_changes.map((item) => <p key={`${item.feature}-quality`}><b>{formatCode(item.feature, language)}</b><span>{formatCode(item.from, language)} → {formatCode(item.to, language)}</span></p>)}</div>;
}
