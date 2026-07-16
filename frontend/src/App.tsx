import {
  Activity,
  BrainCircuit,
  Cpu,
  Database,
  Gauge,
  GitBranch,
  LineChart,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  Signal,
  Settings,
  TerminalSquare,
  Zap,
} from "lucide-react";
import { motion } from "framer-motion";
import { CSSProperties, useEffect, useState } from "react";
import { EquityChart, MarketChart } from "./charts";
import {
  demoSnapshot,
  fetchEthSnapshot,
  fetchSignalAnalysis,
  REAL_BACKTEST_TRADES,
  generateDemoTrades,
  generateOrderBook,
  headlineMetrics,
  MarketSnapshot,
  SignalAnalysis,
  WatchlistItem,
  fetchOkxWatchlist,
  PaperStatus,
  fetchPaperStatus,
  strategyComparison,
  strategyEvolution,
} from "./data";

const navItems = [
  ["Command Center", "command"],
  ["Strategy Lab", "strategy"],
  ["Backtest", "backtest"],
  ["Execution", "execution"],
];

function formatSigned(value: number, suffix = "") {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
}

const demoSignal: SignalAnalysis = {
  score: 0,
  title: "Loading signal state",
  summary: "Waiting for multi-timeframe market data.",
  source: "Demo",
  updatedAt: "--",
  conditions: [
    { label: "4H trend", value: "--", tone: "watch" },
    { label: "1H filter", value: "--", tone: "watch" },
    { label: "15m score", value: "--", tone: "watch" },
    { label: "EMA20 distance", value: "--", tone: "watch" },
    { label: "Risk mode", value: "--", tone: "watch" },
  ],
};

