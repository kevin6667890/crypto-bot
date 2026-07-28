import { describe, expect, it } from "vitest";
import { arrangePriceLabels, exactValuesAtTimestamp, formatChartPrice, PriceLabelSource } from "./priceLabels";

const sources: PriceLabelSource[] = [
  { id: "candles", name: "K线", color: "#00b37e", values: [{ time: 10, value: 1881.99 }, { time: 20, value: 1882.5 }] },
  { id: "ema20", name: "EMA20", color: "#2563eb", values: [{ time: 10, value: 1884.67 }, { time: 20, value: 1885 }] },
  { id: "ma60", name: "MA60", color: "#f59e0b", values: [{ time: 10, value: 1884 }] },
  { id: "ma200", name: "MA200", color: "#7c3aed", values: [{ time: 20, value: 1883.08 }] },
];

describe("multi-series price labels", () => {
  it("returns every configured series at the exact crosshair timestamp with its own color", () => {
    expect(exactValuesAtTimestamp(10, sources)).toEqual([
      { id: "candles", name: "K线", color: "#00b37e", value: 1881.99 },
      { id: "ema20", name: "EMA20", color: "#2563eb", value: 1884.67 },
      { id: "ma60", name: "MA60", color: "#f59e0b", value: 1884 },
    ]);
  });

  it("never borrows a future value and hides an unseeded moving average", () => {
    expect(exactValuesAtTimestamp(15, sources)).toEqual([]);
    expect(exactValuesAtTimestamp(10, sources).some(label => label.id === "ma200")).toBe(false);
  });

  it("separates close prices deterministically without changing their values", () => {
    const arranged = arrangePriceLabels([
      { id: "candles", name: "K线", color: "#00b37e", value: 100, coordinate: 50, order: 0 },
      { id: "ema20", name: "EMA20", color: "#2563eb", value: 100.01, coordinate: 52, order: 1 },
      { id: "ma60", name: "MA60", color: "#f59e0b", value: 100.02, coordinate: 51, order: 2 },
    ], 21, 10, 100);
    expect(arranged.map(label => label.value)).toEqual([100, 100.01, 100.02]);
    const sortedTops = arranged.map(label => label.top).sort((a, b) => a - b);
    expect(sortedTops[1] - sortedTops[0]).toBeGreaterThanOrEqual(21);
    expect(sortedTops[2] - sortedTops[1]).toBeGreaterThanOrEqual(21);
  });

  it("formats using the chart's two-decimal instrument convention", () => {
    expect(formatChartPrice(1881.9)).toBe("1,881.90");
  });
});
