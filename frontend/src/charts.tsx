import { DependencyList, useEffect, useRef, useState } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi, LineSeries, UTCTimestamp } from "lightweight-charts";
import { Candle, fetchEthCandles, generateCandles, generateEquityCurve } from "./data";

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

export function MarketChart({ instrument = "ETH-USDT", interval = "15m", showBoll = true }: { instrument?: string; interval?: string; showBoll?: boolean }) {
  const [candles, setCandles] = useState<Candle[]>(() => generateCandles());

  useEffect(() => {
    let cancelled = false;

    async function loadCandles() {
      try {
        const liveCandles = await fetchEthCandles(interval, 160, instrument);
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
      series.setData(candles);
      const closes = candles.map((c) => c.close);
      const movingAverage = (period: number) => candles.slice(period - 1).map((c, i) => ({
        time: c.time,
        value: closes.slice(i, i + period).reduce((sum, value) => sum + value, 0) / period,
      }));
      const ma5 = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 1, priceLineVisible: false });
      const ma10 = chart.addSeries(LineSeries, { color: "#8b5cf6", lineWidth: 1, priceLineVisible: false });
      ma5.setData(movingAverage(5));
      ma10.setData(movingAverage(10));
      if (showBoll) {
        const upper = chart.addSeries(LineSeries, { color: "rgba(14, 165, 233, .55)", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
        const lower = chart.addSeries(LineSeries, { color: "rgba(14, 165, 233, .55)", lineWidth: 1, lineStyle: 2, priceLineVisible: false });
        const boll = candles.slice(19).map((c, i) => {
          const sample = closes.slice(i, i + 20);
          const mean = sample.reduce((sum, value) => sum + value, 0) / sample.length;
          const deviation = Math.sqrt(sample.reduce((sum, value) => sum + (value - mean) ** 2, 0) / sample.length);
          return { time: c.time, upper: mean + 2 * deviation, lower: mean - 2 * deviation };
        });
        upper.setData(boll.map((point) => ({ time: point.time, value: point.upper })));
        lower.setData(boll.map((point) => ({ time: point.time, value: point.lower })));
      }
    } catch {
      series.setData(generateCandles());
    }
    chart.timeScale().fitContent();
    return chart;
  }, [candles]);

  return <div className="chart-canvas" ref={ref} />;
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

export function FlowChart({ points, color = "#7c3aed" }: { points: Array<{ time: number; value: number }>; color?: string }) {
  const ref = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight, rightPriceScale: { visible: false }, timeScale: { visible: false } });
    const series = chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}33`, bottomColor: `${color}08`, lineWidth: 2, priceLineVisible: false });
    series.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    chart.timeScale().fitContent();
    return chart;
  }, [points, color]);
  return <div className="flow-canvas" ref={ref} />;
}
