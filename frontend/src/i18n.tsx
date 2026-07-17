import React, { createContext, useContext, useEffect, useMemo, useState } from "react";

export type Language = "en" | "zh";
type Params = Record<string, string | number | boolean | null | undefined>;

const en = {
  "app.title": "Crypto-Bot · Decision Workspace",
  "app.workspace": "Decision Workspace",
  "nav.market": "Market Analysis", "nav.research": "Strategy Research", "nav.operations": "Operations",
  "common.refresh": "Refresh", "common.settings": "Settings", "common.language": "Language", "common.instrument": "Instrument",
  "common.timeframe": "Timeframe", "common.status": "Status", "common.action": "Action", "common.start": "Start", "common.stop": "Stop",
  "common.pause": "Pause", "common.resume": "Resume", "common.archive": "Archive", "common.duplicate": "Duplicate", "common.cancel": "Cancel",
  "common.retry": "Retry", "common.close": "Close", "common.new": "New", "common.update": "Update", "common.delete": "Delete",
  "common.previous": "Previous", "common.next": "Next", "common.time": "Time", "common.asset": "Asset", "common.side": "Side",
  "common.score": "Score", "common.gap": "Gap", "common.result": "Result", "common.return": "Return", "common.drawdown": "Drawdown",
  "common.trades": "Trades", "common.fees": "Fees", "common.value": "Value", "common.threshold": "Threshold", "common.evidence": "Evidence",
  "common.requirement": "Requirement", "common.notAvailable": "Not Available", "common.notApplicable": "Not Applicable", "common.insufficientData": "Insufficient Data",
  "common.loading": "Loading", "common.error": "Error", "common.created": "Created", "common.type": "Type", "common.queue": "Queue",
  "common.messageOrError": "Message / Error", "common.all": "All", "common.allSides": "All Sides", "common.allResults": "All Results",
  "common.newestFirst": "Newest First", "common.oldestFirst": "Oldest First", "common.save": "Save", "common.csv": "CSV",
  "common.queuedRun": "Queued run #{id} · {count} combinations", "common.runFailed": "Run failed", "common.notReady": "Not ready",
  "market.publicData": "OKX Public Data", "market.okxSpot": "OKX spot", "market.scanner": "Market Scanner",
  "market.scannerNote": "24h momentum · public market feed", "market.loadingWatchlist": "Loading OKX watchlist…",
  "market.scannerRefresh": "Scanner refreshes every 60s", "market.updatedAt": "Updated {time} · public market data",
  "market.liveChart": "Live chart", "market.priceStructure": "Price & Structure",
  "market.flowProxy": "OKX public flow data · CVD is a recent taker-delta proxy", "market.publicDerivatives": "OKX public derivatives",
  "market.orderFlowOi": "Order Flow & Open Interest", "market.cvdRecent": "CVD · recent 100 trades",
  "market.cvdHelp": "Signed spot taker notional; positive means buy-side dominance in this sample.", "market.swapOi": "SWAP Open Interest",
  "market.oiChange": "OKX {instrument} public OI · {change}% since last collection", "market.explainableEngine": "Explainable rule engine",
  "market.scoreContribution": "Score Contribution", "market.snapshots": "Stored decision snapshots", "market.replay": "Historical Signal Replay",
  "market.chooseSnapshot": "Choose a snapshot", "market.collectingSnapshots": "Collecting snapshots…",
  "market.replayEmpty": "Select a stored snapshot to restore its 15m candles, indicators, rule score and recorded execution outcome.",
  "market.noReplayOutcome": "No subsequent execution event is stored for this snapshot.", "market.auditableActivity": "Auditable engine activity",
  "market.decisionLog": "Decision & Trade Log", "market.recentEvents": "{count} recent events", "market.aiBrief": "Hourly AI brief · {source}",
  "market.waitingPaper": "waiting for paper service", "market.aiUnavailable": "AI analysis is not available yet.",
  "market.aiDefault": "The always-on paper service stores one multi-factor market summary per hour.", "market.briefSoon": "Brief history soon",
  "market.copilot": "Market Copilot · DeepSeek", "market.askCurrent": "Ask the current market",
  "market.copilotHelp": "The answer uses the latest OKX indicators, rule score, paper positions and recent outcomes.",
  "market.copilotPlaceholder": "e.g. Why is the current setup WAIT?", "market.thinking": "Thinking…", "market.askCopilot": "Ask Copilot",
  "market.copilotFailed": "Copilot request failed.", "market.replayFailed": "Replay request failed.",
  "paper.ledger": "SQLite paper ledger", "paper.executionResults": "Execution & Results", "paper.open": "Open", "paper.winRate": "Win rate",
  "paper.total": "Total", "paper.startApi": "Start paper_api.py to enable", "paper.entry": "Entry {price}", "paper.opened": "Opened {time}",
  "paper.noPosition": "No open paper position. The rule engine opens one only when trend, pullback, volume and RSI conditions align.",
  "paper.apiOffline": "Local paper API is offline; live market display remains available.", "paper.mode": "Paper trading only",
  "paper.noLiveOrder": "No exchange key or live order is used.",
  "decision.ruleEngine": "Rule engine · {source}", "decision.paperService": "Paper service", "decision.biasUpdated": "{bias} bias · updated {time}",
  "decision.version": "Version", "decision.config": "Config", "decision.signal": "Signal", "decision.collectorFreshness": "Collector Freshness",
  "decision.starting": "Starting", "decision.summary": "Rule score uses multi-timeframe trend, EMA20 pullback, 15m volume, RSI and public order flow. A paper trade opens only when all entry gates align.",
  "decision.trendStructure": "MA60 / MA200 + EMA20 slope", "decision.idealEntry": "Ideal entry", "decision.invalidation": "Invalidation",
  "decision.firstTarget": "First target", "decision.calculating": "Calculating", "decision.ruleChecks": "Rule checks", "decision.riskControls": "Risk controls",
  "decision.portfolioPositions": "Portfolio positions", "decision.dailyPnl": "Daily P&L", "decision.consecutiveLosses": "Consecutive losses",
  "decision.entriesAllowed": "New paper entries are permitted.", "decision.blocked": "Blocked: {reasons}",
  "decision.contribution.trend":"Trend","decision.contribution.structure":"Structure","decision.contribution.pullback":"Pullback","decision.contribution.momentum":"Momentum","decision.contribution.flow":"Flow",
  "decision.contribution_detail.trend":"Confirmed 1H + 4H trend alignment","decision.contribution_detail.structure":"MA60 / MA200 structure","decision.contribution_detail.pullback":"{close} close vs EMA20","decision.contribution_detail.momentum":"Volume {volume}x · RSI {rsi}","decision.contribution_detail.flow":"CVD {cvd} · OI {oi}%",
  "event.signal_rejected":"Rule gates rejected entry",
  "settings.workspace": "Workspace", "settings.marketSource": "Market source", "settings.watchlistSize": "Watchlist size",
  "settings.liquidPairs": "5 liquid USDT pairs", "settings.refreshInterval": "Refresh interval", "settings.seconds60": "60 seconds",
  "settings.minutes5": "5 minutes", "settings.aiCadence": "AI brief cadence", "settings.hourlyBackend": "Every 1 hour (backend required)",
  "settings.noApiKeys": "API keys are intentionally not accepted by this browser workspace.",
  "research.title": "Strategy Research", "research.realData": "Real historical research · OKX public data",
  "research.description": "Deterministic research only. No AI-generated signals and no exchange order execution.",
  "research.startDate": "Start Date", "research.endDate": "End Date", "research.runBacktest": "Run Backtest", "research.preparing": "Preparing",
  "research.serverRunNote": "The run is processed once on the server. The button remains locked until it finishes.",
  "research.restoreDefaults": "Restore Defaults", "research.enableLong": "Enable Long", "research.enableShort": "Enable Short",
  "research.enableDaily": "Enable Confirmed 1D Context", "research.maxPosition": "Maximum open positions: 1 per single-asset run",
  "research.loadConfig": "Load Configuration", "research.selectStrategy": "Select saved strategy", "research.configName": "Configuration Name",
  "research.dataContract": "Data & Execution Contract", "research.contract": "Signal closes first; entry uses the next candle open. Historical CVD/OI is unavailable and never fabricated.",
  "research.noResult": "No backtest result", "research.noResultHelp": "Choose a real date range and run the deterministic engine. No demo metrics are shown.",
  "research.noTrades": "No trades to display", "research.compareSelected": "Compare Selected", "research.validationAfter": "Validation appears after a completed run.",
  "parameter.fastMa":"Fast MA","parameter.slowMa":"Slow MA","parameter.emaPeriod":"EMA Pullback Period","parameter.emaDistance":"EMA Pullback Distance",
  "parameter.rsiPeriod":"RSI Period","parameter.rsiMin":"RSI Min","parameter.rsiMax":"RSI Max","parameter.volumeRatio":"Minimum Volume Ratio",
  "parameter.minimumScore":"Minimum Score","parameter.atrPeriod":"ATR Period","parameter.stopAtr":"Stop Loss ATR Multiplier","parameter.riskReward":"Risk / Reward Ratio",
  "parameter.fee":"Trading Fee","parameter.slippage":"Slippage","parameter.cooldown":"Cooldown Bars","parameter.capital":"Initial Capital","parameter.riskTrade":"Risk Per Trade",
  "metric.initialCapital":"Initial Capital","metric.finalEquity":"Final Equity","metric.netProfit":"Net Profit","metric.totalReturn":"Total Return","metric.annualizedReturn":"Annualized Return",
  "metric.totalTrades":"Total Trades","metric.winRate":"Win Rate","metric.profitFactor":"Profit Factor","metric.expectancy":"Expectancy","metric.averageWin":"Average Win",
  "metric.averageLoss":"Average Loss","metric.realizedRiskReward":"Realized Risk / Reward","metric.maximumDrawdown":"Maximum Drawdown","metric.sharpe":"Sharpe Ratio",
  "metric.sortino":"Sortino Ratio","metric.consecutiveWins":"Consecutive Wins","metric.consecutiveLosses":"Consecutive Losses","metric.feesPaid":"Fees Paid",
  "metric.longShortTrades":"Long / Short Trades","metric.holdingTime":"Average Holding Time",
  "research.trainingWindow": "Training Window", "research.testWindow": "Test Window", "research.rollingStep": "Rolling Step", "research.days": "days",
  "research.window": "Window", "research.trainReturn": "Train Return", "research.testReturn": "Test Return", "research.trainPf": "Train PF",
  "research.testPf": "Test PF", "research.testDrawdown": "Test Drawdown", "research.methodology": "Methodology",
  "research.parameters": "Strategy Parameters", "research.configurations": "Strategy Configurations", "research.corePerformance": "Core Performance",
  "research.equityCurve": "Equity Curve", "research.drawdownCurve": "Drawdown Curve", "research.candlesExecutions": "Candles & Executions",
  "research.returnDiagnostics": "Return Diagnostics", "research.tradeLedger": "Trade Ledger", "research.strategyComparison": "Strategy Comparison",
  "research.isOos": "In-Sample / Out-of-Sample", "research.walkForward": "Walk-Forward Validation", "research.reconciliation": "Exact Paper vs Backtest Reconciliation",
  "research.tradeDistribution": "Trade R Distribution", "research.monthlyReturns": "Monthly Returns", "research.sideResults": "Long / Short Results",
  "research.candleMarkers": "Candles and trade markers", "research.noSeries": "No series data", "research.loadCandles": "Run a backtest to load confirmed OKX candles.",
  "portfolio.title": "Portfolio Research", "portfolio.engine": "Unified decision engine · shared capital",
  "portfolio.description": "BTC, ETH and SOL events are processed together. Cash and risk cannot be reused across assets.",
  "portfolio.stream": "Shared cash · chronological event stream", "portfolio.backtest": "Portfolio Backtest", "portfolio.run": "Run Portfolio",
  "portfolio.return": "Portfolio Return", "portfolio.maxDrawdown": "Maximum Drawdown", "portfolio.sharpe": "Sharpe", "portfolio.totalTrades": "Total Trades",
  "portfolio.exposure": "Exposure", "portfolio.cashUtilization": "Cash Utilization", "portfolio.longExposure": "Long Exposure",
  "portfolio.shortExposure": "Short Exposure", "portfolio.concurrentPositions": "Concurrent Positions", "portfolio.pnl": "P&L", "portfolio.contribution": "Contribution",
  "portfolio.failed": "Portfolio job failed.", "portfolio.couldNotStart": "Portfolio backtest could not start.", "portfolio.queuePosition": "queue #{position}",
  "portfolio.progress.checking_cache": "Checking the request and candle cache", "portfolio.progress.loading_candles": "Loading confirmed {instrument} candles{loadedText}",
  "portfolio.progress.loaded_candles": "Loaded {loaded} {instrument} candles", "portfolio.progress.rate_limited": "Waiting for OKX rate limiting to recover ({instrument}, retry {attempt})",
  "portfolio.progress.aligning_timeline": "Aligning the unified portfolio timeline", "portfolio.progress.processing_timestamps": "Processing {processed} / {total} timestamps",
  "portfolio.progress.calculating_metrics": "Calculating portfolio equity, risk and performance", "portfolio.progress.metrics_complete": "Calculated portfolio metrics from {points} equity points",
  "portfolio.progress.persisting_trades": "Persisting portfolio trades ({saved} / {total})", "portfolio.progress.persisting_equity": "Persisting portfolio equity ({saved} / {total})",
  "portfolio.progress.results_saved": "Saved {trades} trades and {points} equity points", "portfolio.progress.completed": "Portfolio backtest completed",
  "validation.title": "Strategy Validation", "validation.phase": "Phase 4 · deterministic validation",
  "validation.description": "Stability, rejection mechanics and counterfactual evidence. No threshold is changed automatically.",
  "validation.gateFunnel": "Gate Funnel", "validation.nearMiss": "Near Miss", "validation.sensitivity": "Sensitivity", "validation.benchmarks": "Benchmarks",
  "validation.robustness": "Robustness", "validation.completePayload": "Complete canonical decision payload", "validation.noPayload": "No decision payloads match the filters",
  "validation.noProxy": "Run a backtest or wait for confirmed paper decisions; no final-Action proxy is substituted.",
  "validation.perAsset": "Per-Asset Comparison", "validation.rejectionReasons": "Top Rejection Reasons", "validation.noRejections": "No rejections in this selection.",
  "validation.scoreDistribution": "Score Distribution", "validation.rejectionTimeline": "Daily Rejection Timeline",
  "validation.nearMissAnalysis": "Near-Miss Analysis", "validation.counterfactual": "Counterfactual · never counted as paper P&L", "validation.scoreGap": "Score Gap",
  "validation.noNearMiss": "No near misses yet", "validation.nearMissHelp": "A full gate aggregation identifies qualifying WAIT decisions without weakening any rule.",
  "validation.bias": "Bias", "validation.failedGates": "Failed Gates", "validation.regime": "Regime", "validation.outcome": "Outcome",
  "validation.prevented": "What prevented entry?", "validation.emaGap": "EMA gap", "validation.rsiGap": "RSI gap", "validation.volumeGap": "Volume gap",
  "validation.flow": "Flow", "validation.changed": "What would have changed?", "validation.sensitivityCaution": "Rule-level sensitivity only. It does not say the trade would have been profitable.",
  "validation.failedGate": "Failed gate", "validation.parameterSensitivity": "Parameter Sensitivity", "validation.bounded": "Bounded neighborhood · maximum 100 combinations",
  "validation.estimated": "Estimated:", "validation.runOat": "Run OAT", "validation.refreshResult": "Refresh Result", "validation.parameter": "Parameter",
  "validation.oosReturn": "OOS Return", "validation.stability": "Stability", "validation.assessment": "Assessment",
  "validation.sensitivityMethod": "Stability = 25% neighborhood variance + 25% OOS degradation + 20% positive neighborhood + 15% drawdown stability + 15% sample size. Highest return is not the default recommendation.",
  "validation.queueSensitivity": "Queue a bounded run, then refresh after it completes in Operations.", "validation.benchmarkComparison": "Benchmark Comparison",
  "validation.benchmarkContract": "Same assets · dates · capital · fees · slippage", "validation.runBenchmarks": "Run Benchmarks",
  "validation.noBenchmark": "No benchmark run selected. Negative comparisons remain visible and the date range is never switched automatically.",
  "validation.robustnessTitle": "Monte Carlo & Bootstrap Robustness", "validation.perturbation": "Reproducible sample perturbation · maximum 5,000 simulations",
  "validation.inputRun": "Input Run", "validation.simulations": "Simulations", "validation.randomSeed": "Random Seed", "validation.runRobustness": "Run Robustness",
  "validation.medianReturn": "Median Return", "validation.returnRange": "5th / 95th Return", "validation.medianDrawdown": "Median Drawdown",
  "validation.drawdown95": "95th Drawdown", "validation.positiveProbability": "Positive Probability", "validation.riskOfRuin": "Risk of Ruin",
  "validation.selectTrades": "Select a completed backtest with actual trades. Empty samples are reported as Insufficient Data.", "validation.backtestId": "Backtest ID",
  "shadow.title": "Shadow Experiments", "shadow.description": "Independent counterfactual accounts · confirmed OKX candles",
  "shadow.safety": "Canonical decisions only. Shadow candidates never alter active paper positions and never send exchange orders.",
  "shadow.comparison": "Shadow Comparison", "shadow.candidateComparison": "Candidate comparison", "shadow.active": "Active Shadow Experiments",
  "shadow.noData": "No shadow data", "shadow.createHelp": "Create a candidate. It begins in Draft and records no invented history.",
  "shadow.disclaimer": "Insufficient Data is shown until runtime and closed-trade samples support descriptive comparison. Shadow results are counterfactual and excluded from official paper P&L.",
  "lifecycle.title": "Strategy Lifecycle", "lifecycle.description": "Audited strategy governance · one Active version",
  "lifecycle.policy": "Backtest return alone cannot qualify a strategy. Active promotion always requires explicit Admin confirmation.",
  "lifecycle.strategyStatus": "Strategy Status", "lifecycle.checklist": "Promotion Checklist", "lifecycle.evaluate": "Evaluate", "lifecycle.candidate": "Candidate",
  "lifecycle.promote": "Promote", "lifecycle.reject": "Reject", "lifecycle.rollback": "Rollback", "lifecycle.select": "Select a strategy to inspect evidence.",
  "lifecycle.audit": "Audit History", "lifecycle.none": "No strategy selected.", "lifecycle.notEvaluated": "Not evaluated",
  "lifecycle.insufficient": "Insufficient evidence for promotion.",
  "operations.title": "Operations", "operations.description": "Production operations · sanitized public status",
  "operations.help": "Service health, collectors, persistent research queue and in-app alerts.", "operations.adminToken": "Admin Token",
  "operations.tokenOptional": "Optional · session only", "operations.httpWarning": "Plain HTTP does not secure token transport.",
  "operations.serviceHealth": "Service Health", "operations.collectorFreshness": "Collector Freshness", "operations.databaseStatus": "Database Status",
  "operations.integrity": "Integrity", "operations.size": "Size", "operations.runtimeStatus": "Runtime Status", "operations.paperScheduler": "Paper Scheduler",
  "operations.lastCycle": "Last Cycle", "operations.cycleDuration": "Cycle Duration", "operations.shadowStatus": "Shadow Scheduler Status",
  "operations.scheduler": "Scheduler", "operations.activeCandidates": "Active Candidates", "operations.shadowFreshness": "Shadow Collector Freshness",
  "operations.validation": "Validation Operations", "operations.jobTypes": "Job Types", "operations.phaseRows": "Phase 4 Rows",
  "operations.auditAlerts": "Promotion Audit Alerts", "operations.activeJob": "Active Job", "operations.job": "Job", "operations.progress": "Progress",
  "operations.noActiveJob": "No heavy research job is running.", "operations.recentJobs": "Recent Completed Jobs", "operations.jobQueue": "Job Queue",
  "operations.alertCenter": "Alert Center", "operations.acknowledge": "Acknowledge", "operations.noAlerts": "No alerts recorded.",
  "operations.dataCoverage": "Data Coverage", "operations.confirmedRows": "Confirmed Rows", "operations.firstCandle": "First Candle", "operations.lastCandle": "Last Candle",
  "reconciliation.required": "A completed run is required. No matches are synthesized.", "reconciliation.allLineage": "All Lineage",
  "reconciliation.matched": "Matched", "reconciliation.divergent": "Unmatched / Divergent",
  "job.queued": "Queued", "job.initializing": "Initializing the task", "job.completed": "Completed", "job.failed": "Failed: {error}",
  "job.cancelled": "Cancelled", "job.cancellation_requested": "Cancellation requested", "job.interrupted.restart": "Interrupted because the service restarted",
  "status.QUEUED": "Queued", "status.RUNNING": "Running", "status.CANCEL_REQUESTED": "Cancellation requested", "status.CANCELLED": "Cancelled",
  "status.COMPLETED": "Completed", "status.FAILED": "Failed", "status.INTERRUPTED": "Interrupted",
  "enum.LONG": "Long", "enum.SHORT": "Short", "enum.WAIT": "Wait", "enum.WATCH": "Watch", "enum.TAKE_PROFIT": "Take profit",
  "enum.STOP_LOSS": "Stop loss", "enum.COOLDOWN": "Cooldown", "enum.DATA_GAP": "Data gap", "enum.END_OF_DATA": "End of data",
  "enum.Bullish":"Bullish","enum.Bearish":"Bearish","enum.Mixed":"Mixed","enum.Unknown":"Unknown","enum.Draft":"Draft","enum.Candidate":"Candidate",
  "enum.Shadow":"Shadow","enum.Active":"Active","enum.Paused":"Paused","enum.Stopped":"Stopped","enum.Archived":"Archived","enum.Configured":"Configured","enum.Disabled":"Disabled","enum.CONFIGURED":"Configured","enum.DISABLED":"Disabled",
  "legacy.SIGNAL_REJECTED": "Signal rejected", "legacy.Rule gates rejected entry": "Rule gates rejected entry",
} as const;

