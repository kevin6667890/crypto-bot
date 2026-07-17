import { useEffect, useMemo, useState } from "react";
import { Copy, Download, FlaskConical, Play, RotateCcw, Save, Trash2 } from "lucide-react";
import { BacktestCandleChart, DistributionCharts, LineResearchChart } from "./ResearchCharts";
import { BacktestMetrics, BacktestRun, BacktestTrade, DEFAULT_RESEARCH_PARAMETERS, EquityPoint, Reconciliation, researchApi, StrategyConfig, StrategyParameters } from "./research";
import PortfolioResearch from "./PortfolioResearch";
import ReconciliationPanel from "./ReconciliationPanel";

const dateText = (date: Date) => date.toISOString().slice(0, 10);
const initialEnd = dateText(new Date());
const initialStart = dateText(new Date(Date.now() - 180 * 86400_000));
const parameterFields: Array<{ key: keyof StrategyParameters; label: string; step?: number; percent?: boolean; min: number; max: number }> = [
  { key: "fast_ma", label: "Fast MA", min: 2, max: 300 }, { key: "slow_ma", label: "Slow MA", min: 10, max: 500 },
  { key: "ema_pullback_period", label: "EMA Pullback Period", min: 2, max: 200 }, { key: "ema_pullback_distance", label: "EMA Pullback Distance", step: .01, percent: true, min: .01, max: 5 },
  { key: "rsi_period", label: "RSI Period", min: 2, max: 100 }, { key: "rsi_min", label: "RSI Min", step: .1, min: 0, max: 99 },
  { key: "rsi_max", label: "RSI Max", step: .1, min: 1, max: 100 }, { key: "minimum_volume_ratio", label: "Minimum Volume Ratio", step: .05, min: .1, max: 10 },
  { key: "minimum_score", label: "Minimum Score", step: 1, min: 0, max: 100 }, { key: "atr_period", label: "ATR Period", min: 2, max: 100 },
  { key: "stop_loss_atr_multiplier", label: "Stop Loss ATR Multiplier", step: .1, min: .1, max: 10 }, { key: "risk_reward_ratio", label: "Risk / Reward Ratio", step: .1, min: .2, max: 10 },
  { key: "trading_fee", label: "Trading Fee", step: .01, percent: true, min: 0, max: 2 }, { key: "slippage", label: "Slippage", step: .01, percent: true, min: 0, max: 2 },
  { key: "cooldown_bars", label: "Cooldown Bars", min: 0, max: 1000 }, { key: "initial_capital", label: "Initial Capital", step: 100, min: 100, max: 100000000 },
  { key: "risk_per_trade", label: "Risk Per Trade", step: .1, percent: true, min: .01, max: 10 },
];

