import { DependencyList, useEffect, useRef, useState } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi, LineSeries, UTCTimestamp } from "lightweight-charts";
import { Candle, fetchEthCandles, generateCandles, generateEquityCurve } from "./data";
import {useLanguage} from "./i18n";
import { ChartCacheKey, formatMillions, loadChartSnapshot, normalizePoints, saveChartSnapshot } from "./chartState";

const chartTheme = {
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: "#6b7280",
    fontFamily: "Inter, ui-sans-serif, system-ui",
  },
  grid: {
    vertLines: { color: "rgba(17, 24, 39, 0.06)" },
    horzLines: { color: "rgba(17, 24, 39, 0.06)" },
  },
  rightPriceScale: { borderColor: "rgba(17, 24, 39, 0.1)" },
  timeScale: { borderColor: "rgba(17, 24, 39, 0.1)", timeVisible: true, fixLeftEdge: true, fixRightEdge: true },
  crosshair: {
    vertLine: { color: "rgba(0, 179, 126, 0.28)" },
    horzLine: { color: "rgba(0, 179, 126, 0.28)" },
  },
};

function useResponsiveChart(factory: (container: HTMLDivElement) => IChartApi, deps: DependencyList = []) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    let chart: IChartApi | null = null;
    let lastWidth = 0;
    let lastHeight = 0;
    let delayedResize: number | undefined;
    const resizeChart = () => {
      const bounds = ref.current?.getBoundingClientRect();
      if (!bounds || bounds.width < 20 || bounds.height < 20) return;
      if (!chart) {
        try { chart = factory(ref.current!); } catch { return; }
      }
      const width = Math.floor(bounds.width);
      const height = Math.floor(bounds.height);
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      chart?.resize(width, height, true);
    };
    const queueResize = () => {
      requestAnimationFrame(resizeChart);
      window.clearTimeout(delayedResize);
      delayedResize = window.setTimeout(resizeChart, 160);
    };
    // A hidden tab can report a zero-sized container.  Defer construction until
    // ResizeObserver reports a usable box instead of abandoning this chart.
    const resize = new ResizeObserver(queueResize);
    resize.observe(ref.current);
    window.addEventListener("resize", queueResize);
    window.visualViewport?.addEventListener("resize", queueResize);
    document.addEventListener("visibilitychange", queueResize);
    window.addEventListener("focus", queueResize);
    queueResize();

    return () => {
      resize.disconnect();
      window.clearTimeout(delayedResize);
      window.removeEventListener("resize", queueResize);
      window.visualViewport?.removeEventListener("resize", queueResize);
      document.removeEventListener("visibilitychange", queueResize);
      window.removeEventListener("focus", queueResize);
      chart?.remove();
    };
  }, deps);

  return ref;
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
  // Do not render the outgoing instrument/timeframe during the first render
  // after a selection change; hydrate that exact key synchronously instead.
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

