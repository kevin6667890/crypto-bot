import type { UTCTimestamp } from "lightweight-charts";

export type Metric = {
  label: string;
  value: string;
  delta?: string;
  tone?: "positive" | "negative" | "neutral" | "warning";
};

export const headlineMetrics: Metric[] = [
  { label: "Profit Factor", value: "2.60", delta: "+173.7% vs baseline", tone: "positive" },
  { label: "Annual Return", value: "+46.43%", delta: "2Y validation", tone: "positive" },
  { label: "Max Drawdown", value: "4.14%", delta: "controlled risk", tone: "warning" },
  { label: "Win Rate", value: "33.8%", delta: "high payoff profile", tone: "neutral" },
  { label: "Trades", value: "68", delta: "2024-2026", tone: "neutral" },
  { label: "Signal Latency", value: "10s", delta: "scanner interval", tone: "positive" },
];

export const strategyEvolution = [
  {
    id: "V1",
    title: "Breakout Entry",
    result: "Rejected",
    pf: "0.95",
    trades: 379,
    insight: "Direct breakout entries overtraded false moves and fees consumed expectancy.",
  },
  {
    id: "V2",
    title: "EMA20 Pullback",
    result: "Edge Found",
    pf: "1.94",
    trades: 44,
    insight: "Waiting for a pullback reduced noise and lifted signal quality immediately.",
  },
  {
    id: "V3",
    title: "Pullback + 1R BE",
    result: "Optimized",
    pf: "3.04",
    trades: 44,
    insight: "Break-even protection removed profitable trades that reverted into losses.",
  },
  {
    id: "Final",
    title: "MTF Score + 3R Target",
    result: "Validated",
    pf: "2.60",
    trades: 68,
    insight: "4H/1H/15m structure scoring preserved PF while improving sample robustness.",
  },
];

export const strategyComparison = [
  { name: "Trend_EMA20_3R", pf: 2.6, annual: 46.43, drawdown: 4.14, trades: 68, winRate: 33.8 },
  { name: "Range_ZLEMA_2R", pf: 1.55, annual: 103.78, drawdown: 7.83, trades: 444, winRate: 37.2 },
  { name: "Adaptive_ADX30", pf: 1.81, annual: 110.78, drawdown: 7.59, trades: 250, winRate: 37.2 },
  { name: "Breakout_2.0R", pf: 0.95, annual: -11.49, drawdown: 27.33, trades: 379, winRate: 38.3 },
];

export type DemoTrade = {
  id: number;
  time: string;
  side: "LONG" | "SHORT";
  entry: number;
  exit: number;
  result: "WIN" | "LOSS" | "BE";
  r: number;
  closeReason: string;
};

export type OrderBookLevel = {
  price: number;
  size: number;
  side: "ask" | "bid";
};

export function generateDemoTrades(basePrice: number, count = 100): DemoTrade[] {
  const trades: DemoTrade[] = [];
  const now = new Date();
  const start = new Date(now);
  start.setMonth(start.getMonth() - 6);
  const totalMs = now.getTime() - start.getTime();

  for (let i = 0; i < count; i += 1) {
    const progress = count === 1 ? 1 : i / (count - 1);
    const date = new Date(start.getTime() + totalMs * progress);
    date.setHours((9 + i * 7) % 24, (15 + i * 11) % 60, 0, 0);
    const wave = Math.sin(i * 0.37) * 0.085 + Math.cos(i * 0.11) * 0.055;
    const entry = basePrice * (1 - 0.08 + progress * 0.1 + wave);
    const side: DemoTrade["side"] = i % 4 === 1 || i % 9 === 0 ? "SHORT" : "LONG";
    const bucket = (i * 17 + 11) % 20;
    const result: DemoTrade["result"] = bucket < 7 ? "WIN" : bucket < 10 ? "BE" : "LOSS";
    const r =
      result === "WIN"
        ? Number((2.35 + ((i * 13) % 55) / 100).toFixed(2))
        : result === "BE"
          ? 0
          : Number((-0.92 - ((i * 7) % 18) / 100).toFixed(2));
    const riskPct = 0.0065 + ((i * 5) % 13) / 10_000;
    const priceMove = entry * riskPct * Math.abs(r || 0.18);
    const exit =
      result === "BE"
        ? entry
        : side === "LONG"
          ? entry + priceMove * Math.sign(r)
          : entry - priceMove * Math.sign(r);

    trades.push({
      id: i + 1,
      time: formatDateTime(date),
      side,
      entry: Number(entry.toFixed(2)),
      exit: Number(exit.toFixed(2)),
      result,
      r,
      closeReason: result === "WIN" ? "TP Hit" : result === "LOSS" ? "SL Hit" : "Breakeven Exit",
    });
  }

  return trades.reverse();
}

