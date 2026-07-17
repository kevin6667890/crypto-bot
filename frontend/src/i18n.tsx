import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";

export type Language = "en" | "zh";

const ZH: Record<string, string> = {
  "Decision Workspace":"决策工作台","Market Analysis":"市场分析","Strategy Research":"策略研究","Operations":"系统运维",
  "OKX Public Data":"OKX 公共数据","OKX spot":"OKX 现货","Market Scanner":"市场扫描器","Instrument":"交易标的",
  "24h momentum · public market feed":"24小时动量 · 公共行情数据","Scanner refreshes every 60s":"扫描器每60秒刷新",
  "Loading OKX watchlist…":"正在加载 OKX 自选列表…","Live chart":"实时图表","Price & Structure":"价格与结构",
  "Order Flow & Open Interest":"订单流与未平仓量","OKX public derivatives":"OKX 公共衍生品数据",
  "CVD · recent 100 trades":"CVD · 最近100笔成交","SWAP Open Interest":"永续合约未平仓量",
  "Signed spot taker notional; positive means buy-side dominance in this sample.":"现货主动成交名义金额；正值表示当前样本中买方占优。",
  "Explainable rule engine":"可解释规则引擎","Score Contribution":"评分贡献","Unavailable":"不可用",
  "Stored decision snapshots":"已存储的决策快照","Historical Signal Replay":"历史信号回放","Choose a snapshot":"选择一个快照",
  "Collecting snapshots…":"正在收集快照…","Auditable engine activity":"可审计的引擎活动","Decision & Trade Log":"决策与交易日志",
  "Hourly AI brief":"每小时 AI 简报","waiting for paper service":"等待模拟交易服务","AI analysis is not available yet.":"AI 分析暂不可用。",
  "The always-on paper service stores one multi-factor market summary per hour.":"持续运行的模拟交易服务每小时保存一份多因子市场摘要。",
  "Brief history soon":"简报历史即将上线","Market Copilot · DeepSeek":"市场助手 · DeepSeek","Ask the current market":"询问当前市场",
  "The answer uses the latest OKX indicators, rule score, paper positions and recent outcomes.":"回答将使用最新 OKX 指标、规则评分、模拟持仓和近期结果。",
  "Ask Copilot":"询问助手","Thinking…":"思考中…","SQLite paper ledger":"SQLite 模拟交易账本","Execution & Results":"执行与结果",
  "Open":"持仓中","Win rate":"胜率","Total":"合计","Start paper_api.py to enable":"启动 paper_api.py 后启用",
  "Rule checks":"规则检查","Risk controls":"风险控制","Portfolio positions":"组合持仓","Daily P&L":"当日盈亏",
  "Consecutive losses":"连续亏损","New paper entries are permitted.":"当前允许新的模拟开仓。","Paper trading only":"仅模拟交易",
  "No exchange key or live order is used.":"不使用交易所密钥，也不会发送真实订单。","Settings":"设置","Workspace":"工作区",
  "Market source":"行情来源","Watchlist size":"自选列表数量","Refresh interval":"刷新间隔","AI brief cadence":"AI 简报频率",
  "OKX Public Market Data":"OKX 公共行情数据","5 liquid USDT pairs":"5个高流动性 USDT 交易对","60 seconds":"60秒","5 minutes":"5分钟",
  "Every 1 hour (backend required)":"每1小时（需要后端）","API keys are intentionally not accepted by this browser workspace.":"出于安全考虑，浏览器工作区不接收 API 密钥。",
  "Ideal entry":"理想入场","Invalidation":"失效位置","First target":"第一目标","Risk mode":"风险模式","Starting":"正在启动",
  "Bullish":"看涨","Bearish":"看跌","Mixed":"混合","Unknown":"未知","Bull Trend":"多头趋势","Bear Trend":"空头趋势","Range":"震荡区间",
  "Strategy Validation":"策略验证","Phase 4 · deterministic validation":"Phase 4 · 确定性验证","Stability, rejection mechanics and counterfactual evidence. No threshold is changed automatically.":"分析稳定性、拒绝机制和反事实证据；系统不会自动修改任何阈值。",
  "Gate Funnel":"条件漏斗","Near Miss":"近失信号","Sensitivity":"参数敏感性","Benchmarks":"基准比较","Robustness":"稳健性",
  "Gate data unavailable":"条件数据不可用","Run a backtest or wait for confirmed paper decisions; no final-Action proxy is substituted.":"请先运行回测或等待已确认的模拟决策；系统不会使用最终动作代替条件数据。",
  "Complete canonical decision payload":"完整的标准决策载荷","Run a backtest to load confirmed OKX candles.":"运行回测以加载已确认的 OKX K线。",
  "Gate aggregation queued as run #":"条件聚合任务已进入队列，运行编号 #","Could not queue aggregation":"无法将聚合任务加入队列",
  "Independent":"独立通过率","Conditional":"条件通过率","Signals Lost":"损失信号数","Exclusive":"单一失败","Combined":"组合失败",
  "Top Rejection Reasons":"主要拒绝原因","No rejections in this selection.":"当前筛选范围没有拒绝记录。","Score Distribution":"评分分布","Daily Rejection Timeline":"每日拒绝时间线",
  "No decision payloads match the filters":"没有符合筛选条件的决策载荷","Counterfactual · never counted as paper P&L":"反事实分析 · 永不计入模拟交易盈亏",
  "Near-Miss Analysis":"近失信号分析","Failed gate":"失败条件","Score Gap":"评分差距","Newest First":"最新优先","Oldest First":"最早优先",
  "No near misses yet":"暂无近失信号","A full gate aggregation identifies qualifying WAIT decisions without weakening any rule.":"完整条件聚合会识别符合要求的 WAIT 决策，而不会放宽任何规则。",
  "Time":"时间","Asset":"资产","Bias":"方向偏向","Score":"评分","Gap":"差距","Failed Gates":"失败条件","Regime":"市场状态","Outcome":"结果",
  "Close":"关闭","What prevented entry?":"什么阻止了入场？","What would have changed?":"如果放行会发生什么？","EMA gap":"EMA 差距","RSI gap":"RSI 差距","Volume gap":"成交量差距","Flow":"订单流",
  "Rule-level sensitivity only. It does not say the trade would have been profitable.":"这只是规则层面的敏感性分析，并不代表该交易本可盈利。",
  "Parameter Sensitivity":"参数敏感性","Bounded neighborhood · maximum 100 combinations":"有限参数邻域 · 最多100种组合","Input Run":"输入回测","Parameter":"参数","Start":"起始值","Stop":"终止值","Step":"步长","Run OAT":"运行单参数扫描",
  "Queue a bounded run, then refresh after it completes in Operations.":"将有限扫描加入队列，完成后可在系统运维页面刷新结果。","Refresh Result":"刷新结果","Stability":"稳定性","OOS Return":"样本外收益","Test PF":"测试集盈利因子","Test Drawdown":"测试集回撤",
  "Benchmark Comparison":"基准比较","Same assets · dates · capital · fees · slippage":"相同资产、日期、资金、手续费与滑点","Run Benchmarks":"运行基准比较","No benchmark run selected. Negative comparisons remain visible and the date range is never switched automatically.":"尚未选择基准运行；负向比较会如实显示，日期范围也不会被自动更改。",
  "Per-Asset Comparison":"分资产比较","Monte Carlo & Bootstrap Robustness":"蒙特卡洛与 Bootstrap 稳健性","Reproducible sample perturbation · maximum 5,000 simulations":"可复现的样本扰动 · 最多5,000次模拟",
  "Random Seed":"随机种子","Simulations":"模拟次数","Run Robustness":"运行稳健性分析","Select a completed backtest with actual trades. Empty samples are reported as Insufficient Data.":"请选择包含真实交易的已完成回测；空样本将显示为数据不足。",
  "Median Return":"收益中位数","Median Drawdown":"回撤中位数","95th Drawdown":"95分位回撤","Positive Probability":"正收益概率","Risk of Ruin":"破产风险","5th / 95th Return":"收益第5/95分位",
  "Shadow Experiments":"影子实验","Independent counterfactual accounts · confirmed OKX candles":"独立反事实账户 · 已确认 OKX K线","Active Shadow Experiments":"运行中的影子实验","Shadow Comparison":"影子策略比较",
  "Create a candidate. It begins in Draft and records no invented history.":"创建候选策略；它将从草稿状态开始，不会生成虚构历史。","New":"新建","Duplicate":"复制","Pause":"暂停","Resume":"恢复","Archive":"归档","Refresh":"刷新",
  "Canonical decisions only. Shadow candidates never alter active paper positions and never send exchange orders.":"仅使用标准决策；影子候选策略不会改变正式模拟持仓，也不会发送交易所订单。",
  "Insufficient Data":"数据不足","No shadow data":"暂无影子数据","Current Equity":"当前权益","Closed Trades":"已平仓交易","Fees":"手续费","Drawdown":"回撤","Gate Pass Rate":"条件通过率",
  "Strategy Lifecycle":"策略生命周期","Audited strategy governance · one Active version":"可审计的策略治理 · 仅允许一个 Active 版本","Strategy Status":"策略状态","Evaluate":"评估","Promote":"晋级","Reject":"拒绝","Rollback":"回滚",
  "Promotion Checklist":"晋级检查清单","Requirement":"要求","Evidence":"证据","Assessment":"评估","Passed":"通过","Failed":"失败","Not evaluated":"未评估","Insufficient evidence for promotion.":"晋级证据不足。",
  "Backtest return alone cannot qualify a strategy. Active promotion always requires explicit Admin confirmation.":"仅凭回测收益不能使策略合格；晋级 Active 始终需要管理员明确确认。",
  "Audit History":"审计历史","Action":"操作","Created":"创建时间","No strategy selected.":"尚未选择策略。","Select a strategy to inspect evidence.":"选择策略以查看证据。",
  "Portfolio Research":"组合研究","Unified decision engine · shared capital":"统一决策引擎 · 共享资金","Shared cash · chronological event stream":"共享现金 · 按时间顺序处理事件","Run Portfolio":"运行组合回测",
  "Portfolio Backtest":"组合回测","BTC, ETH and SOL events are processed together. Cash and risk cannot be reused across assets.":"BTC、ETH 和 SOL 事件统一按时间处理，资金和风险预算不能在资产之间重复使用。",
  "Exact Paper vs Backtest Reconciliation":"模拟交易与回测精确对账","Signal lineage · no inferred matches":"信号血缘 · 不推测匹配","A completed run is required. No matches are synthesized.":"需要已完成的运行；系统不会合成匹配记录。",
  "Matched":"已匹配","Unmatched / Divergent":"未匹配 / 存在偏差","All Results":"全部结果","All Sides":"全部方向","All Lineage":"全部血缘",
  "Service Health":"服务健康","Production operations · sanitized public status":"生产运维 · 已脱敏的公共状态","Service health, collectors, persistent research queue and in-app alerts.":"服务健康、采集器、持久研究队列与应用内告警。",
  "Runtime Status":"运行状态","Database Status":"数据库状态","Integrity":"完整性","Paper Scheduler":"模拟交易调度器","Shadow Scheduler Status":"影子调度器状态","Collector Freshness":"采集器新鲜度","Cycle Duration":"周期耗时","Active Job":"当前任务","Job Types":"任务类型",
  "Data Coverage":"数据覆盖","Job Queue":"任务队列","No heavy research job is running.":"当前没有运行中的重型研究任务。","Recent Completed Jobs":"最近完成的任务","Alert Center":"告警中心","No alerts recorded.":"暂无告警记录。","Acknowledge":"确认","Retry":"重试","Cancel":"取消",
  "Admin Token":"管理员令牌","Optional · session only":"可选 · 仅当前会话","Plain HTTP does not secure token transport.":"普通 HTTP 无法保障令牌传输安全。","Validation Operations":"验证运维",
  "Real historical research · OKX public data":"真实历史研究 · OKX 公共数据","Data & Execution Contract":"数据与执行约定","Confirmed Rows":"确认K线数量","First Candle":"首根K线","Last Candle":"末根K线",
  "Strategy Configurations":"策略配置","Configuration Name":"配置名称","Load Configuration":"加载配置","Restore Defaults":"恢复默认值","Enable Long":"启用做多","Enable Short":"启用做空","Enable Confirmed 1D Context":"启用已确认日线背景","Start Date":"开始日期","End Date":"结束日期","Run Backtest":"运行回测",
  "Strategy Parameters":"策略参数","Fast MA":"快速均线","Slow MA":"慢速均线","EMA Pullback Period":"EMA 回调周期","EMA Pullback Distance":"EMA 回调距离","RSI Period":"RSI 周期","RSI Min":"RSI 下限","RSI Max":"RSI 上限","Minimum Volume Ratio":"最低成交量比例","Minimum Score":"最低评分","ATR Period":"ATR 周期","Stop Loss ATR Multiplier":"止损 ATR 倍数","Risk / Reward Ratio":"风险收益比","Trading Fee":"交易手续费","Slippage":"滑点","Cooldown Bars":"冷却K线数","Initial Capital":"初始资金","Risk Per Trade":"单笔风险",
  "Indicator Warm-up":"指标预热","Directional Bias":"方向偏向","Higher-Timeframe Alignment":"高周期一致性","MA60 / MA200 Structure":"MA60 / MA200 结构","EMA20 Pullback":"EMA20 回调","RSI Range":"RSI 区间","Volume Ratio":"成交量比例","Momentum Combined":"动量组合","CVD Alignment":"CVD 一致性","OI Context":"OI 背景","Flow Combined":"订单流组合","Risk / Cooldown / Existing Position":"风险 / 冷却 / 已有持仓","Final Entry Allowed":"最终允许入场",
  "Core Performance":"核心表现","Total Return":"总收益","Annualized Return":"年化收益","Maximum Drawdown":"最大回撤","Profit Factor":"盈利因子","Sharpe Ratio":"夏普比率","Sortino Ratio":"索提诺比率","Total Trades":"总交易数","Average Win":"平均盈利","Average Loss":"平均亏损","Expectancy":"期望值","Final Equity":"最终权益","Fees Paid":"已付手续费",
  "Net Profit":"净利润","Initial Equity":"初始权益","Average Holding Time":"平均持仓时间","Cash Utilization":"资金利用率","Concurrent Positions":"并发持仓数","Long Exposure":"多头敞口","Short Exposure":"空头敞口","Position Size":"仓位大小","Stop Loss":"止损","Take Profit":"止盈","Entry Price":"入场价格","Exit Price":"出场价格","Entry Time":"入场时间","Exit Time":"出场时间","Exit Reason":"离场原因","Trade ID":"交易编号","Candle Close":"K线收盘",
  "Equity Curve":"权益曲线","Drawdown Curve":"回撤曲线","Monthly Returns":"月度收益","Trade R Distribution":"交易R分布","Long / Short Results":"多空结果","Trade Ledger":"交易账本","No trades to display":"暂无交易记录","Previous":"上一页","Next":"下一页","CSV":"导出CSV",
  "Walk-Forward Validation":"滚动前向验证","Training Window":"训练窗口","Test Window":"测试窗口","Rolling Step":"滚动步长","Run Walk-Forward":"运行滚动前向验证","Overfitting Warning":"过拟合警告",
  "Strategy Comparison":"策略比较","Compare Selected":"比较所选策略","Select saved strategy":"选择已保存策略","Save":"保存","Update":"更新","Delete":"删除",
  "Status":"状态","Progress":"进度","Type":"类型","Message / Error":"消息 / 错误","Return":"收益","Trades":"交易数","Win Rate":"胜率","P&L":"盈亏","Entry":"入场","Exit":"出场","Side":"方向","Value":"数值","Threshold":"阈值","Window":"窗口","Days":"天",
  "Trend":"趋势","Structure":"结构","Pullback":"回调","Momentum":"动量","Flow alignment":"订单流一致性","Risk permission":"风险许可","Cooldown":"冷却","Existing position":"已有持仓","Minimum score":"最低评分","Warm-up":"预热","Long":"做多","Short":"做空","All":"全部","ALL":"全部",
  "healthy":"健康","fresh":"新鲜","configured":"已配置","resolved":"已解决","warning":"警告","critical":"严重","open":"未关闭","SQLite persisted":"已保存至 SQLite","Primary feed online":"主数据源在线","Live":"实时","Busy":"繁忙","Loading":"加载中","Queuing":"正在排队","Preparing":"准备中","Validating":"验证中","No sample":"无样本",
  "QUEUED":"排队中","RUNNING":"运行中","COMPLETED":"已完成","FAILED":"失败","CANCELLED":"已取消","INTERRUPTED":"已中断","PAUSED":"已暂停","DRAFT":"草稿","Candidate":"候选","Shadow":"影子","Qualified":"已合格","Active":"已启用","Watch":"观察","Rejected":"已拒绝","Archived":"已归档",
  "Pass":"通过","Fail":"失败","Pending":"待处理","WIN":"盈利","LOSS":"亏损","LONG":"做多","SHORT":"做空","WAIT":"等待","WATCH":"观察","Paper service":"模拟交易服务","BACKTEST":"回测","PAPER":"模拟交易"
};

