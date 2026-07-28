import { describe, expect, it } from "vitest";
import { exactValuesAtTimestamp, NativePriceAxisLabelOptions, PriceLabelSource, updateLatestNativePriceAxisLabels, updateNativePriceAxisLabels } from "./priceLabels";

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

  it("updates four native price-axis labels with colors and values but no title text", () => {
    const applied = new Map<string, NativePriceAxisLabelOptions>();
    const labels = Object.fromEntries(sources.map(source => [
      source.id,
      { applyOptions: (options: NativePriceAxisLabelOptions) => applied.set(source.id, options) },
    ]));
    updateNativePriceAxisLabels(20, sources, labels);
    expect(applied.get("candles")).toMatchObject({ price: 1882.5, color: "#00b37e", lineVisible: false, axisLabelVisible: true });
    expect(applied.get("ema20")).toMatchObject({ price: 1885, color: "#2563eb", lineVisible: false, axisLabelVisible: true });
    expect(applied.get("ma60")).toMatchObject({ color: "#f59e0b", axisLabelVisible: false });
    expect(applied.get("ma200")).toMatchObject({ price: 1883.08, color: "#7c3aed", axisLabelVisible: true });
    expect([...applied.values()].every(options => !("title" in options))).toBe(true);
    expect([...applied.values()].every(options => options.axisLabelColor === options.color)).toBe(true);
  });

  it("restores every available latest value when the mouse leaves", () => {
    const latestSources = sources.map(source => source.id === "ma60"
      ? { ...source, values: [...source.values, { time: 20, value: 1884 }] }
      : source);
    const applied: NativePriceAxisLabelOptions[] = [];
    const labels = Object.fromEntries(latestSources.map(source => [
      source.id,
      { applyOptions: (options: NativePriceAxisLabelOptions) => applied.push(options) },
    ]));
    const values = updateLatestNativePriceAxisLabels(latestSources, labels);
    expect(values.map(value => value.id)).toEqual(["candles", "ema20", "ma60", "ma200"]);
    expect(applied.every(options => options.axisLabelVisible)).toBe(true);
  });
});
