import { useEffect, useRef, useState } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi, ISeriesApi, LineSeries, UTCTimestamp } from "lightweight-charts";
import { Candle, fetchEthCandles, generateEquityCurve } from "./data";
import { useLanguage } from "./i18n";
import { ChartCacheKey, formatMillions, loadChartSnapshot, normalizePoints, saveChartSnapshot } from "./chartState";

const chartTheme = {
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#6b7280", fontFamily: "Inter, ui-sans-serif, system-ui" },
  grid: { vertLines: { color: "rgba(17, 24, 39, 0.06)" }, horzLines: { color: "rgba(17, 24, 39, 0.06)" } },
  rightPriceScale: { borderColor: "rgba(17, 24, 39, 0.1)" },
  timeScale: { borderColor: "rgba(17, 24, 39, 0.1)", timeVisible: true, fixLeftEdge: true, fixRightEdge: true },
  crosshair: { vertLine: { color: "rgba(0, 179, 126, 0.28)" }, horzLine: { color: "rgba(0, 179, 126, 0.28)" } },
};

type ChartFactory = (container: HTMLDivElement) => IChartApi;

/**
 * Creates a chart once for the lifetime of its DOM node.  In particular, data
 * changes, visibility changes and ResizeObserver callbacks only mutate that
 * instance; they never run this effect's cleanup.
 */
function useResponsiveChart(factory: ChartFactory, onRecover?: () => void) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const factoryRef = useRef(factory);
  const recoverRef = useRef(onRecover);
  factoryRef.current = factory;
  recoverRef.current = onRecover;

  useEffect(() => {
    let frame = 0;
    let recoveryQueued = false;
    let disposed = false;
    let lastWidth = 0;
    let lastHeight = 0;

    const ensureChart = () => {
      const node = containerRef.current;
      if (node && !chartRef.current) chartRef.current = factoryRef.current(node);
      return chartRef.current;
    };
    const resize = () => {
      const bounds = containerRef.current?.getBoundingClientRect();
      if (!bounds || bounds.width < 20 || bounds.height < 20) return false;
      const chart = ensureChart();
      if (!chart) return false;
      const width = Math.floor(bounds.width);
      const height = Math.floor(bounds.height);
      if (width !== lastWidth || height !== lastHeight) {
        lastWidth = width;
        lastHeight = height;
        chart.resize(width, height, true);
      }
      return true;
    };
    const queueResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { if (!disposed) resize(); });
    };
    const recover = () => {
      if (document.hidden || recoveryQueued) return;
      recoveryQueued = true;
      const priorRange = chartRef.current?.timeScale().getVisibleLogicalRange() ?? null;
      let attempts = 0;
      const reflow = () => {
        if (disposed) return;
        if (!resize() && ++attempts < 8) { frame = requestAnimationFrame(reflow); return; }
        if (resize()) {
          // Firefox can retain a mounted canvas without repainting it after a
          // background-tab transition. Reapplying non-empty in-memory data is
          // safe and forces that repaint without ever clearing a series.
          recoverRef.current?.();
          if (priorRange) chartRef.current?.timeScale().setVisibleLogicalRange(priorRange);
        }
        recoveryQueued = false;
      };
      frame = requestAnimationFrame(reflow);
    };
    const onVisibility = () => { if (!document.hidden) recover(); };
    const observer = new ResizeObserver(queueResize);
    if (containerRef.current) observer.observe(containerRef.current);
    window.addEventListener("resize", queueResize);
    window.visualViewport?.addEventListener("resize", queueResize);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", recover);
    queueResize();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", queueResize);
      window.visualViewport?.removeEventListener("resize", queueResize);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", recover);
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, []);
  return { containerRef, chartRef };
}

type FlowPaneData = { cvd_series: Array<{ time: number; value: number }>; oi_series: Array<{ time: number; value: number }> };
const isCandle = (point: unknown): point is Candle => {
  const row = point as Candle;
  return !!row && [row.time, row.open, row.high, row.low, row.close, row.volume].every(Number.isFinite);
};
const isFlowPoint = (point: unknown): point is { time: number; value: number } => {
  const row = point as { time?: number; value?: number };
  return !!row && Number.isFinite(row.time) && Number.isFinite(row.value);
};