const PHRASES: Array<[string, string]> = [
  ["Updated ", "更新于 "],["Opened ", "开仓于 "],["recent events", "条近期事件"],["Page ", "第 "],[" of ", " / "],
  ["Run #", "运行 #"],["Job #", "任务 #"],["Benchmark run #", "基准运行 #"],["Robustness run #", "稳健性运行 #"],
  [" days", " 天"],[" trades", " 笔交易"],[" confirmed rows", " 根已确认K线"],[" missing bars", " 根缺失K线"],
  ["since last collection", "较上次采集"],["public market data", "公共行情数据"],["OKX public trades + SWAP OI", "OKX 公共成交 + 永续合约 OI"],
  ["Entry ", "入场 "],["Signal ", "信号 "],["Version ", "版本 "],["Config ", "配置 "],["Source: ", "来源："],
  ["Blocked: ", "已阻止："],["No series data", "暂无序列数据"],["Insufficient sample", "样本不足"],["Not ready", "尚未就绪"]
];

function localize(value: string, language: Language): string {
  if (language === "en" || !value.trim()) return value;
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  const core = value.trim();
  let translated = ZH[core] || core;
  if (translated === core) for (const [from, to] of PHRASES) translated = translated.split(from).join(to);
  return `${leading}${translated}${trailing}`;
}

