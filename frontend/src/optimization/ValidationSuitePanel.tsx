import { useEffect, useMemo, useState } from "react";
import { type OptimizationFamily, type OptimizationRun, type OptimizationTrial, type ResearchJob, researchApi, type ValidationSuite, type ValidationSuiteResult } from "../research";
import { useLanguage } from "../i18n";

const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]);
const retryable = new Set(["FAILED", "CANCELLED", "INTERRUPTED"]);
const cancellable = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);
const assets = ["BTC-USDT", "ETH-USDT", "SOL-USDT"];

function number(value?: number | null, suffix = "") { return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(2)}${suffix}`; }
function stamp(value?: string) { return value ? new Date(value).toLocaleString() : "—"; }
function quality(value?: Record<string, unknown>) {
  if (!value) return "—";
  const parts = [value.missing_bars != null && `missing ${value.missing_bars}`, value.coverage != null && `coverage ${value.coverage}`, value.source && `source ${value.source}`].filter(Boolean);
  const warnings = Array.isArray(value.warnings) ? value.warnings.join("; ") : "";
  return [...parts, warnings].filter(Boolean).join(" · ") || "—";
}
function evidence(result: ValidationSuiteResult) {
  const m = result.metrics;
  if (result.status !== "COMPLETED" || !m || (m.total_trades ?? 0) < 20) return "suite.insufficient";
  if ((m.total_return ?? 0) < 0 || (m.profit_factor ?? 0) < 1 || (m.maximum_drawdown ?? 0) > 25) return "suite.weak";
  if ((m.total_return ?? 0) > 0 && (m.profit_factor ?? 0) > 1 && (m.maximum_drawdown ?? 100) <= 20) return "suite.supportive";
  return "suite.mixed";
}

export default function ValidationSuitePanel({ family, history, onOpenRun }: { family: OptimizationFamily | null; history: OptimizationRun[]; onOpenRun: (id:number) => void }) {
  const { t, message, value } = useLanguage();
  const [runId, setRunId] = useState<number | "">(""); const [trialId, setTrialId] = useState<number | "">("");
  const [primary, setPrimary] = useState(true); const [finalOot, setFinalOot] = useState(false); const [cross, setCross] = useState(true);
  const [instruments, setInstruments] = useState<string[]>([]); const [suites, setSuites] = useState<ValidationSuite[]>([]); const [suite, setSuite] = useState<ValidationSuite | null>(null); const [job, setJob] = useState<ResearchJob | null>(null); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const familyRuns = useMemo(() => family ? history.filter(run => run.experiment_family_id === family.id && run.status === "COMPLETED") : [], [family, history]);
  const run = useMemo(() => familyRuns.find(item => item.id === runId) || null, [familyRuns, runId]);
  const trials = useMemo(() => run?.trials.filter(item => item.status === "COMPLETED") || [], [run]);
  const trial = trials.find(item => item.id === trialId) || null;
  const refreshHistory = async () => setSuites(await researchApi.validationSuites());
  const open = async (id:number) => { const detail = await researchApi.validationSuite(id); setSuite(detail); setJob(detail.job_id ? await researchApi.job(detail.job_id) : null); };

  useEffect(() => { void refreshHistory().catch(() => undefined); }, []);
  useEffect(() => { setRunId(""); setTrialId(""); setFinalOot(!!family?.final_oot_start_ts); setInstruments(family ? assets.filter(asset => asset !== family.instrument) : []); }, [family?.id]);
  useEffect(() => { setTrialId(""); }, [runId]);
  useEffect(() => {
    if (!suite || !job || terminal.has(job.status)) return;
    const timer = window.setInterval(() => { void open(suite.id).catch(() => undefined); void refreshHistory().catch(() => undefined); }, 2500);
    return () => window.clearInterval(timer);
  }, [suite?.id, job?.status]);

  const validate = () => {
    if (!family) return t("suite.selectFamily");
    if (!run || run.experiment_family_id !== family.id || run.status !== "COMPLETED") return t("suite.invalidRun");
    if (!trial || !run.trials.some(item => item.id === trial.id && item.status === "COMPLETED")) return t("suite.invalidTrial");
    if (!primary && !finalOot && !cross) return t("suite.noStage");
    return "";
  };
  const start = async () => {
    const problem = validate(); if (problem || !family || !run || !trial) { setError(problem); return; }
    try { setError(""); setNotice(""); const created = await researchApi.validationSuiteRun({ experiment_family_id: family.id, optimization_run_id: run.id, trial_id: trial.id, instruments: cross ? instruments : [], timeframe: "15m", include_primary_holdout: primary, include_final_out_of_time: finalOot, include_cross_asset_transfer: cross }); await refreshHistory(); await open(created.id); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t("optimization.startFailed")); }
  };
  const cancel = async (item:ValidationSuite) => { if (!item.job_id) return; try { await researchApi.cancelJob(item.job_id); await open(item.id); await refreshHistory(); } catch (caught) { setError(caught instanceof Error ? caught.message : t("common.error")); } };
  const retry = async (item:ValidationSuite) => { if (!item.job_id) return; try { const next = await researchApi.retryJob(item.job_id); setNotice(t("suite.retryCreated", { new: next.id, old: item.id })); await refreshHistory(); await open(next.id); } catch (caught) { setError(caught instanceof Error ? caught.message : t("common.error")); } };
  const toggle = (asset:string) => setInstruments(current => current.includes(asset) ? current.filter(item => item !== asset) : [...current, asset]);
  const selectedTrial = (item:OptimizationTrial) => <option key={item.id} value={item.id}>#{item.trial_number} · {number(item.score)} · {number(item.validation_metrics?.total_return, "%")} · PF {number(item.validation_metrics?.profit_factor)} · Sharpe {number(item.validation_metrics?.sharpe_ratio)} · DD {number(item.validation_metrics?.maximum_drawdown, "%")} · {item.validation_metrics?.total_trades ?? 0}</option>;
  return <section className="research-panel">
    <div className="research-panel-head"><h2>{t("suite.title")}</h2><button className="text-button" onClick={() => void refreshHistory()}>{t("common.refresh")}</button></div>
    <p className="research-note">{t("suite.warning")}</p>{error && <p className="research-alert error">{error}</p>}{notice && <p className="research-note">{notice}</p>}
    <div className="research-command-fields">
      <label>{t("family.select")}<select value={family?.id || ""} disabled><option>{family ? `#${family.id} ${family.name}` : t("suite.selectFamily")}</option></select></label>
      <label>{t("suite.selectRun")}<select value={runId} onChange={event => setRunId(event.target.value ? Number(event.target.value) : "")} disabled={!family}><option value="">{t("common.notAvailable")}</option>{familyRuns.map(item => <option key={item.id} value={item.id}>#{item.id} · {item.status}</option>)}</select></label>
      <label>{t("suite.selectTrial")}<select value={trialId} onChange={event => setTrialId(event.target.value ? Number(event.target.value) : "")} disabled={!run}><option value="">{trials.length ? t("common.notAvailable") : t("suite.noCompletedTrials")}</option>{trials.map(selectedTrial)}</select></label>
    </div>
    {trial && <p className="research-note">{t("optimization.parameters")}: <code>{JSON.stringify(trial.parameters)}</code> · {run?.holdout_revealed_at ? t("family.revealed") : t("family.lockedHoldout")} · {run?.post_holdout_adjustment ? t("family.contaminated") : "—"}</p>}
    <div className="research-command-fields">
      <label><input type="checkbox" checked={primary} onChange={event => setPrimary(event.target.checked)} /> {t("suite.includePrimary")}</label>
      <label><input type="checkbox" checked={finalOot} disabled={!family?.final_oot_start_ts} onChange={event => setFinalOot(event.target.checked)} /> {t("suite.includeFinal")}</label>
      <label><input type="checkbox" checked={cross} onChange={event => setCross(event.target.checked)} /> {t("suite.includeCross")}</label>
      {cross && assets.filter(asset => asset !== family?.instrument).map(asset => <label key={asset}><input type="checkbox" checked={instruments.includes(asset)} onChange={() => toggle(asset)} /> {asset}</label>)}
      <button className="primary-btn" onClick={() => void start()} disabled={!family}>{t("suite.start")}</button>
    </div>
    {family && !family.final_oot_start_ts && <p className="research-note">{t("suite.finalNotConfigured")}</p>}
    <div className="research-table-wrap"><table><thead><tr><th>{t("suite.history")}</th><th>{t("family.title")}</th><th>{t("suite.source")}</th><th>{t("common.status")}</th><th>{t("suite.attempt")}</th><th>{t("suite.retryOf")}</th><th>{t("common.created")}</th><th>{t("suite.completed")}</th><th>{t("suite.stages")}</th><th>{t("common.action")}</th></tr></thead><tbody>{suites.map(item => { const complete=item.results?.filter(row => row.status === "COMPLETED").length ?? 0; const failed=item.results?.filter(row => row.status === "FAILED").length ?? 0; return <tr key={item.id}><td>#{item.id}</td><td>#{item.experiment_family_id}</td><td><button className="text-button" onClick={() => onOpenRun(item.source_optimization_run_id)}>#{item.source_optimization_run_id}</button> / #{item.source_trial_id}</td><td>{value(item.status)}</td><td>{item.attempt_number || 1}</td><td>{item.retry_of_suite_id ? `#${item.retry_of_suite_id}` : "—"}</td><td>{stamp(item.created_at)}</td><td>{stamp(item.completed_at)}</td><td>{complete} / {failed}</td><td><button className="text-button" onClick={() => void open(item.id)}>{t("suite.view")}</button>{item.job_id && cancellable.has(item.status) && <button className="text-button" onClick={() => void cancel(item)}>{t("common.cancel")}</button>}{item.job_id && retryable.has(item.status) && <button className="text-button" onClick={() => void retry(item)}>{t("suite.retry")}</button>}</td></tr>; })}</tbody></table></div>
    {suite && <div className="research-panel"><h3>{t("suite.detail")} #{suite.id}</h3>{job && <p>{job.queue_position != null && `${t("common.queue")}: ${job.queue_position} · `}{message(job.message_code, job.message_params, job.progress_message)} · {job.progress}%<br/>{stamp(job.created_at)} · {stamp(job.started_at)} · {stamp(job.completed_at)}{job.error && <><br/>{job.error}</>}</p>}{suite.error && <p className="research-alert error">{suite.error}</p>}<h3>{t("suite.matrix")}</h3><div className="research-table-wrap"><table><thead><tr><th>Stage</th><th>{t("common.asset")}</th><th>Period</th><th>{t("common.status")}</th><th>{t("common.return")}</th><th>Profit Factor</th><th>Sharpe</th><th>{t("common.drawdown")}</th><th>{t("common.trades")}</th><th>{t("suite.buyHold")}</th><th>{t("suite.relative")}</th><th>{t("suite.dataQuality")}</th><th>{t("common.error")}</th><th>{t("common.evidence")}</th></tr></thead><tbody>{suite.results?.length ? suite.results.map(row => { const buy=row.buy_hold_metrics?.total_return; const result=row.metrics?.total_return; return <tr key={`${row.stage}-${row.instrument}`}><td>{row.stage}</td><td>{row.instrument}</td><td>{new Date(row.start_ts * 1000).toLocaleDateString()} – {new Date(row.end_ts * 1000).toLocaleDateString()}</td><td>{value(row.status)}</td><td>{number(result, "%")}</td><td>{number(row.metrics?.profit_factor)}</td><td>{number(row.metrics?.sharpe_ratio)}</td><td>{number(row.metrics?.maximum_drawdown, "%")}</td><td>{row.metrics?.total_trades ?? "—"}</td><td>{number(buy, "%")}</td><td>{result != null && buy != null ? number(result - buy, "%") : "—"}</td><td>{quality(row.data_quality)}</td><td>{row.error || "—"}</td><td>{t(evidence(row))}</td></tr>; }) : <tr><td colSpan={14}>{t("suite.noResults")}</td></tr>}</tbody></table></div></div>}
  </section>;
}