export function MarketChart({ instrument = "ETH-USDT", interval = "15m", flow }: { instrument?: string; interval?: string; flow?: FlowPaneData }) {
  const candleKey = { instrument, timeframe: interval, series: "candles" as const };
  const [receivedCandles, setReceivedCandles] = useState<Candle[]>([]);
  const candles = useLastKnownGood(candleKey, receivedCandles, isCandle);
  const cvd = useLastKnownGood({ instrument, timeframe: interval, series: "cvd" }, flow?.cvd_series, isFlowPoint);
  const oi = useLastKnownGood({ instrument, timeframe: interval, series: "oi" }, flow?.oi_series, isFlowPoint);
  const requestId = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const request = ++requestId.current;

    async function loadCandles() {
      try {
        const liveCandles = await fetchEthCandles(interval, 500, instrument, controller.signal);
        // Ignore stale, malformed, and temporary-empty responses.  The last
        // known good snapshot remains visible while the chart is stale.
        if (request === requestId.current && normalizePoints(liveCandles, isCandle).length) setReceivedCandles(liveCandles);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      }
    }

    loadCandles();
    const timer = window.setInterval(loadCandles, 30_000);

    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [instrument, interval]);

  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, {
      ...chartTheme,
      width: container.clientWidth,
      height: container.clientHeight,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#00b37e",
      downColor: "#f6465d",
      borderUpColor: "#00b37e",
      borderDownColor: "#f6465d",
      wickUpColor: "#00b37e",
      wickDownColor: "#f6465d",
    });
    const visibleCandles = candles.slice(-260);
    try {
      series.setData(visibleCandles);
      const closes = candles.map((c) => c.close);
      const movingAverage = (period: number) => candles.slice(period - 1).map((c, i) => ({
        time: c.time,
        value: closes.slice(i, i + period).reduce((sum, value) => sum + value, 0) / period,
      }));
      const ma60 = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 2, priceLineVisible: false });
      const ma200 = chart.addSeries(LineSeries, { color: "#7c3aed", lineWidth: 2, priceLineVisible: false });
      ma60.setData(movingAverage(60).slice(-260));
      ma200.setData(movingAverage(200).slice(-260));
      if (cvd.length) {
        const cvdSeries = chart.addSeries(AreaSeries, { lineColor: "#7c3aed", topColor: "rgba(124,58,237,.22)", bottomColor: "rgba(124,58,237,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } }, 1);
        const alignedCvd = alignFlowToCandles(visibleCandles, cvd, interval);
        cvdSeries.setData(alignedCvd);
        cvdSeries.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0.00M" });
      }
      if (oi.length) {
        const oiSeries = chart.addSeries(AreaSeries, { lineColor: "#0ea5e9", topColor: "rgba(14,165,233,.20)", bottomColor: "rgba(14,165,233,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } }, 2);
        oiSeries.setData(alignFlowToCandles(visibleCandles, oi, interval));
      }
    } catch {
      // Retain the existing data; malformed transient inputs must not blank it.
    }
    chart.timeScale().fitContent();
    // Keep the initial viewport tied to the candle history.  Flow data starts
    // collecting later than the price history, especially on higher intervals.
    // It must not determine the visible time range.
    if (visibleCandles.length > 1) {
      chart.timeScale().setVisibleRange({
        from: visibleCandles[0].time,
        to: visibleCandles[visibleCandles.length - 1].time,
      });
    }
    const panes = chart.panes();
    panes[0]?.setStretchFactor(3);
    panes[1]?.setStretchFactor(1);
    panes[2]?.setStretchFactor(1);
    return chart;
  }, [candles, cvd, oi, interval]);

  return <div className="chart-canvas" ref={ref} />;
}

export function ReplayChart({ candles }: { candles: Candle[] }) {
  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight });
    const series = chart.addSeries(CandlestickSeries, { upColor: "#00b37e", downColor: "#f6465d", borderVisible: false, wickUpColor: "#00b37e", wickDownColor: "#f6465d" });
    series.setData(candles);
    chart.timeScale().fitContent();
    return chart;
  }, [candles]);
  return <div className="replay-canvas" ref={ref} />;
}

export function EquityChart() {
  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, {
      ...chartTheme,
      width: container.clientWidth,
      height: container.clientHeight,
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: "#00b37e",
      topColor: "rgba(0, 179, 126, 0.2)",
      bottomColor: "rgba(0, 179, 126, 0.02)",
      lineWidth: 2,
      priceLineVisible: false,
    });
    try {
      series.setData(generateEquityCurve());
    } catch {
      series.setData([]);
    }
    chart.timeScale().fitContent();
    return chart;
  });

  return <div className="chart-canvas" ref={ref} />;
}

export function FlowChart({ points, color = "#7c3aed", zeroLine = false, instrument = "ETH-USDT", interval = "15m", seriesType = "cvd" }: { points: Array<{ time: number; value: number }>; color?: string; zeroLine?: boolean; instrument?: string; interval?: string; seriesType?: "cvd" | "oi" }) {
  const {t}=useLanguage();
  const retained = useLastKnownGood({ instrument, timeframe: interval, series: seriesType }, points, isFlowPoint);
  const normalized = [...retained];
  if (normalized.length === 1) normalized.unshift({ time: normalized[0].time - 1, value: normalized[0].value });
  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight, rightPriceScale: { visible: true, borderVisible: false, scaleMargins: { top: .15, bottom: .15 } }, timeScale: { visible: true, borderVisible: false, timeVisible: true, secondsVisible: true, fixLeftEdge: true, fixRightEdge: true } });
    const series = chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}38`, bottomColor: `${color}05`, lineWidth: 2, priceLineVisible: true, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } });
    series.setData(normalized.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    if (zeroLine) series.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0" });
    chart.timeScale().fitContent();
    return chart;
  }, [normalized, color, zeroLine]);
  return <div className="flow-canvas">{normalized.length ? <div className="flow-canvas-inner" ref={ref} /> : <span className="flow-empty">{t("research.noSeries")}</span>}</div>;
}
