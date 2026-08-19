import { BrainCircuit, CheckCircle2, Clock3, ExternalLink, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { AuditedAiBrief, AuditedAiReportDetail, fetchAuditedAiBrief, fetchAuditedAiHistory, fetchAuditedAiReport } from "./data";

const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "—";
const source = (brief: AuditedAiBrief | null) => [brief?.provider, brief?.model].filter(Boolean).join(" / ") || "DeepSeek";
const qualityLabel: Record<string, string> = { AVAILABLE: "可用", PARTIAL: "历史不足", STALE: "数据较旧", MISSING: "数据不足" };
const phaseLabel: Record<string, string> = { FAILED_BREAKOUT: "失败突破", BREAKOUT_ATTEMPT: "突破尝试", RANGE_BUILDING: "区间构建", COMPRESSION: "波动压缩", RETEST: "回测确认", UNCLASSIFIED: "待分类" };

function freshness(brief: AuditedAiBrief) {
  const age = brief.freshness.age_seconds ?? 0;
  const limit = brief.freshness.threshold_seconds ?? 7200;
  if (brief.status === "STALE_AUDITED_REPORT" || brief.freshness.status === "STALE") return { label: "已过期", tone: "stale" };
  if (age >= limit * .75) return { label: "即将过期", tone: "aging" };
  return { label: "当前", tone: "current" };
}

function DataCoverage({ brief }: { brief: AuditedAiBrief }) {
  const items = brief.timeframe_quality || [];
  return <div className="ai-coverage"><span className="ai-meta-label">数据覆盖：{brief.freshness.quality === "PARTIAL" ? "部分" : brief.freshness.quality === "AVAILABLE" ? "完整" : "受限"}</span>
    <div>{items.filter((item) => item.availability !== "AVAILABLE").map((item) => <span className={`coverage-chip ${item.availability.toLowerCase()}`} key={item.timeframe}>
      <b>{item.timeframe}</b> · {item.availability === "PARTIAL" && item.reason_code !== "INDICATOR_WARMUP_INCOMPLETE" ? "部分可用" : qualityLabel[item.availability] || "受限"}
    </span>)}</div>
  </div>;
}

export function WorkspaceAiBrief({ instrument }: { instrument: string }) {
  const [brief, setBrief] = useState<AuditedAiBrief | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true); setBrief(null);
    fetchAuditedAiBrief(instrument).then((value) => { if (active) setBrief(value); }).catch(() => { if (active) setBrief(null); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrument]);
  const current = Boolean(brief?.display_eligible && brief.status === "CURRENT_AUDITED_REPORT");
  const stale = Boolean(brief?.display_eligible && brief.status === "STALE_AUDITED_REPORT");
  const fresh = brief ? freshness(brief) : null;
  return <section className={`ai-insight-hero ${stale ? "stale" : ""}`} data-testid="workspace-ai6b-brief" aria-labelledby="ai-insight-title">
    <header className="ai-hero-header">
      <div className="ai-hero-title"><BrainCircuit size={23} /><div><span className="eyebrow">Audited market intelligence</span><h2 id="ai-insight-title">AI 市场研判</h2></div></div>
      <div className="ai-hero-badges"><span>{source(brief)}</span><span>{brief?.mode || "QUICK"}</span>{brief?.audit.status === "PASSED" && <span className="audit-pass" title="内容已通过：引用、数值、方向、语义、关键位置与场景一致性"><CheckCircle2 size={14} /> AI 审计通过 · {brief.audit.overall_score ?? 100} / 100</span>}{fresh && <span className={`freshness-badge ${fresh.tone}`}><Clock3 size={13} />{fresh.label}</span>}</div>
    </header>
    {loading ? <div className="ai-hero-empty"><strong>正在读取审计后的 AI 分析…</strong></div> : current && brief ? <div className="ai-hero-layout">
      <div className="ai-hero-primary">
        <span className="ai-conclusion-label">结论 · {brief.decision_label || "观察"}</span>
        <h3>{brief.headline}</h3>
        <p>{brief.executive_summary}</p>
        <div className="ai-hero-section"><span>关键依据</span><ul>{(brief.drivers || []).slice(0, 3).map((item, index) => <li key={`${item.label}-${index}`}><b>{item.label}</b>{item.value != null && <small>{String(item.value)}</small>}</li>)}{!brief.drivers?.length && <li><b>市场阶段</b><small>{phaseLabel[brief.market_phase || ""] || brief.market_phase || "观察中"}</small></li>}</ul></div>
        {(brief.levels?.length || brief.scenarios?.length) ? <div className="ai-hero-section"><span>关注条件</span><ul>{brief.levels?.slice(0, 2).map((item) => <li key={item.level_id}><b>{item.primary_timeframe || "关键位"} · {item.asserted_role || "LEVEL"}</b><small>{item.representative_price ?? "—"}</small></li>)}{brief.scenarios?.slice(0, 1).map((item) => <li key={item.scenario_id}><b>{item.scenario_type || "情景确认"}</b><small>{item.trigger_text || item.confirmation_text || "等待确认"}</small></li>)}</ul></div> : null}
      </div>
      <div className="ai-hero-meta">
        <dl><div><dt>更新时间</dt><dd>{when(brief.generated_at)}</dd></div><div><dt>数据时间</dt><dd>{when(brief.market_snapshot_at)}</dd></div><div><dt>市场阶段</dt><dd>{phaseLabel[brief.market_phase || ""] || brief.market_phase || "—"}</dd></div><div><dt>置信边界</dt><dd>{brief.confidence || "—"}</dd></div></dl>
        <DataCoverage brief={brief} />
        {!!brief.risks?.length && <div className="ai-risk-list"><span>风险 / 限制</span>{brief.risks.slice(0, 3).map((item) => <p key={item}>{item}</p>)}</div>}
        <a className="secondary-btn ai-research-link" href={`#research/report/${encodeURIComponent(brief.report_id)}`}>查看完整 AI 分析 <ExternalLink size={14} /></a>
      </div>
    </div> : stale && brief ? <div className="ai-hero-empty stale"><strong>AI 分析已过期</strong><p>最后有效分析：{when(brief.generated_at)}。历史内容不会作为当前市场结论展示，正在等待下一次有效分析。</p><DataCoverage brief={brief} /></div> : <div className="ai-hero-empty failed"><ShieldCheck size={20} /><strong>{brief?.latest_generated?.eligibility === "AUDIT_FAILED" ? "最新 AI 分析未通过审计" : "暂无当前有效 AI 分析"}</strong><p>等待下一次有效分析。未通过审计的报告正文不会展示。</p></div>}
  </section>;
}

