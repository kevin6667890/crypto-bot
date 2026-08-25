import { useEffect, useState } from "react";
import { useLanguage } from "./i18n";

type ResearchCounts = { development_candidates: number; eligible_finalists: number; rejected_candidates: number; screened_candidates?: number };
type Cycle = { id: number; status: string; research_start: number; research_end: number; completed_at?: string; dataset_fingerprint?: string; checkpoint?: Record<string, unknown>; research_counts?: ResearchCounts; result?: { approved?: number; active_registry_id?: string } };
type ActiveStrategy = { family?: string; strategy_version?: string; direction_capability?: string; research_cycle_id?: number; registry_id?: string; serialized_definition?: { validation_status?: Record<string, string> } };
type Scheduler = { interval_hours?: number; next_due_at?: string };
type Summary = { latest_cycle: Cycle | null; recent_cycles: Cycle[]; active_strategy: ActiveStrategy | null; scheduler: Scheduler | null; scheduler_enabled: boolean; interval_hours?: number; next_due_at?: string };
type Reason = { code: string; count: number; percentage: number; description: string };
type Candidate = { candidate_id: number; candidate_number: number; description: string; direction: string; complexity: number; development_score: number | null; eligibility_status: string; rejection_reasons: string[] };
type Diagnostics = { diagnostics_available: boolean; cycle_summary?: { total_candidates: number; eligible_candidates: number; rejected_candidates: number }; rejection_summary?: Reason[]; items?: Candidate[]; page: number; page_size: number; total_items: number };

const day = (timestamp?: number) => timestamp ? new Date(timestamp * 1000).toISOString().slice(0, 10) : "—";
const compactHash = (input?: string) => input ? `${input.slice(0, 12)}…` : "—";
const formatTime = (value?: string) => value ? `${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value))} UTC` : "—";
const tone = (status?: string) => {
  const current = (status || "").toUpperCase();
  if (["COMPLETED", "PASS", "ACTIVE", "APPROVED", "ENABLED"].includes(current)) return "healthy";
  if (["FAILED", "CANCELLED", "INTERRUPTED", "NO_ELIGIBLE", "REJECTED"].includes(current)) return "warning";
  return "neutral";
};

