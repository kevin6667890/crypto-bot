import { useEffect, useState } from "react";
import { Play, RefreshCw } from "lucide-react";
import { type OptimizationRun, type ResearchJob, researchApi, type StrategyParameters } from "../research";
import { useLanguage } from "../i18n";

const terminal = new Set(["COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"]);

export default function OptimizationLab({ endDate, parameters }: { endDate: string; parameters: StrategyParameters }) {
  const { t, message, value } = useLanguage();
  const [budget, setBudget] = useState(100); const [seed, setSeed] = useState(20260717);
  const [rangeStart, setRangeStart] = useState(() => new Date(Date.now() - 2 * 365 * 86400_000).toISOString().slice(0, 10)); const [rangeEnd, setRangeEnd] = useState(endDate);
  const [run, setRun] = useState<OptimizationRun | null>(null); const [job, setJob] = useState<ResearchJob | null>(null); const [error, setError] = useState(""); const [starting, setStarting] = useState(false);
  const refresh = async (id = run?.id) => {
    if (!id) return;
    try {
      const nextRun = await researchApi.optimizationRun(id); setRun(nextRun);
      setJob(nextRun.job_id ? await researchApi.job(nextRun.job_id) : null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : t("optimization.startFailed")); }
  };
  useEffect(() => { researchApi.optimizationHistory().then(items => { if (items[0]) void refresh(items[0].id); }).catch(() => undefined); }, []);
  useEffect(() => {
    if (!run || !job || terminal.has(job.status)) return;
    const timer = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(timer);
  }, [run?.id, job?.status]);
  const start = async () => {
    setStarting(true); setError("");
    try { const queued = await researchApi.optimization({ instrument: "BTC-USDT", timeframe: "15m", start_date: rangeStart, end_date: rangeEnd, parameters, trial_budget: budget, seed }); await refresh(queued.id); }
    catch (caught) { setError(caught instanceof Error ? `${t("optimization.startFailed")} ${caught.message}` : t("optimization.startFailed")); }
    finally { setStarting(false); }
  };
  const reason = (code?: string) => code === "minimum_validation_trades" ? t("optimization.reason.minimumTrades") : code === "maximum_drawdown" ? t("optimization.reason.maximumDrawdown") : code === "trial_error" ? t("optimization.reason.trialError") : code || t("common.notAvailable");
  const metric = (number?: number | null, suffix = "") => number == null || !Number.isFinite(number) ? t("common.notAvailable") : `${number.toFixed(2)}${suffix}`;
  const displayStatus = job?.status || run?.status;
  return <>
    <section className="research-command"><div><span className="eyebrow">{t("optimization.method")}</span><h1>{t("optimization.title")}</h1><p>{t("optimization.description")}</p></div><div className="research-command-fields">
      <label>{t("research.startDate")}<input type="date" value={rangeStart} max={rangeEnd} onChange={e => setRangeStart(e.target.value)} /></label><label>{t("research.endDate")}<input type="date" value={rangeEnd} min={rangeStart} onChange={e => setRangeEnd(e.target.value)} /></label><label>{t("optimization.budget")}<input type="number" min="1" max="500" value={budget} onChange={e => setBudget(Math.max(1, Math.min(500, Number(e.target.value))))} /></label><label>{t("optimization.seed")}<input type="number" value={seed} onChange={e => setSeed(Number(e.target.value))} /></label><button className="primary-btn run-backtest" disabled={starting} onClick={start}><Play size={15}/>{t("optimization.run")}</button>
    </div></section>
    {error && <div className="research-alert error">{error}</div>}
    <section className="research-panel"><div className="research-panel-head"><div><span className="eyebrow">{t("optimization.status")}</span><h2>{displayStatus ? value(displayStatus) : t("optimization.noRuns")}</h2></div>{run && <button className="text-button" onClick={() => void refresh()}><RefreshCw size={14}/>{t("common.refresh")}</button>}</div>
      {job && <div className="research-progress"><div><span>{message(job.message_code, job.message_params, job.progress_message)}</span><b>{job.progress}%</b></div><i><span style={{ width: `${job.progress}%` }} /></i><small>{t("optimization.jobProgress")}</small></div>}
      {run && <><p className="research-note">{t("optimization.warning")}</p><p className="research-note">{t("optimization.neighborhood")}</p><p className="research-note">{t("optimization.noResume")}</p>
        <div className="research-table-wrap"><table><thead><tr><th>{t("optimization.trial")}</th><th>{t("optimization.score")}</th><th>{t("optimization.validation")}</th><th>{t("optimization.holdout")}</th><th>{t("common.trades")}</th><th>{t("optimization.eliminated")}</th></tr></thead><tbody>{run.trials.map(trial => <tr key={trial.id}><td>#{trial.trial_number}</td><td>{metric(trial.score)}</td><td>{metric(trial.validation_metrics?.total_return, "%")} / {t("validation.profitFactor")} {metric(trial.validation_metrics?.profit_factor)}</td><td>{metric(trial.holdout_metrics?.total_return, "%")} / {t("validation.profitFactor")} {metric(trial.holdout_metrics?.profit_factor)}</td><td>{trial.validation_metrics?.total_trades ?? t("common.notAvailable")}</td><td>{trial.elimination_reasons?.map(reason).join(", ") || (trial.status === "FAILED" ? reason("trial_error") : t("common.notAvailable"))}</td></tr>)}</tbody></table></div>
      </>}
    </section>
  </>;
}