export function AiReportResearch({ instrument }: { instrument: string }) {
  const [items, setItems] = useState<AuditedAiBrief[]>([]);
  const [detail, setDetail] = useState<AuditedAiReportDetail | null>(null);
  const open = (item: AuditedAiBrief) => fetchAuditedAiReport(instrument, item.report_id, item.mode).then(setDetail).catch(() => setDetail(null));
  useEffect(() => {
    let active = true; setItems([]); setDetail(null);
    fetchAuditedAiHistory(instrument).then((value) => {
      if (!active) return; setItems(value);
      const requested = decodeURIComponent(window.location.hash.split("/report/")[1] || "");
      const target = value.find((item) => item.report_id === requested) || value.find((item) => item.display_eligible);
      if (target) open(target);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [instrument]);
  return <section className="ai-report-research" data-testid="research-ai6b-reports">
    <div className="section-title"><div><span className="eyebrow">AI 深度中心 · 审计报告</span><h2>Latest Analysis</h2></div><span className="muted">QUICK · FULL · POSITION（可用时）</span></div>
    {detail?.report && <article className="ai-report-detail"><div className="ai-report-detail-head"><div><span className="status-pill healthy">Audit {detail.summary.audit.overall_score ?? "PASS"}</span><span className={`freshness-badge ${freshness(detail.summary).tone}`}>{freshness(detail.summary).label}</span></div><small>{when(detail.summary.market_snapshot_at)}</small></div><h3>{detail.report.headline}</h3><DataCoverage brief={detail.summary} />{detail.report.sections.map((section) => <section key={section.section_id}><h4>{section.title || section.section_id}</h4><p>{section.body}</p>{section.uncertainties?.map((item) => <small key={item}>· {item}</small>)}</section>)}</article>}
    <div className="ai-history-title"><h3>History</h3><span>历史报告不会替代当前状态</span></div>
    {!items.length ? <p className="muted">暂无报告记录。</p> : <div className="ai-report-history">{items.map((item) => <button key={item.report_id} onClick={() => open(item)} aria-label={`打开 ${item.mode} AI 报告`}><b>{item.mode}</b><span>{when(item.generated_at)}</span><span className={`status-pill ${item.display_eligible ? "healthy" : "unhealthy"}`}>{item.display_eligible ? `Audit ${item.audit.overall_score ?? "PASS"}` : "不可展示"}</span></button>)}</div>}
  </section>;
}