function MetricCard({
  label,
  value,
  delta,
  tone = "neutral",
}: {
  label: string;
  value: string;
  delta?: string;
  tone?: "positive" | "negative" | "neutral" | "warning";
}) {
  return (
    <motion.div
      className={`metric-card tone-${tone}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
    >
      <span>{label}</span>
      <strong>{value}</strong>
      {delta && <small>{delta}</small>}
    </motion.div>
  );
}

function Panel({
  title,
  eyebrow,
  icon,
  children,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <div>
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          <h2>{title}</h2>
        </div>
        {icon && <div className="panel-icon">{icon}</div>}
      </div>
      {children}
    </section>
  );
}

function SystemRail() {
  const [activeSection, setActiveSection] = useState(navItems[0][1]);

  useEffect(() => {
    const handleScroll = () => {
      let current = navItems[0][1];
      let minDistance = Infinity;

      navItems.forEach(([_, target]) => {
        const element = document.getElementById(target);
        if (element) {
          const rect = element.getBoundingClientRect();
          // Distance from top of viewport. We add an offset to trigger slightly before it hits the exact top.
          const distance = Math.abs(rect.top - 100); 
          
          if (distance < minDistance) {
            minDistance = distance;
            current = target;
          }
        }
      });
      setActiveSection(current);
    };

    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  const checks = [
    ["Scanner", "10s", "online"],
    ["Binance REST", "30s TTL", "online"],
    ["Paper Trade DB", "SQLite", "online"],
    ["AI Review", "DeepSeek", "standby"],
  ];

  return (
    <aside className="system-rail">
      <div className="brand-block">
        <TerminalSquare size={26} />
        <div>
          <strong>Crypto-Bot</strong>
          <span>Quant Signal Terminal</span>
        </div>
      </div>
      <nav>
        <span className="nav-label">Jump to section</span>
        {navItems.map(([label, target]) => (
          <a 
            key={target} 
            href={`#${target}`} 
            className={activeSection === target ? "active" : ""}
            onClick={(e) => {
              e.preventDefault();
              document.getElementById(target)?.scrollIntoView({ behavior: 'smooth' });
              setActiveSection(target);
            }}
          >
            {label}
          </a>
        ))}
      </nav>
      <div className="rail-card">
        <span className="eyebrow">Runtime Stack</span>
        <div className="stack-row">
          <Cpu size={16} />
          Python asyncio
        </div>
        <div className="stack-row">
          <Database size={16} />
          SQLite paper ledger
        </div>
        <div className="stack-row">
          <BrainCircuit size={16} />
          AI market review
        </div>
      </div>
      <div className="health-list">
        {checks.map(([name, value, state]) => (
          <div className="health-row" key={name}>
            <span className={`pulse ${state}`} />
            <div>
              <strong>{name}</strong>
              <small>{value}</small>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}

function Header({
  snapshot,
  loading,
  onRefresh,
  onLoginClick,
  isConnected,
}: {
  snapshot: MarketSnapshot;
  loading: boolean;
  onRefresh: () => void;
  onLoginClick: () => void;
  isConnected: string | null;
}) {
  const distance =
    snapshot.ema20 === null ? null : ((snapshot.price - snapshot.ema20) / snapshot.ema20) * 100;

  return (
    <header className="terminal-header" id="command">
      <div className="header-info">
        <span className="eyebrow">ETH/USDT Research System</span>
        <h1>Quant Trading Terminal</h1>
        <p>
          Multi-timeframe EMA20 pullback strategy with live market context, paper execution tracking,
          backtest validation, and AI-assisted trade review.
        </p>
        <div className="header-actions">
          <button className={`primary-btn ${isConnected ? "connected" : ""}`} onClick={onLoginClick}>
            <ShieldCheck size={16} />
            {isConnected ? `Connected to ${isConnected} (Disconnect)` : "Connect Exchange API"}
          </button>
          <div className="header-badges">
            <span className="badge">V3 Strategy</span>
            <span className="badge">Paper Trading</span>
          </div>
        </div>
      </div>
      <div className="market-ticker" aria-label="ETH market snapshot">
        <div className="ticker-top">
          <span>ETH/USDT · {snapshot.source}</span>
          <button className="icon-button" onClick={onRefresh} disabled={loading} title="Refresh market data">
            <RefreshCw size={15} className={loading ? "spinning" : ""} />
          </button>
        </div>
        <strong>${snapshot.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
        <div className="ticker-bottom">
          <span className={snapshot.changePct >= 0 ? "positive" : "negative"}>
            {formatSigned(snapshot.changePct, "%")}
          </span>
          <span>EMA20 {snapshot.ema20 ? snapshot.ema20.toFixed(2) : "--"}</span>
          <span>Dist {distance === null ? "--" : formatSigned(distance, "%")}</span>
          <span>Updated {snapshot.updatedAt}</span>
        </div>
        <div className={`data-status ${snapshot.source === "Demo" ? "warning" : ""}`}>
          {snapshot.source === "Binance"
            ? "Primary feed online"
            : snapshot.source === "OKX"
              ? "Binance restricted; OKX live fallback active"
              : "Demo fallback active"}
        </div>
      </div>
    </header>
  );
}

function CommandCenter({ signal, loadingSignal }: { signal: SignalAnalysis; loadingSignal: boolean }) {
  return (
    <>
      <div className="metrics-grid">
        {headlineMetrics.map((metric) => (
          <MetricCard key={metric.label} {...metric} />
        ))}
      </div>
      <div className="main-grid">
        <Panel title="Live Market Context" eyebrow="15m execution chart" icon={<LineChart size={18} />} className="chart-panel">
          <div className="chart-shell">
            <MarketChart />
          </div>
        </Panel>
        <Panel
          title="Signal Engine"
          eyebrow={loadingSignal ? "updating decision state" : `${signal.source.toLowerCase()} decision state`}
          icon={<Signal size={18} />}
        >
          <div className="signal-score">
            <div
              className="score-ring"
              style={{ "--score": `${signal.score}%` } as CSSProperties}
              aria-label={`Signal score ${signal.score} out of 100`}
            >
              <span>{signal.score}</span>
              <small>/100</small>
            </div>
            <div>
              <strong>{signal.title}</strong>
              <p>{signal.summary}</p>
              <div className={`data-status compact ${signal.source === "Demo" ? "warning" : ""}`}>
                {signal.source === "Live" ? `Live score updated ${signal.updatedAt}` : "Demo signal fallback active"}
              </div>
            </div>
          </div>
          <div className="condition-list">
            {signal.conditions.map((condition) => (
              <div className="condition-row" key={condition.label}>
                <span>{condition.label}</span>
                <strong className={condition.tone}>{condition.value}</strong>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}

function StrategyLab() {
  return (
    <Panel title="Strategy Discovery Lab" eyebrow="from noisy breakout to validated edge" icon={<GitBranch size={18} />} className="wide-panel">
      <div className="timeline">
        {strategyEvolution.map((stage, index) => (
          <motion.article
            className={stage.id === "Final" ? "timeline-card final" : "timeline-card"}
            key={stage.id}
            initial={{ opacity: 0, y: 14 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: index * 0.08 }}
          >
            <div className="timeline-top">
              <span>{stage.id}</span>
              <small>{stage.result}</small>
            </div>
            <h3>{stage.title}</h3>
            <div className="timeline-stats">
              <strong>PF {stage.pf}</strong>
              <span>{stage.trades} trades</span>
            </div>
            <p>{stage.insight}</p>
          </motion.article>
        ))}
      </div>
    </Panel>
  );
}

function BacktestIntelligence() {
  return (
    <div className="main-grid" id="backtest">
      <Panel title="Backtest Equity Curve" eyebrow="2-year validation" icon={<Activity size={18} />} className="chart-panel">
        <div className="chart-shell small">
          <EquityChart />
        </div>
      </Panel>
      <Panel title="Strategy Comparison" eyebrow="risk-adjusted ranking" icon={<Gauge size={18} />}>
        <div className="comparison-table">
          <div className="table-row table-head">
            <span>Strategy</span>
            <span>PF</span>
            <span>Return</span>
            <span>DD</span>
          </div>
          {strategyComparison.map((row) => (
            <div className={row.name === "Trend_EMA20_3R" ? "table-row selected" : "table-row"} key={row.name}>
              <span>{row.name}</span>
              <strong>{row.pf.toFixed(2)}</strong>
              <strong className={row.annual >= 0 ? "positive" : "negative"}>{formatSigned(row.annual, "%")}</strong>
              <span>{row.drawdown.toFixed(2)}%</span>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function ExecutionConsole({ basePrice }: { basePrice: number }) {
  const paperTrades = [...REAL_BACKTEST_TRADES].reverse();
  const orderBook = generateOrderBook(basePrice);
  const totalR = paperTrades.reduce((sum, trade) => sum + trade.r, 0);
  const wins = paperTrades.filter((trade) => trade.result === "WIN").length;
  const losses = paperTrades.filter((trade) => trade.result === "LOSS").length;
  const winRate = (wins / paperTrades.length) * 100;

  const [page, setPage] = useState(1);
  const pageSize = 15;
  const totalPages = Math.ceil(paperTrades.length / pageSize);
  const paginatedTrades = paperTrades.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div className="execution-grid" id="execution">
      <Panel title="Paper Execution Console" eyebrow="demo simulation ledger" icon={<ShieldCheck size={18} />}>
        <div className="demo-note">
          {paperTrades.length} deterministic demo trades over the last 6 months, generated around the current ETH-USDT market price.
        </div>
        <div className="execution-summary">
          <MetricCard label="Total P&L" value={formatSigned(totalR, "%")} tone="positive" />
          <MetricCard label="Demo Trades" value={String(paperTrades.length)} tone="neutral" />
          <MetricCard label="Win Rate" value={`${winRate.toFixed(1)}%`} tone={wins > losses ? "positive" : "warning"} />
        </div>
        <div className="trade-table">
          <div className="trade-row table-head">
            <span style={{ width: 40 }}>#</span>
            <span>Time</span>
            <span>Side</span>
            <span>Entry</span>
            <span>Exit</span>
            <span>R</span>
          </div>
          {paginatedTrades.map((trade) => (
            <div className="trade-row" key={trade.id}>
              <span style={{ width: 40, color: "var(--muted)" }}>{trade.id}</span>
              <span>{trade.time}</span>
              <strong className={trade.side === "LONG" ? "positive" : "negative"}>{trade.side}</strong>
              <span>{trade.entry.toFixed(2)}</span>
              <span>{trade.exit.toFixed(2)}</span>
              <strong className={trade.r > 0 ? "positive" : trade.r < 0 ? "negative" : ""}>
                {formatSigned(trade.r, "%")}
              </strong>
            </div>
          ))}
        </div>
        <div className="pagination">
          <button 
            className="secondary-btn" 
            disabled={page === 1} 
            onClick={() => setPage(p => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <span>Page {page} of {totalPages}</span>
          <button 
            className="secondary-btn" 
            disabled={page === totalPages} 
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
          >
            Next
          </button>
        </div>
      </Panel>
      <Panel title="Order Flow Snapshot" eyebrow="demo depth monitor" icon={<RadioTower size={18} />}>
        <div className="demo-note">Demo depth model generated from the current ETH-USDT reference price.</div>
        <div className="orderbook">
          {orderBook.map((level) => (
            <div className={`book-row ${level.side}`} key={`${level.price}-${level.side}`}>
              <span>{level.price.toFixed(2)}</span>
              <div>
                <i style={{ width: `${Math.min(level.size * 4.5, 96)}%` }} />
              </div>
              <strong>{level.size.toFixed(2)}</strong>
            </div>
          ))}
        </div>
        <div className="ai-review">
          <BrainCircuit size={18} />
          <div>
            <strong>AI Review</strong>
            <p>Last closed trade followed the plan. No revenge entry detected. Continue waiting for EMA20 retest.</p>
          </div>
        </div>
      </Panel>
    </div>
  );
}

function LoginModal({
  onClose,
  onConnect,
}: {
  onClose: () => void;
  onConnect: (exchange: string) => void;
}) {
  const [exchange, setExchange] = useState("Binance");
  const [apiKey, setApiKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    await new Promise((r) => setTimeout(r, 1200));
    setLoading(false);
    onConnect(exchange);
  }

  return (
    <div className="modal-overlay">
      <div className="modal-card">
        <div className="modal-header">
          <h2>Connect Exchange API</h2>
          <button type="button" onClick={onClose} className="close-btn">×</button>
        </div>
        <p className="modal-desc">Bind your Binance or OKX account for live execution. Keys are stored locally.</p>
        <form onSubmit={handleConnect}>
          <div className="form-group">
            <label>Exchange</label>
            <select value={exchange} onChange={(e) => setExchange(e.target.value)}>
              <option value="Binance">Binance</option>
              <option value="OKX">OKX</option>
            </select>
          </div>
          <div className="form-group">
            <label>API Key</label>
            <input type="text" required placeholder="Enter API Key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Secret Key</label>
            <input type="password" required placeholder="Enter Secret Key" value={secretKey} onChange={(e) => setSecretKey(e.target.value)} />
          </div>
          {exchange === "OKX" && (
            <div className="form-group">
              <label>Passphrase</label>
              <input type="password" required placeholder="Enter API Passphrase" value={passphrase} onChange={(e) => setPassphrase(e.target.value)} />
            </div>
          )}
          <div className="modal-actions">
            <button type="button" onClick={onClose} className="secondary-btn">Cancel</button>
            <button type="submit" className="primary-btn" disabled={loading}>
              {loading ? "Validating..." : "Connect"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function LegacyApp() {
  const [snapshot, setSnapshot] = useState<MarketSnapshot>(() => demoSnapshot());
  const [loadingMarket, setLoadingMarket] = useState(false);
  const [signal, setSignal] = useState<SignalAnalysis>(demoSignal);
  const [loadingSignal, setLoadingSignal] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const [connectedExchange, setConnectedExchange] = useState<string | null>(null);

  async function refreshMarket() {
    setLoadingMarket(true);
    try {
      setSnapshot(await fetchEthSnapshot());
    } catch {
      setSnapshot(demoSnapshot());
    } finally {
      setLoadingMarket(false);
    }
  }

  async function refreshSignal() {
    setLoadingSignal(true);
    try {
      setSignal(await fetchSignalAnalysis());
    } finally {
      setLoadingSignal(false);
    }
  }

  useEffect(() => {
    refreshMarket();
    refreshSignal();
    const timer = window.setInterval(refreshMarket, 30_000);
    const signalTimer = window.setInterval(refreshSignal, 60_000);
    return () => {
      window.clearInterval(timer);
      window.clearInterval(signalTimer);
    };
  }, []);

  return (
    <div className="terminal-app">
      <SystemRail />
      <main>
        {showLogin && (
          <LoginModal
            onClose={() => setShowLogin(false)}
            onConnect={(ex) => {
              setConnectedExchange(ex);
              setShowLogin(false);
            }}
          />
        )}
        <Header
          snapshot={snapshot}
          loading={loadingMarket}
          onRefresh={refreshMarket}
          onLoginClick={() => {
            if (connectedExchange) {
              setConnectedExchange(null);
            } else {
              setShowLogin(true);
            }
          }}
          isConnected={connectedExchange}
        />
        <CommandCenter signal={signal} loadingSignal={loadingSignal} />
        <div id="strategy">
          <StrategyLab />
        </div>
        <BacktestIntelligence />
        <ExecutionConsole basePrice={snapshot.price} />
        <footer>
          <span>
            <Zap size={14} /> Educational research dashboard. Not financial advice.
          </span>
          <span>Python · asyncio · Binance/OKX Market Data · SQLite · DeepSeek AI · React</span>
        </footer>
      </main>
    </div>
  );
}

function Workspace() {
  const [snapshot, setSnapshot] = useState<MarketSnapshot>(() => demoSnapshot());
  const [signal, setSignal] = useState<SignalAnalysis>(demoSignal);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [instrument, setInstrument] = useState("ETH-USDT");
  const [interval, setInterval] = useState("15m");
  const [showBoll, setShowBoll] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [paper, setPaper] = useState<PaperStatus | null>(null);
  const [activePage, setActivePage] = useState<"market" | "research">("market");

  async function refresh() {
    setLoading(true);
    try {
      const [market, analysis] = await Promise.all([fetchEthSnapshot(instrument), fetchSignalAnalysis(instrument)]);
      setSnapshot(market);
      setSignal(analysis);
    } catch {
      setSnapshot(demoSnapshot());
    } finally {
      setLoading(false);
    }
    try {
      setWatchlist(await fetchOkxWatchlist());
    } catch {
      setWatchlist((current) => current);
    }
    try {
      setPaper(await fetchPaperStatus());
    } catch {
      setPaper(null);
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [instrument]);

  const runtimeAnalysis = instrument === "ETH-USDT" ? paper?.analysis : null;
  const action = runtimeAnalysis?.action || (signal.score >= 70 ? "WATCH" : "WAIT");
  const decisionScore = runtimeAnalysis?.score ?? signal.score;
  const decisionConditions = runtimeAnalysis?.conditions?.map((condition) => ({ ...condition, tone: condition.pass ? "pass" : "watch" as const })) ?? signal.conditions;
  const risk = snapshot.price * 0.015;
  return (
    <div className="workspace">
      <header className="workspace-topbar">
        <div className="workspace-brand"><TerminalSquare size={19} /><strong>Crypto-Bot</strong><span>Decision Workspace</span><div className="page-switch"><button className={activePage === "market" ? "active" : ""} onClick={() => setActivePage("market")}>Market Analysis</button><button className={activePage === "research" ? "active" : ""} onClick={() => setActivePage("research")}>Strategy Research</button></div></div>
        <div className="market-controls">
          <span className="live-dot" /> <strong>OKX Public Data</strong>
          <select value={instrument} onChange={(event) => setInstrument(event.target.value)} aria-label="Instrument"><option>BTC-USDT</option><option>ETH-USDT</option><option>SOL-USDT</option><option>XRP-USDT</option><option>DOGE-USDT</option></select>
          <select value={interval} onChange={(event) => setInterval(event.target.value)} aria-label="Chart interval"><option>15m</option><option>1h</option><option>4h</option></select>
          <button className="icon-button" onClick={refresh} disabled={loading} title="Refresh"><RefreshCw size={15} className={loading ? "spinning" : ""} /></button>
          <button className="icon-button" onClick={() => setSettingsOpen(true)} title="Settings"><Settings size={16} /></button>
        </div>
      </header>

      {activePage === "market" ? <div className="workspace-grid">
        <aside className="watchlist-panel">
          <div className="section-title"><div><span className="eyebrow">OKX spot</span><h2>Market Scanner</h2></div><span className="count-badge">{watchlist.length || 5}</span></div>
          <p className="scanner-note">24h momentum · public market feed</p>
          <div className="scanner-head"><span>Instrument</span><span>24h</span></div>
          {watchlist.length ? watchlist.map((item) => (
            <button className={item.instrument === instrument ? "scan-row selected" : "scan-row"} onClick={() => setInstrument(item.instrument)} key={item.instrument}>
              <span><strong>{item.instrument.replace("-USDT", "")}</strong><small>${item.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</small></span>
              <b className={item.changePct >= 0 ? "positive" : "negative"}>{formatSigned(item.changePct, "%")}</b>
            </button>
          )) : <div className="scanner-loading">Loading OKX watchlist…</div>}
          <div className="scanner-foot"><span className="pulse" /> Scanner refreshes every 60s</div>
        </aside>

        <main className="workspace-main">
          <section className="market-summary">
            <div><span className="eyebrow">{instrument} · OKX spot</span><div className="price-line"><strong>${snapshot.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong><b className={snapshot.changePct >= 0 ? "positive" : "negative"}>{formatSigned(snapshot.changePct, "%")}</b></div><small>Updated {snapshot.updatedAt} · public market data</small></div>
            <div className="summary-stats"><span><small>24H HIGH</small><b>{snapshot.high24.toFixed(2)}</b></span><span><small>24H LOW</small><b>{snapshot.low24.toFixed(2)}</b></span><span><small>EMA20</small><b>{snapshot.ema20?.toFixed(2) ?? "--"}</b></span></div>
          </section>
          <section className="chart-workspace">
            <div className="chart-toolbar"><div><span className="eyebrow">Live chart</span><h2>Price & Structure</h2></div><div className="indicator-toggles"><button className="active">MA 5</button><button className="active">MA 10</button><button className={showBoll ? "active" : ""} onClick={() => setShowBoll(!showBoll)}>BOLL</button></div></div>
            <div className="workspace-chart"><MarketChart instrument={instrument} interval={interval} showBoll={showBoll} /></div>
            <div className="chart-legend"><span><i className="ma5" /> MA5</span><span><i className="ma10" /> MA10</span><span><i className="boll" /> Bollinger (20,2)</span><span className="muted">CVD / OI require the backend collector</span></div>
          </section>
          <section className="ai-brief"><BrainCircuit size={19} /><div><span className="eyebrow">Hourly AI brief · {paper?.ai_brief?.source || "waiting for paper service"}</span><strong>{paper?.ai_brief?.created_at ? `Updated ${new Date(paper.ai_brief.created_at).toLocaleString()}` : "AI analysis is not available yet."}</strong><p>{paper?.ai_brief?.content || "Start the local paper service. When DEEPSEEK_API_KEY is configured on the server, it stores one cautious market summary per hour without controlling trade execution."}</p></div><button className="secondary-btn" disabled>Brief history soon</button></section>
          <section className="paper-ledger"><div className="section-title"><div><span className="eyebrow">SQLite paper ledger</span><h2>Execution & Results</h2></div>{paper ? <div className="ledger-stats"><span>Open <b>{paper.summary.open}</b></span><span>Win rate <b>{paper.summary.win_rate}%</b></span><span>Total <b className={paper.summary.total_r >= 0 ? "positive" : "negative"}>{formatSigned(paper.summary.total_r, "R")}</b></span></div> : <span className="api-offline">Start paper_api.py to enable</span>}</div>
            {paper?.open_trades?.length ? <div className="position-list">{paper.open_trades.map((trade) => <div className="position-row" key={trade.id}><span><b className={trade.side === "LONG" ? "positive" : "negative"}>{trade.side}</b> {trade.instrument}</span><span>Entry {trade.entry.toFixed(2)}</span><span>SL {trade.stop_loss.toFixed(2)}</span><span>TP {trade.take_profit.toFixed(2)}</span><small>Opened {new Date(trade.created_at).toLocaleString()}</small></div>)}</div> : <p className="empty-ledger">{paper ? "No open paper position. The rule engine opens one only when trend, pullback, volume and RSI conditions align." : "Local paper API is offline; live market display remains available."}</p>}
            {paper?.closed_trades?.length ? <div className="closed-trades">{paper.closed_trades.slice(0, 5).map((trade) => <div key={trade.id}><span>#{trade.id} · {trade.side}</span><span>{trade.reason}</span><b className={(trade.pnl_r || 0) >= 0 ? "positive" : "negative"}>{formatSigned(trade.pnl_r || 0, "R")}</b></div>)}</div> : null}
          </section>
        </main>

        <aside className="decision-panel">
          <span className="eyebrow">Rule engine · {runtimeAnalysis ? "Paper service" : signal.source}</span><div className="decision-head"><div><h2>{action}</h2><p>{runtimeAnalysis ? `${runtimeAnalysis.bias || "WAIT"} bias · updated ${runtimeAnalysis.updated_at || "--"}` : signal.title}</p></div><div className="score-box"><strong>{decisionScore}</strong><small>/100</small></div></div>
          <p className="decision-summary">{runtimeAnalysis ? `Rule score uses multi-timeframe trend, EMA20 pullback, 15m volume and RSI. A paper trade is opened automatically only when all entry gates align.` : signal.summary}</p>
          <div className="trade-plan"><div><span>Ideal entry</span><b>{snapshot.ema20 ? `${(snapshot.ema20 * 0.997).toFixed(2)} – ${(snapshot.ema20 * 1.003).toFixed(2)}` : "Calculating"}</b></div><div><span>Invalidation</span><b>{(snapshot.price - risk).toFixed(2)}</b></div><div><span>First target</span><b>{(snapshot.price + risk * 2).toFixed(2)}</b></div></div>
          <div className="rule-list"><span className="eyebrow">Rule checks</span>{decisionConditions.map((condition) => <div className="rule-row" key={condition.label}><span>{condition.label}</span><b className={condition.tone}>{condition.value}</b></div>)}</div>
          <div className="paper-mode"><ShieldCheck size={17} /><div><strong>Paper trading only</strong><span>No exchange key or live order is used.</span></div></div>
        </aside>
      </div> : <section className="research-suite">
        <div className="research-intro"><span className="eyebrow">Existing project modules</span><h2>Strategy research, backtest & execution history</h2><p>These original modules are retained as research tools rather than removed from the project.</p></div>
        <StrategyLab />
        <BacktestIntelligence />
        <ExecutionConsole basePrice={snapshot.price} />
      </section>}
      {settingsOpen && <div className="settings-backdrop" onClick={() => setSettingsOpen(false)}><section className="settings-drawer" onClick={(event) => event.stopPropagation()}><div className="section-title"><div><span className="eyebrow">Workspace</span><h2>Settings</h2></div><button className="icon-button" onClick={() => setSettingsOpen(false)}>×</button></div><label>Market source<select><option>OKX Public Market Data</option></select></label><label>Watchlist size<select><option>5 liquid USDT pairs</option></select></label><label>Refresh interval<select><option>60 seconds</option><option>5 minutes</option></select></label><label>AI brief cadence<select><option>Every 1 hour (backend required)</option></select></label><p className="settings-note">API keys are intentionally not accepted by this browser workspace.</p></section></div>}
    </div>
  );
}

export default function App() {
  return <Workspace />;
}