export default function DiscoveryLab() {
  const { language, t, value } = useLanguage();
  const zh = language === "zh";
  const local = zh ? {
    refresh: "刷新", latest: "最近周期与验证", registry: "验证 / 注册表", mode: "发现模式", program: "程序 / 模板", awaiting: "等待周期数据", generated: "生成 / 筛选", approvedRegistry: "获批注册表", researchCycle: "研究周期", registryStatus: "注册状态", noStrategy: "暂无获批策略", noStrategyHelp: "自动研究仅用于研究，真实交易已禁用。", recent: "近期研究", noCycles: "尚无研究周期记录。", disclaimer: "发现结果不会绕过验证或获批注册表。", validation: "验证进度",
    diagnostics: "高级诊断：淘汰原因分析", rejectionEyebrow: "开发阶段淘汰分析", evaluated: "个候选已评估", eligible: "合格", rejected: "淘汰", screeningHelp: "筛选条件属于研究证据，不代表未来盈利预估。", allGates: "全部未通过门槛", search: "搜索候选", candidate: "程序 / 候选", direction: "方向", complexity: "复杂度", failedGates: "未通过门槛", page: "第", diagnosticsUnavailable: "该历史周期暂无详细淘汰诊断。", details: "详情：数据集指纹与研究元数据", registryId: "注册表 ID",
  } : {
    refresh: "Refresh", latest: "Latest cycle & validation", registry: "Validation / Registry", mode: "Discovery mode", program: "Program / Template", awaiting: "Awaiting cycle data", generated: "Generated / screened", approvedRegistry: "Approved registry", researchCycle: "Research cycle", registryStatus: "Registry status", noStrategy: "No approved strategy", noStrategyHelp: "Automatic Research remains research-only. Live trading is disabled.", recent: "Recent research", noCycles: "No research cycles recorded.", disclaimer: "Discovery output never bypasses validation or the approved registry.", validation: "Validation progress",
    diagnostics: "Advanced diagnostics: rejection analysis", rejectionEyebrow: "Development rejection analysis", evaluated: "candidates evaluated", eligible: "eligible", rejected: "rejected", screeningHelp: "Screening conditions are research evidence, not estimates of future profitability.", allGates: "All failed gates", search: "Search candidate", candidate: "Program / Candidate", direction: "Direction", complexity: "Complexity", failedGates: "Failed Gates", page: "Page", diagnosticsUnavailable: "Detailed rejection diagnostics are unavailable for this historical cycle.", details: "Details: dataset fingerprint and research metadata", registryId: "Registry ID",
  };
  const researchStatus = (status?: string) => status === "NOT_RUN" ? (zh ? "尚未运行" : "Not run") : value(status || "");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState("");
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [selectedCycle, setSelectedCycle] = useState<number>();
  const [reason, setReason] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const load = () => {
    setMessage("");
    return fetch("/api/automatic-research")
      .then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<Summary>; })
      .then(setSummary)
      .catch(() => setMessage(t("autoResearch.unavailable", { error: "" })));
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const cycleId = selectedCycle || summary?.latest_cycle?.id;
    if (!cycleId) { setDiagnostics(null); return; }
    const query = new URLSearchParams({ page: String(page), page_size: "25", eligibility: "REJECTED" });
    if (reason) query.set("reason", reason);
    if (search) query.set("search", search);
    let active = true;
    void fetch(`/api/automatic-research/cycles/${cycleId}/diagnostics?${query}`)
      .then(response => response.ok ? response.json() as Promise<Diagnostics> : null)
      .then(result => { if (active) setDiagnostics(result); })
      .catch(() => { if (active) setDiagnostics(null); });
    return () => { active = false; };
  }, [selectedCycle, summary?.latest_cycle?.id, reason, search, page]);

  const latest = summary?.latest_cycle;
  const active = summary?.active_strategy;
  const scheduler = summary?.scheduler;
  const counts = latest?.research_counts || { development_candidates: 0, eligible_finalists: 0, rejected_candidates: 0 };
  const checkpointStages = Object.keys(latest?.checkpoint || {});
  const progress = checkpointStages[checkpointStages.length - 1]?.replace(/_complete$/i, " completed") || latest?.status || "NOT_RUN";
  const complete = latest?.status === "COMPLETED";
  const noEligibleCandidate = complete && counts.development_candidates > 0 && counts.eligible_finalists === 0;
  const validation = active?.serialized_definition?.validation_status || {};
  const stages = [[zh ? "开发阶段" : "Development", validation.development || (complete ? "COMPLETED" : latest?.status || "NOT_RUN")], [zh ? "滚动验证" : "Walk-forward", validation.walk_forward || "NOT_RUN"], ["Holdout", validation.holdout || "NOT_RUN"], ["OOT", validation.oot || "NOT_RUN"], [zh ? "跨资产" : "Cross-asset", validation.cross_asset || "NOT_RUN"], [zh ? "稳健性" : "Robustness", validation.robustness || "NOT_RUN"], [t("autoResearch.approved"), active ? "APPROVED" : "NOT_RUN"]];
  const interval = summary?.interval_hours || scheduler?.interval_hours;
  const nextDue = summary?.next_due_at || scheduler?.next_due_at;
  const setFilter = (nextReason: string) => { setReason(nextReason); setPage(1); };

  return <section className="research-panel automatic-research-panel" data-automatic-research>
    <div className="research-panel-head automatic-research-head"><div><span className="eyebrow">{t("autoResearch.eyebrow")}</span><h2>{t("autoResearch.title")}</h2><p>{t("autoResearch.subtitle")}</p></div><button className="text-button" onClick={() => void load()}>{local.refresh}</button></div>
    {message && <p className="research-alert error" role="alert">{message}</p>}
    <div className="automatic-research-summary">
      <article><span>{t("autoResearch.latestCycle")}</span><div className="automatic-research-card-title"><b>{latest ? `#${latest.id}` : "—"}</b>{latest && <i className={`status-pill ${tone(latest.status)}`}>{researchStatus(latest.status)}</i>}</div><small>{latest ? `${day(latest.research_start)} → ${day(latest.research_end)}` : t("autoResearch.noCycle")}</small></article>
      <article><span>{t("autoResearch.researchProgress")}</span><b>{researchStatus(progress)}</b><small>{t("autoResearch.candidatesEvaluated", { count: counts.development_candidates })} · {t("autoResearch.eligibleFinalists")} {counts.eligible_finalists}</small></article>
      <article><span>{t("autoResearch.activeStrategy")}</span><b>{active?.family || t("autoResearch.noApprovedCandidate")}</b><small>{active ? `${active.strategy_version || "—"} · ${active.direction_capability || "—"}` : t("autoResearch.noApprovedCandidate")}</small></article>
      <article><span>{t("autoResearch.scheduler")}</span><div className="automatic-research-card-title"><b>{summary?.scheduler_enabled ? t("autoResearch.enabled") : t("autoResearch.disabled")}</b><i className={`status-pill ${summary?.scheduler_enabled ? "healthy" : "neutral"}`}>{interval ? t("autoResearch.everyHours", { hours: interval }) : "—"}</i></div><small>{t("autoResearch.nextRun", { time: formatTime(nextDue) })}</small></article>
    </div>
    <section className="automatic-research-details"><div className="automatic-research-section-head"><div><span className="eyebrow">{local.latest}</span><h3>{local.registry}</h3></div>{latest && <i className={`status-pill ${tone(latest.status)}`}>{researchStatus(latest.status)}</i>}</div>
      <dl className="automatic-research-facts"><div><dt>{t("autoResearch.datasetRange")}</dt><dd>{latest ? `${day(latest.research_start)} → ${day(latest.research_end)}` : "—"}</dd></div><div><dt>{local.mode}</dt><dd>{latest?.result?.active_registry_id ? local.program : local.awaiting}</dd></div><div><dt>{local.generated}</dt><dd>{counts.development_candidates} / {counts.screened_candidates ?? counts.development_candidates}</dd></div><div><dt>{t("autoResearch.eligibleFinalists")}</dt><dd>{counts.eligible_finalists}</dd></div><div><dt>{t("autoResearch.approved")} / {t("autoResearch.rejected")}</dt><dd>{latest?.result?.approved || 0} / {counts.rejected_candidates}</dd></div><div><dt>{t("autoResearch.completed")}</dt><dd>{formatTime(latest?.completed_at)}</dd></div></dl>
      <div className="automatic-research-validation" aria-label={local.validation}>{stages.map(([label, status]) => <div key={label}><span>{label}</span><b className={`validation-badge ${tone(status)}`}>{researchStatus(status)}</b></div>)}</div>
      {noEligibleCandidate && <p className="automatic-research-notice"><strong>{t("autoResearch.noEligibilityTitle")}</strong><span>{t("autoResearch.noEligibilityHelp")}</span></p>}
      <details className="advanced-rejection-diagnostics"><summary>{local.diagnostics}</summary>
        {diagnostics?.diagnostics_available && <section className="rejection-diagnostics"><div className="automatic-research-section-head"><div><span className="eyebrow">{local.rejectionEyebrow}</span><h3>{diagnostics.cycle_summary?.total_candidates} {local.evaluated} · {diagnostics.cycle_summary?.eligible_candidates} {local.eligible} · {diagnostics.cycle_summary?.rejected_candidates} {local.rejected}</h3></div>{summary?.recent_cycles?.length ? <select value={selectedCycle || latest?.id || ""} onChange={event => { setSelectedCycle(Number(event.target.value)); setPage(1); }} aria-label={local.researchCycle}>{summary.recent_cycles.map(cycle => <option key={cycle.id} value={cycle.id}>{local.researchCycle} #{cycle.id}</option>)}</select> : null}</div><p>{local.screeningHelp}</p><div className="reason-list">{diagnostics.rejection_summary?.map(item => <div className="reason-row" key={item.code}><div><b>{item.code}</b><small>{item.description}</small></div><span>{item.count} {t("autoResearch.candidates")} · {item.percentage.toFixed(1)}%</span><i><em style={{ width: `${item.percentage}%` }} /></i></div>)}</div><div className="table-controls"><select value={reason} onChange={event => setFilter(event.target.value)}><option value="">{local.allGates}</option>{diagnostics.rejection_summary?.map(item => <option key={item.code} value={item.code}>{item.code}</option>)}</select><input value={search} placeholder={local.search} onChange={event => { setSearch(event.target.value); setPage(1); }} /></div><div className="research-table-wrap"><table><thead><tr><th>{local.candidate}</th><th>{local.direction}</th><th>{local.complexity}</th><th>{t("common.score")}</th><th>{local.failedGates}</th><th>{t("common.status")}</th></tr></thead><tbody>{diagnostics.items?.map(item => <tr key={item.candidate_id}><td>{item.description} #{item.candidate_number}</td><td>{item.direction}</td><td>{item.complexity}</td><td>{item.development_score?.toFixed(2) || "—"}</td><td>{item.rejection_reasons.join(", ")}</td><td>{value(item.eligibility_status)}</td></tr>)}</tbody></table></div><div className="research-pagination"><button disabled={page === 1} onClick={() => setPage(page - 1)}>{t("common.previous")}</button><span>{local.page} {page}</span><button disabled={page * diagnostics.page_size >= diagnostics.total_items} onClick={() => setPage(page + 1)}>{t("common.next")}</button></div></section>}
        {diagnostics && !diagnostics.diagnostics_available && <p className="research-empty compact">{local.diagnosticsUnavailable}</p>}
      </details>
      <details className="automatic-research-fingerprint"><summary>{local.details} · {compactHash(latest?.dataset_fingerprint)}</summary><code>{t("autoResearch.datasetFingerprint")}: {latest?.dataset_fingerprint || t("common.notAvailable")}<br />{local.registryId}: {active?.registry_id || latest?.result?.active_registry_id || t("common.notAvailable")}</code></details>
    </section>
    <section className="automatic-research-approved"><div className="automatic-research-section-head"><div><span className="eyebrow">{local.approvedRegistry}</span><h3>{t("autoResearch.approved")} / {t("autoResearch.activeStrategy")}</h3></div></div>{active ? <div className="automatic-research-active"><div><span>{t("autoResearch.activeStrategy")}</span><b>{active.family}</b></div><div><span>{local.researchCycle}</span><b>#{active.research_cycle_id || "—"}</b></div><div><span>{local.registryStatus}</span><b>{t("autoResearch.approved")}</b></div></div> : <div className="research-empty compact"><strong>{local.noStrategy}</strong><span>{local.noStrategyHelp}</span></div>}</section>
    <section className="automatic-research-recent"><div className="automatic-research-section-head"><div><span className="eyebrow">{t("autoResearch.evidence")}</span><h3>{local.recent}</h3></div></div><div className="research-table-wrap"><table><thead><tr><th>{t("autoResearch.cycle")}</th><th>{t("autoResearch.range")}</th><th>{t("common.status")}</th><th>{t("autoResearch.candidates")}</th><th>{t("autoResearch.eligibleFinalists")}</th><th>{t("autoResearch.approved")}</th><th>{t("autoResearch.completed")}</th></tr></thead><tbody>{summary?.recent_cycles?.length ? summary.recent_cycles.map(cycle => <tr key={cycle.id}><td>#{cycle.id}</td><td>{day(cycle.research_start)} → {day(cycle.research_end)}</td><td><i className={`status-pill ${tone(cycle.status)}`}>{value(cycle.status)}</i></td><td>{cycle.research_counts?.development_candidates || 0}</td><td>{cycle.research_counts?.eligible_finalists || 0}</td><td>{cycle.result?.approved || 0}</td><td>{formatTime(cycle.completed_at)}</td></tr>) : <tr><td colSpan={7}>{local.noCycles}</td></tr>}</tbody></table></div></section>
    <footer className="automatic-research-footnotes"><strong>{t("autoResearch.liveDisabled")}</strong><span>{local.disclaimer}</span></footer>
  </section>;
}
