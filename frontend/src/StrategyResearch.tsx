import { useEffect, useMemo, useState } from "react";
import { Copy, Download, FlaskConical, Play, RotateCcw, Save, Trash2 } from "lucide-react";
import { BacktestCandleChart, DistributionCharts, LineResearchChart } from "./ResearchCharts";
import { BacktestMetrics, BacktestRun, BacktestTrade, DEFAULT_RESEARCH_PARAMETERS, EquityPoint, Reconciliation, researchApi, StrategyConfig, StrategyParameters } from "./research";
import PortfolioResearch from "./PortfolioResearch";
import ReconciliationPanel from "./ReconciliationPanel";
import ValidationWorkspace from "./validation/ValidationWorkspace";
import ShadowExperiments from "./shadow/ShadowExperiments";
import StrategyLifecycle from "./lifecycle/StrategyLifecycle";
import { useLanguage, type TranslationKey } from "./i18n";

const dateText = (date: Date) => date.toISOString().slice(0, 10);
const initialEnd = dateText(new Date());
const initialStart = dateText(new Date(Date.now() - 180 * 86400_000));
const parameterFields: Array<{ key: keyof StrategyParameters; label: TranslationKey; step?: number; percent?: boolean; min: number; max: number }> = [
  { key: "fast_ma", label: "parameter.fastMa", min: 2, max: 300 }, { key: "slow_ma", label: "parameter.slowMa", min: 10, max: 500 },
  { key: "ema_pullback_period", label: "parameter.emaPeriod", min: 2, max: 200 }, { key: "ema_pullback_distance", label: "parameter.emaDistance", step: .01, percent: true, min: .01, max: 5 },
  { key: "rsi_period", label: "parameter.rsiPeriod", min: 2, max: 100 }, { key: "rsi_min", label: "parameter.rsiMin", step: .1, min: 0, max: 99 },
  { key: "rsi_max", label: "parameter.rsiMax", step: .1, min: 1, max: 100 }, { key: "minimum_volume_ratio", label: "parameter.volumeRatio", step: .05, min: .1, max: 10 },
  { key: "minimum_score", label: "parameter.minimumScore", step: 1, min: 0, max: 100 }, { key: "atr_period", label: "parameter.atrPeriod", min: 2, max: 100 },
  { key: "stop_loss_atr_multiplier", label: "parameter.stopAtr", step: .1, min: .1, max: 10 }, { key: "risk_reward_ratio", label: "parameter.riskReward", step: .1, min: .2, max: 10 },
  { key: "trading_fee", label: "parameter.fee", step: .01, percent: true, min: 0, max: 2 }, { key: "slippage", label: "parameter.slippage", step: .01, percent: true, min: 0, max: 2 },
  { key: "cooldown_bars", label: "parameter.cooldown", min: 0, max: 1000 }, { key: "initial_capital", label: "parameter.capital", step: 100, min: 100, max: 100000000 },
  { key: "risk_per_trade", label: "parameter.riskTrade", step: .1, percent: true, min: .01, max: 10 },
];

