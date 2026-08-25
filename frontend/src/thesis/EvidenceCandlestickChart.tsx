import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, type SeriesMarker, type UTCTimestamp } from "lightweight-charts";
import type { ThesisEventContext } from "./types";

export default function EvidenceCandlestickChart({ context, accessibleLabel }: { context: ThesisEventContext; accessibleLabel: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth, height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: "#0a1621" }, textColor: "#8fa4b8" },
      grid: { vertLines: { color: "rgba(130,155,180,.08)" }, horzLines: { color: "rgba(130,155,180,.08)" } },
      rightPriceScale: { borderColor: "rgba(130,155,180,.14)" },
      timeScale: { borderColor: "rgba(130,155,180,.14)", timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: "rgba(93,224,200,.35)" }, horzLine: { color: "rgba(93,224,200,.35)" } },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#24c69a", downColor: "#ef646f", borderVisible: false,
      wickUpColor: "#24c69a", wickDownColor: "#ef646f",
    });
    series.setData(context.candles.map((candle) => ({
      time: candle.close_timestamp as UTCTimestamp, open: candle.open, high: candle.high,
      low: candle.low, close: candle.close,
    })));
    const structure = context.event.structure_context?.[0];
    if (structure) series.createPriceLine({
      price: structure.reference_level, color: "#9d8cff", lineWidth: 1,
      lineStyle: 2, axisLabelVisible: true, title: "Reference",
    });
    const markers: SeriesMarker<UTCTimestamp>[] = [{
      time: context.event.timestamp as UTCTimestamp, position: "belowBar", color: "#5de0c8",
      shape: "arrowUp", text: structure?.failure_confirmation_timestamp ? "Failure confirmed" : "Event",
    }];
    if (structure?.failure_confirmation_timestamp && structure.original_breakout_timestamp !== context.event.timestamp) markers.push({
      time: structure.original_breakout_timestamp as UTCTimestamp, position: "aboveBar", color: "#9d8cff",
      shape: "circle", text: "Original breakout",
    });
    for (const horizon of context.horizons) if (horizon.available && horizon.candle_index !== null) markers.push({
      time: horizon.target_timestamp as UTCTimestamp, position: "aboveBar", color: "#f2bd62",
      shape: "square", text: horizon.horizon,
    });
    markers.sort((left, right) => Number(left.time) - Number(right.time));
    createSeriesMarkers(series, markers);
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    observer.observe(container);
    return () => { observer.disconnect(); chart.remove(); };
  }, [context]);

  return <div className="thesis-evidence-chart" ref={containerRef} role="img" aria-label={accessibleLabel}
    data-event-marker-timestamp={context.event.timestamp}
    data-reference-level={context.event.structure_context?.[0]?.reference_level}
    data-original-breakout-timestamp={context.event.structure_context?.[0]?.original_breakout_timestamp}
    data-horizon-marker-timestamps={context.horizons.filter((item) => item.available && item.candle_index !== null)
      .map((item) => `${item.horizon}:${item.target_timestamp}`).join(",")} />;
}
