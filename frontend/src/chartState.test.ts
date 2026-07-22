import { afterEach, describe, expect, it } from "vitest";
import { CHART_POINT_LIMIT, CHART_RETENTION_MS, chartCacheKey, formatMillions, loadChartSnapshot, normalizePoints, saveChartSnapshot } from "./chartState";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}
const memory = new MemoryStorage();
Object.defineProperty(globalThis, "window", { value: { localStorage: memory }, configurable: true });
const candle = (time: number) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 3 });
const isCandle = (value: unknown): value is ReturnType<typeof candle> => {
  const row = value as ReturnType<typeof candle>; return !!row && [row.time, row.open, row.high, row.low, row.close, row.volume].every(Number.isFinite);
};
const isFlow = (value: unknown): value is { time: number; value: number } => {
  const row = value as { time?: number; value?: number }; return !!row && Number.isFinite(row.time) && Number.isFinite(row.value);
};
const btc15 = { instrument: "BTC-USDT", timeframe: "15m", series: "candles" as const };

afterEach(() => memory.clear());

describe("last-known-good chart snapshots", () => {
  it("stores a nonempty successful candle response", () => {
    expect(saveChartSnapshot(btc15, [candle(1)], isCandle, 10)).toBe(true);
    expect(loadChartSnapshot(btc15, isCandle, 11)).toEqual([candle(1)]);
  });
  it("retains a candle snapshot when a refresh is empty", () => {
    saveChartSnapshot(btc15, [candle(1)], isCandle); saveChartSnapshot(btc15, [], isCandle);
    expect(loadChartSnapshot(btc15, isCandle)).toHaveLength(1);
  });
  it("retains CVD and OI snapshots when their refresh is empty or errors", () => {
    for (const series of ["cvd", "oi"] as const) {
      const key = { instrument: "BTC-USDT", timeframe: "15m", series };
      saveChartSnapshot(key, [{ time: 1, value: 12 }], isFlow); saveChartSnapshot(key, [], isFlow);
      expect(loadChartSnapshot(key, isFlow)).toEqual([{ time: 1, value: 12 }]);
    }
  });
  it("hydrates only the exact instrument and timeframe", () => {
    saveChartSnapshot(btc15, [candle(1)], isCandle);
    expect(loadChartSnapshot({ ...btc15, instrument: "ETH-USDT" }, isCandle)).toEqual([]);
    expect(loadChartSnapshot({ ...btc15, timeframe: "1h" }, isCandle)).toEqual([]);
  });
  it("sorts, deduplicates, filters malformed points, and bounds retention", () => {
    const points = Array.from({ length: CHART_POINT_LIMIT + 3 }, (_, time) => ({ time, value: time }));
    const normalized = normalizePoints([...points, { time: 2, value: 99 }, { time: NaN, value: 3 }], isFlow);
    expect(normalized).toHaveLength(CHART_POINT_LIMIT); expect(normalized[0].time).toBe(3); expect(normalized[normalized.length - 1]).toEqual({ time: CHART_POINT_LIMIT + 2, value: CHART_POINT_LIMIT + 2 });
  });
  it("ignores malformed, incompatible, and expired persisted payloads", () => {
    memory.setItem(chartCacheKey(btc15), "not-json"); expect(loadChartSnapshot(btc15, isCandle)).toEqual([]);
    memory.setItem(chartCacheKey(btc15), JSON.stringify({ version: 99, savedAt: Date.now(), points: [candle(1)] })); expect(loadChartSnapshot(btc15, isCandle)).toEqual([]);
    memory.setItem(chartCacheKey(btc15), JSON.stringify({ version: 1, savedAt: 0, points: [candle(1)] })); expect(loadChartSnapshot(btc15, isCandle, CHART_RETENTION_MS + 1)).toEqual([]);
  });
});

describe("CVD/OI display formatting", () => {
  it("formats positive, negative, and zero values in millions", () => {
    expect(formatMillions(12_500_000)).toBe("12.50M"); expect(formatMillions(-8_400_000)).toBe("-8.40M"); expect(formatMillions(0)).toBe("0.00M");
  });
  it("uses the no-data placeholder for null and nonfinite values", () => {
    expect(formatMillions(null)).toBe("--"); expect(formatMillions(Number.NaN)).toBe("--"); expect(formatMillions(Infinity)).toBe("--");
  });
});