function formatMetric(value: number | null | undefined, style: "money" | "percent" | "ratio" | "number" = "number") {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (style === "money") return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  if (style === "percent") return `${value.toFixed(2)}%`;
  if (style === "ratio") return value.toFixed(2);
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function duration(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(1)}d`;
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 60)}m`;
}
function ResearchSection({ title, eyebrow, actions, children, className = "" }: { title: string; eyebrow: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={`research-panel ${className}`}><div className="research-panel-head"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{actions}</div>{children}</section>;
}
type ResearchMode = "single" | "portfolio" | "validation" | "shadow" | "lifecycle";
function ResearchModeNav({mode,setMode}:{mode:ResearchMode;setMode:(mode:ResearchMode)=>void}) { const {t}=useLanguage(); return <div className="mode-switch research-modes">{[["single",t("research.title")],["portfolio",t("portfolio.title")],["validation",t("validation.title")],["shadow",t("shadow.title")],["lifecycle",t("lifecycle.title")]].map(([key,label])=><button key={key} className={mode===key?"active":""} onClick={()=>setMode(key as ResearchMode)}>{label}</button>)}</div>; }

export default function StrategyResearch() {
  const {t,value}=useLanguage();
  const [researchMode,setResearchMode]=useState<ResearchMode>("single");
  const [instrument, setInstrument] = useState("BTC-USDT"); const [timeframe, setTimeframe] = useState("15m");
  const [startDate, setStartDate] = useState(initialStart); const [endDate, setEndDate] = useState(initialEnd);
  const [parameters, setParameters] = useState<StrategyParameters>({ ...DEFAULT_RESEARCH_PARAMETERS });
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]); const [strategyId, setStrategyId] = useState<number | null>(null); const [configName, setConfigName] = useState("My Strategy");
  const [run, setRun] = useState<BacktestRun | null>(null); const [trades, setTrades] = useState<BacktestTrade[]>([]); const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [history, setHistory] = useState<BacktestRun[]>([]); const [selectedConfigs, setSelectedConfigs] = useState<number[]>([]); const [comparison, setComparison] = useState<Array<Record<string, number | string | null>>>([]);
  const [reconciliation, setReconciliation] = useState<Reconciliation | null>(null); const [error, setError] = useState("");
  const [tradePage, setTradePage] = useState(1); const [sideFilter, setSideFilter] = useState("ALL"); const [resultFilter, setResultFilter] = useState("ALL"); const [tradeSort, setTradeSort] = useState("time-desc");
  const [walkLoading, setWalkLoading] = useState(false); const [walkResult, setWalkResult] = useState<{ windows: Array<Record<string, unknown>>; note: string } | null>(null);
  const [trainDays, setTrainDays] = useState(90); const [testDays, setTestDays] = useState(30); const [stepDays, setStepDays] = useState(30);

  async function loadMetadata() {
    try { const [configs, runs] = await Promise.all([researchApi.strategies(), researchApi.history()]); setStrategies(configs); setHistory(runs); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Research API unavailable."); }
  }
  useEffect(() => { loadMetadata(); }, []);
  useEffect(() => {
    if (!run || (run.status !== "QUEUED" && run.status !== "RUNNING")) return;
    const timer = window.setInterval(async () => { try {
      const next = await researchApi.getRun(run.id); setRun(next);
      if (next.status === "COMPLETED") { const [nextTrades, nextEquity, nextReconciliation] = await Promise.all([researchApi.trades(next.id), researchApi.equity(next.id), researchApi.reconciliation(next.id)]); setTrades(nextTrades); setEquity(nextEquity); setReconciliation(nextReconciliation); await loadMetadata(); }
      if (next.status === "FAILED") setError(next.error || "Backtest failed.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not refresh backtest status."); } }, 1000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  const validationError = useMemo(() => {
    if (!startDate || !endDate || startDate >= endDate) return "Start date must be earlier than end date.";
    if (parameters.fast_ma >= parameters.slow_ma) return "Fast MA must be smaller than Slow MA.";
    if (parameters.rsi_min >= parameters.rsi_max) return "RSI Min must be smaller than RSI Max.";
    if (!parameters.enable_long && !parameters.enable_short) return "Enable at least one trade direction.";
    return "";
  }, [startDate, endDate, parameters]);
  const busy = run?.status === "QUEUED" || run?.status === "RUNNING";
  async function startBacktest() {
    if (validationError || busy) return; setError(""); setTrades([]); setEquity([]); setReconciliation(null);
    try { setRun(await researchApi.run({ instrument, timeframe, start_date: startDate, end_date: endDate, parameters, strategy_config_id: strategyId, validation_split: .7 })); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Backtest could not start."); }
  }
  function loadConfig(config: StrategyConfig) { setStrategyId(config.id); setConfigName(config.name); setParameters(config.parameters); setInstrument(config.instrument); setTimeframe(config.timeframe); if (config.start_date) setStartDate(config.start_date); if (config.end_date) setEndDate(config.end_date); }
  async function saveConfig(asNew = false) { try { const saved = await researchApi.saveStrategy({ name: configName, parameters, instrument, timeframe, start_date: startDate, end_date: endDate }, asNew ? undefined : strategyId || undefined); setStrategyId(saved.id); await loadMetadata(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Strategy save failed."); } }
  async function deleteConfig() { if (!strategyId) return; try { await researchApi.deleteStrategy(strategyId); setStrategyId(null); setConfigName("My Strategy"); await loadMetadata(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Strategy delete failed."); } }
  async function duplicateConfig() { if (!strategyId) return; try { const copy = await researchApi.duplicateStrategy(strategyId); loadConfig(copy); await loadMetadata(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Strategy duplicate failed."); } }
  async function compareRuns() { try { setComparison(await researchApi.compareStrategies(selectedConfigs)); } catch (caught) { setError(caught instanceof Error ? caught.message : "Comparison failed."); } }
  async function runWalkForward() { if (validationError || walkLoading) return; setWalkLoading(true); setError(""); try { const job=await researchApi.walkForward({ instrument, timeframe, start_date: startDate, end_date: endDate, parameters, train_days: trainDays, test_days: testDays, step_days: stepDays }); setWalkResult({windows:[],note:`Walk-forward queued as job #${job.id}. Track progress in Operations.`}); } catch (caught) { setError(caught instanceof Error ? caught.message : "Walk-forward failed."); } finally { setWalkLoading(false); } }

  const filteredTrades = useMemo(() => trades.filter((trade) => sideFilter === "ALL" || trade.side === sideFilter).filter((trade) => resultFilter === "ALL" || (resultFilter === "WIN" ? trade.pnl > 0 : trade.pnl <= 0)).sort((a, b) => tradeSort === "time-asc" ? a.entry_ts - b.entry_ts : tradeSort === "pnl-desc" ? b.pnl - a.pnl : tradeSort === "pnl-asc" ? a.pnl - b.pnl : b.entry_ts - a.entry_ts), [trades, sideFilter, resultFilter, tradeSort]);
  const pageSize = 20, pages = Math.max(1, Math.ceil(filteredTrades.length / pageSize)), visibleTrades = filteredTrades.slice((tradePage - 1) * pageSize, tradePage * pageSize);
  useEffect(() => setTradePage(1), [sideFilter, resultFilter, tradeSort]);
  function exportCsv() { if (!filteredTrades.length) return; const keys = Object.keys(filteredTrades[0]) as Array<keyof BacktestTrade>; const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`; const csv = [keys.join(","), ...filteredTrades.map((trade) => keys.map((key) => escape(trade[key])).join(","))].join("\n"); const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); link.download = `backtest-${run?.id || "trades"}.csv`; link.click(); URL.revokeObjectURL(link.href); }
  const result = run?.result, metrics = result?.metrics;
  const metricCards: Array<[string, string]> = metrics ? [
    ["Initial Capital", formatMetric(metrics.initial_capital, "money")], ["Final Equity", formatMetric(metrics.final_equity, "money")], ["Net Profit", formatMetric(metrics.net_profit, "money")], ["Total Return", formatMetric(metrics.total_return, "percent")], ["Annualized Return", formatMetric(metrics.annualized_return, "percent")], ["Total Trades", String(metrics.total_trades)], ["Win Rate", formatMetric(metrics.win_rate, "percent")], ["Profit Factor", formatMetric(metrics.profit_factor, "ratio")], ["Expectancy", formatMetric(metrics.expectancy, "money")], ["Average Win", formatMetric(metrics.average_win, "money")], ["Average Loss", formatMetric(metrics.average_loss, "money")], ["Risk / Reward Realized", formatMetric(metrics.realized_risk_reward, "ratio")], ["Maximum Drawdown", formatMetric(metrics.maximum_drawdown, "percent")], ["Sharpe Ratio", formatMetric(metrics.sharpe_ratio, "ratio")], ["Sortino Ratio", formatMetric(metrics.sortino_ratio, "ratio")], ["Consecutive Wins", String(metrics.consecutive_wins)], ["Consecutive Losses", String(metrics.consecutive_losses)], ["Fees Paid", formatMetric(metrics.fees_paid, "money")], ["Long / Short Trades", `${metrics.long_trades} / ${metrics.short_trades}`], ["Average Holding Time", duration(metrics.average_holding_seconds)]
  ] : [];

  if(researchMode==="portfolio") return <main className="research-workspace"><ResearchModeNav mode={researchMode} setMode={setResearchMode}/><section className="research-command"><div><span className="eyebrow">{t("portfolio.engine")}</span><h1>{t("portfolio.title")}</h1><p>{t("portfolio.description")}</p></div><div className="research-command-fields"><label>{t("research.startDate")}<input type="date" value={startDate} onChange={e=>setStartDate(e.target.value)}/></label><label>{t("research.endDate")}<input type="date" value={endDate} onChange={e=>setEndDate(e.target.value)}/></label></div></section><PortfolioResearch startDate={startDate} endDate={endDate} parameters={parameters}/></main>;
  if(researchMode==="validation") return <main className="research-workspace"><ResearchModeNav mode={researchMode} setMode={setResearchMode}/><ValidationWorkspace startDate={startDate} endDate={endDate} parameters={parameters}/></main>;
  if(researchMode==="shadow") return <main className="research-workspace"><ResearchModeNav mode={researchMode} setMode={setResearchMode}/><ShadowExperiments/></main>;
  if(researchMode==="lifecycle") return <main className="research-workspace"><ResearchModeNav mode={researchMode} setMode={setResearchMode}/><StrategyLifecycle/></main>;

  return <main className="research-workspace">
    <ResearchModeNav mode={researchMode} setMode={setResearchMode}/>
    <section className="research-command"><div><span className="eyebrow">{t("research.realData")}</span><h1>{t("research.title")}</h1><p>{t("research.description")}</p></div><div className="research-command-fields"><label>{t("common.instrument")}<select value={instrument} onChange={(event) => setInstrument(event.target.value)}><option>BTC-USDT</option><option>ETH-USDT</option><option>SOL-USDT</option></select></label><label>{t("common.timeframe")}<select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option>15m</option><option>1H</option><option>4H</option></select></label><label>{t("research.startDate")}<input type="date" value={startDate} max={endDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>{t("research.endDate")}<input type="date" value={endDate} min={startDate} max={initialEnd} onChange={(event) => setEndDate(event.target.value)} /></label><button className="primary-btn run-backtest" disabled={Boolean(validationError) || busy} onClick={startBacktest}><Play size={15} />{busy?t("status.RUNNING"):t("research.runBacktest")}</button></div></section>
    {(error || validationError) && <div className="research-alert error">{error || validationError}</div>}
    {busy && <div className="research-progress"><div><span>{run?.progress_message || t("research.preparing")}</span><b>{run?.progress || 0}%</b></div><i><span style={{ width: `${run?.progress || 0}%` }} /></i><small>{t("research.serverRunNote")}</small></div>}

    <div className="research-two-column">
      <ResearchSection title={t("research.parameters")} eyebrow={t("research.description")} actions={<button className="text-button" onClick={() => setParameters({ ...DEFAULT_RESEARCH_PARAMETERS })}><RotateCcw size={13}/>{t("research.restoreDefaults")}</button>}>
        <div className="parameter-grid">{parameterFields.map((field) => <label key={field.key}>{t(field.label)}<div><input type="number" min={field.min} max={field.max} step={field.step || 1} value={field.percent ? Number(parameters[field.key]) * 100 : Number(parameters[field.key])} onChange={(event) => setParameters((current) => ({ ...current, [field.key]: (field.percent ? Number(event.target.value) / 100 : Number(event.target.value)) }))} />{field.percent && <span>%</span>}</div></label>)}</div>
        <div className="toggle-row"><label><input type="checkbox" checked={parameters.enable_long} onChange={(event) => setParameters({ ...parameters, enable_long: event.target.checked })}/>{t("research.enableLong")}</label><label><input type="checkbox" checked={parameters.enable_short} onChange={(event) => setParameters({ ...parameters, enable_short: event.target.checked })}/>{t("research.enableShort")}</label><label><input type="checkbox" checked={parameters.enable_daily_context} onChange={(event) => setParameters({ ...parameters, enable_daily_context: event.target.checked })}/>{t("research.enableDaily")}</label><span>{t("research.maxPosition")}</span></div>
      </ResearchSection>
      <ResearchSection title={t("research.configurations")} eyebrow="SQLite">
        <label className="config-select">{t("research.loadConfig")}<select value={strategyId || ""} onChange={(event) => { const selected = strategies.find((item) => item.id === Number(event.target.value)); if (selected) loadConfig(selected); }}><option value="">{t("research.selectStrategy")}</option>{strategies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="config-select">{t("research.configName")}<input value={configName} maxLength={80} onChange={(event) => setConfigName(event.target.value)} /></label>
        <div className="config-actions"><button onClick={() => saveConfig(true)}><Save size={14}/>{t("common.new")}</button><button disabled={!strategyId} onClick={() => saveConfig(false)}><Save size={14}/>{t("common.update")}</button><button disabled={!strategyId} onClick={duplicateConfig}><Copy size={14}/>{t("common.duplicate")}</button><button disabled={!strategyId} className="danger" onClick={deleteConfig}><Trash2 size={14}/>{t("common.delete")}</button></div>
        <div className="data-quality"><strong>{t("research.dataContract")}</strong><p>{t("research.contract")}</p></div>
      </ResearchSection>
    </div>

    <ResearchSection title={t("research.corePerformance")} eyebrow={t("research.title")}>{metrics ? <><div className="research-metrics">{metricCards.map(([label, metric]) => <article key={label}><span>{value(label)}</span><strong>{metric}</strong></article>)}</div>{metrics.sample_note && <div className="research-alert warning">{metrics.sample_note}</div>}</> : <div className="research-empty"><FlaskConical size={28}/><strong>{t("research.noResult")}</strong><span>{t("research.noResultHelp")}</span></div>}</ResearchSection>

    <div className="research-chart-pair"><ResearchSection title={t("research.equityCurve")} eyebrow={t("research.realData")}><LineResearchChart points={equity} field="equity" color="#039855" label={t("research.equityCurve")} /></ResearchSection><ResearchSection title={t("research.drawdownCurve")} eyebrow={t("research.realData")}><LineResearchChart points={(result?.drawdown || []) as Array<Record<string, number>>} field="drawdown" color="#d92d20" label={t("research.drawdownCurve")} /></ResearchSection></div>
    <ResearchSection title={t("research.candlesExecutions")} eyebrow={t("research.realData")}><BacktestCandleChart candles={result?.candles || []} trades={trades} /></ResearchSection>
    <ResearchSection title={t("research.returnDiagnostics")} eyebrow={t("research.title")}><DistributionCharts trades={trades} monthly={result?.monthly_returns || []} /></ResearchSection>

    <ResearchSection title={t("research.tradeLedger")} eyebrow={t("research.title")} actions={<div className="table-controls"><select value={sideFilter} onChange={(event) => setSideFilter(event.target.value)}><option value="ALL">{t("common.allSides")}</option><option>LONG</option><option>SHORT</option></select><select value={resultFilter} onChange={(event) => setResultFilter(event.target.value)}><option value="ALL">{t("common.allResults")}</option><option>WIN</option><option>LOSS</option></select><select value={tradeSort} onChange={(event) => setTradeSort(event.target.value)}><option value="time-desc">{t("common.newestFirst")}</option><option value="time-asc">{t("common.oldestFirst")}</option><option value="pnl-desc">P&amp;L ↓</option><option value="pnl-asc">P&amp;L ↑</option></select><button disabled={!filteredTrades.length} onClick={exportCsv}><Download size={13}/>{t("common.csv")}</button></div>}>
      {visibleTrades.length ? <><div className="research-table-wrap"><table><thead><tr>{["ID",t("common.instrument"),t("common.time"),t("common.created"),t("common.side"),"Entry","Exit","SL","TP","Size","P&L","P&L %","R",t("common.fees"),t("common.result"),"Duration",t("common.score")].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{visibleTrades.map((trade) => <tr key={trade.trade_id}><td>#{trade.trade_id}</td><td>{trade.instrument}</td><td>{new Date(trade.entry_time).toLocaleString()}</td><td>{new Date(trade.exit_time).toLocaleString()}</td><td className={trade.side === "LONG" ? "positive" : "negative"}>{value(trade.side)}</td><td>{trade.entry_price.toFixed(4)}</td><td>{trade.exit_price.toFixed(4)}</td><td>{trade.stop_loss.toFixed(4)}</td><td>{trade.take_profit.toFixed(4)}</td><td>{trade.position_size.toFixed(6)}</td><td className={trade.pnl >= 0 ? "positive" : "negative"}>{formatMetric(trade.pnl, "money")}</td><td>{trade.pnl_pct.toFixed(3)}%</td><td>{trade.result_r.toFixed(2)}R</td><td>{formatMetric(trade.fees, "money")}</td><td>{value(trade.exit_reason)}</td><td>{duration(trade.holding_seconds)}</td><td>{trade.signal_score}/100</td></tr>)}</tbody></table></div><div className="research-pagination"><button disabled={tradePage === 1} onClick={() => setTradePage((page) => page - 1)}>{t("common.previous")}</button><span>{tradePage} / {pages} · {filteredTrades.length}</span><button disabled={tradePage === pages} onClick={() => setTradePage((page) => page + 1)}>{t("common.next")}</button></div></> : <div className="research-empty compact"><strong>{t("research.noTrades")}</strong></div>}
    </ResearchSection>

    <ResearchSection title={t("research.strategyComparison")} eyebrow={t("research.configurations")} actions={<button className="secondary-btn" disabled={selectedConfigs.length < 2} onClick={compareRuns}>{t("research.compareSelected")}</button>}><div className="history-selector">{strategies.filter((item) => item.latest_summary).map((item) => <label key={item.id}><input type="checkbox" checked={selectedConfigs.includes(item.id)} onChange={(event) => setSelectedConfigs((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} /> {item.name} · {item.latest_summary?.instrument} {item.latest_summary?.timeframe}</label>)}</div>{comparison.length > 0 && <div className="research-table-wrap"><table><thead><tr>{[t("research.configurations"),t("common.return"),"PF",t("common.drawdown"),"Sharpe",t("paper.winRate"),t("common.trades"),t("common.fees"),"Expectancy"].map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>{comparison.map((item) => <tr key={String(item.id)}><td>{item.label}</td><td>{formatMetric(item.return as number, "percent")}</td><td>{formatMetric(item.profit_factor as number, "ratio")}</td><td>{formatMetric(item.drawdown as number, "percent")}</td><td>{formatMetric(item.sharpe as number, "ratio")}</td><td>{formatMetric(item.win_rate as number, "percent")}</td><td>{item.trades}</td><td>{formatMetric(item.fees as number, "money")}</td><td>{formatMetric(item.expectancy as number, "money")}</td></tr>)}</tbody></table></div>}</ResearchSection>

    <div className="research-two-column validation-grid"><ResearchSection title={t("research.isOos")} eyebrow="70 / 30">{result?.validation ? <><div className="validation-cards">{[["IS PF", result.validation.in_sample.profit_factor, "ratio"],["OOS PF",result.validation.out_of_sample.profit_factor,"ratio"],["IS",result.validation.in_sample.total_return,"percent"],["OOS",result.validation.out_of_sample.total_return,"percent"],["IS DD",result.validation.in_sample.maximum_drawdown,"percent"],["OOS DD",result.validation.out_of_sample.maximum_drawdown,"percent"]].map(([label,metric,style]) => <article key={String(label)}><span>{label}</span><b>{formatMetric(metric as number, style as "percent" | "ratio")}</b></article>)}</div><div className={`research-alert ${result.validation.overfitting_warning ? "error" : "success"}`}>{result.validation.message}</div></> : <div className="research-empty compact">{t("research.validationAfter")}</div>}</ResearchSection>
      <ResearchSection title={t("research.walkForward")} eyebrow={t("validation.stability")}><div className="walk-controls"><label>{t("research.trainingWindow")}<input type="number" min="14" max="730" value={trainDays} onChange={(event) => setTrainDays(Number(event.target.value))}/><span>{t("research.days")}</span></label><label>{t("research.testWindow")}<input type="number" min="7" max="365" value={testDays} onChange={(event) => setTestDays(Number(event.target.value))}/><span>{t("research.days")}</span></label><label>{t("research.rollingStep")}<input type="number" min="7" max="365" value={stepDays} onChange={(event) => setStepDays(Number(event.target.value))}/><span>{t("research.days")}</span></label><button className="secondary-btn" disabled={walkLoading || Boolean(validationError)} onClick={runWalkForward}>{walkLoading?t("common.loading"):t("research.walkForward")}</button></div>{walkResult && <><p className="method-note">{walkResult.note}</p><div className="research-table-wrap"><table><thead><tr><th>{t("research.window")}</th><th>{t("research.trainReturn")}</th><th>{t("research.testReturn")}</th><th>{t("research.trainPf")}</th><th>{t("research.testPf")}</th><th>{t("research.testDrawdown")}</th></tr></thead><tbody>{walkResult.windows.map((window, index) => { const train = window.train as BacktestMetrics, test = window.test as BacktestMetrics; return <tr key={index}><td>{index + 1}</td><td>{formatMetric(train.total_return,"percent")}</td><td>{formatMetric(test.total_return,"percent")}</td><td>{formatMetric(train.profit_factor,"ratio")}</td><td>{formatMetric(test.profit_factor,"ratio")}</td><td>{formatMetric(test.maximum_drawdown,"percent")}</td></tr>; })}</tbody></table></div></>}</ResearchSection></div>

    <ResearchSection title={t("research.reconciliation")} eyebrow={t("decision.signal")}><ReconciliationPanel data={reconciliation} /></ResearchSection>
    {result && <div className="research-method"><strong>{t("research.methodology")}</strong><span>{result.execution_model}</span><span>{result.indicator_model}</span></div>}
  </main>;
}
