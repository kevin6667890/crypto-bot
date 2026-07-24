import { afterEach, describe, expect, it, vi } from "vitest";
import { saveChartSnapshot } from "./chartState";
import {
  FlowCoverage,
  FlowHistoryResponse,
  FlowSelectionGuard,
  __resetFlowHistoryForTests,
  formatFlowCoverage,
  hydrateFlowHistory,
  historyRequestUrl,
  mergeHistoryPoints,
  olderPageRequest,
  persistedFlowInstrument,
  requestFlowHistory,
  retainServerHistory,
  visibleRangeFromCandles,
  withPreservedLogicalRange,
} from "./flowHistory";

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

const coverage = (overrides: Partial<FlowCoverage> = {}): FlowCoverage => ({
  api_version: "flow-history-v1",
  instrument: "BTC-USDT",
  series: "cvd",
  requested_start: 100,
  requested_end: 300,
  available_start: 0,
  available_end: 300,
  latest_timestamp: 300,
  raw_row_count: 3,
  returned_point_count: 3,
  resolution: "5m",
  resolution_seconds: 300,
  stale: false,
  has_history: true,
  has_more_before: true,
  has_more_after: false,
  next_before_cursor: "cursor-1",
  source: "durable persisted aggregates",
  retention_policy_version: "flow-retention-v2",
  has_gaps: false,
  gap_count: 0,
  fallback: false,
  ...overrides,
});
const response = (
  points: Array<{ time: number; value: number }>,
  overrides: Partial<FlowHistoryResponse> = {},
): FlowHistoryResponse => ({ ...coverage(), points, ...overrides });
const valid = (point: unknown): point is { time: number; value: number } => {
  const row = point as { time?: number; value?: number };
  return !!row && Number.isFinite(row.time) && Number.isFinite(row.value);
};

afterEach(() => {
  storage.clear();
  __resetFlowHistoryForTests();
  vi.restoreAllMocks();
});

describe("range-driven flow history", () => {
  it("derives the initial request from the current visible candle range", () => {
    const candles = Array.from({ length: 300 }, (_, time) => ({ time }));
    expect(visibleRangeFromCandles(candles)).toEqual({ start: 40, end: 299 });
  });

  it("builds a cursor request when scrolling backward", () => {
    const older = olderPageRequest(coverage(), [{ time: 100, value: 1 }], 110, 30);
    expect(older).toEqual({ start: 0, end: 110, maxPoints: 1200, cursor: "cursor-1" });
    const url = historyRequestUrl({ instrument: "BTC-USDT", series: "cvd", ...older! });
    expect(url).toContain("cursor=cursor-1");
  });

  it("merges pages by timestamp without duplicates and lets server values win", () => {
    expect(mergeHistoryPoints(
      [{ time: 1, value: 1 }, { time: 2, value: 2 }],
      [{ time: 2, value: 20 }, { time: 3, value: 3 }],
    )).toEqual([{ time: 1, value: 1 }, { time: 2, value: 20 }, { time: 3, value: 3 }]);
  });

  it("preserves retained points after empty and error responses", async () => {
    retainServerHistory("15m", response([{ time: 1, value: 1 }]));
    expect(retainServerHistory("15m", response([]))).toEqual([{ time: 1, value: 1 }]);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(requestFlowHistory({
      instrument: "BTC-USDT", series: "cvd", start: 1, end: 2,
    })).rejects.toThrow("offline");
    expect(hydrateFlowHistory("BTC-USDT", "15m", "cvd")).toEqual([{ time: 1, value: 1 }]);
  });

  it("rejects stale instrument selections and keeps timeframe state isolated", () => {
    const guard = new FlowSelectionGuard();
    guard.select("BTC-USDT", "15m");
    const stale = guard.token();
    guard.select("ETH-USDT", "1h");
    expect(guard.accepts(stale)).toBe(false);
    expect(hydrateFlowHistory("ETH-USDT", "15m", "cvd")).toEqual([]);
  });

  it("maps perpetual candle instruments to persisted flow instruments", () => {
    expect(persistedFlowInstrument("ETH-USDT-SWAP")).toBe("ETH-USDT");
    expect(persistedFlowInstrument("BTC-USDT")).toBe("BTC-USDT");
  });

  it("hydrates retained data after a remount", () => {
    retainServerHistory("15m", response([{ time: 1, value: 8 }]));
    expect(hydrateFlowHistory("BTC-USDT", "15m", "cvd")).toEqual([{ time: 1, value: 8 }]);
  });

  it("preserves the logical visible range while series data changes", () => {
    const calls: unknown[] = [];
    const scale = {
      getVisibleLogicalRange: () => ({ from: 10, to: 20 }),
      setVisibleLogicalRange: (range: unknown) => calls.push(range),
    };
    withPreservedLogicalRange(scale, () => calls.push("updated"));
    expect(calls).toEqual(["updated", { from: 10, to: 20 }]);
  });

  it("renders coverage resolution, staleness, and gap state", () => {
    const text = formatFlowCoverage(coverage({ stale: true, has_gaps: true, gap_count: 2 }));
    expect(text).toContain("5m");
    expect(text).toContain("stale");
    expect(text).toContain("2 gaps");
    expect(formatFlowCoverage()).toBe("No persisted coverage");
  });

  it("prefers server history over a no-expiry local fallback", () => {
    saveChartSnapshot(
      { instrument: "BTC-USDT", timeframe: "15m", series: "cvd" },
      [{ time: 1, value: 2 }],
      valid,
      0,
    );
    expect(hydrateFlowHistory("BTC-USDT", "15m", "cvd")).toEqual([{ time: 1, value: 2 }]);
    expect(retainServerHistory("15m", response([{ time: 1, value: 9 }]))).toEqual([{ time: 1, value: 9 }]);
  });
});