export type TranslationKey = keyof typeof en;
const zh: Record<TranslationKey, string> = {
  ...en,
  "decision.contribution.trend":"趋势","decision.contribution.structure":"结构","decision.contribution.pullback":"回调","decision.contribution.momentum":"动量","decision.contribution.flow":"订单流",
  "decision.contribution_detail.trend":"1 小时与 4 小时已确认趋势一致性","decision.contribution_detail.structure":"MA60／MA200 结构","decision.contribution_detail.pullback":"收盘价 {close}，对比 EMA20","decision.contribution_detail.momentum":"成交量 {volume} 倍 · RSI {rsi}","decision.contribution_detail.flow":"CVD {cvd} · OI {oi}%","event.signal_rejected":"入场条件未全部通过",
  "enum.Bullish":"看涨","enum.Bearish":"看跌","enum.Mixed":"混合","enum.Unknown":"未知","enum.Draft":"草稿","enum.Candidate":"候选","enum.Shadow":"影子验证","enum.Active":"正式运行","enum.Paused":"已暂停","enum.Stopped":"已停止","enum.Archived":"已归档","enum.Configured":"已配置","enum.Disabled":"已禁用","enum.CONFIGURED":"已配置","enum.DISABLED":"已禁用",
  "parameter.fastMa":"快速均线","parameter.slowMa":"慢速均线","parameter.emaPeriod":"EMA 回调周期","parameter.emaDistance":"EMA 回调距离","parameter.rsiPeriod":"RSI 周期","parameter.rsiMin":"RSI 下限","parameter.rsiMax":"RSI 上限","parameter.volumeRatio":"最低成交量比例","parameter.minimumScore":"最低评分","parameter.atrPeriod":"ATR 周期","parameter.stopAtr":"止损 ATR 倍数","parameter.riskReward":"风险收益比","parameter.fee":"交易手续费","parameter.slippage":"滑点","parameter.cooldown":"冷却K线数","parameter.capital":"初始资金","parameter.riskTrade":"单笔风险",
  "metric.initialCapital":"初始资金","metric.finalEquity":"最终权益","metric.netProfit":"净利润","metric.totalReturn":"总收益","metric.annualizedReturn":"年化收益","metric.totalTrades":"总交易数","metric.winRate":"胜率","metric.profitFactor":"盈利因子","metric.expectancy":"期望值","metric.averageWin":"平均盈利","metric.averageLoss":"平均亏损","metric.realizedRiskReward":"实际风险收益比","metric.maximumDrawdown":"最大回撤","metric.sharpe":"夏普比率","metric.sortino":"索提诺比率","metric.consecutiveWins":"连续盈利","metric.consecutiveLosses":"连续亏损","metric.feesPaid":"已付手续费","metric.longShortTrades":"多空交易数","metric.holdingTime":"平均持仓时间",
  "app.title":"Crypto-Bot · 量化决策工作台","app.workspace":"决策工作台","nav.market":"市场分析","nav.research":"策略研究","nav.operations":"系统运维",
  "common.refresh":"刷新","common.settings":"设置","common.language":"语言","common.instrument":"交易标的","common.timeframe":"周期","common.status":"状态","common.action":"操作","common.start":"启动","common.stop":"停止","common.pause":"暂停","common.resume":"恢复","common.archive":"归档","common.duplicate":"复制","common.cancel":"取消","common.retry":"重试","common.close":"关闭","common.new":"新建","common.update":"更新","common.delete":"删除","common.previous":"上一页","common.next":"下一页","common.time":"时间","common.asset":"资产","common.side":"方向","common.score":"评分","common.gap":"差距","common.result":"结果","common.return":"收益","common.drawdown":"回撤","common.trades":"交易数","common.fees":"手续费","common.value":"当前值","common.threshold":"阈值","common.evidence":"证据","common.requirement":"要求","common.notAvailable":"暂不可用","common.notApplicable":"不适用","common.insufficientData":"数据不足","common.loading":"加载中","common.error":"错误","common.created":"创建时间","common.type":"类型","common.queue":"队列","common.messageOrError":"消息／错误","common.all":"全部","common.allSides":"全部方向","common.allResults":"全部结果","common.newestFirst":"最新优先","common.oldestFirst":"最早优先","common.save":"保存","common.csv":"导出 CSV","common.queuedRun":"运行 #{id} 已排队，共 {count} 个组合","common.runFailed":"运行失败","common.notReady":"尚未就绪",
  "market.publicData":"OKX 公共数据","market.okxSpot":"OKX 现货","market.scanner":"市场扫描器","market.scannerNote":"24 小时动量 · 公共行情数据","market.loadingWatchlist":"正在加载 OKX 自选列表…","market.scannerRefresh":"扫描器每 60 秒刷新","market.updatedAt":"更新于 {time} · 公共行情数据","market.liveChart":"实时图表","market.priceStructure":"价格与结构","market.flowProxy":"OKX 公共订单流数据 · CVD 为近期主动成交差值代理","market.publicDerivatives":"OKX 公共衍生品数据","market.orderFlowOi":"订单流与未平仓量","market.cvdRecent":"CVD · 最近 100 笔成交","market.cvdHelp":"现货主动成交名义金额；正值表示当前样本中买方占优。","market.swapOi":"永续合约未平仓量","market.oiChange":"OKX {instrument} 公共 OI · 较上次采集 {change}%","market.explainableEngine":"可解释规则引擎","market.scoreContribution":"评分贡献","market.snapshots":"已存储的决策快照","market.replay":"历史信号回放","market.chooseSnapshot":"选择一个快照","market.collectingSnapshots":"正在采集快照…","market.replayEmpty":"选择已存快照，可恢复当时的 15 分钟K线、指标、规则评分和已记录的执行结果。","market.noReplayOutcome":"该快照之后没有已存储的执行事件。","market.auditableActivity":"可审计的引擎活动","market.decisionLog":"决策与交易日志","market.recentEvents":"最近 {count} 条事件","market.aiBrief":"每小时 AI 简报 · {source}","market.waitingPaper":"等待模拟交易服务","market.aiUnavailable":"AI 分析暂不可用。","market.aiDefault":"持续运行的模拟交易服务每小时保存一份多因子市场摘要。","market.briefSoon":"简报历史即将上线","market.copilot":"市场助手 · DeepSeek","market.askCurrent":"询问当前市场","market.copilotHelp":"回答将使用最新 OKX 指标、规则评分、模拟持仓和近期结果。","market.copilotPlaceholder":"例如：为什么当前信号是等待？","market.thinking":"思考中…","market.askCopilot":"询问助手","market.copilotFailed":"市场助手请求失败。","market.replayFailed":"信号回放请求失败。",
  "paper.ledger":"SQLite 模拟交易账本","paper.executionResults":"执行与结果","paper.open":"持仓中","paper.winRate":"胜率","paper.total":"合计","paper.startApi":"启动 paper_api.py 后启用","paper.entry":"入场价 {price}","paper.opened":"开仓于 {time}","paper.noPosition":"当前没有模拟持仓。只有趋势、回调、成交量和 RSI 条件全部一致时，规则引擎才会开仓。","paper.apiOffline":"本地模拟交易 API 离线；实时行情仍可查看。","paper.mode":"仅模拟交易","paper.noLiveOrder":"不使用交易所密钥，也不会发送真实订单。",
  "decision.ruleEngine":"规则引擎 · {source}","decision.paperService":"模拟交易服务","decision.biasUpdated":"{bias}倾向 · 更新于 {time}","decision.version":"版本","decision.config":"配置","decision.signal":"信号","decision.collectorFreshness":"采集器新鲜度","decision.starting":"正在启动","decision.summary":"规则评分综合多周期趋势、EMA20 回调、15 分钟成交量、RSI 和公共订单流；只有全部入场条件一致时才会开启模拟交易。","decision.trendStructure":"MA60／MA200 与 EMA20 斜率","decision.idealEntry":"理想入场区间","decision.invalidation":"失效位置","decision.firstTarget":"第一目标","decision.calculating":"计算中","decision.ruleChecks":"规则检查","decision.riskControls":"风险控制","decision.portfolioPositions":"组合持仓","decision.dailyPnl":"当日盈亏","decision.consecutiveLosses":"连续亏损","decision.entriesAllowed":"当前允许新的模拟开仓。","decision.blocked":"已阻止：{reasons}",
  "settings.workspace":"工作区","settings.marketSource":"行情来源","settings.watchlistSize":"自选列表数量","settings.liquidPairs":"5 个高流动性 USDT 交易对","settings.refreshInterval":"刷新间隔","settings.seconds60":"60 秒","settings.minutes5":"5 分钟","settings.aiCadence":"AI 简报频率","settings.hourlyBackend":"每 1 小时（需要后端服务）","settings.noApiKeys":"出于安全考虑，浏览器工作区不接收 API 密钥。",
  "research.title":"策略研究","research.realData":"真实历史研究 · OKX 公共数据","research.description":"仅进行确定性研究，不生成 AI 信号，也不执行交易所订单。","research.startDate":"开始日期","research.endDate":"结束日期","research.runBacktest":"运行回测","research.preparing":"准备中","research.serverRunNote":"服务器只处理一次该任务；完成前运行按钮保持锁定。","research.restoreDefaults":"恢复默认值","research.enableLong":"启用做多","research.enableShort":"启用做空","research.enableDaily":"启用已确认日线背景","research.maxPosition":"单资产回测最多同时持有 1 个仓位","research.loadConfig":"加载配置","research.selectStrategy":"选择已保存策略","research.configName":"配置名称","research.dataContract":"数据与执行约定","research.contract":"信号在K线收盘时确认，下一根K线开盘入场。历史 CVD／OI 不可用且绝不伪造。","research.noResult":"暂无回测结果","research.noResultHelp":"选择真实日期范围并运行确定性引擎；不会显示演示指标。","research.noTrades":"暂无交易记录","research.compareSelected":"比较所选策略","research.validationAfter":"回测完成后将显示验证结果。","research.trainingWindow":"训练窗口","research.testWindow":"测试窗口","research.rollingStep":"滚动步长","research.days":"天","research.window":"窗口","research.trainReturn":"训练集收益","research.testReturn":"测试集收益","research.trainPf":"训练集盈利因子","research.testPf":"测试集盈利因子","research.testDrawdown":"测试集回撤","research.methodology":"方法说明","research.parameters":"策略参数","research.configurations":"策略配置","research.corePerformance":"核心表现","research.equityCurve":"权益曲线","research.drawdownCurve":"回撤曲线","research.candlesExecutions":"K线与执行记录","research.returnDiagnostics":"收益诊断","research.tradeLedger":"交易账本","research.strategyComparison":"策略比较","research.isOos":"样本内／样本外","research.walkForward":"滚动前向验证","research.reconciliation":"模拟交易与回测精确对账","research.tradeDistribution":"交易 R 值分布","research.monthlyReturns":"月度收益","research.sideResults":"多空结果","research.candleMarkers":"K线和交易标记","research.noSeries":"暂无序列数据","research.loadCandles":"运行回测以加载 OKX 已确认K线。",
  "portfolio.title":"组合研究","portfolio.engine":"统一决策引擎 · 共享资金","portfolio.description":"BTC、ETH 和 SOL 事件按统一时间顺序处理，资金和风险预算不能在资产间重复使用。","portfolio.stream":"共享资金 · 按时间顺序处理事件","portfolio.backtest":"组合回测","portfolio.run":"运行组合回测","portfolio.return":"组合收益","portfolio.maxDrawdown":"最大回撤","portfolio.sharpe":"夏普比率","portfolio.totalTrades":"总交易数","portfolio.exposure":"敞口","portfolio.cashUtilization":"资金利用率","portfolio.longExposure":"多头敞口","portfolio.shortExposure":"空头敞口","portfolio.concurrentPositions":"最大并发持仓","portfolio.pnl":"盈亏","portfolio.contribution":"收益贡献","portfolio.failed":"组合回测任务失败。","portfolio.couldNotStart":"无法启动组合回测。","portfolio.queuePosition":"队列第 {position} 位","portfolio.progress.checking_cache":"正在检查请求与K线缓存","portfolio.progress.loading_candles":"正在加载 {instrument} 已确认K线{loadedText}","portfolio.progress.loaded_candles":"已加载 {loaded} 根 {instrument} K线","portfolio.progress.rate_limited":"等待 OKX 限流恢复（{instrument}，第 {attempt} 次重试）","portfolio.progress.aligning_timeline":"正在对齐统一组合时间线","portfolio.progress.processing_timestamps":"正在处理 {processed}／{total} 个时间点","portfolio.progress.calculating_metrics":"正在计算组合权益、风险和绩效","portfolio.progress.metrics_complete":"已根据 {points} 个权益点计算组合指标","portfolio.progress.persisting_trades":"正在保存组合交易记录（{saved}／{total}）","portfolio.progress.persisting_equity":"正在保存组合权益记录（{saved}／{total}）","portfolio.progress.results_saved":"已保存 {trades} 笔交易和 {points} 个权益点","portfolio.progress.completed":"组合回测已完成",
  "validation.title":"策略验证","validation.phase":"第四阶段 · 确定性验证","validation.description":"分析稳定性、拒绝机制和反事实证据；系统不会自动修改任何阈值。","validation.gateFunnel":"条件漏斗","validation.nearMiss":"近失信号","validation.sensitivity":"参数敏感性","validation.benchmarks":"基准比较","validation.robustness":"稳健性","validation.completePayload":"完整的标准决策载荷","validation.noPayload":"没有符合筛选条件的决策载荷","validation.noProxy":"请先运行回测或等待已确认的模拟决策；系统不会用最终动作代替条件数据。","validation.perAsset":"分资产比较","validation.rejectionReasons":"主要拒绝原因","validation.noRejections":"当前筛选范围没有拒绝记录。","validation.scoreDistribution":"评分分布","validation.rejectionTimeline":"每日拒绝时间线","validation.nearMissAnalysis":"近失信号分析","validation.counterfactual":"反事实分析 · 永不计入模拟交易盈亏","validation.scoreGap":"评分差距","validation.noNearMiss":"暂无近失信号","validation.nearMissHelp":"完整条件聚合会识别符合要求的等待决策，不会放宽任何规则。","validation.bias":"方向倾向","validation.failedGates":"未通过条件","validation.regime":"市场状态","validation.outcome":"结果","validation.prevented":"哪些条件阻止了入场？","validation.emaGap":"EMA 差距","validation.rsiGap":"RSI 差距","validation.volumeGap":"成交量差距","validation.flow":"订单流","validation.changed":"满足哪些条件后可能放行？","validation.sensitivityCaution":"这里只分析规则层面的敏感性，并不代表该交易本可盈利。","validation.failedGate":"未通过条件","validation.parameterSensitivity":"参数敏感性","validation.bounded":"有限参数邻域 · 最多 100 种组合","validation.estimated":"预计组合数：","validation.runOat":"运行单参数扫描","validation.refreshResult":"刷新结果","validation.parameter":"参数","validation.oosReturn":"样本外收益","validation.stability":"稳定性","validation.assessment":"评估","validation.sensitivityMethod":"稳定性评分由邻域方差、样本外退化、正收益邻域、回撤稳定性和样本量共同构成；系统不会默认推荐收益最高的参数。","validation.queueSensitivity":"将有限扫描加入队列，完成后可在系统运维页面刷新结果。","validation.benchmarkComparison":"基准比较","validation.benchmarkContract":"相同资产 · 日期 · 资金 · 手续费 · 滑点","validation.runBenchmarks":"运行基准比较","validation.noBenchmark":"尚未选择基准运行；负向比较会如实显示，日期范围也不会自动更改。","validation.robustnessTitle":"蒙特卡洛与 Bootstrap 稳健性","validation.perturbation":"可复现的样本扰动 · 最多 5,000 次模拟","validation.inputRun":"输入回测","validation.simulations":"模拟次数","validation.randomSeed":"随机种子","validation.runRobustness":"运行稳健性分析","validation.medianReturn":"收益中位数","validation.returnRange":"收益第 5／95 分位","validation.medianDrawdown":"回撤中位数","validation.drawdown95":"回撤第 95 分位","validation.positiveProbability":"正收益概率","validation.riskOfRuin":"破产风险","validation.selectTrades":"请选择包含真实交易的已完成回测；空样本将显示为数据不足。","validation.backtestId":"回测编号",
  "shadow.title":"影子实验","shadow.description":"独立反事实账户 · OKX 已确认K线","shadow.safety":"仅使用标准决策；影子候选策略不会改变正式模拟持仓，也不会发送交易所订单。","shadow.comparison":"影子策略比较","shadow.candidateComparison":"候选策略比较","shadow.active":"运行中的影子实验","shadow.noData":"暂无影子数据","shadow.createHelp":"创建候选策略；它将从草稿状态开始，不会生成虚构历史。","shadow.disclaimer":"只有运行时长和已平仓样本足够后才显示描述性比较；影子结果属于反事实，不计入正式模拟交易盈亏。",
  "lifecycle.title":"策略生命周期","lifecycle.description":"可审计的策略治理 · 仅允许一个正式版本","lifecycle.policy":"仅凭回测收益不能使策略合格；晋级为正式版本始终需要管理员明确确认。","lifecycle.strategyStatus":"策略状态","lifecycle.checklist":"晋级检查清单","lifecycle.evaluate":"评估","lifecycle.candidate":"候选","lifecycle.promote":"晋级","lifecycle.reject":"拒绝","lifecycle.rollback":"回滚","lifecycle.select":"选择策略以查看证据。","lifecycle.audit":"审计历史","lifecycle.none":"尚未选择策略。","lifecycle.notEvaluated":"尚未评估","lifecycle.insufficient":"晋级证据不足。",
  "operations.title":"系统运维","operations.description":"生产运维 · 已脱敏的公共状态","operations.help":"查看服务健康、采集器、持久化研究队列和应用内告警。","operations.adminToken":"管理员令牌","operations.tokenOptional":"可选 · 仅当前会话","operations.httpWarning":"普通 HTTP 无法保障令牌传输安全。","operations.serviceHealth":"服务健康","operations.collectorFreshness":"采集器新鲜度","operations.databaseStatus":"数据库状态","operations.integrity":"完整性","operations.size":"大小","operations.runtimeStatus":"运行状态","operations.paperScheduler":"模拟交易调度器","operations.lastCycle":"最近周期","operations.cycleDuration":"周期耗时","operations.shadowStatus":"影子调度器状态","operations.scheduler":"调度器","operations.activeCandidates":"活跃候选数","operations.shadowFreshness":"影子采集器新鲜度","operations.validation":"验证运维","operations.jobTypes":"任务类型","operations.phaseRows":"第四阶段记录数","operations.auditAlerts":"晋级审计告警","operations.activeJob":"当前任务","operations.job":"任务","operations.progress":"进度","operations.noActiveJob":"当前没有运行中的重型研究任务。","operations.recentJobs":"最近完成的任务","operations.jobQueue":"任务队列","operations.alertCenter":"告警中心","operations.acknowledge":"确认","operations.noAlerts":"暂无告警记录。","operations.dataCoverage":"数据覆盖","operations.confirmedRows":"已确认K线数","operations.firstCandle":"首根K线","operations.lastCandle":"末根K线",
  "reconciliation.required":"需要已完成的运行；系统不会合成匹配记录。","reconciliation.allLineage":"全部血缘","reconciliation.matched":"已匹配","reconciliation.divergent":"未匹配／存在偏差",
  "job.queued":"已排队","job.initializing":"正在初始化任务","job.completed":"已完成","job.failed":"失败：{error}","job.cancelled":"已取消","job.cancellation_requested":"已请求取消","job.interrupted.restart":"服务重启导致任务中断","status.QUEUED":"排队中","status.RUNNING":"运行中","status.CANCEL_REQUESTED":"正在取消","status.CANCELLED":"已取消","status.COMPLETED":"已完成","status.FAILED":"失败","status.INTERRUPTED":"已中断","enum.LONG":"做多","enum.SHORT":"做空","enum.WAIT":"等待","enum.WATCH":"观察","enum.TAKE_PROFIT":"止盈","enum.STOP_LOSS":"止损","enum.COOLDOWN":"冷却期","enum.DATA_GAP":"数据缺口","enum.END_OF_DATA":"数据结束","legacy.SIGNAL_REJECTED":"信号已拒绝","legacy.Rule gates rejected entry":"入场条件未全部通过",
};

