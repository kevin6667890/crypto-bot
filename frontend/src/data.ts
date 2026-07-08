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
  return [{"time": 1719221400, "value": 7637.46}, {"time": 1719430500, "value": 7624.71}, {"time": 1719585600, "value": 7612.06}, {"time": 1720057080, "value": 7751.57}, {"time": 1720359780, "value": 7893.66}, {"time": 1720667880, "value": 7801.6}, {"time": 1720702200, "value": 7944.66}, {"time": 1720967400, "value": 7931.49}, {"time": 1721024640, "value": 8076.93}, {"time": 1721071320, "value": 8225.01}, {"time": 1721107380, "value": 8211.33}, {"time": 1721313180, "value": 8197.7}, {"time": 1721545320, "value": 8184.05}, {"time": 1721584260, "value": 8087.88}, {"time": 1721630220, "value": 7993.55}, {"time": 1721855520, "value": 8140.06}, {"time": 1721869620, "value": 8289.21}, {"time": 1721885880, "value": 8275.31}, {"time": 1721940960, "value": 8261.43}, {"time": 1722113100, "value": 8247.63}, {"time": 1722225780, "value": 8398.91}, {"time": 1722262440, "value": 8300.95}, {"time": 1722289140, "value": 8204.07}, {"time": 1722351480, "value": 8108.42}, {"time": 1722372060, "value": 8094.87}, {"time": 1722454080, "value": 8000.47}, {"time": 1722486420, "value": 8147.08}, {"time": 1722530040, "value": 8296.42}, {"time": 1722542460, "value": 8199.61}, {"time": 1722647400, "value": 8349.83}, {"time": 1722738780, "value": 8252.38}, {"time": 1722791400, "value": 8403.62}, {"time": 1722819300, "value": 8557.56}, {"time": 1722994920, "value": 8457.67}, {"time": 1723037520, "value": 8443.5}, {"time": 1723045980, "value": 8429.23}, {"time": 1723064580, "value": 8415.14}, {"time": 1723374060, "value": 8401.13}, {"time": 1723576860, "value": 8303.19}, {"time": 1723639920, "value": 8206.4}, {"time": 1724629680, "value": 8108.24}, {"time": 1724767980, "value": 8256.83}, {"time": 1724794620, "value": 8408.09}, {"time": 1725140160, "value": 8384.67}, {"time": 1725183420, "value": 8370.63}, {"time": 1725205080, "value": 8356.62}, {"time": 1725374820, "value": 8509.77}, {"time": 1725411540, "value": 8665.72}, {"time": 1725586860, "value": 8651.23}, {"time": 1725654840, "value": 8809.83}, {"time": 1725833760, "value": 8707.02}, {"time": 1726017900, "value": 8692.64}, {"time": 1726682400, "value": 8678.13}, {"time": 1726729860, "value": 8837.31}, {"time": 1726892700, "value": 8734.26}, {"time": 1726962000, "value": 8892.71}, {"time": 1727061660, "value": 9055.8}, {"time": 1727126400, "value": 8950.2}, {"time": 1727178060, "value": 8845.81}, {"time": 1727240400, "value": 8742.67}, {"time": 1727425440, "value": 8902.99}, {"time": 1727513460, "value": 8888.19}, {"time": 1727789580, "value": 8784.53}, {"time": 1727795160, "value": 8945.54}, {"time": 1727806200, "value": 8841.1}, {"time": 1727885100, "value": 8737.9}, {"time": 1727946240, "value": 8898.07}, {"time": 1727996460, "value": 8794.18}, {"time": 1728219840, "value": 8691.54}, {"time": 1728292500, "value": 8590.14}, {"time": 1728685260, "value": 8575.9}, {"time": 1728916140, "value": 8733.16}, {"time": 1729085940, "value": 8718.69}, {"time": 1729282860, "value": 8704.2}, {"time": 1729433340, "value": 8858.3}, {"time": 1729493940, "value": 8754.97}, {"time": 1729696440, "value": 8915.44}, {"time": 1729702140, "value": 9078.82}, {"time": 1729713000, "value": 8972.91}, {"time": 1729845360, "value": 8868.2}, {"time": 1729966200, "value": 8764.67}, {"time": 1730035380, "value": 8654.69}, {"time": 1730099640, "value": 8553.7}, {"time": 1730220720, "value": 8710.58}, {"time": 1730289900, "value": 8696.05}, {"time": 1730383440, "value": 8854.96}, {"time": 1730426940, "value": 9017.29}, {"time": 1730601660, "value": 9180.81}, {"time": 1730608140, "value": 9165.43}, {"time": 1730666100, "value": 9058.42}, {"time": 1730755440, "value": 9224.44}, {"time": 1730859600, "value": 9393.79}, {"time": 1730927460, "value": 9566.16}, {"time": 1730939160, "value": 9550.22}, {"time": 1730959860, "value": 9438.88}, {"time": 1731024480, "value": 9611.98}, {"time": 1731100500, "value": 9596.0}, {"time": 1731131400, "value": 9771.99}, {"time": 1731240300, "value": 9951.21}, {"time": 1731351900, "value": 10133.88}, {"time": 1731701940, "value": 10015.52}, {"time": 1731737640, "value": 9998.89}, {"time": 1732329420, "value": 9982.24}, {"time": 1732378860, "value": 9965.66}, {"time": 1732527300, "value": 10148.46}, {"time": 1732534800, "value": 10131.59}, {"time": 1732704000, "value": 10317.35}, {"time": 1732722240, "value": 10506.59}, {"time": 1732745820, "value": 10699.22}, {"time": 1732762980, "value": 10574.47}, {"time": 1732897020, "value": 10556.91}, {"time": 1732961760, "value": 10539.39}, {"time": 1733082600, "value": 10521.84}, {"time": 1733111760, "value": 10399.11}, {"time": 1733298060, "value": 10589.8}, {"time": 1733314680, "value": 10572.21}, {"time": 1733412180, "value": 10448.89}, {"time": 1733527260, "value": 10326.97}, {"time": 1733645760, "value": 10201.98}, {"time": 1733708820, "value": 10083.01}, {"time": 1733778180, "value": 10267.74}, {"time": 1733849760, "value": 10147.78}, {"time": 1733854800, "value": 10029.37}, {"time": 1733948640, "value": 10213.27}, {"time": 1733973960, "value": 10400.54}, {"time": 1734021360, "value": 10383.3}, {"time": 1734105060, "value": 10262.2}, {"time": 1734574260, "value": 10450.27}, {"time": 1734576960, "value": 10328.17}, {"time": 1734691200, "value": 10310.5}, {"time": 1734697080, "value": 10190.13}, {"time": 1734787920, "value": 10376.88}, {"time": 1735201440, "value": 10567.05}, {"time": 1735919100, "value": 10760.82}, {"time": 1735996440, "value": 10958.13}, {"time": 1736157960, "value": 10830.34}, {"time": 1736295960, "value": 10703.94}, {"time": 1736324340, "value": 10579.02}, {"time": 1736431080, "value": 10772.88}, {"time": 1736594280, "value": 10647.15}, {"time": 1736695140, "value": 10522.87}, {"time": 1736760540, "value": 10715.75}, {"time": 1736761080, "value": 10697.84}, {"time": 1736774280, "value": 10679.94}, {"time": 1736781720, "value": 10555.25}, {"time": 1736947800, "value": 10432.07}, {"time": 1736970720, "value": 10623.31}, {"time": 1738078260, "value": 10499.31}, {"time": 1738100220, "value": 10691.68}, {"time": 1738321620, "value": 10887.73}, {"time": 1738537440, "value": 11087.06}, {"time": 1738547220, "value": 11290.48}, {"time": 1738709280, "value": 11271.47}, {"time": 1738784400, "value": 11139.89}, {"time": 1738856220, "value": 11344.06}, {"time": 1738960140, "value": 11551.96}, {"time": 1738971720, "value": 11532.51}, {"time": 1739137380, "value": 11743.88}, {"time": 1739286120, "value": 11724.25}, {"time": 1739310240, "value": 11704.64}, {"time": 1739398800, "value": 11919.31}, {"time": 1739555280, "value": 12137.9}, {"time": 1740371400, "value": 12360.39}, {"time": 1740417600, "value": 12339.74}, {"time": 1740443040, "value": 12195.55}, {"time": 1740578400, "value": 12419.05}, {"time": 1740594540, "value": 12646.61}, {"time": 1740691020, "value": 12625.45}, {"time": 1740717720, "value": 12604.27}, {"time": 1740834960, "value": 12835.29}, {"time": 1740847080, "value": 12685.45}, {"time": 1741026720, "value": 12918.04}, {"time": 1741517400, "value": 13154.8}, {"time": 1741536660, "value": 13132.81}, {"time": 1741856400, "value": 12979.55}, {"time": 1741890420, "value": 12957.84}, {"time": 1742123820, "value": 13190.12}, {"time": 1743037320, "value": 13036.2}, {"time": 1743136800, "value": 13275.18}, {"time": 1743207240, "value": 13252.99}, {"time": 1743266760, "value": 13495.84}, {"time": 1743269460, "value": 13473.26}, {"time": 1743601920, "value": 13316.03}, {"time": 1743687060, "value": 13293.68}, {"time": 1743885780, "value": 13271.48}, {"time": 1743947580, "value": 13514.71}, {"time": 1743966960, "value": 13492.01}, {"time": 1743977040, "value": 13469.54}, {"time": 1744132320, "value": 13716.33}, {"time": 1744438260, "value": 13556.28}, {"time": 1745377800, "value": 13533.78}, {"time": 1745392080, "value": 13376.06}, {"time": 1745547900, "value": 13220.09}, {"time": 1745717460, "value": 13198.15}, {"time": 1745844960, "value": 13176.17}, {"time": 1745932560, "value": 13022.5}, {"time": 1746016320, "value": 12870.6}, {"time": 1746036420, "value": 12720.21}, {"time": 1746095400, "value": 12953.47}, {"time": 1746714840, "value": 13190.96}, {"time": 1746718080, "value": 13432.83}, {"time": 1746718920, "value": 13679.21}, {"time": 1746897060, "value": 13930.12}, {"time": 1746899280, "value": 13907.03}, {"time": 1747138260, "value": 13883.88}, {"time": 1747153680, "value": 14138.49}, {"time": 1747383420, "value": 14115.0}, {"time": 1747398300, "value": 13950.36}, {"time": 1747579680, "value": 14206.22}, {"time": 1747589220, "value": 14182.66}, {"time": 1747673760, "value": 14442.76}, {"time": 1748276520, "value": 14418.79}, {"time": 1748753160, "value": 14394.69}, {"time": 1748793900, "value": 14226.66}, {"time": 1748920320, "value": 14203.05}, {"time": 1748967660, "value": 14176.85}, {"time": 1749042300, "value": 14011.5}, {"time": 1749303960, "value": 13847.96}, {"time": 1749664740, "value": 13825.01}, {"time": 1749962760, "value": 13801.92}, {"time": 1749996540, "value": 13640.87}, {"time": 1750113360, "value": 13618.19}, {"time": 1750173960, "value": 13867.77}, {"time": 1750186860, "value": 13705.88}, {"time": 1750255440, "value": 13545.9}, {"time": 1750354560, "value": 13523.24}, {"time": 1750440180, "value": 13771.17}, {"time": 1750541040, "value": 14023.57}, {"time": 1750549800, "value": 13859.77}, {"time": 1751058420, "value": 13698.01}, {"time": 1751231040, "value": 13673.24}, {"time": 1751546280, "value": 13513.82}, {"time": 1751684700, "value": 13356.07}, {"time": 1751986500, "value": 13333.87}, {"time": 1752093780, "value": 13311.73}, {"time": 1752182580, "value": 13555.85}, {"time": 1752323820, "value": 13397.72}, {"time": 1752416340, "value": 13643.38}, {"time": 1752442680, "value": 13484.3}, {"time": 1752508080, "value": 13461.86}, {"time": 1752620880, "value": 13708.72}, {"time": 1752676380, "value": 13960.12}, {"time": 1752689160, "value": 14216.08}, {"time": 1752701880, "value": 14050.21}, {"time": 1752759780, "value": 13886.41}, {"time": 1752801120, "value": 14141.13}, {"time": 1752824400, "value": 14117.6}, {"time": 1752982200, "value": 14376.53}, {"time": 1753025640, "value": 14640.17}, {"time": 1753031400, "value": 14615.78}, {"time": 1753114500, "value": 14591.48}, {"time": 1753542960, "value": 14421.33}, {"time": 1753680060, "value": 14685.83}, {"time": 1753786620, "value": 14661.44}, {"time": 1754010120, "value": 14636.81}, {"time": 1754051460, "value": 14465.97}, {"time": 1754081880, "value": 14441.81}, {"time": 1754337600, "value": 14417.75}, {"time": 1754562600, "value": 14682.14}, {"time": 1754577120, "value": 14510.85}, {"time": 1754633400, "value": 14486.81}, {"time": 1754715900, "value": 14752.5}, {"time": 1754747340, "value": 14580.4}, {"time": 1754779620, "value": 14556.15}, {"time": 1754899440, "value": 14531.99}, {"time": 1755002520, "value": 14798.44}, {"time": 1755029760, "value": 15069.75}, {"time": 1755093360, "value": 15044.73}, {"time": 1755131700, "value": 15019.75}, {"time": 1755261000, "value": 14994.8}, {"time": 1755428100, "value": 15269.79}, {"time": 1755449520, "value": 15091.67}, {"time": 1755485400, "value": 15368.22}, {"time": 1755510360, "value": 15342.58}, {"time": 1755593520, "value": 15316.96}, {"time": 1755631620, "value": 15291.44}, {"time": 1755651960, "value": 15265.88}, {"time": 1755700200, "value": 15240.25}, {"time": 1755713160, "value": 15215.01}, {"time": 1755785820, "value": 15037.4}, {"time": 1755828720, "value": 14861.89}, {"time": 1755883440, "value": 15134.56}, {"time": 1756031400, "value": 14958.06}, {"time": 1756057200, "value": 15232.34}, {"time": 1756064040, "value": 15054.74}, {"time": 1756172760, "value": 14879.01}, {"time": 1756255560, "value": 14854.32}, {"time": 1757593800, "value": 14829.6}, {"time": 1757656560, "value": 15101.53}, {"time": 1757710080, "value": 15378.42}, {"time": 1757771460, "value": 15352.82}, {"time": 1757854260, "value": 15173.54}, {"time": 1757921820, "value": 14996.54}, {"time": 1758060540, "value": 14971.47}, {"time": 1758175800, "value": 14946.63}, {"time": 1758501120, "value": 15209.71}, {"time": 1758520800, "value": 15488.6}, {"time": 1758692460, "value": 15462.66}, {"time": 1758770520, "value": 15746.04}, {"time": 1758820500, "value": 16034.4}, {"time": 1759075620, "value": 15844.5}, {"time": 1759103940, "value": 16135.04}, {"time": 1759265160, "value": 15946.7}, {"time": 1759387320, "value": 16239.17}, {"time": 1759463700, "value": 16212.21}, {"time": 1759510800, "value": 16023.33}, {"time": 1759660500, "value": 15996.8}, {"time": 1759759560, "value": 16290.19}, {"time": 1759800060, "value": 16263.16}, {"time": 1759844700, "value": 16073.59}, {"time": 1759986180, "value": 16046.72}, {"time": 1760031660, "value": 16019.83}, {"time": 1760130720, "value": 16313.28}, {"time": 1760217840, "value": 16285.87}, {"time": 1760408280, "value": 16095.92}, {"time": 1760423580, "value": 16391.0}, {"time": 1760543160, "value": 16691.4}, {"time": 1760552520, "value": 16496.55}, {"time": 1760656860, "value": 16304.08}, {"time": 1760696580, "value": 16602.88}, {"time": 1760948520, "value": 16575.33}, {"time": 1761049320, "value": 16547.75}, {"time": 1761065940, "value": 16354.76}, {"time": 1761135480, "value": 16327.41}, {"time": 1761233580, "value": 16136.78}, {"time": 1761309000, "value": 16432.65}, {"time": 1761516180, "value": 16733.99}, {"time": 1761553680, "value": 16538.8}, {"time": 1761662700, "value": 16345.93}, {"time": 1761755640, "value": 16318.67}, {"time": 1761853140, "value": 16617.71}, {"time": 1761857580, "value": 16423.77}, {"time": 1762126920, "value": 16232.06}, {"time": 1762178820, "value": 16042.51}, {"time": 1762255080, "value": 15855.32}, {"time": 1762281180, "value": 16145.94}, {"time": 1762293300, "value": 15957.18}, {"time": 1762444320, "value": 16249.58}, {"time": 1762526640, "value": 16222.42}, {"time": 1762670460, "value": 16033.06}, {"time": 1762826280, "value": 16006.4}, {"time": 1762897860, "value": 16299.74}, {"time": 1763128260, "value": 16272.25}, {"time": 1763261280, "value": 16082.31}, {"time": 1763310240, "value": 16377.09}, {"time": 1763408100, "value": 16349.73}, {"time": 1763411100, "value": 16322.41}, {"time": 1763531220, "value": 16621.6}, {"time": 1763584560, "value": 16427.62}, {"time": 1763654640, "value": 16728.44}, {"time": 1763820960, "value": 16533.19}, {"time": 1764167460, "value": 16340.18}, {"time": 1764180420, "value": 16639.86}, {"time": 1764247920, "value": 16445.84}, {"time": 1764283500, "value": 16253.99}, {"time": 1764770640, "value": 16064.47}, {"time": 1764798960, "value": 16359.34}, {"time": 1764810780, "value": 16659.35}, {"time": 1764853380, "value": 16465.23}, {"time": 1765399860, "value": 16273.44}, {"time": 1765553280, "value": 16569.95}, {"time": 1765712700, "value": 16865.02}, {"time": 1765809960, "value": 16668.28}, {"time": 1765823700, "value": 16973.81}, {"time": 1766050500, "value": 16775.73}, {"time": 1766095500, "value": 16579.9}, {"time": 1766320920, "value": 16538.42}, {"time": 1766443980, "value": 16510.73}, {"time": 1766508780, "value": 16483.1}, {"time": 1767000780, "value": 16290.89}, {"time": 1767348420, "value": 16583.58}, {"time": 1767376200, "value": 16556.07}, {"time": 1767488100, "value": 16846.48}, {"time": 1767577020, "value": 17150.43}, {"time": 1767717780, "value": 17121.85}, {"time": 1767890880, "value": 16921.9}, {"time": 1768110360, "value": 16668.24}, {"time": 1768414620, "value": 16640.65}, {"time": 1768889820, "value": 16937.9}, {"time": 1768920540, "value": 17248.4}, {"time": 1768937700, "value": 17564.54}, {"time": 1769004360, "value": 17535.16}, {"time": 1769095440, "value": 17856.55}, {"time": 1769181660, "value": 17826.78}, {"time": 1769330460, "value": 18120.45}, {"time": 1769358000, "value": 18447.0}, {"time": 1769370780, "value": 18785.19}, {"time": 1769389440, "value": 18566.0}, {"time": 1769523300, "value": 18349.39}, {"time": 1769653860, "value": 18135.4}, {"time": 1769698800, "value": 18467.77}, {"time": 1769848800, "value": 18806.22}, {"time": 1769879460, "value": 19150.96}, {"time": 1769886360, "value": 18927.33}, {"time": 1769963100, "value": 18895.45}, {"time": 1770142680, "value": 19241.63}, {"time": 1770229620, "value": 19209.33}, {"time": 1770649260, "value": 18985.17}, {"time": 1770734520, "value": 19333.17}, {"time": 1770811620, "value": 19300.82}, {"time": 1771118880, "value": 19075.75}, {"time": 1771177500, "value": 19425.34}, {"time": 1771263060, "value": 19198.55}, {"time": 1771808580, "value": 19550.43}, {"time": 1771895700, "value": 19908.69}, {"time": 1772041320, "value": 20273.75}, {"time": 1772208360, "value": 20645.43}, {"time": 1772231640, "value": 21023.87}, {"time": 1772313540, "value": 20988.96}, {"time": 1772397300, "value": 21373.56}, {"time": 1772806200, "value": 21765.23}, {"time": 1772824380, "value": 21511.28}, {"time": 1773407940, "value": 21905.79}, {"time": 1773551820, "value": 22284.17}, {"time": 1773613920, "value": 22692.77}, {"time": 1773646680, "value": 22428.03}, {"time": 1773702240, "value": 22390.75}, {"time": 1773947160, "value": 22129.36}, {"time": 1773994140, "value": 21871.06}, {"time": 1774214400, "value": 22271.85}, {"time": 1774359600, "value": 22012.12}, {"time": 1774383960, "value": 21755.18}, {"time": 1774451340, "value": 21718.97}, {"time": 1774607880, "value": 22117.0}, {"time": 1774920180, "value": 21858.85}, {"time": 1775030220, "value": 21822.55}, {"time": 1775140500, "value": 21786.12}, {"time": 1775480160, "value": 21749.87}, {"time": 1775583360, "value": 21495.99}, {"time": 1775602680, "value": 21890.25}, {"time": 1775741880, "value": 21626.63}, {"time": 1775836620, "value": 21590.64}, {"time": 1776108840, "value": 21986.75}, {"time": 1776326040, "value": 21950.28}, {"time": 1776431880, "value": 22352.96}, {"time": 1776472440, "value": 22092.27}, {"time": 1776648660, "value": 22055.36}, {"time": 1778074620, "value": 22018.72}, {"time": 1778137920, "value": 21979.99}, {"time": 1778261460, "value": 21723.49}, {"time": 1778648160, "value": 21469.9}, {"time": 1778738820, "value": 21219.25}, {"time": 1778852340, "value": 21608.17}, {"time": 1779061260, "value": 22004.13}] as { time: UTCTimestamp, value: number }[];
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
