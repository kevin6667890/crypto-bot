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
import { CSSProperties, useEffect, useMemo, useState } from "react";
import { EquityChart, FlowChart, MarketChart, ReplayChart } from "./charts";
import StrategyResearch from "./StrategyResearch";
import Operations from "./Operations";
import { useLanguage } from "./i18n";
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
  askMarketCopilot,
  PaperStatus,
  fetchPaperStatus,
  fetchVpvrProfile,
  VpvrProfile,
  fetchReplayItems,
  fetchReplayDetail,
  ReplayItem,
  ReplayDetail,
  strategyComparison,
  strategyEvolution,
} from "./data";

function formatSigned(value: number, suffix = "") {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}${suffix}`;
}

function VpvrHistogram({ profile, poc, vah, val, professional, viewport }: { profile: Array<{ price_low: number; price_high: number; volume: number; delta: number; trades: number }>; poc?: number; vah?: number; val?: number; professional?: boolean; viewport?: { top: number; bottom: number } }) {
  const rows = [...profile].sort((a, b) => b.price_low - a.price_low);
  const maxVolume = Math.max(...rows.map((row) => row.volume), 1);
  return <div className="vpvr-histogram" style={viewport ? { top: viewport.top, bottom: `calc(100% - ${viewport.bottom}px)` } : undefined}>
    <div className="vpvr-histogram-head"><span>成交价档位分布</span><small>{professional ? "绿色：主动买盘 Delta；红色：主动卖盘 Delta" : "逐笔流就绪前显示 K 线成交量近似"}</small></div>
    <div className="vpvr-rows">{rows.map((row) => {
      const midpoint = (row.price_low + row.price_high) / 2;
      const inValueArea = midpoint >= (val ?? -Infinity) && midpoint <= (vah ?? Infinity);
      const isPoc = poc !== undefined && Math.abs(midpoint - poc) <= (row.price_high - row.price_low) / 2;
      const deltaClass = professional ? (row.delta >= 0 ? "buy" : "sell") : "fallback";
      return <div className={`vpvr-row ${inValueArea ? "value-area" : ""} ${isPoc ? "poc" : ""}`} key={`${row.price_low}-${row.price_high}`}>
        <span className="vpvr-price" /><div className="vpvr-track"><i className={deltaClass} style={{ width: `${Math.max(2, row.volume / maxVolume * 100)}%` }} /></div><span className="vpvr-tags">{isPoc ? "POC" : ""}</span>
      </div>;
    })}</div>
  </div>;
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

/* Legacy prototype retained outside the active application tree.
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

*/
function Workspace() {
  const {
    language,
    setLanguage,
    t,
    value: localValue,
    message,
  } = useLanguage();
  const engineInstruments = ["BTC-USDT", "ETH-USDT"];
  const [snapshot, setSnapshot] = useState<MarketSnapshot>(() =>
    demoSnapshot()
  );
  const [signal, setSignal] = useState<SignalAnalysis>(demoSignal);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [instrument, setInstrument] = useState("ETH-USDT");
  const [interval, setInterval] = useState("15m");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [paper, setPaper] = useState<PaperStatus | null>(null);
  const [vpvr, setVpvr] = useState<VpvrProfile | null>(null);
  const [activePage, setActivePage] = useState<
    "market" | "research" | "operations"
  >("market");
  const [question, setQuestion] = useState("");
  const [chatAnswer, setChatAnswer] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [replayItems, setReplayItems] = useState<ReplayItem[]>([]);
  const [replayDetail, setReplayDetail] = useState<ReplayDetail | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      const [market, analysis] = await Promise.all([
        fetchEthSnapshot(instrument),
        fetchSignalAnalysis(instrument),
      ]);
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
    if (engineInstruments.includes(instrument)) {
      try {
        const [paperStatus, history] = await Promise.all([
          fetchPaperStatus(instrument),
          fetchReplayItems(instrument),
        ]);
        setPaper(paperStatus);
        setReplayItems(history);
      } catch {
        setPaper(null);
        setReplayItems([]);
      }
    } else {
      setPaper(null);
      setReplayItems([]);
    }
  }

  useEffect(() => {
    setReplayDetail(null);
    setChatAnswer("");
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [instrument]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    const connect = () => {
      socket = new WebSocket("wss://ws.okx.com:8443/ws/v5/public");
      socket.onopen = () => socket?.send(JSON.stringify({ op: "subscribe", args: [{ channel: "tickers", instId: instrument }] }));
      socket.onmessage = (event) => {
        try {
          const row = JSON.parse(event.data)?.data?.[0];
          const price = Number(row?.last), open = Number(row?.open24h);
          if (!Number.isFinite(price)) return;
          setSnapshot((current) => ({ ...current, price, changePct: open > 0 ? (price - open) / open * 100 : current.changePct, high24: Number(row.high24h) || current.high24, low24: Number(row.low24h) || current.low24, volume: Number(row.vol24h) || current.volume, updatedAt: new Date(Number(row.ts) || Date.now()).toLocaleTimeString(), source: "OKX" }));
        } catch { /* Preserve the last confirmed HTTP snapshot on malformed frames. */ }
      };
      socket.onclose = () => { if (!closed) retry = window.setTimeout(connect, 3000); };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { closed = true; window.clearTimeout(retry); socket?.close(); };
  }, [instrument]);

  useEffect(() => {
    let cancelled = false;
    if (!engineInstruments.includes(instrument)) { setVpvr(null); return; }
    const load = () => fetchVpvrProfile(instrument, interval).then((profile) => { if (!cancelled) setVpvr(profile); }).catch(() => { if (!cancelled) setVpvr(null); });
    load();
    const timer = window.setInterval(load, 60_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [instrument, interval]);

  const runtimeAnalysis =
    paper?.instrument === instrument ? paper.analysis : null;
  const legacyVpvr = runtimeAnalysis?.vpvr;
  const chartFlow = useMemo(() => paper?.flow?.professional?.available ? { cvd_series: paper.flow.professional.cvd_series, oi_series: paper.flow.professional.oi_series } : undefined, [paper?.flow?.professional]);
  const action =
    runtimeAnalysis?.action || (signal.score >= 70 ? "WATCH" : "WAIT");
  const decisionScore = runtimeAnalysis?.score ?? signal.score;
  const decisionConditions =
    runtimeAnalysis?.contributions?.map((item) => ({
      label: message(item.label_code, item.detail_params, item.label),
      value: message(item.detail_code, item.detail_params, item.detail),
      tone: item.status === "pass" ? ("pass" as const) : ("watch" as const),
    })) ?? signal.conditions;
  const risk = snapshot.price * 0.015;
  async function submitQuestion(event: React.FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setChatLoading(true);
    try {
      setChatAnswer(await askMarketCopilot(question, instrument));
    } catch (error) {
      setChatAnswer(`${t("market.copilotFailed")} ${t("common.technicalDetail", {detail:error instanceof Error?error.message:String(error)})}`);
    } finally {
      setChatLoading(false);
    }
  }
  async function selectReplay(createdAt: string) {
    if (!createdAt) {
      setReplayDetail(null);
      return;
    }
    setReplayLoading(true);
    try {
      setReplayDetail(await fetchReplayDetail(instrument, createdAt));
    } catch (error) {
      setChatAnswer(`${t("market.replayFailed")} ${t("common.technicalDetail", {detail:error instanceof Error?error.message:String(error)})}`);
    } finally {
      setReplayLoading(false);
    }
  }
  return (
    <div className="workspace">
      <header className="workspace-topbar">
        <div className="workspace-brand">
          <TerminalSquare size={19} />
          <strong>Crypto-Bot</strong>
          <span>{t("app.workspace")}</span>
          <div className="page-switch">
            <button
              className={activePage === "market" ? "active" : ""}
              onClick={() => setActivePage("market")}
            >
              {t("nav.market")}
            </button>
            <button
              className={activePage === "research" ? "active" : ""}
              onClick={() => setActivePage("research")}
            >
              {t("nav.research")}
            </button>
            <button
              className={activePage === "operations" ? "active" : ""}
              onClick={() => setActivePage("operations")}
            >
              {t("nav.operations")}
            </button>
          </div>
        </div>
        <div className="market-controls">
          <span className="live-dot" />{" "}
          <strong>{t("market.publicData")}</strong>
          <select
            value={instrument}
            onChange={(event) => setInstrument(event.target.value)}
            aria-label={t("common.instrument")}
          >
            <option>BTC-USDT</option>
            <option>ETH-USDT</option>
          </select>
          <select
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
            aria-label={t("common.timeframe")}
          >
            <option>1m</option>
            <option>5m</option>
            <option>15m</option>
            <option>1h</option>
            <option>4h</option>
            <option>1D</option>
          </select>
          <div
            className="language-switch"
            role="group"
            aria-label={t("common.language")}
          >
            <button
              className={language === "en" ? "active" : ""}
              onClick={() => setLanguage("en")}
            >
              EN
            </button>
            <button
              className={language === "zh" ? "active" : ""}
              onClick={() => setLanguage("zh")}
            >
              中文
            </button>
          </div>
          <button
            className="icon-button"
            onClick={refresh}
            disabled={loading}
            title={t("common.refresh")}
            aria-label={t("common.refresh")}
          >
            <RefreshCw size={15} className={loading ? "spinning" : ""} />
          </button>
          <button
            className="icon-button"
            onClick={() => setSettingsOpen(true)}
            title={t("common.settings")}
            aria-label={t("common.settings")}
          >
            <Settings size={16} />
          </button>
        </div>
      </header>

      {activePage === "market" ? (
        <div className="workspace-grid">
          <aside className="watchlist-panel">
            <div className="section-title">
              <div>
                <span className="eyebrow">{t("market.okxSpot")}</span>
                <h2>{t("market.scanner")}</h2>
              </div>
              <span className="count-badge">{watchlist.length || 2}</span>
            </div>
            <p className="scanner-note">{t("market.scannerNote")}</p>
            <div className="scanner-head">
              <span>{t("common.instrument")}</span>
              <span>24h</span>
            </div>
            {watchlist.length ? (
              watchlist.map((item) => (
                <button
                  className={
                    item.instrument === instrument
                      ? "scan-row selected"
                      : "scan-row"
                  }
                  onClick={() => setInstrument(item.instrument)}
                  key={item.instrument}
                >
                  <span>
                    <strong>{item.instrument.replace("-USDT", "")}</strong>
                    <small>
                      $
                      {item.price.toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })}
                    </small>
                  </span>
                  <b className={item.changePct >= 0 ? "positive" : "negative"}>
                    {formatSigned(item.changePct, "%")}
                  </b>
                </button>
              ))
            ) : (
              <div className="scanner-loading">
                {t("market.loadingWatchlist")}
              </div>
            )}
            <div className="scanner-foot">
              <span className="pulse" />
              {t("market.scannerRefresh")}
            </div>
          </aside>

          <main className="workspace-main">
            <section className="market-summary">
              <div>
                <span className="eyebrow">
                  {instrument} · {t("market.okxSpot")}
                </span>
                <div className="price-line">
                  <strong>
                    $
                    {snapshot.price.toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })}
                  </strong>
                  <b
                    className={
                      snapshot.changePct >= 0 ? "positive" : "negative"
                    }
                  >
                    {formatSigned(snapshot.changePct, "%")}
                  </b>
                </div>
                <small>
                  {t("market.updatedAt", { time: snapshot.updatedAt })}
                </small>
              </div>
              <div className="summary-stats">
                <span>
                  <small>{t("market.high24")}</small>
                  <b>{snapshot.high24.toFixed(2)}</b>
                </span>
                <span>
                  <small>{t("market.low24")}</small>
                  <b>{snapshot.low24.toFixed(2)}</b>
                </span>
                <span>
                  <small>EMA20</small>
                  <b>{snapshot.ema20?.toFixed(2) ?? "--"}</b>
                </span>
              </div>
            </section>
            <section className="chart-workspace">
              <div className="chart-toolbar">
                <div>
                  <span className="eyebrow">{t("market.liveChart")}</span>
                  <h2>{t("market.priceStructure")}</h2>
                </div>
                <div className="indicator-toggles">
                  {["1m", "5m", "15m", "1h", "4h", "1D"].map((item) => (
                    <button
                      key={item}
                      className={interval === item ? "active" : ""}
                      onClick={() => setInterval(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
              <div className="workspace-chart">
                <MarketChart instrument={instrument} interval={interval} flow={chartFlow} />
                {paper?.flow?.professional?.available && <div className="flow-pane-labels"><span>CVD · 逐笔主动成交差</span><span>OI · 永续未平仓量</span></div>}
              </div>
              <div className="chart-legend">
                <span>
                  <i className="ma60" /> MA60
                </span>
                <span>
                  <i className="ma200" /> MA200
                </span>
                <span className="muted">{t("market.flowProxy")}</span>
              </div>
            </section>
            {legacyVpvr?.available && (
              <section className="flow-panel legacy-vpvr">
                <div className="section-title">
                  <div>
                    <span className="eyebrow">{legacyVpvr.professional ? t("market.vpvrProfessional") : t("market.vpvrMethod")}</span>
                    <h2>{t("market.vpvrTitle")}</h2>
                  </div>
                  <small>{legacyVpvr.professional ? t("market.vpvrCoverage", { count: Math.round((legacyVpvr.coverage_seconds || 0) / 60) }) : t("market.vpvrCollecting", { count: Math.round((legacyVpvr.collection?.coverage_seconds || 0) / 60) })}</small>
                </div>
                <div className="flow-grid">
                  <article><div className="flow-head"><span>{t("market.vpvrPoc")}</span><b>${legacyVpvr.poc?.toFixed(2)}</b></div><small>{t("market.vpvrPocHelp")}</small></article>
                  <article><div className="flow-head"><span>{t("market.vpvrValueArea")}</span><b>${legacyVpvr.val?.toFixed(2)} – ${legacyVpvr.vah?.toFixed(2)}</b></div><small>{t("market.vpvrValueAreaHelp", { percent: legacyVpvr.value_area_pct || 0 })}</small></article>
                </div>
                {!!legacyVpvr.profile?.length && <VpvrHistogram profile={legacyVpvr.profile} poc={legacyVpvr.poc} vah={legacyVpvr.vah} val={legacyVpvr.val} professional={legacyVpvr.professional} />}
              </section>
            )}
            {paper?.flow && (
              <section className="flow-panel flow-legacy">
                <div className="section-title">
                  <div>
                    <span className="eyebrow">
                      {t("market.publicDerivatives")}
                    </span>
                    <h2>{t("market.orderFlowOi")}</h2>
                  </div>
                  <small>{t("flow.source.okxTradesAndSwapOi")}</small>
                </div>
                <div className="flow-grid">
                  <article>
                    <div className="flow-head">
                      <span>{paper.flow.professional?.available ? "专业 CVD · 最近 6 小时" : t("market.cvdRecent")}</span>
                      <b
                        className={
                          paper.flow.cvd_delta >= 0 ? "positive" : "negative"
                        }
                      >
                        {(paper.flow.professional?.available ? paper.flow.professional.cvd : paper.flow.cvd_delta) >= 0 ? "+" : ""}
                        {(paper.flow.professional?.available ? paper.flow.professional.cvd : paper.flow.cvd_delta).toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </b>
                    </div>
                    <FlowChart points={paper.flow.professional?.available ? paper.flow.professional.cvd_series : paper.flow.cvd_series} zeroLine />
                    <small>{paper.flow.professional?.available ? `WebSocket 逐笔成交聚合 · 已覆盖 ${Math.round(paper.flow.professional.coverage_seconds / 60)} 分钟 · 当前不参与评分` : t("market.cvdHelp")}</small>
                  </article>
                  <article>
                    <div className="flow-head">
                      <span>{t("market.swapOi")}</span>
                      <b>
                        $
                        {paper.flow.oi.toLocaleString(undefined, {
                          maximumFractionDigits: 0,
                        })}
                      </b>
                    </div>
                    <FlowChart
                      color="#0ea5e9"
                      points={(paper.flow.professional?.available ? paper.flow.professional.oi_series : paper.flow.oi_history.map((point, index) => ({
                        time:
                          Math.floor(
                            new Date(point.created_at).getTime() / 1000
                          ) || index,
                        value: point.oi,
                      }))) }
                    />
                    <small>
                      {t("market.oiChange", {
                        instrument: instrument.replace("-USDT", "-USDT-SWAP"),
                        change: `${
                          paper.flow.oi_change_pct >= 0 ? "+" : ""
                        }${paper.flow.oi_change_pct.toFixed(3)}`,
                      })}
                    </small>
                  </article>
                </div>
              </section>
            )}
            {runtimeAnalysis?.contributions && (
              <section className="explain-panel">
                <div className="section-title">
                  <div>
                    <span className="eyebrow">
                      {t("market.explainableEngine")}
                    </span>
                    <h2>{t("market.scoreContribution")}</h2>
                  </div>
                  <b>{runtimeAnalysis.score}/100</b>
                </div>
                <div className="contribution-grid">
                  {runtimeAnalysis.contributions.map((item) => (
                    <article key={item.key}>
                      <div>
                        <strong>
                          {message(
                            item.label_code,
                            item.detail_params,
                            item.label
                          )}
                        </strong>
                        <span className={item.status}>
                          {item.max
                            ? `${item.points}/${item.max}`
                            : t("common.notAvailable")}
                        </span>
                      </div>
                      <div className="contribution-track">
                        <i
                          style={{
                            width: `${
                              item.max ? (item.points / item.max) * 100 : 0
                            }%`,
                          }}
                        />
                      </div>
                      <small>
                        {message(
                          item.detail_code,
                          item.detail_params,
                          item.detail
                        )}
                      </small>
                    </article>
                  ))}
                </div>
              </section>
            )}
            {engineInstruments.includes(instrument) && (
              <section className="replay-panel">
                <div className="section-title">
                  <div>
                    <span className="eyebrow">{t("market.snapshots")}</span>
                    <h2>{t("market.replay")}</h2>
                  </div>
                  <select
                    value={replayDetail?.created_at || ""}
                    onChange={(event) => selectReplay(event.target.value)}
                    disabled={replayLoading || !replayItems.length}
                  >
                    <option value="">
                      {replayItems.length
                        ? t("market.chooseSnapshot")
                        : t("market.collectingSnapshots")}
                    </option>
                    {replayItems.map((item) => (
                      <option key={item.id} value={item.created_at}>
                        {new Date(item.created_at).toLocaleString()} ·{" "}
                        {localValue(item.analysis.action)} ·{" "}
                        {item.analysis.score}/100
                      </option>
                    ))}
                  </select>
                </div>
                {replayDetail ? (
                  <div className="replay-content">
                    <div className="replay-chart">
                      <ReplayChart candles={replayDetail.candles} />
                    </div>
                    <div className="replay-facts">
                      <strong>
                        {localValue(replayDetail.analysis.action)} ·{" "}
                        {replayDetail.analysis.score}/100
                      </strong>
                      <span>
                        {new Date(replayDetail.created_at).toLocaleString()}
                      </span>
                      <p>
                        {replayDetail.outcome
                          ? message(replayDetail.outcome.message_code,replayDetail.outcome.message_params,replayDetail.outcome.message)
                          : t("market.noReplayOutcome")}
                      </p>
                      {replayDetail.analysis.contributions?.map((item) => (
                        <small key={item.key}>
                          {message(item.label_code,item.detail_params,item.label)}: {item.points}/{item.max} · {message(item.detail_code,item.detail_params,item.detail)}
                        </small>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="empty-ledger">{t("market.replayEmpty")}</p>
                )}
              </section>
            )}
            {paper?.events?.length ? (
              <section className="event-panel">
                <div className="section-title">
                  <div>
                    <span className="eyebrow">
                      {t("market.auditableActivity")}
                    </span>
                    <h2>{t("market.decisionLog")}</h2>
                  </div>
                  <small>
                    {t("market.recentEvents", {
                      count: Math.min(paper.events.length, 3),
                    })}
                  </small>
                </div>
                <div className="event-list">
                  {paper.events.slice(0, 3).map((event) => (
                    <div key={event.id}>
                      <time>{new Date(event.created_at).toLocaleString()}</time>
                      <b>{localValue(event.event_type)}</b>
                      <span>
                        {message(
                          event.message_code,
                          event.message_params,
                          event.message
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
            <section className="ai-brief">
              <BrainCircuit size={19} />
              <div>
                <span className="eyebrow">
                  {t("market.aiBrief", {
                    source: paper?.ai_brief?.source || t("market.waitingPaper"),
                  })}
                </span>
                <strong>
                  {paper?.ai_brief?.created_at
                    ? t("market.updatedAt", {
                        time: new Date(
                          paper.ai_brief.created_at
                        ).toLocaleString(),
                      })
                    : t("market.aiUnavailable")}
                </strong>
                <p>{paper?.ai_brief?.content || t("market.aiDefault")}</p>
              </div>
              <button className="secondary-btn" disabled>
                {t("market.briefSoon")}
              </button>
            </section>
            <section className="copilot-panel">
              <div>
                <span className="eyebrow">{t("market.copilot")}</span>
                <h2>{t("market.askCurrent")}</h2>
                <p>{t("market.copilotHelp")}</p>
              </div>
              <form onSubmit={submitQuestion}>
                <input
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder={t("market.copilotPlaceholder")}
                  maxLength={1200}
                />
                <button className="primary-btn" disabled={chatLoading}>
                  {chatLoading ? t("market.thinking") : t("market.askCopilot")}
                </button>
              </form>
              {chatAnswer && <div className="copilot-answer">{chatAnswer}</div>}
            </section>
            <section className="paper-ledger">
              <div className="section-title">
                <div>
                  <span className="eyebrow">{t("paper.ledger")}</span>
                  <h2>{t("paper.executionResults")}</h2>
                </div>
                {paper ? (
                  <div className="ledger-stats">
                    <span>
                      {t("paper.open")} <b>{paper.summary.open}</b>
                    </span>
                    <span>
                      {t("paper.winRate")} <b>{paper.summary.win_rate}%</b>
                    </span>
                    <span>
                      {t("paper.total")}{" "}
                      <b
                        className={
                          paper.summary.total_r >= 0 ? "positive" : "negative"
                        }
                      >
                        {formatSigned(paper.summary.total_r, "R")}
                      </b>
                    </span>
                  </div>
                ) : (
                  <span className="api-offline">{t("paper.startApi")}</span>
                )}
              </div>
              {paper?.open_trades?.length ? (
                <div className="position-list">
                  {paper.open_trades.map((trade) => (
                    <div className="position-row" key={trade.id}>
                      <span>
                        <b
                          className={
                            trade.side === "LONG" ? "positive" : "negative"
                          }
                        >
                          {localValue(trade.side)}
                        </b>{" "}
                        {trade.instrument}
                      </span>
                      <span>
                        {t("paper.entry", { price: trade.entry.toFixed(2) })}
                      </span>
                      <span>SL {trade.stop_loss.toFixed(2)}</span>
                      <span>TP {trade.take_profit.toFixed(2)}</span>
                      <small>
                        {t("paper.opened", {
                          time: new Date(trade.created_at).toLocaleString(),
                        })}
                      </small>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="empty-ledger">
                  {paper ? t("paper.noPosition") : t("paper.apiOffline")}
                </p>
              )}
              {paper?.closed_trades?.length ? (
                <div className="closed-trades">
                  {paper.closed_trades.slice(0, 5).map((trade) => (
                    <div key={trade.id}>
                      <span>
                        #{trade.id} · {localValue(trade.side)}
                      </span>
                      <span>{localValue(trade.reason)}</span>
                      <b
                        className={
                          (trade.pnl_r || 0) >= 0 ? "positive" : "negative"
                        }
                      >
                        {formatSigned(trade.pnl_r || 0, "R")}
                      </b>
                    </div>
                  ))}
                </div>
              ) : null}
            </section>
          </main>

          <aside className="decision-panel">
            <span className="eyebrow">
              {t("decision.ruleEngine", {
                source: runtimeAnalysis
                  ? t("decision.paperService")
                  : signal.source,
              })}
            </span>
            <div className="decision-head">
              <div>
                <h2>{localValue(action)}</h2>
                <p>
                  {runtimeAnalysis
                    ? t("decision.biasUpdated", {
                        bias: localValue(runtimeAnalysis.bias || "WAIT"),
                        time: runtimeAnalysis.updated_at || "--",
                      })
                    : signal.title}
                </p>
              </div>
              <div className="score-box">
                <strong>{decisionScore}</strong>
                <small>/100</small>
              </div>
            </div>
            {runtimeAnalysis?.strategy_version && (
              <div className="signal-lineage">
                <span>
                  {t("decision.version")}{" "}
                  <b>{runtimeAnalysis.strategy_version}</b>
                </span>
                <span>
                  {t("decision.config")}{" "}
                  <b title={runtimeAnalysis.config_hash}>
                    {runtimeAnalysis.config_hash?.slice(0, 10)}
                  </b>
                </span>
                <span>
                  {t("decision.signal")}{" "}
                  <b title={runtimeAnalysis.signal_id}>
                    {runtimeAnalysis.signal_id?.slice(0, 10)}
                  </b>
                </span>
                <span>
                  {t("decision.collectorFreshness")}{" "}
                  <b>
                    {runtimeAnalysis.updated_at
                      ? new Date(
                          runtimeAnalysis.updated_at
                        ).toLocaleTimeString()
                      : t("decision.starting")}
                  </b>
                </span>
              </div>
            )}
            <p className="decision-summary">
              {runtimeAnalysis ? t("decision.summary") : signal.summary}
            </p>
            {runtimeAnalysis?.timeframes && (
              <div className="timeframe-table">
                <span className="eyebrow">{t("decision.trendStructure")}</span>
                {Object.entries(runtimeAnalysis.timeframes).map(
                  ([frame, item]) => (
                    <div key={frame}>
                      <b>{frame}</b>
                      <span
                        className={
                          item.trend === "Bullish"
                            ? "positive"
                            : item.trend === "Bearish"
                            ? "negative"
                            : "watch"
                        }
                      >
                        {localValue(item.trend)}
                      </span>
                      <small>
                        {item.ema20_slope_pct >= 0 ? "+" : ""}
                        {item.ema20_slope_pct.toFixed(3)}%
                      </small>
                    </div>
                  )
                )}
              </div>
            )}
            <div className="trade-plan">
              <div>
                <span>{t("decision.idealEntry")}</span>
                <b>
                  {snapshot.ema20
                    ? `${(snapshot.ema20 * 0.997).toFixed(2)} – ${(
                        snapshot.ema20 * 1.003
                      ).toFixed(2)}`
                    : t("decision.calculating")}
                </b>
              </div>
              <div>
                <span>{t("decision.invalidation")}</span>
                <b>{(snapshot.price - risk).toFixed(2)}</b>
              </div>
              <div>
                <span>{t("decision.firstTarget")}</span>
                <b>{(snapshot.price + risk * 2).toFixed(2)}</b>
              </div>
            </div>
            {vpvr?.available && (
              <div className="vpvr-summary">
                <span className="eyebrow">VPVR · {vpvr.interval || interval} · {vpvr.professional ? "逐笔成交价" : "已确认K线"}</span>
                <div><span>成交量控制点 POC</span><b>${vpvr.poc?.toFixed(2)}</b></div>
                <div><span>价值区下沿 · 支撑</span><b>${vpvr.val?.toFixed(2)}</b></div>
                <div><span>价值区上沿 · 压力</span><b>${vpvr.vah?.toFixed(2)}</b></div>
                <div><span>筹码密集区</span><b>${vpvr.val?.toFixed(2)} – ${vpvr.vah?.toFixed(2)}</b></div>
                <small>{snapshot.price > (vpvr.vah || Infinity) ? "现价位于价值区上方" : snapshot.price < (vpvr.val || -Infinity) ? "现价位于价值区下方" : "现价位于筹码密集区"}</small>
              </div>
            )}
            {paper?.flow?.professional?.available && (
              <div className="flow-quality-summary">
                <span className="eyebrow">CVD / OI 数据质量</span>
                <div><span>价格 - OI 状态</span><b>{paper.flow.professional.price_oi_state?.label || "采集中"}</b></div>
                <div><span>逐笔覆盖</span><b>{Math.round(paper.flow.professional.coverage_seconds / 60)} 分钟</b></div>
                <div><span>数据缺口</span><b>{paper.flow.professional.quality?.gap_count ?? 0}</b></div>
                <div><span>OI 采样数</span><b>{paper.flow.professional.quality?.oi_samples ?? 0}</b></div>
              </div>
            )}
            <div className="rule-list">
              <span className="eyebrow">{t("decision.ruleChecks")}</span>
              {decisionConditions.map((condition) => (
                <div className="rule-row" key={condition.label}>
                  <span>{condition.label}</span>
                  <b className={condition.tone}>{condition.value}</b>
                </div>
              ))}
            </div>
            {paper?.risk && (
              <div
                className={`risk-console ${
                  paper.risk.allowed ? "safe" : "blocked"
                }`}
              >
                <span className="eyebrow">{t("decision.riskControls")}</span>
                <div>
                  <span>{t("decision.portfolioPositions")}</span>
                  <b>
                    {paper.risk.open_positions}/{paper.risk.max_open_positions}
                  </b>
                </div>
                <div>
                  <span>{t("decision.dailyPnl")}</span>
                  <b>{formatSigned(paper.risk.daily_pnl_r, "R")}</b>
                </div>
                <div>
                  <span>{t("decision.consecutiveLosses")}</span>
                  <b>
                    {paper.risk.consecutive_losses}/
                    {paper.risk.max_consecutive_losses}
                  </b>
                </div>
                <p>
                  {paper.risk.allowed
                    ? t("decision.entriesAllowed")
                    : t("decision.blocked", {
                        reasons: paper.risk.blockers.map(localValue).join("、"),
                      })}
                </p>
              </div>
            )}
            <div className="paper-mode">
              <ShieldCheck size={17} />
              <div>
                <strong>{t("paper.mode")}</strong>
                <span>{t("paper.noLiveOrder")}</span>
              </div>
            </div>
          </aside>
        </div>
      ) : activePage === "research" ? (
        <StrategyResearch />
      ) : (
        <Operations />
      )}
      {settingsOpen && (
        <div
          className="settings-backdrop"
          onClick={() => setSettingsOpen(false)}
        >
          <section
            className="settings-drawer"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="section-title">
              <div>
                <span className="eyebrow">{t("settings.workspace")}</span>
                <h2>{t("common.settings")}</h2>
              </div>
              <button
                className="icon-button"
                aria-label={t("common.close")}
                onClick={() => setSettingsOpen(false)}
              >
                ×
              </button>
            </div>
            <label>
              {t("settings.marketSource")}
              <select>
                <option>{t("market.publicData")}</option>
              </select>
            </label>
            <label>
              {t("settings.watchlistSize")}
              <select>
                <option>{t("settings.liquidPairs")}</option>
              </select>
            </label>
            <label>
              {t("settings.refreshInterval")}
              <select>
                <option>{t("settings.seconds60")}</option>
                <option>{t("settings.minutes5")}</option>
              </select>
            </label>
            <label>
              {t("settings.aiCadence")}
              <select>
                <option>{t("settings.hourlyBackend")}</option>
              </select>
            </label>
            <p className="settings-note">{t("settings.noApiKeys")}</p>
          </section>
        </div>
      )}
    </div>
  );
}

export default function App() {
  return <Workspace />;
}