function interpolate(template: string, params: Params = {}): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => params[key] == null ? "" : String(params[key]));
}

const legacyCodes: Record<string, TranslationKey> = {
  SIGNAL_REJECTED:"legacy.SIGNAL_REJECTED", "Rule gates rejected entry":"legacy.Rule gates rejected entry",
  TAKE_PROFIT:"enum.TAKE_PROFIT", STOP_LOSS:"enum.STOP_LOSS", COOLDOWN:"enum.COOLDOWN", DATA_GAP:"enum.DATA_GAP", END_OF_DATA:"enum.END_OF_DATA",
  "Initial Capital":"metric.initialCapital","Final Equity":"metric.finalEquity","Net Profit":"metric.netProfit","Total Return":"metric.totalReturn","Annualized Return":"metric.annualizedReturn",
  "Total Trades":"metric.totalTrades","Win Rate":"metric.winRate","Profit Factor":"metric.profitFactor","Expectancy":"metric.expectancy","Average Win":"metric.averageWin",
  "Average Loss":"metric.averageLoss","Risk / Reward Realized":"metric.realizedRiskReward","Maximum Drawdown":"metric.maximumDrawdown","Sharpe Ratio":"metric.sharpe",
  "Sortino Ratio":"metric.sortino","Consecutive Wins":"metric.consecutiveWins","Consecutive Losses":"metric.consecutiveLosses","Fees Paid":"metric.feesPaid",
  "Long / Short Trades":"metric.longShortTrades","Average Holding Time":"metric.holdingTime",
};