type LanguageContextValue = { language: Language; setLanguage: (language: Language) => void };
const LanguageContext = createContext<LanguageContextValue>({ language: "en", setLanguage: () => undefined });

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>(() => localStorage.getItem("crypto-bot-language") === "zh" ? "zh" : "en");
  const textOriginals = useRef(new WeakMap<Text, string>());
  const attributeOriginals = useRef(new WeakMap<Element, Map<string, string>>());
  const value = useMemo(() => ({ language, setLanguage: (next: Language) => { localStorage.setItem("crypto-bot-language", next); setLanguageState(next); } }), [language]);

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    document.title = language === "zh" ? "Crypto-Bot · 量化决策工作台" : "Crypto-Bot · Decision Workspace";
    const translateNode = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node as Text;
        if (!textOriginals.current.has(text)) textOriginals.current.set(text, text.data);
        const original = textOriginals.current.get(text) || "";
        const next = localize(original, language);
        if (text.data !== next) text.data = next;
        return;
      }
      if (!(node instanceof Element)) return;
      for (const attribute of ["placeholder", "title", "aria-label"]) {
        if (!node.hasAttribute(attribute)) continue;
        let originals = attributeOriginals.current.get(node);
        if (!originals) { originals = new Map(); attributeOriginals.current.set(node, originals); }
        if (!originals.has(attribute)) originals.set(attribute, node.getAttribute(attribute) || "");
        const next = localize(originals.get(attribute) || "", language);
        if (node.getAttribute(attribute) !== next) node.setAttribute(attribute, next);
      }
      node.childNodes.forEach(translateNode);
    };
    translateNode(document.body);
    const observer = new MutationObserver((mutations) => mutations.forEach((mutation) => {
      if (mutation.type === "characterData") translateNode(mutation.target);
      mutation.addedNodes.forEach(translateNode);
      if (mutation.type === "attributes") translateNode(mutation.target);
    }));
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title", "aria-label"] });
    return () => observer.disconnect();
  }, [language]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() { return useContext(LanguageContext); }
