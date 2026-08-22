import { BrainCircuit, CheckCircle2, Clock3, ExternalLink, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AuditedAiBrief, AuditedAiReportDetail, fetchAuditedAiBrief, fetchAuditedAiHistory, fetchAuditedAiReport } from "./data";
import { translateKnownEnum } from "./aiMarketAnalysis/enumTranslations";
import { compactAiSummary, coverageMatrixRows, isPresent, localizeWorkspaceNarrative, presentAiLevels, renderIfPresent, workspaceScenarioLabel } from "./aiWorkspaceSemantics";
import { useLanguage, type Language } from "./i18n";
import { researchPresentationCopy, selectResearchReport } from "./aiResearchPresentation";
import { workspaceAiPrimaryState } from "./aiWorkspaceState";
import { crossTimeframeNarrative, derivativesNarrative, frameNarrative, intelligenceCenter, levelLabel, localizeAiRule, scenarioLabel, visibleFrame, volumeImpulseNarrative } from "./aiIntelligencePresentation";

const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : "—";
const source = (brief: AuditedAiBrief | null) => [brief?.provider, brief?.model].filter(Boolean).join(" / ") || "DeepSeek";
const containsCjk = (value: unknown) => /[\u3400-\u9fff]/.test(String(value || ""));
const workspaceCopy = {
  zh: { eyebrow: "审计后市场研判", title: "AI 市场研判", audit: "AI 审计通过", auditTitle: "内容已通过引用、数值、方向、语义、关键位置与场景一致性检查", loading: "正在读取审计后的 AI 分析…", conclusion: "结论", observe: "观察", evidence: "关键依据", phase: "市场阶段", watching: "观察中", levels: "关键位置", noLevels: "当前无可靠关键位置", support: "支撑", resistance: "压力", level: "关键位", multi: "多周期", invalidation: "失效", scenario: "关注场景", updated: "更新时间", dataTime: "数据时间", confidence: "置信边界", coverage: "数据覆盖", complete: "完整", partial: "部分", limited: "受限", risks: "风险 / 限制", report: "查看完整 AI 分析", staleTitle: "AI 分析已过期", staleBody: "历史内容不会作为当前市场结论展示，正在等待下一次有效分析。", lastValid: "最后有效分析", next: "下一次预计更新时间", history: "查看历史完整 AI 分析", auditFailed: "最新 AI 分析未通过审计", unavailable: "暂无当前有效 AI 分析", unavailableBody: "等待下一次有效分析。未通过审计的报告正文不会展示。", dataQuality: "数据质量", dataLimit: "数据限制", current: "当前", aging: "即将过期", stale: "已过期" },
  en: { eyebrow: "Audited market intelligence", title: "AI Market View", audit: "AI audit passed", auditTitle: "Citations, values, direction, semantics, levels and scenarios passed consistency checks", loading: "Loading the audited AI analysis…", conclusion: "Conclusion", observe: "Observe", evidence: "Key evidence", phase: "Market phase", watching: "Monitoring", levels: "Key levels", noLevels: "No reliable key levels are available", support: "Support", resistance: "Resistance", level: "Key level", multi: "Multi-timeframe", invalidation: "Invalidation", scenario: "Scenario", updated: "Updated", dataTime: "Market data time", confidence: "Confidence boundary", coverage: "Data coverage", complete: "Complete", partial: "Partial", limited: "Limited", risks: "Data limitations", report: "View full AI analysis", staleTitle: "AI analysis is stale", staleBody: "Historical content is not shown as a current market conclusion. Waiting for the next eligible analysis.", lastValid: "Last valid analysis", next: "Next expected update", history: "View full historical AI analysis", auditFailed: "Latest AI analysis did not pass audit", unavailable: "No current eligible AI analysis", unavailableBody: "Waiting for the next eligible analysis. Audit-failed report content remains hidden.", dataQuality: "Data quality", dataLimit: "Data limitation", current: "Current", aging: "Aging", stale: "Stale" },
} as const;
function freshness(brief: AuditedAiBrief, language: Language = "zh") {
  const copy = workspaceCopy[language];
  const age = brief.freshness.age_seconds ?? 0;
  const limit = brief.freshness.threshold_seconds ?? 7200;
  if (brief.status === "STALE_AUDITED_REPORT" || brief.freshness.status === "STALE") return { label: copy.stale, tone: "stale" };
  if (age >= limit * .75) return { label: copy.aging, tone: "aging" };
  return { label: copy.current, tone: "current" };
}