function useLastKnownGood<T extends { time: number }>(key: ChartCacheKey, incoming: unknown, valid: (point: unknown) => point is T) {
  const serializedKey = `${key.series}:${key.instrument}:${key.timeframe}`;
  const [points, setPoints] = useState<T[]>(() => loadChartSnapshot(key, valid));
  const activeKey = useRef(serializedKey);
  const changedKey = activeKey.current !== serializedKey;
  if (changedKey) activeKey.current = serializedKey;
  useEffect(() => { setPoints(loadChartSnapshot(key, valid)); }, [serializedKey, valid]);
  useEffect(() => {
    const normalized = normalizePoints(incoming, valid);
    if (!normalized.length) return;
    saveChartSnapshot(key, normalized, valid);
    setPoints(normalized);
  }, [incoming, serializedKey, valid]);
  return changedKey ? loadChartSnapshot(key, valid) : points;
}

function alignFlowToCandles(candles: Candle[], points: Array<{ time: number; value: number }>, interval: string) {
  const seconds = interval === "1m" ? 60 : interval === "5m" ? 300 : interval === "15m" ? 900 : interval === "1h" ? 3600 : interval === "4h" ? 14400 : 86400;
  const ordered = [...points].sort((a, b) => a.time - b.time);
  const aligned: Array<{ time: UTCTimestamp; value: number }> = [];
  let cursor = 0, latest: number | undefined;
  for (const candle of candles) {
    const closeTime = Number(candle.time) + seconds;
    while (cursor < ordered.length && ordered[cursor].time <= closeTime) latest = ordered[cursor++].value;
    if (latest !== undefined) aligned.push({ time: candle.time, value: latest });
  }
  return aligned;
}

type MarketSeries = { candles: ISeriesApi<"Candlestick">; ma60: ISeriesApi<"Line">; ma200: ISeriesApi<"Line">; cvd: ISeriesApi<"Area">; oi: ISeriesApi<"Area"> };

export function MarketChart({ instrument = "ETH-USDT", interval = "15m", flow }: { instrument?: string; interval?: string; flow?: FlowPaneData }) {
  const candleKey = { instrument, timeframe: interval, series: "candles" as const };
  const [receivedCandles, setReceivedCandles] = useState<Candle[]>([]);
  const candles = useLastKnownGood(candleKey, receivedCandles, isCandle);
  const cvd = useLastKnownGood({ instrument, timeframe: interval, series: "cvd" }, flow?.cvd_series, isFlowPoint);
  const oi = useLastKnownGood({ instrument, timeframe: interval, series: "oi" }, flow?.oi_series, isFlowPoint);
  const requestId = useRef(0);
  const loadRef = useRef<() => void>(() => undefined);
  const seriesRef = useRef<MarketSeries | null>(null);
  const dataRef = useRef({ candles, cvd, oi, interval });
  dataRef.current = { candles, cvd, oi, interval };

  const applyData = () => {
    const series = seriesRef.current;
    const data = dataRef.current;
    if (!series || !data.candles.length) return;
    const visible = data.candles.slice(-260);
    series.candles.setData(visible);
    const average = (period: number) => data.candles.slice(period - 1).map((candle, index) => ({ time: candle.time, value: data.candles.slice(index, index + period).reduce((sum, item) => sum + item.close, 0) / period })).slice(-260);
    const ma60 = average(60), ma200 = average(200);
    if (ma60.length) series.ma60.setData(ma60);
    if (ma200.length) series.ma200.setData(ma200);
    const alignedCvd = alignFlowToCandles(visible, data.cvd, data.interval);
    const alignedOi = alignFlowToCandles(visible, data.oi, data.interval);
    if (alignedCvd.length) series.cvd.setData(alignedCvd);
    if (alignedOi.length) series.oi.setData(alignedOi);
  };
  const { containerRef } = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight });
    seriesRef.current = {
      candles: chart.addSeries(CandlestickSeries, { upColor: "#00b37e", downColor: "#f6465d", borderUpColor: "#00b37e", borderDownColor: "#f6465d", wickUpColor: "#00b37e", wickDownColor: "#f6465d" }),
      ma60: chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 2, priceLineVisible: false }),
      ma200: chart.addSeries(LineSeries, { color: "#7c3aed", lineWidth: 2, priceLineVisible: false }),
      cvd: chart.addSeries(AreaSeries, { lineColor: "#7c3aed", topColor: "rgba(124,58,237,.22)", bottomColor: "rgba(124,58,237,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } }, 1),
      oi: chart.addSeries(AreaSeries, { lineColor: "#0ea5e9", topColor: "rgba(14,165,233,.20)", bottomColor: "rgba(14,165,233,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } }, 2),
    };
    seriesRef.current.cvd.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0.00M" });
    applyData();
    const initial = dataRef.current.candles.slice(-260);
    if (initial.length > 1) chart.timeScale().setVisibleRange({ from: initial[0].time, to: initial[initial.length - 1].time });
    chart.panes()[0]?.setStretchFactor(3); chart.panes()[1]?.setStretchFactor(1); chart.panes()[2]?.setStretchFactor(1);
    return chart;
  }, () => { applyData(); loadRef.current(); });
  useEffect(() => { applyData(); }, [candles, cvd, oi, interval]);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const request = ++requestId.current;
      try {
        const live = await fetchEthCandles(interval, 500, instrument, controller.signal);
        if (request === requestId.current && normalizePoints(live, isCandle).length) setReceivedCandles(live);
      } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) { /* retain LKG */ } }
    };
    loadRef.current = () => { void load(); };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => { controller.abort(); window.clearInterval(timer); loadRef.current = () => undefined; };
  }, [instrument, interval]);
  return <div className="chart-canvas" ref={containerRef} />;
}

