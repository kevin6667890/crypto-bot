import { useCallback, useEffect, useRef, useState } from "react";
import { AreaData, AreaSeries, CandlestickSeries, ColorType, createChart, IChartApi, ISeriesApi, LineSeries, UTCTimestamp, WhitespaceData } from "lightweight-charts";
import { Candle, fetchEthCandles, fetchOlderCandles, generateEquityCurve } from "./data";
import { useLanguage } from "./i18n";
import { formatMillions, normalizePoints } from "./chartState";
import { chartFollowRegistry, RangeChangeSource, synchronizeLiveViewport } from "./liveFollow";
import { NativePriceAxisLabel, PriceLabelSource, updateLatestNativePriceAxisLabels, updateNativePriceAxisLabels } from "./priceLabels";
import { PriceAxisLabelPrimitive } from "./priceAxisLabelPrimitive";
import {
  FlowCoverage,
  FLOW_HISTORY_MAX_POINTS,
  FlowHistoryPoint,
  FlowRangeRequest,
  FlowSelectionGuard,
  FlowSeriesName,
  formatFlowCoverage,
  flowStatusAtCandle,
  hydrateFlowHistory,
  olderPageRequest,
  persistedFlowInstrument,
  requestFlowHistory,
  retainFallbackHistory,
  retainedCoverage,
  retainServerHistory,
  visibleRangeFromCandles,
} from "./flowHistory";
import {
  CandleSelectionGuard,
  exponentialMovingAverageSeries,
  flowOnCandleTimeline,
  hydrateCandleHistory,
  movingAverageSeries,
  olderCandlePageRequest,
  retainCandlePage,
  withPreservedTimeRange,
} from "./candleHistory";

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
      const priorRange = chartRef.current?.timeScale().getVisibleRange() ?? null;
      let attempts = 0;
      const reflow = () => {
        if (disposed) return;
        if (!resize() && ++attempts < 8) { frame = requestAnimationFrame(reflow); return; }
        if (resize()) {
          // Firefox can retain a mounted canvas without repainting it after a
          // background-tab transition. Reapplying non-empty in-memory data is
          // safe and forces that repaint without ever clearing a series.
          recoverRef.current?.();
          if (priorRange) chartRef.current?.timeScale().setVisibleRange(priorRange);
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

function intervalSeconds(interval: string) {
  return interval === "1m" ? 60 : interval === "5m" ? 300 : interval === "15m" ? 900 : interval === "1h" ? 3600 : interval === "4h" ? 14400 : 86400;
}

function useServerFlowHistory(
  instrument: string,
  timeframe: string,
  series: FlowSeriesName,
  fallback: unknown,
) {
  const guard = useRef(new FlowSelectionGuard());
  guard.current.select(instrument, timeframe);
  const [points, setPoints] = useState<FlowHistoryPoint[]>(() => hydrateFlowHistory(instrument, timeframe, series));
  const [coverage, setCoverage] = useState<FlowCoverage | undefined>(() => retainedCoverage(instrument, timeframe, series));
  const selection = `${instrument}:${timeframe}:${series}`;
  const activeSelection = useRef(selection);
  const selectionChanged = activeSelection.current !== selection;
  if (selectionChanged) activeSelection.current = selection;

  useEffect(() => {
    setPoints(hydrateFlowHistory(instrument, timeframe, series));
    setCoverage(retainedCoverage(instrument, timeframe, series));
  }, [selection, instrument, timeframe, series]);

  useEffect(() => {
    if (series === "cvd") return;
    const retained = retainFallbackHistory(instrument, timeframe, series, fallback);
    if (retained.length) setPoints(retained);
  }, [fallback, selection, instrument, timeframe, series]);

  const load = useCallback(async (range: Omit<FlowRangeRequest, "instrument" | "series">) => {
    const token = guard.current.token();
    try {
      const response = await requestFlowHistory({ instrument, series, ...range });
      if (!guard.current.accepts(token) || response.instrument !== instrument || response.series !== series) return;
      const retained = retainServerHistory(timeframe, response);
      if (retained.length) setPoints(retained);
      setCoverage(response);
    } catch {
      // Network and temporary server failures never clear retained history.
    }
  }, [instrument, timeframe, series]);
  return {
    points: selectionChanged ? hydrateFlowHistory(instrument, timeframe, series) : points,
    coverage: selectionChanged ? retainedCoverage(instrument, timeframe, series) : coverage,
    load,
  };
}

function gapAware(
  points: FlowHistoryPoint[],
  resolutionSeconds: number,
): Array<AreaData<UTCTimestamp> | WhitespaceData<UTCTimestamp>> {
  const result: Array<AreaData<UTCTimestamp> | WhitespaceData<UTCTimestamp>> = [];
  points.forEach((point, index) => {
    const previous = points[index - 1];
    if (previous && point.time - previous.time > resolutionSeconds * 1.5) {
      result.push({ time: (previous.time + resolutionSeconds) as UTCTimestamp });
    }
    result.push(
      point.status === "WHITESPACE" || !Number.isFinite(point.value)
        ? { time: point.time as UTCTimestamp }
        : { time: point.time as UTCTimestamp, value: Number(point.value) },
    );
  });
  return result;
}

const PRICE_SERIES_CONFIG = [
  { id: "candles", name: "K线", color: "#00b37e" },
  { id: "ema20", name: "EMA20", color: "#2563eb" },
  { id: "ma60", name: "MA60", color: "#f59e0b" },
  { id: "ma200", name: "MA200", color: "#7c3aed" },
] as const;
const FLOW_SERIES_CONFIG = [
  { id: "cvd", name: "CVD", color: "#7c3aed" },
  { id: "oi", name: "OI", color: "#0ea5e9" },
] as const;
const PRICE_FORMAT = { type: "price" as const, precision: 2, minMove: .01 };
const PRICE_AXIS_MINIMUM_WIDTH = 82;

type PriceSeriesId = typeof PRICE_SERIES_CONFIG[number]["id"];
type AxisSeriesId = PriceSeriesId | typeof FLOW_SERIES_CONFIG[number]["id"];
type MarketSeries = {
  candles: ISeriesApi<"Candlestick">;
  ema20: ISeriesApi<"Line">;
  ma60: ISeriesApi<"Line">;
  ma200: ISeriesApi<"Line">;
  cvd: ISeriesApi<"Area">;
  oi: ISeriesApi<"Area">;
};

export function MarketChart({ instrument = "ETH-USDT", interval = "15m", flow }: { instrument?: string; interval?: string; flow?: FlowPaneData }) {
  const { t } = useLanguage();
  const candleSelection = `${instrument}:${interval}`;
  const [followState, setFollowState] = useState(() => chartFollowRegistry.follow(candleSelection));
  const followStateRef = useRef(followState);
  followStateRef.current = followState;
  const candleGuard = useRef(new CandleSelectionGuard());
  candleGuard.current.select(instrument, interval);
  const [retainedCandles, setRetainedCandles] = useState<Candle[]>(() => hydrateCandleHistory(instrument, interval));
  const activeCandleSelection = useRef(candleSelection);
  const candleSelectionChanged = activeCandleSelection.current !== candleSelection;
  if (candleSelectionChanged) activeCandleSelection.current = candleSelection;
  const candles = candleSelectionChanged ? hydrateCandleHistory(instrument, interval) : retainedCandles;
  const flowInstrument = persistedFlowInstrument(instrument);
  const cvdHistory = useServerFlowHistory(flowInstrument, interval, "cvd", flow?.cvd_series);
  const oiHistory = useServerFlowHistory(flowInstrument, interval, "oi", flow?.oi_series);
  const cvd = cvdHistory.points, oi = oiHistory.points;
  const requestId = useRef(0);
  const loadRef = useRef<{ refresh: () => void; older: (start: number) => void }>({ refresh: () => undefined, older: () => undefined });
  const seriesRef = useRef<MarketSeries | null>(null);
  const marketChartRef = useRef<IChartApi | null>(null);
  const rangeTimer = useRef(0);
  const interactionTimer = useRef(0);
  const internalRangeFrame = useRef(0);
  const internalRangeToken = useRef(0);
  const internalRangeActive = useRef(false);
  const latestCandleTime = useRef<number | null>(null);
  const crosshairTimestamp = useRef<number | null>(null);
  const [crosshairFlow, setCrosshairFlow] = useState<{
    cvd: ReturnType<typeof flowStatusAtCandle>;
    oi: ReturnType<typeof flowStatusAtCandle>;
  } | null>(null);
  const rangeChangeSource = useRef(new RangeChangeSource());
  const priceSourcesRef = useRef<PriceLabelSource[]>([]);
  const axisLabelsRef = useRef<Record<AxisSeriesId, NativePriceAxisLabel> | null>(null);
  const historyLoadRef = useRef({ cvd: cvdHistory.load, oi: oiHistory.load });
  historyLoadRef.current = { cvd: cvdHistory.load, oi: oiHistory.load };
  const dataRef = useRef({ candles, cvd, oi, interval, cvdCoverage: cvdHistory.coverage, oiCoverage: oiHistory.coverage });
  dataRef.current = { candles, cvd, oi, interval, cvdCoverage: cvdHistory.coverage, oiCoverage: oiHistory.coverage };

  const endInternalRangeUpdate = (token = internalRangeToken.current) => {
    if (token !== internalRangeToken.current || !internalRangeActive.current) return;
    internalRangeActive.current = false;
    rangeChangeSource.current.endInternal();
  };
  const scrollToLatest = () => {
    const timeScale = marketChartRef.current?.timeScale();
    if (!timeScale || !dataRef.current.candles.length) return;
    const token = ++internalRangeToken.current;
    if (!internalRangeActive.current) {
      internalRangeActive.current = true;
      rangeChangeSource.current.beginInternal();
    }
    synchronizeLiveViewport(timeScale, "FOLLOWING_LATEST", null);
    window.cancelAnimationFrame(internalRangeFrame.current);
    internalRangeFrame.current = window.requestAnimationFrame(() => {
      internalRangeFrame.current = window.requestAnimationFrame(() => endInternalRangeUpdate(token));
    });
  };
  const updateAxisLabels = (timestamp?: number) => {
    const labels = axisLabelsRef.current;
    const candleData = dataRef.current.candles;
    if (!labels || !candleData.length) return;
    const target = timestamp ?? Number(candleData[candleData.length - 1].time);
    const candle = candleData.find(point => Number(point.time) === target);
    const sources = priceSourcesRef.current.map(source => source.id === "candles" && candle
      ? { ...source, color: candle.close >= candle.open ? "#00b37e" : "#f6465d" }
      : source);
    if (timestamp === undefined) updateLatestNativePriceAxisLabels(sources, labels);
    else updateNativePriceAxisLabels(target, sources, labels);
  };
  const applyData = () => {
    const series = seriesRef.current;
    const data = dataRef.current;
    if (!series || !data.candles.length) return;
    const timeScale = marketChartRef.current?.timeScale();
    const priorTimeRange = timeScale?.getVisibleRange() ?? null;
    const newestTime = Number(data.candles[data.candles.length - 1].time);
    const hadNewTimestamp = latestCandleTime.current !== null && newestTime > latestCandleTime.current;
    latestCandleTime.current = Math.max(latestCandleTime.current ?? newestTime, newestTime);
    const ingested = chartFollowRegistry.onData(candleSelection, hadNewTimestamp);
    if (ingested.mode !== followStateRef.current.mode || ingested.hasNewData !== followStateRef.current.hasNewData) {
      followStateRef.current = ingested;
      setFollowState(ingested);
    }
    series.candles.setData(data.candles);
    const ema20 = exponentialMovingAverageSeries(data.candles, 20);
    const ma60 = movingAverageSeries(data.candles, 60), ma200 = movingAverageSeries(data.candles, 200);
    series.ema20.setData(ema20);
    series.ma60.setData(ma60);
    series.ma200.setData(ma200);
    const projectedCvd = flowOnCandleTimeline(data.candles, data.cvd, intervalSeconds(data.interval));
    const projectedOi = flowOnCandleTimeline(data.candles, data.oi, intervalSeconds(data.interval));
    series.cvd.setData(projectedCvd);
    series.oi.setData(projectedOi);
    priceSourcesRef.current = [...PRICE_SERIES_CONFIG, ...FLOW_SERIES_CONFIG].map(config => ({
      ...config,
      values: config.id === "candles"
        ? data.candles.map(candle => ({ time: Number(candle.time), value: candle.close }))
        : config.id === "ema20" ? ema20 : config.id === "ma60" ? ma60 : ma200,
      ...(config.id === "cvd" ? { values: projectedCvd.flatMap(point => "value" in point && Number.isFinite(point.value) ? [{ time: Number(point.time), value: Number(point.value) }] : []) } : {}),
      ...(config.id === "oi" ? { values: projectedOi.flatMap(point => "value" in point && Number.isFinite(point.value) ? [{ time: Number(point.time), value: Number(point.value) }] : []) } : {}),
    }));
    if (timeScale && ingested.mode === "FOLLOWING_LATEST") {
      scrollToLatest();
    } else if (timeScale && priorTimeRange) {
      rangeChangeSource.current.beginInternal();
      synchronizeLiveViewport(timeScale, ingested.mode, priorTimeRange);
      rangeChangeSource.current.endInternal();
    }
    updateAxisLabels(crosshairTimestamp.current ?? undefined);
  };
  const { containerRef } = useResponsiveChart((container) => {
    const chart = createChart(container, {
      ...chartTheme,
      width: container.clientWidth,
      height: container.clientHeight,
      crosshair: {
        ...chartTheme.crosshair,
        horzLine: { ...chartTheme.crosshair.horzLine, labelVisible: false },
      },
      rightPriceScale: { ...chartTheme.rightPriceScale, minimumWidth: PRICE_AXIS_MINIMUM_WIDTH },
    });
    marketChartRef.current = chart;
    seriesRef.current = {
      candles: chart.addSeries(CandlestickSeries, { upColor: "#00b37e", downColor: "#f6465d", borderUpColor: "#00b37e", borderDownColor: "#f6465d", wickUpColor: "#00b37e", wickDownColor: "#f6465d", lastValueVisible: false, priceLineVisible: false, priceFormat: PRICE_FORMAT }),
      ema20: chart.addSeries(LineSeries, { color: PRICE_SERIES_CONFIG[1].color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: PRICE_FORMAT }),
      ma60: chart.addSeries(LineSeries, { color: PRICE_SERIES_CONFIG[2].color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: PRICE_FORMAT }),
      ma200: chart.addSeries(LineSeries, { color: PRICE_SERIES_CONFIG[3].color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: PRICE_FORMAT }),
      cvd: chart.addSeries(AreaSeries, { lineColor: FLOW_SERIES_CONFIG[0].color, topColor: "rgba(124,58,237,.22)", bottomColor: "rgba(124,58,237,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: formatMillions } }, 1),
      oi: chart.addSeries(AreaSeries, { lineColor: FLOW_SERIES_CONFIG[1].color, topColor: "rgba(14,165,233,.20)", bottomColor: "rgba(14,165,233,.02)", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: formatMillions } }, 2),
    };
    const axisLabels = {} as Record<AxisSeriesId, NativePriceAxisLabel>;
    [...PRICE_SERIES_CONFIG, ...FLOW_SERIES_CONFIG].forEach(config => {
      const primitive = new PriceAxisLabelPrimitive({ color: config.color });
      seriesRef.current![config.id].attachPrimitive(primitive);
      axisLabels[config.id] = primitive;
    });
    axisLabelsRef.current = axisLabels;
    seriesRef.current.cvd.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    applyData();
    const initial = visibleRangeFromCandles(dataRef.current.candles);
    if (initial) {
      rangeChangeSource.current.beginInternal();
      chart.timeScale().setVisibleRange({ from: initial.start as UTCTimestamp, to: initial.end as UTCTimestamp });
      rangeChangeSource.current.endInternal();
      scrollToLatest();
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (!range) return;
      if (!rangeChangeSource.current.shouldApplyVisibleRange()) return;
      const logical = { from: Number(range.from), to: Number(range.to) };
      const next = chartFollowRegistry.onVisibleRange(candleSelection, logical, dataRef.current.candles.length - 1);
      if (next.mode !== followStateRef.current.mode || next.hasNewData !== followStateRef.current.hasNewData) {
        followStateRef.current = next;
        setFollowState(next);
      }
    });
    chart.subscribeCrosshairMove(param => {
      crosshairTimestamp.current = typeof param.time === "number" ? Number(param.time) : null;
      setCrosshairFlow(crosshairTimestamp.current === null ? null : {
        cvd: flowStatusAtCandle(
          crosshairTimestamp.current,
          intervalSeconds(dataRef.current.interval),
          dataRef.current.cvd,
        ),
        oi: flowStatusAtCandle(
          crosshairTimestamp.current,
          intervalSeconds(dataRef.current.interval),
          dataRef.current.oi,
        ),
      });
      updateAxisLabels(crosshairTimestamp.current ?? undefined);
    });
    chart.timeScale().subscribeVisibleTimeRangeChange(range => {
      if (!range) return;
      window.clearTimeout(rangeTimer.current);
      rangeTimer.current = window.setTimeout(() => {
        const start = Number(range.from), end = Number(range.to);
        const current = dataRef.current, loaders = historyLoadRef.current;
        loadRef.current.older(start);
        void loaders.cvd({ start, end, maxPoints: FLOW_HISTORY_MAX_POINTS });
        void loaders.oi({ start, end, maxPoints: FLOW_HISTORY_MAX_POINTS });
        for (const [load, points, coverage] of [
          [loaders.cvd, current.cvd, current.cvdCoverage],
          [loaders.oi, current.oi, current.oiCoverage],
        ] as const) {
          const older = olderPageRequest(coverage, points, start, intervalSeconds(current.interval) * 3);
          if (older) void load(older);
        }
      }, 120);
    });
    chart.panes()[0]?.setStretchFactor(3); chart.panes()[1]?.setStretchFactor(1); chart.panes()[2]?.setStretchFactor(1);
    return chart;
  }, () => {
    applyData();
    loadRef.current.refresh();
    if (followStateRef.current.mode === "FOLLOWING_LATEST") requestAnimationFrame(scrollToLatest);
  });
  useEffect(() => { applyData(); }, [candles, cvd, oi, interval, cvdHistory.coverage, oiHistory.coverage]);
  useEffect(() => {
    const range = visibleRangeFromCandles(candles);
    if (!range) return;
    void cvdHistory.load({ ...range, maxPoints: FLOW_HISTORY_MAX_POINTS });
    void oiHistory.load({ ...range, maxPoints: FLOW_HISTORY_MAX_POINTS });
  }, [candles, instrument, interval, cvdHistory.load, oiHistory.load]);
  useEffect(() => () => {
    window.clearTimeout(rangeTimer.current);
    window.clearTimeout(interactionTimer.current);
    window.cancelAnimationFrame(internalRangeFrame.current);
    endInternalRangeUpdate();
  }, []);
  useEffect(() => {
    setRetainedCandles(hydrateCandleHistory(instrument, interval));
    const next = chartFollowRegistry.follow(candleSelection);
    followStateRef.current = next;
    setFollowState(next);
    latestCandleTime.current = null;
  }, [candleSelection, instrument, interval]);
  useEffect(() => {
    const controller = new AbortController();
    const olderInflight = new Set<number>();
    const accept = (token: ReturnType<CandleSelectionGuard["token"]>, points: Candle[]) => {
      if (!candleGuard.current.accepts(token)) return;
      const merged = retainCandlePage(instrument, interval, { instrument, timeframe: interval, points });
      if (merged.length) setRetainedCandles(merged);
    };
    const load = async () => {
      const request = ++requestId.current;
      const token = candleGuard.current.token();
      try {
        const live = await fetchEthCandles(interval, 500, instrument, controller.signal);
        if (request === requestId.current) accept(token, normalizePoints(live, isCandle));
      } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) { /* retain LKG */ } }
    };
    const loadOlder = async (visibleStart: number) => {
      const current = hydrateCandleHistory(instrument, interval);
      const request = olderCandlePageRequest(current, visibleStart, intervalSeconds(interval));
      if (!request || olderInflight.has(request.before)) return;
      olderInflight.add(request.before);
      const token = candleGuard.current.token();
      try {
        const older = await fetchOlderCandles(interval, request.limit, instrument, request.before, controller.signal);
        accept(token, normalizePoints(older, isCandle));
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) { /* retain LKG */ }
      } finally {
        olderInflight.delete(request.before);
      }
    };
    loadRef.current = { refresh: () => { void load(); }, older: start => { void loadOlder(start); } };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      loadRef.current = { refresh: () => undefined, older: () => undefined };
    };
  }, [instrument, interval]);
  const beginUserInteraction = () => {
    endInternalRangeUpdate();
    window.clearTimeout(interactionTimer.current);
    rangeChangeSource.current.beginUser();
  };
  const endUserInteraction = () => {
    window.clearTimeout(interactionTimer.current);
    interactionTimer.current = window.setTimeout(() => rangeChangeSource.current.endUser(), 80);
  };
  const handleWheel = () => {
    beginUserInteraction();
    interactionTimer.current = window.setTimeout(() => rangeChangeSource.current.endUser(), 180);
  };
  const latestCvdPartial = [...cvd].reverse().find(point =>
    point.status !== "WHITESPACE" && Number.isFinite(point.value)
  )?.partial_after_gap === true;
  return <div
    className="chart-canvas"
    ref={containerRef}
    onPointerDown={beginUserInteraction}
    onPointerUp={endUserInteraction}
    onPointerCancel={endUserInteraction}
    onWheel={handleWheel}
    onMouseLeave={() => {
      crosshairTimestamp.current = null;
      setCrosshairFlow(null);
      endUserInteraction();
      updateAxisLabels();
    }}
  >
    <div className="market-flow-coverage" aria-label={t("flow.historyCoverage")}>
      {latestCvdPartial && <span>CVD · {t("flow.partial")}</span>}
      <span>CVD（日内累计，UTC 00:00 重置） · {formatFlowCoverage(cvdHistory.coverage)}</span>
      <span>OI · {formatFlowCoverage(oiHistory.coverage)}</span>
    </div>
    {crosshairFlow && <div className="market-flow-crosshair" role="status">
      <span>CVD: {crosshairFlow.cvd.value === null
        ? t("flow.noConfirmed")
        : `${formatMillions(crosshairFlow.cvd.value)}${crosshairFlow.cvd.partial ? ` · ${t("flow.partial")}` : ""}`}</span>
      <span>OI: {crosshairFlow.oi.value === null
        ? t("flow.noConfirmed")
        : formatMillions(crosshairFlow.oi.value)}</span>
    </div>}
  </div>;
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
  const history = useServerFlowHistory(instrument, interval, seriesType, points);
  const retained = history.points;
  const normalized = retained;
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const flowChartRef = useRef<IChartApi | null>(null);
  const rangeTimer = useRef(0);
  const historyRef = useRef(history);
  historyRef.current = history;
  const intervalRef = useRef(interval);
  intervalRef.current = interval;
  const dataRef = useRef(normalized); dataRef.current = normalized;
  const apply = () => {
    if (!dataRef.current.length) return;
    withPreservedTimeRange(flowChartRef.current?.timeScale(), () => {
      seriesRef.current?.setData(gapAware(dataRef.current, history.coverage?.resolution_seconds || intervalSeconds(interval)));
    });
  };
  const { containerRef } = useResponsiveChart((container) => {
    const chart = createChart(container, { ...chartTheme, width: container.clientWidth, height: container.clientHeight, rightPriceScale: { visible: true, borderVisible: false, scaleMargins: { top: .15, bottom: .15 } }, timeScale: { visible: true, borderVisible: false, timeVisible: true, secondsVisible: true, fixLeftEdge: true, fixRightEdge: true } });
    flowChartRef.current = chart;
    seriesRef.current = chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}38`, bottomColor: `${color}05`, lineWidth: 2, priceLineVisible: true, lastValueVisible: true, priceFormat: { type: "custom", formatter: formatMillions } });
    if (zeroLine) seriesRef.current.createPriceLine({ price: 0, color: "rgba(71,84,103,.45)", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "0.00M" });
    apply();
    chart.timeScale().subscribeVisibleTimeRangeChange(range => {
      if (!range) return;
      window.clearTimeout(rangeTimer.current);
      rangeTimer.current = window.setTimeout(() => {
        const start = Number(range.from), end = Number(range.to);
        const current = historyRef.current, coverage = current.coverage;
        void current.load({ start, end, maxPoints: FLOW_HISTORY_MAX_POINTS });
        const older = olderPageRequest(coverage, current.points, start, intervalSeconds(intervalRef.current) * 3);
        if (older) void current.load(older);
      }, 120);
    });
    return chart;
  }, apply);
  useEffect(apply, [normalized, history.coverage, interval]);
  useEffect(() => {
    const end = Math.floor(Date.now() / 1000);
    void history.load({ start: end - intervalSeconds(interval) * 500, end, maxPoints: FLOW_HISTORY_MAX_POINTS });
  }, [instrument, interval, seriesType, history.load]);
  useEffect(() => () => window.clearTimeout(rangeTimer.current), []);
  return <div className="flow-canvas">
    <div className="flow-coverage-state">{formatFlowCoverage(history.coverage)}</div>
    <div className="flow-canvas-inner" ref={containerRef} />
    {!normalized.length && <span className="flow-empty">{t("research.noSeries")}</span>}
  </div>;
}