export function DataCoverage({ brief, language = "zh" }: { brief: AuditedAiBrief; language?: Language }) {
  const items = brief.timeframe_quality || [];
  const copy = workspaceCopy[language];
  const quality = brief.evidence_quality;
  const rows = coverageMatrixRows(quality, language);
  return <div className="ai-coverage" data-testid="ai-coverage-matrix"><span className="ai-meta-label">{copy.coverage}</span>
    <div className="coverage-matrix">{rows.map((item) => <span className={`coverage-row ${item.state}`} key={item.key}><b>{item.label}</b><small>{item.state === "complete" ? "✓" : item.state === "partial" || item.state === "warning" ? "△" : "○"} {item.text}</small></span>)}</div>
    {items.length > 0 && <div className="coverage-timeframes" aria-label={language === "zh" ? "周期覆盖" : "Timeframe coverage"}>{items.map((item) => <span key={item.timeframe} className={item.availability === "AVAILABLE" ? "complete" : "partial"}>{item.timeframe} {item.availability === "AVAILABLE" ? "✓" : "△"}</span>)}</div>}
  </div>;
}

export function WorkspaceAiBrief({ instrument }: { instrument: string }) {
  const { language } = useLanguage();
  const copy = workspaceCopy[language];
  const [brief, setBrief] = useState<AuditedAiBrief | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const load = () => fetchAuditedAiBrief(instrument).then((value) => { if (active) setBrief(value); }).catch(() => { if (active) setBrief(null); }).finally(() => { if (active) setLoading(false); });
    setLoading(true); setBrief(null); void load();
    const timer = window.setInterval(load, 60_000);
    const visible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [instrument]);
  const current = Boolean(brief?.display_eligible && brief.status === "CURRENT_AUDITED_REPORT");
  const stale = Boolean(brief?.display_eligible && brief.status === "STALE_AUDITED_REPORT");
  const primaryState = workspaceAiPrimaryState(brief);
  const latestFailed = primaryState === "LATEST_FAILED";
  const fresh = brief ? freshness(brief, language) : null;
  const levels = presentAiLevels(brief?.levels);
  const scenarios = (brief?.scenarios || []).filter(item => isPresent(item.scenario_type));
  const intelligence = intelligenceCenter((brief || {}) as AuditedAiBrief, [], language);
  const drivers = (brief?.drivers || []).filter(item => isPresent(item.label)).filter((item, index, all) => all.findIndex(other => other.label === item.label && other.value === item.value) === index).slice(0, 3);
  const enumText = (value: unknown) => ["BEARISH_CONTINUATION", "BULLISH_CONTINUATION", "RANGE", "WAIT", "MIXED"].includes(String(value || ""))
    ? workspaceScenarioLabel(value, language)
    : translateKnownEnum(value, language);
  const headline = [intelligence.tactical || enumText(brief?.market_phase) || copy.watching, intelligence.alignment || enumText(brief?.directional_bias) || copy.observe].filter((item, index, all) => item && all.indexOf(item) === index).join(" · ");
  const deterministicSummary = language === "zh"
    ? [
      intelligence.frames.find(item => item.timeframe === "15m")?.state && `15m ${intelligence.frames.find(item => item.timeframe === "15m")?.state}`,
      intelligence.frames.find(item => item.timeframe === "1H")?.momentum && `1H ${intelligence.frames.find(item => item.timeframe === "1H")?.momentum}`,
      intelligence.frames.find(item => item.timeframe === "4H")?.extension && `4H ${intelligence.frames.find(item => item.timeframe === "4H")?.extension}`,
      intelligence.frames.find(item => item.timeframe === "1D")?.extension && `1D ${intelligence.frames.find(item => item.timeframe === "1D")?.extension}`,
    ].filter(Boolean).join("；")
    : `${intelligence.frames.find(item => item.timeframe === "15m")?.state || enumText(brief?.market_phase)}; ${intelligence.frames.find(item => item.timeframe === "1H")?.momentum || enumText(brief?.confidence)}; ${intelligence.frames.find(item => item.timeframe === "4H")?.extension || ""}.`;
  const providerSummary = compactAiSummary(brief?.executive_summary, 220, language);
  const limitationOnly = /限制展示|缺少可审计|unsupported|no auditable/i.test(providerSummary);
  const summary = limitationOnly && deterministicSummary ? deterministicSummary : providerSummary || deterministicSummary;
  const decision = language === "zh" && !containsCjk(brief?.decision_label) ? enumText(brief?.decision_label) : language === "zh" ? brief?.decision_label : copy.observe;
  return <section className={`ai-insight-hero ${stale ? "stale" : ""}`} data-testid="workspace-ai6b-brief" aria-labelledby="ai-insight-title">
    <header className="ai-hero-header">
      <div className="ai-hero-title"><BrainCircuit size={23} /><div><span className="eyebrow">{copy.eyebrow}</span><h2 id="ai-insight-title">{copy.title}</h2></div></div>
      <div className="ai-hero-badges"><span>{source(brief)}</span><span>{brief?.mode || "QUICK"}</span>{current && brief?.audit.status === "PASSED" && <span className="audit-pass" title={copy.auditTitle}><CheckCircle2 size={14} /> {copy.audit} · {brief.audit.overall_score ?? 100} / 100</span>}{!latestFailed && fresh && <span className={`freshness-badge ${fresh.tone}`}><Clock3 size={13} />{fresh.label}</span>}</div>
    </header>
    <div className="ai-hero-content">{loading ? <div className="ai-hero-empty"><strong>{copy.loading}</strong></div> : current && brief ? <div className="ai-hero-layout">
      <div className="ai-hero-primary">
        <span className="ai-conclusion-label">{copy.conclusion} · {decision || copy.observe}</span>
        <h3>{headline}</h3>
        <p>{summary}</p>
        <div className="ai-hero-section ai-hero-conditions" data-testid="workspace-ai-conditions"><span>{language === "zh" ? "短线结构与多周期" : "Tactical and multi-timeframe"}</span><ul>
          {(intelligence.tactical || intelligence.frames.find(item => item.timeframe === "15m")?.state) && <li><b>{language === "zh" ? "短线结构" : "Tactical"}</b><small>{intelligence.tactical || intelligence.frames.find(item => item.timeframe === "15m")?.state}</small></li>}
          {(intelligence.alignment || intelligence.frames.find(item => item.timeframe === "1H")?.momentum) && <li><b>{copy.multi}</b><small>{intelligence.alignment || intelligence.frames.find(item => item.timeframe === "1H")?.momentum}</small></li>}
          {intelligence.flow && <li><b>{language === "zh" ? "订单流 / OI" : "Flow / OI"}</b><small>{[intelligence.flow, intelligence.priceOi || intelligence.oi].filter(Boolean).join(" · ")}</small></li>}
          {!intelligence.tactical && !intelligence.alignment && !intelligence.flow && drivers.slice(0, 2).map((item, index) => <li key={`${item.label}-${index}`}><b>{language === "en" ? (item.label === "数据质量" ? copy.dataQuality : copy.dataLimit) : item.label}</b>{renderIfPresent(item.value, value => <small>{containsCjk(value) && language === "en" ? copy.limited : enumText(value)}</small>)}</li>)}
        </ul></div>
        <div className="ai-hero-section" data-testid="workspace-ai-levels"><span>{copy.levels}</span>{levels.length ? <ul>{levels.slice(0, 2).map((item) => <li key={item.level_id} data-level-card><b>{levelLabel(item as Record<string, unknown>, language)} · {item.primary_timeframe || copy.multi}</b><small>{item.representative_price}{renderIfPresent(item.invalidation, value => ` · ${copy.invalidation}: ${value}`)}</small></li>)}</ul> : <p className="ai-section-empty">{copy.noLevels}</p>}</div>
        {(intelligence.trigger || intelligence.invalidation || scenarios.length) && <div className="ai-hero-section" data-testid="workspace-ai-scenario"><span>{language === "zh" ? "确认 / 失效" : "Confirmation / invalidation"}</span><ul>
          {intelligence.trigger && <li><b>{language === "zh" ? "确认" : "Trigger"}</b><small>{intelligence.trigger}</small></li>}
          {intelligence.invalidation && <li><b>{copy.invalidation}</b><small>{intelligence.invalidation}</small></li>}
          {!intelligence.trigger && !intelligence.invalidation && scenarios.slice(0, 1).map((item) => <li key={item.scenario_id}><b>{workspaceScenarioLabel(item.scenario_type, language)}</b><small>{item.trigger_text || item.invalidation_text || ""}</small></li>)}
        </ul></div>}
      </div>
      <div className="ai-hero-meta">
        <dl><div><dt>{copy.updated}</dt><dd>{when(brief.generated_at)}</dd></div><div><dt>{copy.dataTime}</dt><dd>{when(brief.market_snapshot_at)}</dd></div><div><dt>{copy.phase}</dt><dd>{enumText(brief.market_phase) || "—"}</dd></div><div><dt>{copy.confidence}</dt><dd>{enumText(brief.confidence) || "—"}</dd></div></dl>
        <DataCoverage brief={brief} language={language} />
        <a className="secondary-btn ai-research-link" href={`#research/report/${encodeURIComponent(brief.report_id)}`}>{copy.report} <ExternalLink size={14} /></a>
      </div>
    </div> : latestFailed && brief ? <div className="ai-hero-empty failed"><ShieldCheck size={20} /><strong>{copy.auditFailed}</strong><p>{copy.unavailableBody}</p>{brief.last_display_eligible_report && <p>{copy.lastValid}: {when(brief.last_display_eligible_report.generated_at)} · {copy.stale}</p>}<a className="secondary-btn ai-research-link" href={`#research/report/${encodeURIComponent(brief.last_display_eligible_report?.report_id || brief.report_id)}`}>{copy.history} <ExternalLink size={14} /></a></div> : stale && brief ? <div className="ai-hero-empty stale"><strong>{copy.staleTitle}</strong><p>{copy.lastValid}: {when(brief.generated_at)}. {copy.staleBody}</p>{brief.scheduler?.enabled && <p>{copy.next}: {when(brief.scheduler.next_tick)}</p>}<DataCoverage brief={brief} language={language} /><a className="secondary-btn ai-research-link" href={`#research/report/${encodeURIComponent(brief.report_id)}`}>{copy.history} <ExternalLink size={14} /></a></div> : <div className="ai-hero-empty failed"><ShieldCheck size={20} /><strong>{brief?.latest_generated?.eligibility === "AUDIT_FAILED" ? copy.auditFailed : copy.unavailable}</strong><p>{copy.unavailableBody}</p></div>}</div>
  </section>;
}

