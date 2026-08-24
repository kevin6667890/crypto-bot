import { useEffect, useState } from "react";

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
  const value = (status || "").toUpperCase();
  if (["COMPLETED", "PASS", "ACTIVE", "APPROVED", "ENABLED"].includes(value)) return "healthy";
  if (["FAILED", "CANCELLED", "INTERRUPTED", "NO_ELIGIBLE", "REJECTED"].includes(value)) return "warning";
  return "neutral";
};

export default function DiscoveryLab() {
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
      .catch(() => setMessage("Automatic Research data is temporarily unavailable."));
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const cycleId = selectedCycle || summary?.latest_cycle?.id;
    if (!cycleId) { setDiagnostics(null); return; }
    const query = new URLSearchParams({ page: String(page), page_size: "25", eligibility: "REJECTED" });
    if (reason) query.set("reason", reason);
    if (search) query.set("search", search);
    let active = true;
    // Optional for older paper-api deployments: absence must never make research look failed.
    void fetch(`/api/automatic-research/cycles/${cycleId}/diagnostics?${query}`)
      .then(response => response.ok ? response.json() as Promise<Diagnostics> : null)
      .then(value => { if (active) setDiagnostics(value); })
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
  const stages = [["Development", validation.development || (complete ? "COMPLETED" : latest?.status || "NOT_RUN")], ["Walk-forward", validation.walk_forward || "NOT_RUN"], ["Holdout", validation.holdout || "NOT_RUN"], ["OOT", validation.oot || "NOT_RUN"], ["Cross-asset", validation.cross_asset || "NOT_RUN"], ["Robustness", validation.robustness || "NOT_RUN"], ["Approved", active ? "APPROVED" : "NOT_RUN"]];
  const interval = summary?.interval_hours || scheduler?.interval_hours;
  const nextDue = summary?.next_due_at || scheduler?.next_due_at;
  const setFilter = (nextReason: string) => { setReason(nextReason); setPage(1); };

  return <section className="research-panel automatic-research-panel" data-automatic-research>
    <div className="research-panel-head automatic-research-head"><div><span className="eyebrow">RESEARCH EVIDENCE · AUTOMATIC RESEARCH</span><h2>Automatic Research</h2><p>Candidate generation → deterministic validation → Approved Registry → paper-eligible strategy.</p></div><button className="text-button" onClick={() => void load()}>Refresh</button></div>
    {message && <p className="research-alert error" role="alert">{message}</p>}
    <div className="automatic-research-summary">
      <article><span>Latest Validation</span><div className="automatic-research-card-title"><b>{latest ? `#${latest.id}` : "—"}</b>{latest && <i className={`status-pill ${tone(latest.status)}`}>{latest.status}</i>}</div><small>{latest ? `${day(latest.research_start)} → ${day(latest.research_end)}` : "No completed research cycle"}</small></article>
      <article><span>Validation progress</span><b>{progress}</b><small>{counts.development_candidates} candidates generated · {counts.eligible_finalists} eligible</small></article>
      <article><span>Approved / Active</span><b>{active?.family || "ACTIVE NONE"}</b><small>{active ? `${active.strategy_version || "Version unavailable"} · ${active.direction_capability || "Direction unavailable"}` : "No approved registry entry; canonical Paper remains WAIT."}</small></article>
      <article><span>Automatic Research</span><div className="automatic-research-card-title"><b>{summary?.scheduler_enabled ? "Enabled" : "Disabled"}</b><i className={`status-pill ${summary?.scheduler_enabled ? "healthy" : "neutral"}`}>{interval ? `Every ${interval}h` : "—"}</i></div><small>Next run: {formatTime(nextDue)}</small></article>
    </div>
    <section className="automatic-research-details"><div className="automatic-research-section-head"><div><span className="eyebrow">LATEST VALIDATION</span><h3>Deterministic validation evidence</h3></div>{latest && <i className={`status-pill ${tone(latest.status)}`}>{latest.status}</i>}</div>
      <dl className="automatic-research-facts"><div><dt>Dataset range</dt><dd>{latest ? `${day(latest.research_start)} → ${day(latest.research_end)}` : "—"}</dd></div><div><dt>Discovery mode</dt><dd>{latest?.result?.active_registry_id ? "Program / Template" : "Awaiting cycle data"}</dd></div><div><dt>Generated / screened</dt><dd>{counts.development_candidates} / {counts.screened_candidates ?? counts.development_candidates}</dd></div><div><dt>Eligible finalists</dt><dd>{counts.eligible_finalists}</dd></div><div><dt>Approved / rejected</dt><dd>{latest?.result?.approved || 0} / {counts.rejected_candidates}</dd></div><div><dt>Completed</dt><dd>{formatTime(latest?.completed_at)}</dd></div></dl>
      <div className="automatic-research-validation" aria-label={"Validation progress"}>{stages.map(([label, status]) => <div key={label}><span>{label}</span><b className={`validation-badge ${tone(status)}`}>{status}</b></div>)}</div>
      {noEligibleCandidate && <p className="automatic-research-notice"><strong>No candidate passed Development eligibility</strong><span>NOT_RUN validation stages are expected when no finalist advances; they do not indicate a system failure.</span></p>}
      <details className="advanced-rejection-diagnostics"><summary>Advanced diagnostics: rejection analysis</summary>
        {diagnostics?.diagnostics_available && <section className="rejection-diagnostics"><div className="automatic-research-section-head"><div><span className="eyebrow">DEVELOPMENT REJECTION ANALYSIS</span><h3>{diagnostics.cycle_summary?.total_candidates} candidates evaluated · {diagnostics.cycle_summary?.eligible_candidates} eligible · {diagnostics.cycle_summary?.rejected_candidates} rejected</h3></div>{summary?.recent_cycles?.length ? <select value={selectedCycle || latest?.id || ""} onChange={event => { setSelectedCycle(Number(event.target.value)); setPage(1); }} aria-label={"Research cycle"}>{summary.recent_cycles.map(cycle => <option key={cycle.id} value={cycle.id}>Cycle #{cycle.id}</option>)}</select> : null}</div><p>Screening conditions are research evidence, not estimates of future profitability.</p><div className="reason-list">{diagnostics.rejection_summary?.map(item => <div className="reason-row" key={item.code}><div><b>{item.code}</b><small>{item.description}</small></div><span>{item.count} candidates · {item.percentage.toFixed(1)}%</span><i><em style={{ width: `${item.percentage}%` }} /></i></div>)}</div><div className="table-controls"><select value={reason} onChange={event => setFilter(event.target.value)}><option value="">All failed gates</option>{diagnostics.rejection_summary?.map(item => <option key={item.code} value={item.code}>{item.code}</option>)}</select><input value={search} placeholder={"Search candidate"} onChange={event => { setSearch(event.target.value); setPage(1); }} /></div><div className="research-table-wrap"><table><thead><tr><th>Program / Candidate</th><th>Direction</th><th>Complexity</th><th>Score</th><th>Failed Gates</th><th>Status</th></tr></thead><tbody>{diagnostics.items?.map(item => <tr key={item.candidate_id}><td>{item.description} #{item.candidate_number}</td><td>{item.direction}</td><td>{item.complexity}</td><td>{item.development_score?.toFixed(2) || "—"}</td><td>{item.rejection_reasons.join(", ")}</td><td>{item.eligibility_status}</td></tr>)}</tbody></table></div><div className="research-pagination"><button disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page}</span><button disabled={page * diagnostics.page_size >= diagnostics.total_items} onClick={() => setPage(page + 1)}>Next</button></div></section>}
        {diagnostics && !diagnostics.diagnostics_available && <p className="research-empty compact">Detailed rejection diagnostics are unavailable for this historical cycle.</p>}
      </details>
      <details className="automatic-research-fingerprint"><summary>Details: dataset fingerprint and research metadata · {compactHash(latest?.dataset_fingerprint)}</summary><code>Dataset fingerprint: {latest?.dataset_fingerprint || "Not available"}<br />Registry ID: {active?.registry_id || latest?.result?.active_registry_id || "Not available"}</code></details>
    </section>
    <section className="automatic-research-approved"><div className="automatic-research-section-head"><div><span className="eyebrow">APPROVED / ACTIVE</span><h3>Approved registry and paper state</h3></div></div>{active ? <div className="automatic-research-active"><div><span>Active strategy</span><b>{active.family}</b></div><div><span>Research cycle</span><b>#{active.research_cycle_id || "—"}</b></div><div><span>Registry status</span><b>Approved</b></div></div> : <div className="research-empty compact"><strong>ACTIVE NONE · Paper WAIT</strong><span>No approved strategy is available. Automatic Research is research-only; live trading is disabled.</span></div>}</section>
    <section className="automatic-research-recent"><div className="automatic-research-section-head"><div><span className="eyebrow">RESEARCH EVIDENCE</span><h3>Recent Research</h3></div></div><div className="research-table-wrap"><table><thead><tr><th>Cycle</th><th>Range</th><th>Status</th><th>Candidates</th><th>Eligible</th><th>Approved</th><th>Completed</th></tr></thead><tbody>{summary?.recent_cycles?.length ? summary.recent_cycles.map(cycle => <tr key={cycle.id}><td>#{cycle.id}</td><td>{day(cycle.research_start)} → {day(cycle.research_end)}</td><td><i className={`status-pill ${tone(cycle.status)}`}>{cycle.status}</i></td><td>{cycle.research_counts?.development_candidates || 0}</td><td>{cycle.research_counts?.eligible_finalists || 0}</td><td>{cycle.result?.approved || 0}</td><td>{formatTime(cycle.completed_at)}</td></tr>) : <tr><td colSpan={7}>No research cycles recorded.</td></tr>}</tbody></table></div></section>
    <footer className="automatic-research-footnotes"><strong>RESEARCH ONLY · LIVE TRADING DISABLED</strong><span>Discovery output never bypasses validation or the approved registry.</span></footer>
  </section>;
}
