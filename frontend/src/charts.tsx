import { DependencyList, useEffect, useRef, useState } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi, LineSeries, UTCTimestamp } from "lightweight-charts";
import { Candle, fetchEthCandles, generateCandles, generateEquityCurve } from "./data";
import {useLanguage} from "./i18n";

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
    try {
      chart = factory(ref.current);
    } catch {
      return;
    }
    let lastWidth = 0;
    let lastHeight = 0;
    let delayedResize: number | undefined;
    const resizeChart = () => {
      const bounds = ref.current?.getBoundingClientRect();
      if (!bounds || bounds.width < 20 || bounds.height < 20) return;
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
    const resize = new ResizeObserver(queueResize);
    resize.observe(ref.current);
    window.addEventListener("resize", queueResize);
    window.visualViewport?.addEventListener("resize", queueResize);
    queueResize();

    return () => {
      resize.disconnect();
      window.clearTimeout(delayedResize);
      window.removeEventListener("resize", queueResize);
      window.visualViewport?.removeEventListener("resize", queueResize);
      chart?.remove();
    };
  }, deps);

  return ref;
}

export function MarketChart({ instrument = "ETH-USDT", interval = "15m", onViewportChange }: { instrument?: string; interval?: string; onViewportChange?: (viewport: { low: number; high: number; bins: number; top: number; bottom: number }) => void }) {
  const [candles, setCandles] = useState<Candle[]>(() => generateCandles());

  useEffect(() => {
    let cancelled = false;

    async function loadCandles() {
      try {
        const liveCandles = await fetchEthCandles(interval, 500, instrument);
        if (!cancelled) setCandles(liveCandles);
      } catch {
        if (!cancelled) setCandles(generateCandles());
      }
    }

    loadCandles();
    const timer = window.setInterval(loadCandles, 30_000);

    return () => {
      cancelled = true;
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
    try {
      const visibleCandles = candles.slice(-260);
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
      const reportViewport = () => {
        const range = chart.timeScale().getVisibleLogicalRange();
        if (!range || !onViewportChange) return;
        const from = Math.max(0, Math.floor(range.from));
        const to = Math.min(candles.length - 1, Math.ceil(range.to));
        const visible = candles.slice(from, to + 1);
        if (!visible.length) return;
        const low = Math.min(...visible.map((item) => item.low)), high = Math.max(...visible.map((item) => item.high));
        onViewportChange({ low, high, bins: Math.max(18, Math.min(42, Math.round(container.clientHeight / 15))), top: series.priceToCoordinate(high) ?? 12, bottom: series.priceToCoordinate(low) ?? container.clientHeight - 28 });
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(reportViewport);
      window.setTimeout(reportViewport, 0);
    } catch {
      series.setData(generateCandles());
    }
    chart.timeScale().fitContent();
    return chart;
  }, [candles, onViewportChange]);

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

export function FlowChart({ points, color = "#7c3aed", zeroLine = false }: { points: Array<{ time: number; value: number }>; color?: string; zeroLine?: boolean }) {
  const {t}=useLanguage();
  const normalized = Array.from(new Map(points.filter((point) => Number.isFinite(point.time) && Number.isFinite(point.value)).map((point) => [point.time, point])).values()).sort((a, b) => a.time - b.time);
  if (normalized.length === 1) normalized.unshift({ time: normalized[0].time - 1, value: normalized[0].value });
  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight, rightPriceScale: { visible: true, borderVisible: false, scaleMargins: { top: .15, bottom: .15 } }, timeScale: { visible: true, borderVisible: false, timeVisible: true, secondsVisible: true, fixLeftEdge: true, fixRightEdge: true } });
    const series = chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}38`, bottomColor: `${color}05`, lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
    series.setData(normalized.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    if (zeroLine) series.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0" });
    chart.timeScale().fitContent();
    return chart;
  }, [points, color, zeroLine]);
  return <div className="flow-canvas">{normalized.length ? <div className="flow-canvas-inner" ref={ref} /> : <span className="flow-empty">{t("research.noSeries")}</span>}</div>;
}