export function ReplayChart({ candles }: { candles: Candle[] }) {
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const candlesRef = useRef(candles); candlesRef.current = candles;
  const apply = () => { if (candlesRef.current.length) seriesRef.current?.setData(candlesRef.current); };
  const { containerRef } = useResponsiveChart((container) => { const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight }); seriesRef.current = chart.addSeries(CandlestickSeries, { upColor: "#00b37e", downColor: "#f6465d", borderVisible: false, wickUpColor: "#00b37e", wickDownColor: "#f6465d" }); apply(); chart.timeScale().fitContent(); return chart; }, apply);
  useEffect(apply, [candles]);
  return <div className="replay-canvas" ref={containerRef} />;
}

export function EquityChart() {
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const data = useRef(generateEquityCurve());
  const apply = () => { if (data.current.length) seriesRef.current?.setData(data.current); };
  const { containerRef } = useResponsiveChart((container) => { const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight }); seriesRef.current = chart.addSeries(AreaSeries, { lineColor: "#00b37e", topColor: "rgba(0, 179, 126, 0.2)", bottomColor: "rgba(0, 179, 126, 0.02)", lineWidth: 2, priceLineVisible: false }); apply(); chart.timeScale().fitContent(); return chart; }, apply);
  return <div className="chart-canvas" ref={containerRef} />;
}

export function FlowChart({ points, color = "#7c3aed", zeroLine = false, instrument = "ETH-USDT", interval = "15m", seriesType = "cvd" }: { points: Array<{ time: number; value: number }>; color?: string; zeroLine?: boolean; instrument?: string; interval?: string; seriesType?: "cvd" | "oi" }) {
  const { t } = useLanguage();
  const retained = useLastKnownGood({ instrument, timeframe: interval, series: seriesType }, points, isFlowPoint);
  const normalized = retained.length === 1 ? [{ time: retained[0].time - 1, value: retained[0].value }, retained[0]] : retained;
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const dataRef = useRef(normalized); dataRef.current = normalized;
  const apply = () => { if (dataRef.current.length) seriesRef.current?.setData(dataRef.current.map((point) => ({ time: point.time as UTCTimestamp, value: point.value }))); };
  const { containerRef } = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight, rightPriceScale: { visible: true, borderVisible: false, scaleMargins: { top: .15, bottom: .15 } }, timeScale: { visible: true, borderVisible: false, timeVisible: true, secondsVisible: true, fixLeftEdge: true, fixRightEdge: true } });
    seriesRef.current = chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}38`, bottomColor: `${color}05`, lineWidth: 2, priceLineVisible: true, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } });
    if (zeroLine) seriesRef.current.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0" });
    apply(); if (dataRef.current.length) chart.timeScale().fitContent(); return chart;
  }, apply);
  useEffect(apply, [normalized]);
  return <div className="flow-canvas"><div className="flow-canvas-inner" ref={containerRef} />{!normalized.length && <span className="flow-empty">{t("research.noSeries")}</span>}</div>;
}