function LegacyAiReportResearch({ instrument }: { instrument: string }) {
  const [items, setItems] = useState<AuditedAiBrief[]>([]);
  const [detail, setDetail] = useState<AuditedAiReportDetail | null>(null);
  const open = (item: AuditedAiBrief) => fetchAuditedAiReport(instrument, item.report_id, item.mode).then(setDetail).catch(() => setDetail(null));
  useEffect(() => {
    let active = true; setItems([]); setDetail(null);
    const load = () => fetchAuditedAiHistory(instrument).then((value) => {
      if (!active) return; setItems(value);
      const requested = decodeURIComponent(window.location.hash.split("/report/")[1] || "");
      const target = value.find((item) => item.report_id === requested) || value.find((item) => item.display_eligible);
      if (target) open(target);
    }).catch(() => undefined);
    void load(); const timer = window.setInterval(load, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [instrument]);
  return <section className="ai-report-research" data-testid="research-ai6b-reports">
    <div className="section-title"><div><span className="eyebrow">AI 深度中心 · 审计报告</span><h2>Latest Analysis</h2></div><span className="muted">QUICK · FULL · POSITION（可用时）</span></div>
    {detail?.report && <article className="ai-report-detail"><div className="ai-report-detail-head"><div><span className="status-pill healthy">审计状态：通过 · {detail.summary.audit.overall_score ?? 100}/100</span><span className={`freshness-badge ${freshness(detail.summary).tone}`}>时效状态：{freshness(detail.summary).label}</span></div><small>{detail.summary.status === "STALE_AUDITED_REPORT" ? "历史有效报告 · 已过期" : "当前有效报告"} · {when(detail.summary.market_snapshot_at)}</small></div><h3>{detail.report.headline}</h3><DataCoverage brief={detail.summary} />{detail.report.sections.map((section) => <section key={section.section_id}><h4>{section.title || section.section_id}</h4><p>{section.body}</p>{section.uncertainties?.map((item) => <small key={item}>· {item}</small>)}</section>)}</article>}
    <div className="ai-history-title"><h3>History</h3><span>历史报告不会替代当前状态</span></div>
    {!items.length ? <p className="muted">暂无报告记录。</p> : <div className="ai-report-history">{items.map((item) => <button key={item.report_id} onClick={() => open(item)} aria-label={`打开 ${item.mode} AI 报告`}><b>{item.mode}</b><span>{when(item.generated_at)}</span><span className={`status-pill ${item.display_eligible ? "healthy" : "unhealthy"}`}>{item.display_eligible ? `Audit ${item.audit.overall_score ?? "PASS"}` : "不可展示"}</span></button>)}</div>}
  </section>;
}

function routeReportId() {
  const match = window.location.hash.match(/^#research\/report\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function AiDeepResearch({ detail, language }: { detail: AuditedAiReportDetail; language: Language }) {
  if (!detail.report) return null;
  const brief = { ...detail.summary, intelligence: detail.report.intelligence || detail.summary.intelligence };
  const center = intelligenceCenter(brief, detail.report.sections, language);
  const frames = center.frames.filter(visibleFrame);
  const mediumTerm = frames.filter(frame => frame.timeframe === "1H" || frame.timeframe === "4H").flatMap(frame => [
    frame.localLow != null ? { key: `${frame.timeframe}-low`, label: `${frame.timeframe} ${language === "zh" ? "结构支撑" : "structural support"}`, price: frame.localLow } : null,
    frame.localHigh != null ? { key: `${frame.timeframe}-high`, label: `${frame.timeframe} ${language === "zh" ? "结构压力" : "structural resistance"}`, price: frame.localHigh } : null,
  ]).filter((item): item is { key: string; label: string; price: number } => item != null);
  const labels = language === "zh" ? {
    conclusion: "总体结论", timeframe: "多周期结构", derivatives: "订单流 / 衍生品", volume: "成交量与推进", map: "价格地图", current: "当前关键位置", medium: "中周期结构边界", long: "长期参考", scenarios: "场景", trigger: "触发", invalidation: "失效", audit: "审计与证据", macro: "宏观背景：本轮未纳入",
  } : {
    conclusion: "Overall conclusion", timeframe: "Multi-timeframe structure", derivatives: "Order flow / derivatives", volume: "Volume and impulse", map: "Price map", current: "Current key levels", medium: "Medium-term structural boundaries", long: "Long-term reference", scenarios: "Scenarios", trigger: "Trigger", invalidation: "Invalidation", audit: "Audit and evidence", macro: "Macro context: not included in this report",
  };
  const conclusion = detail.report.sections.find(section => section.section_id === "CONCLUSION")?.body;
  const limitations = [...new Set(detail.report.sections.filter(section => section.section_id === "LIMITATIONS").flatMap(section => [section.body, ...(section.uncertainties || [])]).filter(Boolean).map(item => localizeWorkspaceNarrative(item, language)) as string[])];
  return <div className="ai-deep-center" data-testid="research-ai-deep-center">
    <section className="ai-deep-summary" data-testid="research-ai-overall"><span className="eyebrow">{labels.conclusion}</span><h4>{[center.tactical, center.alignment].filter(Boolean).join(" · ") || detail.report.headline}</h4><p>{conclusion && language === "zh" ? localizeWorkspaceNarrative(conclusion, language) : crossTimeframeNarrative(center, language)}</p></section>
    <section className="ai-deep-section ai-deep-synthesis" data-testid="research-ai-synthesis"><div className="ai-deep-heading"><span className="eyebrow">{language === "zh" ? "多周期综合" : "Multi-timeframe synthesis"}</span><h4>{language === "zh" ? "多周期综合" : "Multi-timeframe synthesis"}</h4></div><p>{crossTimeframeNarrative(center, language)}</p></section>
    {frames.length > 0 && <section className="ai-deep-section" data-testid="research-ai-timeframes"><div className="ai-deep-heading"><span className="eyebrow">{labels.timeframe}</span><h4>{labels.timeframe}</h4></div><div className="ai-timeframe-grid">
      {frames.map(frame => <article key={frame.timeframe} data-timeframe-card><div><b>{frame.timeframe}</b><span>{frame.role}</span></div>{frame.state && <strong>{frame.state}</strong>}{frame.momentum && <p>{language === "zh" ? "动量：" : "Momentum: "}{frame.momentum}</p>}{frame.extension && <p>{language === "zh" ? "伸展：" : "Extension: "}{frame.extension}</p>}{frame.observation && <small>{frame.observation}</small>}</article>)}
    </div><div className="ai-timeframe-narratives" data-testid="research-ai-timeframe-narratives">{frames.map(frame => <article key={`${frame.timeframe}-narrative`} data-testid={`research-ai-narrative-${frame.timeframe}`}><div><b>{frame.timeframe}</b><span>{frame.role}</span></div><p>{frameNarrative(frame, language)}</p>{frame.timeframe === "15m" && (center.trigger || center.invalidation) && <dl>{center.trigger && <div><dt>{labels.trigger}</dt><dd>{center.trigger}</dd></div>}{center.invalidation && <div><dt>{labels.invalidation}</dt><dd>{center.invalidation}</dd></div>}</dl>}</article>)}</div></section>}
    {(center.flow || center.oi || center.priceOi) && <section className="ai-deep-section" data-testid="research-ai-flow"><div className="ai-deep-heading"><span className="eyebrow">{labels.derivatives}</span><h4>{labels.derivatives}</h4></div><div className="ai-deep-facts">
      {center.flow && <article><b>CVD</b><span>{center.flow}</span></article>}{center.oi && <article><b>OI</b><span>{center.oi}</span></article>}{center.priceOi && <article><b>Price × OI</b><span>{center.priceOi}</span></article>}
    </div><p className="ai-deep-prose">{derivativesNarrative(center, language)}</p></section>}
    {(center.volume || center.impulse) && <section className="ai-deep-section" data-testid="research-ai-volume"><div className="ai-deep-heading"><span className="eyebrow">{labels.volume}</span><h4>{labels.volume}</h4></div><div className="ai-deep-facts">{center.impulse && <article><b>{language === "zh" ? "推进" : "Impulse"}</b><span>{center.impulse}</span></article>}{center.volume && <article><b>{language === "zh" ? "量能" : "Volume"}</b><span>{center.volume}</span></article>}</div><p className="ai-deep-prose">{volumeImpulseNarrative(center, language)}</p></section>}
    {(center.priceMap.length || mediumTerm.length || center.longTerm.length) && <section className="ai-deep-section" data-testid="research-ai-price-map"><div className="ai-deep-heading"><span className="eyebrow">{labels.map}</span><h4>{labels.map}</h4></div>{center.priceMap.length > 0 && <><h5>{labels.current}</h5><div className="ai-deep-facts">{center.priceMap.slice(0, 4).map((level, index) => <article key={String(level.level_id || index)}><b>{levelLabel(level, language)}</b><span>{String(level.representative_price ?? "")}</span></article>)}</div></>}{mediumTerm.length > 0 && <><h5>{labels.medium}</h5><div className="ai-deep-facts">{mediumTerm.map(level => <article key={level.key}><b>{level.label}</b><span>{String(level.price)}</span></article>)}</div></>}{center.longTerm.length > 0 && <><h5>{labels.long}</h5><div className="ai-deep-facts long-term">{center.longTerm.slice(0, 4).map((level, index) => <article key={String(level.level_id || index)}><b>{levelLabel(level, language)}</b><span>{String(level.representative_price ?? "")}</span></article>)}</div></>}</section>}
    {center.scenarios.length > 0 && <section className="ai-deep-section" data-testid="research-ai-scenarios"><div className="ai-deep-heading"><span className="eyebrow">{labels.scenarios}</span><h4>{labels.scenarios}</h4></div><div className="ai-scenario-grid">{center.scenarios.slice(0, 3).map((scenario, index) => <article key={String(scenario.scenario_id || index)}><strong>{scenarioLabel(scenario, language)}</strong>{Boolean(scenario.trigger_text) && <p><b>{labels.trigger}</b>{localizeAiRule(scenario.trigger_text, language)}</p>}{Boolean(scenario.confirmation_text) && <p><b>{language === "zh" ? "确认" : "Confirmation"}</b>{localizeAiRule(scenario.confirmation_text, language)}</p>}{Boolean(scenario.invalidation_text) && <p><b>{labels.invalidation}</b>{localizeAiRule(scenario.invalidation_text, language)}</p>}</article>)}</div></section>}
    <section className="ai-deep-section ai-deep-audit" data-testid="research-ai-audit"><div className="ai-deep-heading"><span className="eyebrow">{labels.audit}</span><h4>{labels.audit}</h4></div><p>{language === "zh" ? "审计分数" : "Audit score"}: {String(center.auditScore ?? "—")} · {language === "zh" ? "宏观背景：本轮未纳入" : labels.macro}</p></section>
    {limitations.length > 0 && <section className="ai-deep-limitations"><h4>{language === "zh" ? "数据边界" : "Data boundaries"}</h4>{limitations.map(item => <p key={item}>{item}</p>)}</section>}
  </div>;
}

export function AiReportResearch({ instrument }: { instrument: string }) {
  const { language } = useLanguage();
  const copy = researchPresentationCopy[language];
  // The provider is zh-CN-only. English is a deterministic presentation locale.
  const reportLanguage = "zh-CN";
  const [items, setItems] = useState<AuditedAiBrief[]>([]);
  const [detail, setDetail] = useState<AuditedAiReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [requestedReportId, setRequestedReportId] = useState(routeReportId);
  const generation = useRef(0);

  useEffect(() => {
    const sync = () => setRequestedReportId(routeReportId());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  useEffect(() => {
    const request = ++generation.current;
    setLoading(true); setFailed(false); setItems([]); setDetail(null);
    void (async () => {
      try {
        const history = await fetchAuditedAiHistory(instrument, reportLanguage);
        if (request !== generation.current) return;
        setItems(history);
        const selected = selectResearchReport(history, instrument, requestedReportId);
        if (!selected) return;
        const report = await fetchAuditedAiReport(instrument, selected.report_id, selected.mode, reportLanguage);
        if (request !== generation.current) return;
        if (report.summary.display_eligible && report.summary.audit.status === "PASSED") setDetail(report);
      } catch {
        if (request === generation.current) { setFailed(true); setItems([]); setDetail(null); }
      } finally {
        if (request === generation.current) setLoading(false);
      }
    })();
    return () => { generation.current += 1; };
  }, [instrument, requestedReportId, reportLanguage]);

  const select = (item: AuditedAiBrief) => {
    if (item.instrument !== instrument) return;
    window.location.hash = `research/report/${encodeURIComponent(item.report_id)}`;
  };
  const fresh = detail ? freshness(detail.summary, language) : null;
  return <section className="ai-report-research" data-testid="research-ai6b-reports">
    <div className="section-title"><div><span className="eyebrow">{copy.eyebrow}</span><h2>{copy.latest}</h2></div><span className="muted">{copy.modes}</span></div>
    {loading ? <p className="muted">{copy.loading}</p> : failed ? <p className="muted">{copy.requestFailed}</p> : detail?.report ? <article className="ai-report-detail">
      <div className="ai-report-detail-head"><div><span className="status-pill healthy">{copy.auditPassed} · {detail.summary.audit.overall_score ?? 100}/100</span><span className={`freshness-badge ${fresh?.tone}`}>{fresh?.label}</span></div><small>{detail.summary.status === "STALE_AUDITED_REPORT" ? copy.stale : copy.current} · {when(detail.summary.market_snapshot_at)}</small></div>
      <DataCoverage brief={detail.summary} language={language} />
      <AiDeepResearch detail={detail} language={language} />
    </article> : requestedReportId ? <p className="muted">{copy.unavailable}</p> : null}
    <div className="ai-history-title"><h3>{copy.history}</h3><span>{copy.historyHint}</span></div>
    {!loading && !items.length ? <p className="muted">{copy.empty}</p> : <div className="ai-report-history">{items.map((item) => <button key={item.report_id} onClick={() => select(item)} aria-label={`${copy.openReport}: ${item.mode}`}><b>{item.mode}</b><span>{when(item.generated_at)}</span><span className={`status-pill ${item.display_eligible ? "healthy" : "unhealthy"}`}>{item.display_eligible ? `${copy.auditPassed} ${item.audit.overall_score ?? ""}` : copy.auditHidden}</span></button>)}</div>}
  </section>;
}
