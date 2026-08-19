import { BrainCircuit } from "lucide-react";
import { useEffect, useState } from "react";
import { AuditedAiBrief, AuditedAiReportDetail, fetchAuditedAiBrief, fetchAuditedAiHistory, fetchAuditedAiReport } from "./data";

const when = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const source = (brief: AuditedAiBrief | null) => [brief?.provider, brief?.model].filter(Boolean).join(" / ") || "DeepSeek";

export function WorkspaceAiBrief({ instrument }: { instrument: string }) {
  const [brief, setBrief] = useState<AuditedAiBrief | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true); setBrief(null);
    fetchAuditedAiBrief(instrument).then((value) => { if (active) setBrief(value); }).catch(() => { if (active) setBrief(null); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [instrument]);
  const current = brief?.display_eligible && brief.status === "CURRENT_AUDITED_REPORT";
  const stale = brief?.display_eligible && brief.status === "STALE_AUDITED_REPORT";
  return <section className={`ai-brief ${stale ? "stale" : ""}`} data-testid="workspace-ai6b-brief">
    <BrainCircuit size={19} />
    <div>
      <span className="eyebrow">每小时 AI 简报 · {source(brief)}</span>
      {loading ? <strong>正在读取审计后的 AI 分析…</strong> : current ? <>
        <strong><span className="status-pill healthy">Audit 已通过</span> 更新时间：{when(brief.generated_at)}</strong>
        <div className="ai-brief-meta">数据时间：{when(brief.market_snapshot_at)} · 模式：{brief.mode} · 审计分数：{brief.audit.overall_score ?? "—"}</div>
        <p>{brief.executive_summary || brief.headline}</p>
        <small>数据质量：{brief.freshness.quality || "未知"}{brief.data_warnings.length ? ` · ${brief.data_warnings.join("；")}` : ""}</small>
      </> : stale ? <>
        <strong><span className="status-pill stale">AI 简报已过期</span> 最后有效分析：{when(brief.generated_at)}</strong>
        <div className="ai-brief-meta">最后数据时间：{when(brief.market_snapshot_at)} · 模式：{brief.mode} · Audit 已通过</div>
        <p>正在等待下一次有效分析。历史内容不会作为当前市场结论展示。</p>
      </> : <>
        <strong>暂无当前有效 AI 分析</strong>
        <p>{brief?.latest_generated?.eligibility === "AUDIT_FAILED" ? "最近报告未通过审计，正在等待下一次更新。" : "正在等待下一次通过审计的更新。"}</p>
      </>}
    </div>
  </section>;
}

export function AiReportResearch({ instrument }: { instrument: string }) {
  const [items, setItems] = useState<AuditedAiBrief[]>([]);
  const [detail, setDetail] = useState<AuditedAiReportDetail | null>(null);
  useEffect(() => { let active = true; setItems([]); setDetail(null); fetchAuditedAiHistory(instrument).then((v) => { if (active) setItems(v); }).catch(() => undefined); return () => { active = false; }; }, [instrument]);
  const open = (item: AuditedAiBrief) => fetchAuditedAiReport(instrument, item.report_id, item.mode).then(setDetail).catch(() => setDetail(null));
  return <section className="ai-report-research" data-testid="research-ai6b-reports">
    <div className="section-title"><div><span className="eyebrow">AI6B · 审计报告</span><h2>{instrument} QUICK / FULL / POSITION 历史</h2></div></div>
    {!items.length ? <p className="muted">暂无报告记录。</p> : <div className="ai-report-history">{items.map((item) => <button key={item.report_id} onClick={() => open(item)}>
      <b>{item.mode}</b><span>{when(item.generated_at)}</span><span className={`status-pill ${item.display_eligible ? "healthy" : "unhealthy"}`}>{item.display_eligible ? `Audit ${item.audit.overall_score ?? "PASS"}` : "不可展示"}</span>
    </button>)}</div>}
    {detail?.report && <article className="ai-report-detail"><h3>{detail.report.headline}</h3>{detail.report.sections.map((section) => <section key={section.section_id}><h4>{section.title || section.section_id}</h4><p>{section.body}</p></section>)}</article>}
  </section>;
}
