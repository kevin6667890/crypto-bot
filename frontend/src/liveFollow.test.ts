import { describe, expect, it } from "vitest";
import { ChartFollowRegistry, isAtLatestEdge, RangeChangeSource, rangeAtLatest, synchronizeLiveViewport } from "./liveFollow";
import { mergeCandlePages, flowOnCandleTimeline } from "./candleHistory";
import { mergeHistoryPoints } from "./flowHistory";
import { Candle } from "./data";

const candle = (time: number, close = time): Candle => ({
  time: time as Candle["time"], open: close, high: close, low: close, close, volume: 1,
});

describe("chart live follow", () => {
  it("defaults to following latest and preserves window width on new data", () => {
    const registry = new ChartFollowRegistry();
    expect(registry.state("BTC:15m").mode).toBe("FOLLOWING_LATEST");
    const shifted = rangeAtLatest({ from: 40, to: 99 }, 100);
    expect(shifted).toEqual({ from: 41, to: 100 });
    expect(shifted.to - shifted.from).toBe(59);
  });

  it("manual left scroll pauses while incoming data does not move history", () => {
    const registry = new ChartFollowRegistry();
    expect(registry.onVisibleRange("BTC:15m", { from: 20, to: 70 }, 99).mode).toBe("VIEWING_HISTORY");
    expect(registry.onData("BTC:15m", true)).toEqual({ mode: "VIEWING_HISTORY", hasNewData: true });
    const current = { from: 20, to: 70 };
    expect(registry.state("BTC:15m").mode === "VIEWING_HISTORY" ? current : rangeAtLatest(current, 100)).toBe(current);
  });

  it("return control and manual right-edge return restore following", () => {
    const registry = new ChartFollowRegistry();
    registry.onVisibleRange("BTC:15m", { from: 20, to: 70 }, 99);
    expect(registry.follow("BTC:15m")).toEqual({ mode: "FOLLOWING_LATEST", hasNewData: false });
    registry.onVisibleRange("BTC:15m", { from: 20, to: 70 }, 99);
    expect(registry.onVisibleRange("BTC:15m", { from: 48, to: 98 }, 99).mode).toBe("FOLLOWING_LATEST");
    expect(isAtLatestEdge({ from: 48, to: 97.49 }, 99)).toBe(false);
    expect(isAtLatestEdge({ from: 48, to: 97.5 }, 99)).toBe(true);
  });

  it("isolates instrument/timeframe state", () => {
    const registry = new ChartFollowRegistry();
    registry.onVisibleRange("BTC:15m", { from: 1, to: 3 }, 20);
    expect(registry.state("BTC:15m").mode).toBe("VIEWING_HISTORY");
    expect(registry.state("BTC:1h").mode).toBe("FOLLOWING_LATEST");
    expect(registry.state("ETH:15m").mode).toBe("FOLLOWING_LATEST");
  });

  it("merges duplicates in order and stale pages cannot remove the newest candle", () => {
    const current = mergeCandlePages([candle(1), candle(2)], [candle(2, 22), candle(3)]);
    const afterStale = mergeCandlePages(current, [candle(1, 11), candle(2, 22)]);
    expect(afterStale.map(point => point.time)).toEqual([1, 2, 3]);
    expect(afterStale[1].close).toBe(22);
    expect(mergeHistoryPoints([{ time: 2, value: 1 }], [{ time: 2, value: 2 }, { time: 1, value: 0 }]))
      .toEqual([{ time: 1, value: 0 }, { time: 2, value: 2 }]);
  });

  it("keeps candles as the master timeline for dense CVD and OI", () => {
    const candles = [candle(0, 1), candle(3600, 2)];
    const dense = Array.from({ length: 120 }, (_, index) => ({ time: index * 60, value: index }));
    const projected = flowOnCandleTimeline(candles, dense, 3600);
    expect(projected).toHaveLength(candles.length);
    expect(projected.map(point => point.time)).toEqual([0, 3600]);
  });

  it("ignores programmatic range callbacks but accepts real user interaction", () => {
    const source = new RangeChangeSource();
    source.beginInternal();
    expect(source.shouldApplyVisibleRange()).toBe(false);
    source.beginUser();
    expect(source.shouldApplyVisibleRange()).toBe(false);
    source.endInternal();
    expect(source.shouldApplyVisibleRange()).toBe(true);
    source.endUser();
    expect(source.shouldApplyVisibleRange()).toBe(false);
  });

  it("scrolls every following refresh and preserves the historical viewport", () => {
    let visible = { from: 40, to: 99 };
    let scrolls = 0;
    let fitCalls = 0;
    const width = () => visible.to - visible.from;
    const scale = {
      scrollToRealTime: () => { scrolls += 1; visible = { from: visible.from + 1, to: visible.to + 1 }; },
      setVisibleRange: (range: typeof visible) => { visible = range; },
      fitContent: () => { fitCalls += 1; },
    };
    const initialWidth = width();
    synchronizeLiveViewport(scale, "FOLLOWING_LATEST", null);
    synchronizeLiveViewport(scale, "FOLLOWING_LATEST", null);
    expect(scrolls).toBe(2);
    expect(width()).toBe(initialWidth);
    expect(fitCalls).toBe(0);
    synchronizeLiveViewport(scale, "VIEWING_HISTORY", { from: 10, to: 30 });
    expect(visible).toEqual({ from: 10, to: 30 });
  });
});
