import { DependencyList, useEffect, useRef, useState } from "react";
import { AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi } from "lightweight-charts";
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
    const resize = new ResizeObserver(([entry]) => {
      chart?.applyOptions({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height),
      });
    });
    resize.observe(ref.current);

    return () => {
      resize.disconnect();
      chart?.remove();
    };
  }, deps);

  return ref;
}

export function MarketChart() {
  const [candles, setCandles] = useState<Candle[]>(() => generateCandles());

  useEffect(() => {
    let cancelled = false;

    async function loadCandles() {
      try {
        const liveCandles = await fetchEthCandles("15m", 160);
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
  }, []);

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
