import { afterEach, describe, expect, it } from "vitest";
import { formatMillions } from "./chartState";
import {
  CandleSelectionGuard,
  __resetCandleHistoryForTests,
  flowOnCandleTimeline,
  hydrateCandleHistory,
  mergeCandlePages,
  movingAverageSeries,
  olderCandlePageRequest,
  retainCandlePage,
  withPreservedTimeRange,
} from "./candleHistory";
import { Candle } from "./data";
import { visibleRangeFromCandles } from "./flowHistory";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

const storage = new MemoryStorage();
Object.defineProperty(globalThis, "window", {
  value: { localStorage: storage, __PAPER_API_URL__: "" },
  configurable: true,
});

const candle = (time: number, close = time): Candle => ({
  time: time as Candle["time"],
  open: close,
  high: close + 1,
  low: close - 1,
  close,
  volume: 10,
});

afterEach(() => {
  storage.clear();
  __resetCandleHistoryForTests();
});

describe("candle master timeline", () => {
  it("prevents CVD/OI density from changing the candle master range", () => {
    const candles = [candle(3600), candle(7200), candle(10800)];
    const projected = flowOnCandleTimeline(candles, [
      { time: 3601, value: 1 },
      { time: 3602, value: 2 },
      { time: 7201, value: 3 },
    ], 3600);
    expect(projected.map(point => Number(point.time))).toEqual([3600, 7200, 10800]);
    expect(projected[2]).toEqual({ time: 10800 });
  });

  it("preserves and synchronizes an absolute timestamp range, not logical indices", () => {
    const calls: unknown[] = [];
    const scale = {
      getVisibleRange: () => ({ from: 1_700_000_000, to: 1_700_003_600 }),
      setVisibleRange: (range: unknown) => calls.push(range),
    };
    withPreservedTimeRange(scale, () => calls.push("updated"));
    expect(calls).toEqual(["updated", { from: 1_700_000_000, to: 1_700_003_600 }]);
  });

  it("does not let a short incremental response replace retained history", () => {
    retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "1h",
      points: [candle(1), candle(2), candle(3)],
    });
    expect(retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "1h",
      points: [candle(3, 30)],
    })).toEqual([candle(1), candle(2), candle(3, 30)]);
  });

  it("merges candle pages by timestamp without duplicates", () => {
    expect(mergeCandlePages(
      [candle(1), candle(2)],
      [candle(2, 20), candle(3)],
    )).toEqual([candle(1), candle(2, 20), candle(3)]);
  });

  it("keeps 1H state isolated from 15m pages", () => {
    retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "1h",
      points: [candle(3600)],
    });
    expect(retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "15m",
      points: [candle(4500)],
    })).toEqual([candle(3600)]);
  });

  it("rejects stale instrument and timeframe responses", () => {
    const guard = new CandleSelectionGuard();
    guard.select("ETH-USDT", "1h");
    const stale = guard.token();
    guard.select("BTC-USDT", "4h");
    expect(guard.accepts(stale)).toBe(false);
  });

  it("preserves candle history after empty and error-equivalent responses", () => {
    retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "1h",
      points: [candle(1), candle(2)],
    });
    expect(retainCandlePage("ETH-USDT", "1h", {
      instrument: "ETH-USDT",
      timeframe: "1h",
      points: [],
    })).toEqual([candle(1), candle(2)]);
    expect(hydrateCandleHistory("ETH-USDT", "1h")).toEqual([candle(1), candle(2)]);
  });

  it("keeps moving-average timestamps continuous with candle history", () => {
    const candles = Array.from({ length: 8 }, (_, index) => candle(index * 3600, index + 1));
    expect(movingAverageSeries(candles, 3).map(point => Number(point.time)))
      .toEqual(candles.slice(2).map(point => Number(point.time)));
  });

  it("requests an older candle page when zooming near the retained left edge", () => {
    const candles = Array.from({ length: 500 }, (_, index) => candle(1_000_000 + index * 3600));
    expect(olderCandlePageRequest(candles, 1_000_000 + 10 * 3600, 3600))
      .toEqual({ before: 1_000_000, limit: 500 });
    expect(olderCandlePageRequest(candles, 1_000_000 + 100 * 3600, 3600)).toBeNull();
  });

  it("derives flow-history loading from the candle timestamp range", () => {
    const candles = Array.from({ length: 300 }, (_, index) => candle(index * 3600));
    expect(visibleRangeFromCandles(candles)).toEqual({
      start: 40 * 3600,
      end: 299 * 3600,
    });
  });

  it("retains full flow alignment and millions formatting", () => {
    const candles = [candle(0), candle(3600)];
    const projected = flowOnCandleTimeline(candles, [
      { time: 60, value: 1_250_000 },
      { time: 3660, value: 2_000_000 },
    ], 3600);
    expect(projected).toEqual([
      { time: 0, value: 1_250_000 },
      { time: 3600, value: 2_000_000 },
    ]);
    expect(formatMillions(1_250_000)).toBe("1.25M");
  });

  it("normal refresh preservation never calls fitContent", () => {
    const scale = {
      getVisibleRange: () => ({ from: 1, to: 2 }),
      setVisibleRange: () => undefined,
      fitContent: () => { throw new Error("fitContent must not run"); },
    };
    expect(() => withPreservedTimeRange(scale, () => undefined)).not.toThrow();
  });
});