export function generateOrderBook(basePrice: number): OrderBookLevel[] {
  const asks = Array.from({ length: 5 }, (_, index) => ({
    price: Number((basePrice + (index + 1) * 0.82).toFixed(2)),
    size: Number((8.5 + Math.sin(index + basePrice / 100) * 4.2 + index * 2.1).toFixed(2)),
    side: "ask" as const,
  })).reverse();
  const bids = Array.from({ length: 5 }, (_, index) => ({
    price: Number((basePrice - (index + 1) * 0.78).toFixed(2)),
    size: Number((9.2 + Math.cos(index + basePrice / 120) * 4.8 + index * 1.9).toFixed(2)),
    side: "bid" as const,
  }));
  return [...asks, ...bids];
}

export type Candle = {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type MarketSnapshot = {
  price: number;
  changePct: number;
  high24: number;
  low24: number;
  volume: number;
  ema20: number | null;
  updatedAt: string;
  source: "Binance" | "OKX" | "Demo";
};

export type SignalCondition = {
  label: string;
  value: string;
  tone: "pass" | "watch" | "fail";
};

export type SignalAnalysis = {
  score: number;
  title: string;
  summary: string;
  conditions: SignalCondition[];
  source: "Live" | "Demo";
  updatedAt: string;
};

export function generateCandles(): Candle[] {
  const candles = [];
  let close = 2320;
  const start = Math.floor(Date.now() / 1000) - 86_400 * 70;

  for (let i = 0; i < 70; i += 1) {
    const drift = 3.2 + Math.sin(i / 5) * 10;
    const shock = Math.cos(i / 3) * 16;
    const open = close;
    close = Math.max(1850, close + drift + shock);
    const high = Math.max(open, close) + 18 + (i % 5) * 4;
    const low = Math.min(open, close) - 16 - (i % 4) * 5;
    candles.push({
      time: (start + i * 86_400) as UTCTimestamp,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
    });
  }

  return candles;
}

export function generateEquityCurve() {
  const points = [];
  let equity = 7500;
  const start = Math.floor(Date.now() / 1000) - 86_400 * 110;

  for (let i = 0; i < 110; i += 1) {
    const pulse = Math.sin(i / 6) * 18 + Math.cos(i / 13) * 28;
    const step = 20.4 + pulse;
    equity = Math.max(7200, equity + step);
    if ([22, 41, 73, 91].includes(i)) equity -= 190;
    points.push({
      time: (start + i * 86_400) as UTCTimestamp,
      value: Number(equity.toFixed(2)),
    });
  }

  points[points.length - 1].value = 10982;
  return points;
}

export async function fetchEthCandles(interval = "15m", limit = 160): Promise<Candle[]> {
  try {
    return await fetchBinanceCandles(interval, limit);
  } catch {
    return fetchOkxCandles(interval, limit);
  }
}

export async function fetchSignalAnalysis(): Promise<SignalAnalysis> {
  try {
    const [m15, h1, h4] = await Promise.all([
      fetchEthCandles("15m", 160),
      fetchEthCandles("1h", 120),
      fetchEthCandles("4h", 90),
    ]);
    return buildSignalAnalysis(m15, h1, h4, "Live");
  } catch {
    const demo = generateCandles();
    return buildSignalAnalysis(demo, demo, demo, "Demo");
  }
}

async function fetchBinanceCandles(interval = "15m", limit = 160): Promise<Candle[]> {
  const response = await fetch(
    `https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=${interval}&limit=${limit}`,
  );
  if (!response.ok) {
    throw new Error(`Binance candles request failed: ${response.status}`);
  }
  const raw = (await response.json()) as Array<
    [number, string, string, string, string, string, number, string, number, string, string, string]
  >;
  return raw.map((row) => ({
    time: Math.floor(row[0] / 1000) as UTCTimestamp,
    open: Number(row[1]),
    high: Number(row[2]),
    low: Number(row[3]),
    close: Number(row[4]),
  }));
}

export async function fetchEthSnapshot(): Promise<MarketSnapshot> {
  try {
    return await fetchBinanceSnapshot();
  } catch {
    return fetchOkxSnapshot();
  }
}

async function fetchBinanceSnapshot(): Promise<MarketSnapshot> {
  const [ticker, candles] = await Promise.all([
    fetch("https://api.binance.com/api/v3/ticker/24hr?symbol=ETHUSDT"),
    fetchBinanceCandles("15m", 25),
  ]);
  if (!ticker.ok) {
    throw new Error(`Binance ticker request failed: ${ticker.status}`);
  }
  const data = (await ticker.json()) as {
    lastPrice: string;
    priceChangePercent: string;
    highPrice: string;
    lowPrice: string;
    volume: string;
  };
  const closes = candles.map((candle) => candle.close);
  const ema20 = closes.length >= 20 ? calculateEma(closes, 20) : null;
  return {
    price: Number(data.lastPrice),
    changePct: Number(data.priceChangePercent),
    high24: Number(data.highPrice),
    low24: Number(data.lowPrice),
    volume: Number(data.volume),
    ema20,
    updatedAt: formatTime(Date.now()),
    source: "Binance",
  };
}

async function fetchOkxSnapshot(): Promise<MarketSnapshot> {
  const [tickerResponse, candles] = await Promise.all([
    fetch("https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT"),
    fetchOkxCandles("15m", 25),
  ]);
  if (!tickerResponse.ok) {
    throw new Error(`OKX ticker request failed: ${tickerResponse.status}`);
  }
  const payload = (await tickerResponse.json()) as {
    data?: Array<{
      last: string;
      open24h: string;
      high24h: string;
      low24h: string;
      vol24h: string;
      ts: string;
    }>;
  };
  const data = payload.data?.[0];
  if (!data) throw new Error("OKX ticker response missing data");
  const price = Number(data.last);
  const open24h = Number(data.open24h);
  const closes = candles.map((candle) => candle.close);
  return {
    price,
    changePct: open24h > 0 ? ((price - open24h) / open24h) * 100 : 0,
    high24: Number(data.high24h),
    low24: Number(data.low24h),
    volume: Number(data.vol24h),
    ema20: closes.length >= 20 ? calculateEma(closes, 20) : null,
    updatedAt: formatTime(Number(data.ts)),
    source: "OKX",
  };
}

async function fetchOkxCandles(interval = "15m", limit = 160): Promise<Candle[]> {
  const bar = normalizeOkxBar(interval);
  const response = await fetch(
    `https://www.okx.com/api/v5/market/candles?instId=ETH-USDT&bar=${bar}&limit=${limit}`,
  );
  if (!response.ok) {
    throw new Error(`OKX candles request failed: ${response.status}`);
  }
  const payload = (await response.json()) as {
    data?: string[][];
  };
  if (!payload.data) throw new Error("OKX candles response missing data");
  return payload.data
    .map((row) => ({
      time: Math.floor(Number(row[0]) / 1000) as UTCTimestamp,
      open: Number(row[1]),
      high: Number(row[2]),
      low: Number(row[3]),
      close: Number(row[4]),
    }))
    .reverse();
}

export function demoSnapshot(): MarketSnapshot {
  return {
    price: 2469.6,
    changePct: 2.18,
    high24: 2512.4,
    low24: 2398.1,
    volume: 384211.8,
    ema20: 2431.42,
    updatedAt: formatTime(Date.now()),
    source: "Demo",
  };
}

function calculateEma(values: number[], period: number) {
  const multiplier = 2 / (period + 1);
  return values.reduce((ema, value, index) => {
    if (index === 0) return value;
    return value * multiplier + ema * (1 - multiplier);
  }, values[0]);
}

function buildSignalAnalysis(
  m15: Candle[],
  h1: Candle[],
  h4: Candle[],
  source: SignalAnalysis["source"],
): SignalAnalysis {
  const m15Closes = m15.map((candle) => candle.close);
  const h1Closes = h1.map((candle) => candle.close);
  const h4Closes = h4.map((candle) => candle.close);
  const last = m15Closes[m15Closes.length - 1] ?? 0;
  const ema20 = calculateEma(m15Closes.slice(-40), 20);
  const h1Ema20 = calculateEma(h1Closes.slice(-50), 20);
  const h1Ema50 = calculateEma(h1Closes.slice(-80), 50);
  const h4Ema20 = calculateEma(h4Closes.slice(-50), 20);
  const h4Ema50 = calculateEma(h4Closes.slice(-80), 50);
  const distancePct = ema20 > 0 ? ((last - ema20) / ema20) * 100 : 0;
  const recent = m15.slice(-24);
  const recentHigh = Math.max(...recent.map((candle) => candle.high));
  const recentLow = Math.min(...recent.map((candle) => candle.low));
  const rangePct = last > 0 ? ((recentHigh - recentLow) / last) * 100 : 0;
  const trend4h = classifyTrend(h4Closes[h4Closes.length - 1] ?? 0, h4Ema20, h4Ema50);
  const filter1h = classifyTrend(h1Closes[h1Closes.length - 1] ?? 0, h1Ema20, h1Ema50);
  const pullbackReady = Math.abs(distancePct) <= 0.75;
  const volatilityNormal = rangePct <= 3.5;
  const structureScore =
    trend4h === "Bullish"
      ? last >= recentLow + (recentHigh - recentLow) * 0.42
      : last <= recentHigh - (recentHigh - recentLow) * 0.42;

  const score =
    (trend4h === "Mixed" ? 14 : 28) +
    (filter1h === trend4h ? 22 : filter1h === "Mixed" ? 12 : 4) +
    (structureScore ? 22 : 10) +
    (pullbackReady ? 18 : Math.abs(distancePct) <= 1.4 ? 10 : 3) +
    (volatilityNormal ? 10 : 4);
  const normalizedScore = Math.max(0, Math.min(100, Math.round(score)));
  const title =
    normalizedScore >= 70 && pullbackReady
      ? "Pullback setup armed"
      : normalizedScore >= 70
        ? "Trend setup active"
        : normalizedScore >= 50
          ? "Watchlist only"
          : "No trade state";
  const summary =
    source === "Live"
      ? `${trend4h} 4H trend, ${filter1h.toLowerCase()} 1H filter, ${formatSignedValue(distancePct)}% from EMA20.`
      : "Demo fallback is active because live market data is unavailable.";

  return {
    score: normalizedScore,
    title,
    summary,
    source,
    updatedAt: formatTime(Date.now()),
    conditions: [
      { label: "4H trend", value: trend4h, tone: trend4h === "Mixed" ? "watch" : "pass" },
      {
        label: "1H filter",
        value: filter1h === trend4h ? "Aligned" : filter1h,
        tone: filter1h === trend4h ? "pass" : "watch",
      },
      { label: "15m score", value: `${normalizedScore} / 100`, tone: normalizedScore >= 70 ? "pass" : "watch" },
      { label: "EMA20 distance", value: `${formatSignedValue(distancePct)}%`, tone: pullbackReady ? "pass" : "watch" },
      { label: "Risk mode", value: volatilityNormal ? "Normal" : "Elevated", tone: volatilityNormal ? "pass" : "watch" },
    ],
  };
}

function classifyTrend(price: number, ema20: number, ema50: number) {
  if (price > ema20 && ema20 > ema50) return "Bullish";
  if (price < ema20 && ema20 < ema50) return "Bearish";
  return "Mixed";
}

function normalizeOkxBar(interval: string) {
  if (interval === "1h") return "1H";
  if (interval === "4h") return "4H";
  return interval;
}

function formatSignedValue(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDateTime(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute}`;
}