type LanguageContextValue = {
  language: Language; setLanguage: (language: Language) => void;
  t: (key: TranslationKey, params?: Params) => string;
  message: (code?: string | null, params?: Params, legacy?: string | null) => string;
  value: (raw?: string | null) => string;
};
const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => localStorage.getItem("crypto-bot-language") === "zh" ? "zh" : "en");
  const value = useMemo<LanguageContextValue>(() => {
    const t = (key: TranslationKey, params: Params = {}) => interpolate((language === "zh" ? zh : en)[key], params);
    return {
      language,
      setLanguage(next) { localStorage.setItem("crypto-bot-language", next); setLanguageState(next); },
      t,
      message(code, params = {}, legacy) {
        if (code && code in en) return t(code as TranslationKey, { loadedText: params.loaded ? ` (${params.loaded}/${params.expected || "?"})` : "", ...params });
        const mapped = legacy && legacyCodes[legacy];
        return mapped ? t(mapped) : (legacy || code || "");
      },
      value(raw) { if (!raw) return ""; const normalized=raw.toUpperCase(); const key = (`enum.${raw}` in en ? `enum.${raw}` : `status.${raw}` in en ? `status.${raw}` : `status.${normalized}` in en ? `status.${normalized}` : legacyCodes[raw]) as TranslationKey | undefined; return key ? t(key) : raw; },
    };
  }, [language]);
  useEffect(() => { document.documentElement.lang = language === "zh" ? "zh-CN" : "en"; document.title = value.t("app.title"); }, [language, value]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) throw new Error("useLanguage must be used inside LanguageProvider");
  return context;
}

export const translationKeys = Object.keys(en) as TranslationKey[];
export const translations = { en, zh } as const;
