import { UTCTimestamp, WhitespaceData } from "lightweight-charts";
import { Candle } from "./data";
import { loadChartSnapshot, saveChartSnapshot } from "./chartState";
import { FlowHistoryPoint } from "./flowHistory";

const MEMORY_POINT_LIMIT = 50_000;
const memory = new Map<string, Candle[]>();

export type CandlePage = {
  instrument: string;
  timeframe: string;
  points: Candle[];
};

export type CandleRangeRequest = {
  before: number;
  limit: number;
};

const validCandle = (point: unknown): point is Candle => {
  const row = point as Candle;
  return !!row
    && [row.time, row.open, row.high, row.low, row.close].every(Number.isFinite)
    && (row.volume === undefined || Number.isFinite(row.volume));
};

export function candleHistoryKey(instrument: string, timeframe: string) {
  return `${instrument}:${timeframe}`;
}

export function mergeCandlePages(
  retained: Candle[],
  incoming: unknown,
  limit = MEMORY_POINT_LIMIT,
): Candle[] {
  const byTime = new Map<number, Candle>();
  for (const candle of retained) if (validCandle(candle)) byTime.set(Number(candle.time), candle);
  if (Array.isArray(incoming)) {
    for (const candle of incoming) if (validCandle(candle)) byTime.set(Number(candle.time), candle);
  }
  return [...byTime.values()]
    .sort((a, b) => Number(a.time) - Number(b.time))
    .slice(-limit);
}

export function hydrateCandleHistory(instrument: string, timeframe: string) {
  const key = candleHistoryKey(instrument, timeframe);
  const retained = memory.get(key);
  if (retained?.length) return retained;
  const local = loadChartSnapshot({ instrument, timeframe, series: "candles" }, validCandle);
  if (local.length) memory.set(key, local);
  return local;
}

export function retainCandlePage(
  instrument: string,
  timeframe: string,
  page: CandlePage,
) {
  const current = hydrateCandleHistory(instrument, timeframe);
  if (
    page.instrument !== instrument
    || page.timeframe !== timeframe
    || !page.points.some(validCandle)
  ) return current;
  const merged = mergeCandlePages(current, page.points);
  memory.set(candleHistoryKey(instrument, timeframe), merged);
  saveChartSnapshot({ instrument, timeframe, series: "candles" }, merged, validCandle);
  return merged;
}

export class CandleSelectionGuard {
  private generation = 0;
  private selection = "";

  select(instrument: string, timeframe: string) {
    const next = candleHistoryKey(instrument, timeframe);
    if (next !== this.selection) {
      this.selection = next;
      this.generation += 1;
    }
    return this.generation;
  }

  token() {
    return { selection: this.selection, generation: this.generation };
  }

  accepts(token: { selection: string; generation: number }) {
    return token.selection === this.selection && token.generation === this.generation;
  }
}

export function olderCandlePageRequest(
  candles: Candle[],
  visibleStart: number,
  timeframeSeconds: number,
  thresholdCandles = 20,
  limit = 500,
): CandleRangeRequest | null {
  if (!candles.length) return null;
  const earliest = Number(candles[0].time);
  if (visibleStart > earliest + timeframeSeconds * thresholdCandles) return null;
  return { before: earliest, limit };
}

export function movingAverageSeries(candles: Candle[], period: number) {
  if (period < 1 || candles.length < period) return [];
  let sum = candles.slice(0, period).reduce((total, candle) => total + candle.close, 0);
  const result = [{ time: candles[period - 1].time, value: sum / period }];
  for (let index = period; index < candles.length; index += 1) {
    sum += candles[index].close - candles[index - period].close;
    result.push({ time: candles[index].time, value: sum / period });
  }
  return result;
}

/**
 * Projects flow observations onto candle-open timestamps. The last confirmed
 * value inside each candle bucket is rendered; buckets with no observation are
 * explicit whitespace. Flow timestamps can therefore never add points to or
 * widen the master candle time scale.
 */
export function flowOnCandleTimeline(
  candles: Candle[],
  points: FlowHistoryPoint[],
  timeframeSeconds: number,
): Array<{ time: UTCTimestamp; value: number } | WhitespaceData<UTCTimestamp>> {
  const sorted = [...points].sort((a, b) => a.time - b.time);
  let pointIndex = 0;
  return candles.map(candle => {
    const start = Number(candle.time);
    const end = start + timeframeSeconds;
    while (pointIndex < sorted.length && sorted[pointIndex].time < start) pointIndex += 1;
    let latest: FlowHistoryPoint | undefined;
    while (pointIndex < sorted.length && sorted[pointIndex].time < end) {
      latest = sorted[pointIndex];
      pointIndex += 1;
    }
    return latest
      ? { time: candle.time, value: latest.value }
      : { time: candle.time };
  });
}

export function withPreservedTimeRange<T>(
  timeScale: {
    getVisibleRange(): T | null;
    setVisibleRange(range: T): void;
  } | undefined,
  update: () => void,
) {
  const range = timeScale?.getVisibleRange();
  update();
  if (range) timeScale?.setVisibleRange(range);
}

export function __resetCandleHistoryForTests() {
  memory.clear();
}
