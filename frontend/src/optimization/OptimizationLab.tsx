import { useEffect, useState } from "react";
import { Play, RefreshCw } from "lucide-react";
import { type OptimizationRun, type ResearchJob, researchApi, type StrategyParameters } from "../research";
import { useLanguage } from "../i18n";

const terminal = new Set(["COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"]);

export default function OptimizationLab({ endDate, parameters }: { endDate: string; parameters: StrategyParameters }) {
  const { t, message, value } = useLanguage();
  const [budget, setBudget] = useState(100); const [seed, setSeed] = useState(20260717);
  const [rangeStart, setRangeStart] = useState(() => new Date(Date.now() - 2 * 365 * 86400_000).toISOString().slice(0, 10)); const [rangeEnd, setRangeEnd] = useState(endDate);
  const [run, setRun] = useState<OptimizationRun | null>(null); const [history, setHistory] = useState<OptimizationRun[]>([]); const [selected, setSelected] = useState<number[]>([]); const [comparison, setComparison] = useState<OptimizationRun[]>([]); const [job, setJob] = useState<ResearchJob | null>(null); const [error, setError] = useState(""); const [starting, setStarting] = useState(false);
  const refresh = async (id = run?.id) => {
    if (!id) return;
    try {
      const nextRun = await researchApi.optimizationRun(id); setRun(nextRun);
      setJob(nextRun.job_id ? await researchApi.job(nextRun.job_id) : null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : t("optimization.startFailed")); }
  };
  const refreshHistory = async () => { const items = await researchApi.optimizationHistory(); setHistory(items); return items; };
  useEffect(() => { refreshHistory().then(items => { if (items[0]) void refresh(items[0].id); }).catch(() => undefined); }, []);
  useEffect(() => {
    if (!run || !job || terminal.has(job.status)) return;
    const timer = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(timer);
  }, [run?.id, job?.status]);
  const start = async () => {
    setStarting(true); setError("");
    try { const queued = await researchApi.optimization({ instrument: "BTC-USDT", timeframe: "15m", start_date: rangeStart, end_date: rangeEnd, parameters, trial_budget: budget, seed }); await refresh(queued.id); await refreshHistory(); }
    catch (caught) { setError(caught instanceof Error ? `${t("optimization.startFailed")} ${caught.message}` : t("optimization.startFailed")); }
    finally { setStarting(false); }
  };
  const reason = (code?: string) => code === "minimum_validation_trades" ? t("optimization.reason.minimumTrades") : code === "maximum_drawdown" ? t("optimization.reason.maximumDrawdown") : code === "trial_error" ? t("optimization.reason.trialError") : code || t("common.notAvailable");
  const metric = (number?: number | null, suffix = "") => number == null || !Number.isFinite(number) ? t("common.notAvailable") : `${number.toFixed(2)}${suffix}`;
  const displayStatus = job?.status || run?.status;
  const toggleSelected = async (id:number) => { const next = selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id].slice(-5); setSelected(next); if (next.length) setComparison((await researchApi.optimizationCompare(next)).runs); else setComparison([]); };
  const download = (format:"json"|"csv") => { const rows = comparison.map(item => ({id:item.id,status:item.status,instrument:item.request.instrument,timeframe:item.request.timeframe,seed:item.seed,budget:item.request.trial_budget,policy:item.scoring_policy.version,completed:item.trials.filter(t=>t.status==="COMPLETED").length,eliminated:item.trials.filter(t=>t.status==="ELIMINATED").length,failed:item.trials.filter(t=>t.status==="FAILED").length,contaminated:!!item.post_holdout_adjustment})); const content = format === "json" ? JSON.stringify(rows,null,2) : [Object.keys(rows[0] || {}).join(","),...rows.map(row=>Object.values(row).map(v=>JSON.stringify(v ?? "")).join(","))].join("\n"); const url=URL.createObjectURL(new Blob([content],{type:format === "json" ? "application/json" : "text/csv"})); const link=document.createElement("a");link.href=url;link.download=`optimization-comparison.${format}`;link.click();URL.revokeObjectURL(url); };
  return <>
    <section className="research-command"><div><span className="eyebrow">{t("optimization.method")}</span><h1>{t("optimization.title")}</h1><p>{t("optimization.description")}</p></div><div className="research-command-fields">
      <label>{t("research.startDate")}<input type="date" value={rangeStart} max={rangeEnd} onChange={e => setRangeStart(e.target.value)} /></label><label>{t("research.endDate")}<input type="date" value={rangeEnd} min={rangeStart} onChange={e => setRangeEnd(e.target.value)} /></label><label>{t("optimization.budget")}<input type="number" min="1" max="500" value={budget} onChange={e => setBudget(Math.max(1, Math.min(500, Number(e.target.value))))} /></label><label>{t("optimization.seed")}<input type="number" value={seed} onChange={e => setSeed(Number(e.target.value))} /></label><button className="primary-btn run-backtest" disabled={starting} onClick={start}><Play size={15}/>{t("optimization.run")}</button>
    </div></section>
    {error && <div className="research-alert error">{error}</div>}
    <section className="research-panel"><div className="research-panel-head"><div><span className="eyebrow">{t("optimization.status")}</span><h2>{displayStatus ? value(displayStatus) : t("optimization.noRuns")}</h2></div>{run && <button className="text-button" onClick={() => void refresh()}><RefreshCw size={14}/>{t("common.refresh")}</button>}</div>
      {job && <div className="research-progress"><div><span>{message(job.message_code, job.message_params, job.progress_message)}</span><b>{job.progress}%</b></div><i><span style={{ width: `${job.progress}%` }} /></i><small>{t("optimization.jobProgress")}</small></div>}
      {run && <><p className="research-note">{t("optimization.warning")}</p><p className="research-note">{t("optimization.neighborhood")}</p><p className="research-note">{t("optimization.noResume")}</p>{run.post_holdout_adjustment && <p className="research-alert error">This run was configured after the family holdout had already been revealed. Treat its holdout result as development evidence, not as untouched validation. 此运行在家族保留集揭示后配置；请将其保留集结果视为开发证据。</p>}
        {!run.holdout_revealed_at && <button className="text-button" onClick={() => researchApi.revealOptimizationHoldout(run.id).then(setRun).catch(error => setError(error.message))}>Reveal holdout (records timestamp)</button>}
        <div className="research-table-wrap"><table><thead><tr><th>{t("optimization.trial")}</th><th>{t("optimization.score")}</th><th>{t("optimization.validation")}</th><th>{t("optimization.holdout")}</th><th>{t("common.trades")}</th><th>{t("optimization.eliminated")}</th></tr></thead><tbody>{run.trials.map(trial => <tr key={trial.id}><td>#{trial.trial_number}</td><td>{metric(trial.score)}</td><td>{metric(trial.validation_metrics?.total_return, "%")} / {t("validation.profitFactor")} {metric(trial.validation_metrics?.profit_factor)}</td><td>{metric(trial.holdout_metrics?.total_return, "%")} / {t("validation.profitFactor")} {metric(trial.holdout_metrics?.profit_factor)}</td><td>{trial.validation_metrics?.total_trades ?? t("common.notAvailable")}</td><td>{trial.elimination_reasons?.map(reason).join(", ") || (trial.status === "FAILED" ? reason("trial_error") : t("common.notAvailable"))}</td></tr>)}</tbody></table></div>
      </>}
    </section>
    <section className="research-panel"><div className="research-panel-head"><div><span className="eyebrow">Research evidence</span><h2>Optimization history & comparison</h2></div><button className="text-button" onClick={() => void refreshHistory()}>Refresh history</button></div><p className="research-note">Select up to five runs. Ranking uses development/validation only; holdout, final OOT and cross-asset results never affect scores.</p>
      <div className="research-table-wrap"><table><thead><tr><th>Select</th><th>Run</th><th>Status</th><th>Instrument / range</th><th>Budget / seed</th><th>Family / holdout</th></tr></thead><tbody>{history.map(item => <tr key={item.id}><td><input type="checkbox" checked={selected.includes(item.id)} onChange={() => void toggleSelected(item.id)} /></td><td><button className="text-button" onClick={() => void refresh(item.id)}>#{item.id}</button></td><td>{item.status}</td><td>{item.request.instrument} {item.request.timeframe}<br/>{item.request.start_date} — {item.request.end_date}</td><td>{item.request.trial_budget} / {item.seed}</td><td>{item.experiment_family_id || "Legacy"} / {item.holdout_revealed_at ? "revealed" : "locked"}</td></tr>)}</tbody></table></div>
      {comparison.length > 0 && <><button className="text-button" onClick={() => download("json")}>Export JSON</button><button className="text-button" onClick={() => download("csv")}>Export CSV</button><div className="research-table-wrap"><table><thead><tr><th>Run</th><th>Trials</th><th>Best validation score</th><th>Top parameters</th><th>Contamination</th></tr></thead><tbody>{comparison.map(item => { const top=item.trials.find(trial=>trial.status==="COMPLETED"); return <tr key={item.id}><td>#{item.id}<br/>{item.scoring_policy.version}</td><td>{item.trials.filter(t=>t.status==="COMPLETED").length} complete / {item.trials.filter(t=>t.status==="ELIMINATED").length} eliminated / {item.trials.filter(t=>t.status==="FAILED").length} failed</td><td>{metric(top?.score)}</td><td><code>{top ? JSON.stringify(top.parameters) : "Unavailable"}</code></td><td>{item.post_holdout_adjustment ? "Post-holdout adjustment" : "No"}</td></tr>; })}</tbody></table></div></>}
    </section>
  </>;
}