function formatMetric(value: number | null | undefined, style: "money" | "percent" | "ratio" | "number" = "number") {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Insufficient sample";
  if (style === "money") return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  if (style === "percent") return `${value.toFixed(2)}%`;
  if (style === "ratio") return value.toFixed(2);
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function duration(seconds: number | null) {
  if (seconds === null) return "Insufficient sample";
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(1)}d`;
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 60)}m`;
}
function ResearchSection({ title, eyebrow, actions, children, className = "" }: { title: string; eyebrow: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={`research-panel ${className}`}><div className="research-panel-head"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{actions}</div>{children}</section>;
}

export default function StrategyResearch() {
  const [researchMode,setResearchMode]=useState<"single"|"portfolio">("single");
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

  if(researchMode==="portfolio") return <main className="research-workspace"><div className="mode-switch"><button onClick={()=>setResearchMode("single")}>Single Asset</button><button className="active">Portfolio</button></div><section className="research-command"><div><span className="eyebrow">Unified decision engine · shared capital</span><h1>Portfolio Research</h1><p>BTC, ETH and SOL events are processed together. Cash and risk cannot be reused across assets.</p></div><div className="research-command-fields"><label>Start Date<input type="date" value={startDate} onChange={e=>setStartDate(e.target.value)}/></label><label>End Date<input type="date" value={endDate} onChange={e=>setEndDate(e.target.value)}/></label></div></section><PortfolioResearch startDate={startDate} endDate={endDate} parameters={parameters}/></main>;

  return <main className="research-workspace">
    <div className="mode-switch"><button className="active">Single Asset</button><button onClick={()=>setResearchMode("portfolio")}>Portfolio</button></div>
    <section className="research-command"><div><span className="eyebrow">Real historical research · OKX public data</span><h1>Strategy Research</h1><p>Deterministic research only. No AI-generated signals and no exchange order execution.</p></div><div className="research-command-fields"><label>Instrument<select value={instrument} onChange={(event) => setInstrument(event.target.value)}><option>BTC-USDT</option><option>ETH-USDT</option><option>SOL-USDT</option></select></label><label>Timeframe<select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option>15m</option><option>1H</option><option>4H</option></select></label><label>Start Date<input type="date" value={startDate} max={endDate} onChange={(event) => setStartDate(event.target.value)} /></label><label>End Date<input type="date" value={endDate} min={startDate} max={initialEnd} onChange={(event) => setEndDate(event.target.value)} /></label><button className="primary-btn run-backtest" disabled={Boolean(validationError) || busy} onClick={startBacktest}><Play size={15} />{busy ? "Running…" : "Run Backtest"}</button></div></section>
    {(error || validationError) && <div className="research-alert error">{error || validationError}</div>}
    {busy && <div className="research-progress"><div><span>{run?.progress_message || "Preparing"}</span><b>{run?.progress || 0}%</b></div><i><span style={{ width: `${run?.progress || 0}%` }} /></i><small>The run is processed once on the server. The button remains locked until it finishes.</small></div>}

    <div className="research-two-column">
      <ResearchSection title="Strategy Parameters" eyebrow="Validated deterministic rules" actions={<button className="text-button" onClick={() => setParameters({ ...DEFAULT_RESEARCH_PARAMETERS })}><RotateCcw size={13} /> Restore Defaults</button>}>
        <div className="parameter-grid">{parameterFields.map((field) => <label key={field.key}>{field.label}<div><input type="number" min={field.min} max={field.max} step={field.step || 1} value={field.percent ? Number(parameters[field.key]) * 100 : Number(parameters[field.key])} onChange={(event) => setParameters((current) => ({ ...current, [field.key]: (field.percent ? Number(event.target.value) / 100 : Number(event.target.value)) }))} />{field.percent && <span>%</span>}</div></label>)}</div>
        <div className="toggle-row"><label><input type="checkbox" checked={parameters.enable_long} onChange={(event) => setParameters({ ...parameters, enable_long: event.target.checked })} /> Enable Long</label><label><input type="checkbox" checked={parameters.enable_short} onChange={(event) => setParameters({ ...parameters, enable_short: event.target.checked })} /> Enable Short</label><label><input type="checkbox" checked={parameters.enable_daily_context} onChange={(event) => setParameters({ ...parameters, enable_daily_context: event.target.checked })} /> Enable Confirmed 1D Context</label><span>Maximum open positions: 1 per single-asset run</span></div>
      </ResearchSection>
      <ResearchSection title="Strategy Configurations" eyebrow="SQLite persisted">
        <label className="config-select">Load Configuration<select value={strategyId || ""} onChange={(event) => { const selected = strategies.find((item) => item.id === Number(event.target.value)); if (selected) loadConfig(selected); }}><option value="">Select saved strategy</option>{strategies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="config-select">Configuration Name<input value={configName} maxLength={80} onChange={(event) => setConfigName(event.target.value)} /></label>
        <div className="config-actions"><button onClick={() => saveConfig(true)}><Save size={14} /> New</button><button disabled={!strategyId} onClick={() => saveConfig(false)}><Save size={14} /> Update</button><button disabled={!strategyId} onClick={duplicateConfig}><Copy size={14} /> Duplicate</button><button disabled={!strategyId} className="danger" onClick={deleteConfig}><Trash2 size={14} /> Delete</button></div>
        <div className="data-quality"><strong>Data & Execution Contract</strong><p>Source: OKX confirmed historical candles, cached in SQLite. Slow MA requires {parameters.slow_ma} complete warm-up bars.</p><p>Fee {formatMetric(parameters.trading_fee * 100, "percent")} per side · slippage {formatMetric(parameters.slippage * 100, "percent")} per fill.</p><p>Signal closes first; entry uses the next candle open. Historical CVD/OI is unavailable and never fabricated.</p>{run?.data_quality && <small>{String(run.data_quality.confirmed_rows || 0)} confirmed rows · {String(run.data_quality.missing_bars || 0)} missing bars · cache {run.data_quality.cached ? "hit" : "updated"}</small>}</div>
      </ResearchSection>
    </div>

    <ResearchSection title="Core Performance" eyebrow="Backtest result · descriptive statistics">{metrics ? <><div className="research-metrics">{metricCards.map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>{metrics.sample_note && <div className="research-alert warning">{metrics.sample_note}</div>}</> : <div className="research-empty"><FlaskConical size={28} /><strong>No backtest result</strong><span>Choose a real date range and run the deterministic engine. No demo metrics are shown.</span></div>}</ResearchSection>

    <div className="research-chart-pair"><ResearchSection title="Equity Curve" eyebrow="Marked-to-market equity"><LineResearchChart points={equity} field="equity" color="#039855" label="Equity" /></ResearchSection><ResearchSection title="Drawdown Curve" eyebrow="Peak-to-trough percentage"><LineResearchChart points={(result?.drawdown || []) as Array<Record<string, number>>} field="drawdown" color="#d92d20" label="Drawdown" /></ResearchSection></div>
    <ResearchSection title="Candles & Executions" eyebrow="Confirmed historical bars"><BacktestCandleChart candles={result?.candles || []} trades={trades} /></ResearchSection>
    <ResearchSection title="Return Diagnostics" eyebrow="R, monthly and direction breakdown"><DistributionCharts trades={trades} monthly={result?.monthly_returns || []} /></ResearchSection>

    <ResearchSection title="Trade Ledger" eyebrow="Actual trades from this backtest" actions={<div className="table-controls"><select value={sideFilter} onChange={(event) => setSideFilter(event.target.value)}><option value="ALL">All Sides</option><option>LONG</option><option>SHORT</option></select><select value={resultFilter} onChange={(event) => setResultFilter(event.target.value)}><option value="ALL">All Results</option><option>WIN</option><option>LOSS</option></select><select value={tradeSort} onChange={(event) => setTradeSort(event.target.value)}><option value="time-desc">Newest First</option><option value="time-asc">Oldest First</option><option value="pnl-desc">P&amp;L High</option><option value="pnl-asc">P&amp;L Low</option></select><button disabled={!filteredTrades.length} onClick={exportCsv}><Download size={13} /> CSV</button></div>}>
      {visibleTrades.length ? <><div className="research-table-wrap"><table><thead><tr>{["Trade ID","Instrument","Entry Time","Exit Time","Side","Entry Price","Exit Price","Stop Loss","Take Profit","Position Size","P&L","P&L %","Result in R","Fees","Exit Reason","Holding Time","Signal Score"].map((label) => <th key={label}>{label}</th>)}</tr></thead><tbody>{visibleTrades.map((trade) => <tr key={trade.trade_id}><td>#{trade.trade_id}</td><td>{trade.instrument}</td><td>{new Date(trade.entry_time).toLocaleString()}</td><td>{new Date(trade.exit_time).toLocaleString()}</td><td className={trade.side === "LONG" ? "positive" : "negative"}>{trade.side}</td><td>{trade.entry_price.toFixed(4)}</td><td>{trade.exit_price.toFixed(4)}</td><td>{trade.stop_loss.toFixed(4)}</td><td>{trade.take_profit.toFixed(4)}</td><td>{trade.position_size.toFixed(6)}</td><td className={trade.pnl >= 0 ? "positive" : "negative"}>{formatMetric(trade.pnl, "money")}</td><td>{trade.pnl_pct.toFixed(3)}%</td><td>{trade.result_r.toFixed(2)}R</td><td>{formatMetric(trade.fees, "money")}</td><td>{trade.exit_reason}</td><td>{duration(trade.holding_seconds)}</td><td>{trade.signal_score}/100</td></tr>)}</tbody></table></div><div className="research-pagination"><button disabled={tradePage === 1} onClick={() => setTradePage((page) => page - 1)}>Previous</button><span>Page {tradePage} of {pages} · {filteredTrades.length} trades</span><button disabled={tradePage === pages} onClick={() => setTradePage((page) => page + 1)}>Next</button></div></> : <div className="research-empty compact"><strong>No trades to display</strong><span>{trades.length ? "The selected filters have no matches." : "Run a backtest first; generated or demo trades are never substituted."}</span></div>}
    </ResearchSection>

    <ResearchSection title="Strategy Comparison" eyebrow="Saved configurations · latest SQLite result" actions={<button className="secondary-btn" disabled={selectedConfigs.length < 2} onClick={compareRuns}>Compare Selected</button>}><div className="history-selector">{strategies.filter((item) => item.latest_summary).map((item) => <label key={item.id}><input type="checkbox" checked={selectedConfigs.includes(item.id)} onChange={(event) => setSelectedConfigs((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} /> {item.name} · {item.latest_summary?.instrument} {item.latest_summary?.timeframe}</label>)}</div>{!strategies.some((item) => item.latest_summary) && <div className="research-empty compact">Run at least two saved configurations to compare their latest persisted summaries. {history.filter((item) => item.status === "COMPLETED").length} standalone completed run(s) are retained in history.</div>}{comparison.length > 0 && <div className="research-table-wrap"><table><thead><tr>{["Configuration","Return","Profit Factor","Drawdown","Sharpe","Win Rate","Trades","Fees","Expectancy"].map((item) => <th key={item}>{item}</th>)}</tr></thead><tbody>{comparison.map((item) => <tr key={String(item.id)}><td>{item.label}</td><td>{formatMetric(item.return as number, "percent")}</td><td>{formatMetric(item.profit_factor as number, "ratio")}</td><td>{formatMetric(item.drawdown as number, "percent")}</td><td>{formatMetric(item.sharpe as number, "ratio")}</td><td>{formatMetric(item.win_rate as number, "percent")}</td><td>{item.trades}</td><td>{formatMetric(item.fees as number, "money")}</td><td>{formatMetric(item.expectancy as number, "money")}</td></tr>)}</tbody></table></div>}</ResearchSection>

    <div className="research-two-column validation-grid"><ResearchSection title="In-Sample / Out-of-Sample" eyebrow="Default 70 / 30 split">{result?.validation ? <><div className="validation-cards">{[["IS Profit Factor", result.validation.in_sample.profit_factor, "ratio"],["OOS Profit Factor",result.validation.out_of_sample.profit_factor,"ratio"],["IS Return",result.validation.in_sample.total_return,"percent"],["OOS Return",result.validation.out_of_sample.total_return,"percent"],["IS Drawdown",result.validation.in_sample.maximum_drawdown,"percent"],["OOS Drawdown",result.validation.out_of_sample.maximum_drawdown,"percent"]].map(([label,value,style]) => <article key={String(label)}><span>{label}</span><b>{formatMetric(value as number, style as "percent" | "ratio")}</b></article>)}</div><div className={`research-alert ${result.validation.overfitting_warning ? "error" : "success"}`}>{result.validation.overfitting_warning ? "Overfitting Warning · " : "Robustness Check · "}{result.validation.message}</div></> : <div className="research-empty compact">Validation appears after a completed run.</div>}</ResearchSection>
      <ResearchSection title="Walk-Forward Validation" eyebrow="Rolling stability · no parameter search"><div className="walk-controls"><label>Training Window<input type="number" min="14" max="730" value={trainDays} onChange={(event) => setTrainDays(Number(event.target.value))} /><span>days</span></label><label>Test Window<input type="number" min="7" max="365" value={testDays} onChange={(event) => setTestDays(Number(event.target.value))} /><span>days</span></label><label>Rolling Step<input type="number" min="7" max="365" value={stepDays} onChange={(event) => setStepDays(Number(event.target.value))} /><span>days</span></label><button className="secondary-btn" disabled={walkLoading || Boolean(validationError)} onClick={runWalkForward}>{walkLoading ? "Validating…" : "Run Walk-Forward"}</button></div>{walkResult && <><p className="method-note">{walkResult.note}</p><div className="research-table-wrap"><table><thead><tr><th>Window</th><th>Train Return</th><th>Test Return</th><th>Train PF</th><th>Test PF</th><th>Test Drawdown</th></tr></thead><tbody>{walkResult.windows.map((window, index) => { const train = window.train as BacktestMetrics, test = window.test as BacktestMetrics; return <tr key={index}><td>{index + 1}</td><td>{formatMetric(train.total_return,"percent")}</td><td>{formatMetric(test.total_return,"percent")}</td><td>{formatMetric(train.profit_factor,"ratio")}</td><td>{formatMetric(test.profit_factor,"ratio")}</td><td>{formatMetric(test.maximum_drawdown,"percent")}</td></tr>; })}</tbody></table></div></>}</ResearchSection></div>

    <ResearchSection title="Exact Paper vs Backtest Reconciliation" eyebrow="Signal lineage · no inferred matches"><ReconciliationPanel data={reconciliation} /></ResearchSection>
    {result && <div className="research-method"><strong>Methodology</strong><span>{result.execution_model}</span><span>{result.indicator_model}</span></div>}
  </main>;
}
