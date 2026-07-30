import { describe, expect, it } from "vitest";
import { flowOnCandleTimeline } from "./candleHistory";
import { flowStatusAtCandle } from "./flowHistory";
import { NativePriceAxisLabelOptions, PriceLabelSource, updateLatestNativePriceAxisLabels, updateNativePriceAxisLabels } from "./priceLabels";
import type { Candle } from "./data";

const candles = [100, 160, 220].map((time, index) => ({
  time,
  open: 10 + index,
  high: 12 + index,
  low: 9 + index,
  close: 11 + index,
  volume: 1,
})) as Candle[];

const projectedValues = (
  points: Array<{ time: number; value: number }>,
) => flowOnCandleTimeline(candles, points, 60).flatMap(point =>
  "value" in point ? [{ time: Number(point.time), value: point.value }] : [],
);

const sources: PriceLabelSource[] = [
  { id: "cvd", name: "CVD", color: "#7c3aed", values: projectedValues([
    { time: 110, value: 1_000_000 },
    { time: 170, value: 2_000_000 },
    { time: 230, value: 3_000_000 },
  ]) },
  { id: "oi", name: "OI", color: "#0ea5e9", values: projectedValues([
    { time: 115, value: 9_000_000 },
    { time: 235, value: 11_000_000 },
  ]) },
];

function capture() {
  const applied = new Map<string, NativePriceAxisLabelOptions>();
  const labels = Object.fromEntries(sources.map(source => [
    source.id,
    { applyOptions: (options: NativePriceAxisLabelOptions) => applied.set(source.id, options) },
  ]));
  return { applied, labels };
}

describe("shared historical CVD/OI axis labels", () => {
  it("uses the exact projected candle timestamp instead of the latest value", () => {
    const { applied, labels } = capture();
    updateNativePriceAxisLabels(160, sources, labels);
    expect(applied.get("cvd")).toMatchObject({ price: 2_000_000, axisLabelVisible: true });
    expect(applied.get("oi")).toMatchObject({ axisLabelVisible: false });
  });

  it("does not borrow a future, nearest, interpolated, or latest value", () => {
    const { applied, labels } = capture();
    updateNativePriceAxisLabels(190, sources, labels);
    expect([...applied.values()].every(value => value.axisLabelVisible === false)).toBe(true);
  });

  it("restores each flow series' own latest confirmed value on mouse leave", () => {
    const { applied, labels } = capture();
    updateLatestNativePriceAxisLabels(sources, labels);
    expect(applied.get("cvd")).toMatchObject({ price: 3_000_000, axisLabelVisible: true });
    expect(applied.get("oi")).toMatchObject({ price: 11_000_000, axisLabelVisible: true });
  });

  it("reports no confirmed data at whitespace without borrowing latest", () => {
    const status = flowStatusAtCandle(160, 60, [
      { time: 100, value: 1, status: "VALID", source_complete: true, partial_after_gap: false },
      { time: 160, status: "WHITESPACE", source_complete: false, partial_after_gap: false },
      { time: 220, value: 3, status: "PARTIAL_AFTER_GAP", source_complete: false, partial_after_gap: true },
    ]);
    expect(status).toEqual({ status: "WHITESPACE", value: null, partial: false });
  });
});
