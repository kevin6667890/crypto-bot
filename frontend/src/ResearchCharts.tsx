import { BacktestTrade, EquityPoint } from "./research";
import {useLanguage} from "./i18n";

function pathFor(values: number[], width = 1000, height = 240) {
  if (!values.length) return "";
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  return values.map((value, index) => `${index ? "L" : "M"}${(index / Math.max(values.length - 1, 1)) * width},${height - ((value - min) / range) * (height - 16) - 8}`).join(" ");
}

export function LineResearchChart({ points, field, color, label }: { points: Array<Record<string, number>>; field: string; color: string; label: string }) {
  const {t}=useLanguage();
  const values = points.map((point) => point[field]);
  return <div className="research-chart"><span>{label}</span>{values.length ? <svg viewBox="0 0 1000 240" preserveAspectRatio="none" role="img" aria-label={label}><path className="grid-line" d="M0 60H1000 M0 120H1000 M0 180H1000" /><path d={pathFor(values)} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" /></svg> : <div className="research-empty compact">{t("research.noSeries")}</div>}</div>;
}

export function BacktestCandleChart({ candles, trades }: { candles: Array<{ ts: number; open: number; high: number; low: number; close: number }>; trades: BacktestTrade[] }) {
  const {t}=useLanguage();
  const shown = candles.slice(-220);
  if (!shown.length) return <div className="research-empty">{t("research.loadCandles")}</div>;
  const low = Math.min(...shown.map((row) => row.low)), high = Math.max(...shown.map((row) => row.high)), range = high - low || 1;
  const y = (price: number) => 230 - ((price - low) / range) * 210;
  const start = shown[0].ts, end = shown[shown.length - 1].ts;
  const markers = trades.filter((trade) => trade.entry_ts >= start && trade.entry_ts <= end);
  return <div className="research-chart candle"><span>{t("research.candleMarkers")} · {shown.length}</span><svg viewBox="0 0 1000 250" preserveAspectRatio="none" role="img" aria-label={t("research.candleMarkers")}>
    {shown.map((row, index) => { const x = (index + .5) / shown.length * 1000; const up = row.close >= row.open; return <g key={row.ts}><line x1={x} x2={x} y1={y(row.high)} y2={y(row.low)} stroke={up ? "#039855" : "#d92d20"} vectorEffect="non-scaling-stroke" /><rect x={x - Math.max(1, 350 / shown.length)} width={Math.max(2, 700 / shown.length)} y={Math.min(y(row.open), y(row.close))} height={Math.max(1, Math.abs(y(row.open) - y(row.close)))} fill={up ? "#039855" : "#d92d20"} /></g>; })}
    {markers.map((trade) => { const x = ((trade.entry_ts - start) / Math.max(end - start, 1)) * 1000; return <g key={trade.trade_id}><circle cx={x} cy={trade.side === "LONG" ? 236 : 14} r="6" fill={trade.side === "LONG" ? "#039855" : "#d92d20"} vectorEffect="non-scaling-stroke" /><text x={x + 8} y={trade.side === "LONG" ? 238 : 18} fontSize="12" fill="#475467">{trade.side === "LONG" ? "L" : "S"}</text></g>; })}
  </svg></div>;
}

export function DistributionCharts({ trades, monthly }: { trades: BacktestTrade[]; monthly: Array<{ month: string; return: number }> }) {
  const {t,value}=useLanguage();
  const buckets = [-2, -1, 0, 1, 2, 3];
  const counts = buckets.map((floor, index) => trades.filter((trade) => trade.result_r >= floor && (index === buckets.length - 1 || trade.result_r < buckets[index + 1])).length);
  const maxCount = Math.max(...counts, 1), maxMonth = Math.max(...monthly.map((item) => Math.abs(item.return)), 1);
  const side = (["LONG", "SHORT"] as const).map((name) => { const rows = trades.filter((trade) => trade.side === name); return { name, trades: rows.length, pnl: rows.reduce((sum, row) => sum + row.pnl, 0), wins: rows.filter((row) => row.pnl > 0).length }; });
  return <div className="distribution-grid">
    <div className="mini-chart"><strong>{t("research.tradeDistribution")}</strong><div className="bar-set">{counts.map((count, index) => <div key={buckets[index]}><i style={{ height: `${count / maxCount * 100}%` }} /><small>{buckets[index]}R+</small></div>)}</div></div>
    <div className="mini-chart"><strong>{t("research.monthlyReturns")}</strong><div className="month-set">{monthly.slice(-18).map((item) => <div key={item.month} title={`${item.month}: ${item.return.toFixed(2)}%`}><i className={item.return >= 0 ? "up" : "down"} style={{ height: `${Math.abs(item.return) / maxMonth * 46}%` }} /><small>{item.month.slice(2)}</small></div>)}</div></div>
    <div className="mini-chart"><strong>{t("research.sideResults")}</strong>{side.map((item) => <div className="side-result" key={item.name}><b>{value(item.name)}</b><span>{item.trades}</span><span>{item.trades ? (item.wins / item.trades * 100).toFixed(1) : "--"}%</span><strong className={item.pnl >= 0 ? "positive" : "negative"}>{item.pnl.toFixed(2)}</strong></div>)}</div>
  </div>;
}
